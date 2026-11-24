"""Conversation ownership, guest expiry, request owners and feedback for the service API (T3-51,
docs/service/api.md sections 0, 2, 3, 4).

One implementation over SQLAlchemy. With DATABASE_URL it uses the service PostgreSQL (tables by alembic 0005); without
it, an in-memory SQLite database, so the rules are the same in local runs and tests (state is then lost on restart).

Rules
- A conversation belongs to the session that started it. A request whose X-Session-Id differs (or that sends a
  session when the conversation has none, or the other way round) does not continue it: it starts a new one.
- A guest conversation unused for SESSION_TTL expires: the next question starts a new conversation and the response
  says `expired: true` (F-SGTERZ). The old row and its checkpoint are removed by the cleanup job.
- A thread_id nobody has used yet is adopted by the caller. Every checkpoint is created through open_thread, so an
  unknown id has no saved state to leak.
- Feedback is accepted for a request of the same session within FEEDBACK_WINDOW; the last value wins.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.pool import StaticPool

from kosis_store.db import session_scope
from kosis_store.models import Base
from kosis_store.service_models import ChatRequestRow, ChatSession, Feedback

SESSION_TTL = timedelta(minutes=int(os.environ.get("KOSIS_SESSION_TTL_MINUTES", "60")))  # PL-BIWRBV: 1 hour
FEEDBACK_WINDOW = timedelta(hours=24)  # api.md 3: guest answers take feedback for 24 hours


@dataclass(frozen=True)
class Opened:
    thread_id: str
    expired: bool  # the thread sent had expired; thread_id is a new conversation
    new: bool  # no earlier turn will be continued


def _aware(t: datetime) -> datetime:
    """SQLite gives naive datetimes back; they were stored as UTC."""
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class ServiceStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    @classmethod
    def in_memory(cls) -> ServiceStore:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine, tables=[ChatSession.__table__, ChatRequestRow.__table__, Feedback.__table__])
        return cls(engine)

    def close(self) -> None:
        """Close the engine's pooled connections (server shutdown)."""
        self.engine.dispose()

    # ------------------------------------------------------------ conversations

    def open_thread(self, thread_id: str | None, session_id: str | None, now: datetime) -> Opened:
        """The conversation this question continues, or a new one (see the module rules)."""
        with session_scope(self.engine) as s:
            row = s.get(ChatSession, thread_id) if thread_id else None
            if thread_id and row is None:  # unused id: adopt it
                s.add(ChatSession(thread_id=thread_id, session_id=session_id, created_at=now, last_used_at=now))
                return Opened(thread_id, expired=False, new=True)
            if row is not None and row.session_id == session_id:
                if now - _aware(row.last_used_at) > SESSION_TTL:
                    fresh = uuid.uuid4().hex
                    s.add(ChatSession(thread_id=fresh, session_id=session_id, created_at=now, last_used_at=now))
                    return Opened(fresh, expired=True, new=True)
                row.last_used_at = now
                return Opened(row.thread_id, expired=False, new=False)
            fresh = uuid.uuid4().hex  # no thread_id, or someone else's: a new conversation
            s.add(ChatSession(thread_id=fresh, session_id=session_id, created_at=now, last_used_at=now))
            return Opened(fresh, expired=False, new=True)

    def owns(self, thread_id: str, session_id: str | None) -> bool:
        with session_scope(self.engine) as s:
            row = s.get(ChatSession, thread_id)
            return row is not None and row.session_id == session_id

    def delete_thread(self, thread_id: str, session_id: str | None) -> bool:
        """Remove a conversation of this session (its requests too). False when it is not there or not theirs."""
        with session_scope(self.engine) as s:
            row = s.get(ChatSession, thread_id)
            if row is None or row.session_id != session_id:
                return False
            s.delete(row)
            s.execute(delete(ChatRequestRow).where(ChatRequestRow.thread_id == thread_id))
            return True

    def expired_threads(self, now: datetime) -> list[str]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(ChatSession.thread_id).where(ChatSession.last_used_at < now - SESSION_TTL)))

    # ------------------------------------------------------------ requests and feedback

    def record_request(self, request_id: str, thread_id: str, session_id: str | None, now: datetime) -> None:
        with session_scope(self.engine) as s:
            s.merge(ChatRequestRow(request_id=request_id, thread_id=thread_id, session_id=session_id, created_at=now))

    def request_owner(self, request_id: str) -> tuple[str | None, datetime] | None:
        with session_scope(self.engine) as s:
            row = s.get(ChatRequestRow, request_id)
            return None if row is None else (row.session_id, _aware(row.created_at))

    def save_feedback(self, request_id: str, session_id: str | None, value: str, category: str | None,
                      reason: str | None, now: datetime) -> tuple[uuid.UUID, bool]:
        """Insert or replace the answer's feedback; returns (feedback id, replaced an earlier one)."""
        with session_scope(self.engine) as s:
            row = s.scalar(select(Feedback).where(Feedback.request_id == request_id))
            if row is None:
                row = Feedback(id=uuid.uuid4(), request_id=request_id, session_id=session_id, value=value,
                               category=category, reason=reason, created_at=now, updated_at=now)
                s.add(row)
                return row.id, False
            row.value, row.category, row.reason, row.updated_at = value, category, reason, now
            return row.id, True


def make_service_store() -> ServiceStore:
    """The service PostgreSQL on DATABASE_URL, or in-memory SQLite when it is not set."""
    if not os.environ.get("DATABASE_URL", "").strip():
        return ServiceStore.in_memory()
    from kosis_store.db import make_engine

    return ServiceStore(make_engine())
