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

Members (T3-54)
- A member's conversation belongs to user_id, never to a session, and does not expire after an hour: the cleanup job
  removes it MEMBER_RETENTION after the last question. A guest sending its id gets a new conversation, and so does a
  member sending an id that is not theirs (an unknown id is never adopted).
- One turn at a time: a second question while one runs raises Busy (until it ends, or BUSY_LIMIT after a crash).
- A finished turn is saved as one transaction; a conversation deleted while it ran is not saved again.
- With save_history off a new conversation is not saved (an already saved one goes on). Like a guest's it ends an hour
  after the last question. retention_days (90 or 30) is the member's own limit.
- A login is a random token; only its SHA-256 is stored. It ends LOGIN_TTL after signing in (no extension).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, and_, create_engine, delete, event, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool

from kosis_store.db import session_scope
from kosis_store.models import Base, Conversation, LoginSession, Message, User
from kosis_store.service_models import ChatRequestRow, ChatSession, Feedback

SESSION_TTL = timedelta(minutes=int(os.environ.get("KOSIS_SESSION_TTL_MINUTES", "60")))  # PL-BIWRBV: 1 hour
FEEDBACK_WINDOW = timedelta(hours=24)  # api.md 3: guest answers take feedback for 24 hours
LOGIN_TTL = timedelta(days=14)  # T3-54 decision: 14 days from signing in
MEMBER_RETENTION = timedelta(days=90)  # 11/19 decision
RETENTION_DAYS = (90, 30)  # what a member may choose
BUSY_LIMIT = timedelta(minutes=5)  # a turn that never ended (crash) stops blocking the conversation after this
TITLE_MAX = 40
PAGE = 30


class Busy(Exception):
    """Another question of the same conversation is still being answered."""


class BadCursor(ValueError):
    """A list cursor this server did not make."""


@dataclass(frozen=True)
class Opened:
    thread_id: str
    expired: bool  # the thread sent had expired; thread_id is a new conversation
    new: bool  # no earlier turn will be continued


@dataclass(frozen=True)
class Member:
    id: uuid.UUID
    nickname: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_id(value: str | None) -> uuid.UUID | None:
    """A conversation id as UUID; None for anything else (NUL, over-long text, ...)."""
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


def title_of(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= TITLE_MAX else text[:TITLE_MAX - 1] + "…"


def _encode_cursor(at: datetime, cid: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{_aware(at).isoformat()}|{cid}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        at, cid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        when = datetime.fromisoformat(at)
        if when.tzinfo is None:
            raise ValueError("no zone")
        return when, uuid.UUID(cid)
    except (ValueError, binascii.Error, UnicodeError) as e:
        raise BadCursor(cursor[:20]) from e


def _aware(t: datetime) -> datetime:
    """SQLite gives naive datetimes back; they were stored as UTC."""
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class ServiceStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    @classmethod
    def in_memory(cls) -> ServiceStore:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(engine, tables=[m.__table__ for m in (
            User, LoginSession, Conversation, Message, ChatSession, ChatRequestRow, Feedback)])
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
            if row is not None and row.user_id is None and row.session_id == session_id:
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
            return row is not None and row.user_id is None and row.session_id == session_id

    def delete_thread(self, thread_id: str, session_id: str | None) -> bool:
        """Remove a conversation of this session (its requests too). False when it is not there or not theirs."""
        with session_scope(self.engine) as s:
            row = s.get(ChatSession, thread_id)
            if row is None or row.user_id is not None or row.session_id != session_id:
                return False
            s.delete(row)
            s.execute(delete(ChatRequestRow).where(ChatRequestRow.thread_id == thread_id))
            return True

    def expired_threads(self, now: datetime) -> list[str]:
        with session_scope(self.engine) as s:
            idle = s.execute(select(ChatSession.thread_id, ChatSession.user_id).where(
                ChatSession.last_used_at < now - SESSION_TTL)).all()
            saved = set(s.scalars(select(Conversation.id).where(Conversation.user_id.is_not(None))))
        return [t for t, user in idle if user is None or parse_id(t) not in saved]

    # ------------------------------------------------------------ members (T3-54)

    def login(self, kakao_id: str, nickname: str, now: datetime, old_token: str | None = None) -> str:
        """Find or create the member, end this browser's earlier login, and return a new cookie token."""
        for attempt in (0, 1):
            try:
                with session_scope(self.engine) as s:
                    user = s.scalar(select(User).where(User.kakao_id == kakao_id))
                    if user is None:
                        user = User(id=uuid.uuid4(), kakao_id=kakao_id, nickname=nickname, created_at=now,
                                    last_login_at=now)
                        s.add(user)
                        s.flush()
                    user.nickname, user.last_login_at = nickname, now
                    if old_token:
                        s.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(old_token)))
                    token = secrets.token_urlsafe(32)
                    s.add(LoginSession(token_hash=token_hash(token), user_id=user.id, created_at=now,
                                       expires_at=now + LOGIN_TTL))
                return token
            except IntegrityError:  # two first logins at once: the other one made the member
                if attempt:
                    raise
        raise AssertionError("unreachable")

    def member(self, token: str | None, now: datetime) -> Member | None:
        """The signed-in member of a cookie token; None when it is missing, unknown or expired."""
        if not token:
            return None
        with session_scope(self.engine) as s:
            found = s.execute(select(User.id, User.nickname, LoginSession.expires_at)
                              .join(LoginSession, LoginSession.user_id == User.id)
                              .where(LoginSession.token_hash == token_hash(token))).first()
            return None if found is None or _aware(found[2]) <= now else Member(found[0], found[1])

    def logout(self, token: str | None) -> None:
        if token:
            with session_scope(self.engine) as s:
                s.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(token)))

    def open_member_thread(self, thread_id: str | None, user_id: uuid.UUID, now: datetime) -> Opened:
        """The member's conversation to continue, or a new one; marked busy until end_turn. Raises Busy."""
        cid = parse_id(thread_id)
        with session_scope(self.engine) as s:
            row = s.get(ChatSession, str(cid), with_for_update=True) if cid else None
            if row is not None and row.user_id == user_id:
                if row.busy_until is not None and _aware(row.busy_until) > now:
                    raise Busy(row.thread_id)
                if now - _aware(row.last_used_at) > SESSION_TTL and s.get(Conversation, cid) is None:
                    fresh = str(uuid.uuid4())  # an unsaved conversation ends like a guest's
                    s.add(ChatSession(thread_id=fresh, user_id=user_id, created_at=now, last_used_at=now,
                                      busy_until=now + BUSY_LIMIT))
                    return Opened(fresh, expired=True, new=True)
                row.last_used_at, row.busy_until = now, now + BUSY_LIMIT
                return Opened(row.thread_id, expired=False, new=False)
            fresh = str(uuid.uuid4())
            s.add(ChatSession(thread_id=fresh, user_id=user_id, created_at=now, last_used_at=now,
                              busy_until=now + BUSY_LIMIT))
            return Opened(fresh, expired=False, new=True)

    def save_turn(self, thread_id: str, user_id: uuid.UUID, question: str, answer: str, result: dict,
                  now: datetime) -> bool:
        """Save a finished member turn (question and answer together). False: not saved, because the conversation was
        deleted meanwhile or is a new one of a member who keeps no history."""
        cid = uuid.UUID(thread_id)
        with session_scope(self.engine) as s:
            row = s.get(ChatSession, thread_id, with_for_update=True)  # one writer per conversation
            if row is None or row.user_id != user_id:
                return False
            conv = s.get(Conversation, cid)
            if conv is None:
                if not s.scalar(select(User.save_history).where(User.id == user_id)):
                    return False
                s.add(Conversation(id=cid, user_id=user_id, title=title_of(question), created_at=now, updated_at=now))
                s.flush()
            else:
                conv.updated_at = now
            turn = (s.scalar(select(func.max(Message.turn)).where(Message.conversation_id == cid)) or 0) + 1
            s.add_all([Message(conversation_id=cid, turn=turn, role="user", content=question, created_at=now),
                       Message(conversation_id=cid, turn=turn, role="assistant", content=answer, result=result,
                               created_at=now)])
            return True

    def end_turn(self, thread_id: str) -> bool:
        """The turn is over: the next question may start. False when the conversation is gone."""
        with session_scope(self.engine) as s:
            return s.execute(update(ChatSession).where(ChatSession.thread_id == thread_id)
                             .values(busy_until=None)).rowcount > 0

    def conversations(self, user_id: uuid.UUID, cursor: str | None = None,
                      limit: int = PAGE) -> tuple[list[Conversation], str | None]:
        """The member's conversations, last asked first, and the cursor of the next page."""
        query = select(Conversation).where(Conversation.user_id == user_id)
        if cursor:
            at, cid = _decode_cursor(cursor)
            query = query.where(or_(Conversation.updated_at < at,
                                    and_(Conversation.updated_at == at, Conversation.id < cid)))
        with session_scope(self.engine) as s:
            rows = list(s.scalars(query.order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                                  .limit(limit + 1)))
        more = len(rows) > limit
        rows = rows[:limit]
        return rows, _encode_cursor(rows[-1].updated_at, rows[-1].id) if more else None

    def question_counts(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
        with session_scope(self.engine) as s:
            rows = s.execute(select(Message.conversation_id, func.count()).where(
                Message.conversation_id.in_(ids), Message.role == "user").group_by(Message.conversation_id))
            return {cid: n for cid, n in rows}

    def account(self, user_id: uuid.UUID, changes: dict | None = None) -> User | None:
        """The member's row, after applying the settings in `changes`; None when the account is gone."""
        with session_scope(self.engine) as s:
            user = s.get(User, user_id)
            if user is not None:
                for name, value in (changes or {}).items():
                    setattr(user, name, value)
            return user

    def conversation(self, conversation_id: str, user_id: uuid.UUID) -> tuple[Conversation, list[Message]] | None:
        cid = parse_id(conversation_id)
        with session_scope(self.engine) as s:
            conv = s.get(Conversation, cid) if cid else None
            if conv is None or conv.user_id != user_id:
                return None
            return conv, list(s.scalars(select(Message).where(Message.conversation_id == cid)
                                        .order_by(Message.turn, Message.id)))

    def member_thread(self, thread_id: str, user_id: uuid.UUID) -> str | None:
        """The stored id of this member's conversation; None when it is not there or not theirs."""
        cid = parse_id(thread_id)
        with session_scope(self.engine) as s:
            row = s.get(ChatSession, str(cid)) if cid else None
            return row.thread_id if row is not None and row.user_id == user_id else None

    def member_threads(self, user_id: uuid.UUID) -> list[str]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(ChatSession.thread_id).where(ChatSession.user_id == user_id)))

    def delete_threads(self, thread_ids: list[str]) -> None:
        """Remove conversations' rows (sessions, requests, saved turns). Delete their checkpoints first."""
        if not thread_ids:
            return
        with session_scope(self.engine) as s:
            s.execute(delete(ChatRequestRow).where(ChatRequestRow.thread_id.in_(thread_ids)))
            s.execute(delete(ChatSession).where(ChatSession.thread_id.in_(thread_ids)))
            s.execute(delete(Conversation).where(Conversation.user_id.is_not(None), Conversation.id.in_(
                [cid for t in thread_ids if (cid := parse_id(t))])))

    def delete_member(self, user_id: uuid.UUID) -> None:
        """Withdrawal: the member and, by foreign key, every login, conversation, turn, request and feedback of theirs."""
        with session_scope(self.engine) as s:
            s.execute(delete(User).where(User.id == user_id))

    def old_member_threads(self, now: datetime) -> list[str]:
        with session_scope(self.engine) as s:
            return list(s.scalars(select(ChatSession.thread_id).join(User, User.id == ChatSession.user_id).where(or_(
                *(and_(User.retention_days == d, ChatSession.last_used_at < now - timedelta(days=d))
                  for d in RETENTION_DAYS)))))

    def purge_logins(self, now: datetime) -> int:
        with session_scope(self.engine) as s:
            return s.execute(delete(LoginSession).where(LoginSession.expires_at <= now)).rowcount

    # ------------------------------------------------------------ requests and feedback

    def record_request(self, request_id: str, thread_id: str, session_id: str | None, now: datetime,
                       user_id: uuid.UUID | None = None) -> None:
        with session_scope(self.engine) as s:
            s.merge(ChatRequestRow(request_id=request_id, thread_id=thread_id, session_id=session_id, created_at=now,
                                   user_id=user_id))

    def request_owner(self, request_id: str) -> tuple[str | None, datetime, uuid.UUID | None] | None:
        """(session, time, member) of a question; member is None for a guest."""
        with session_scope(self.engine) as s:
            row = s.get(ChatRequestRow, request_id)
            return None if row is None else (row.session_id, _aware(row.created_at), row.user_id)

    def save_feedback(self, request_id: str, session_id: str | None, value: str, category: str | None,
                      reason: str | None, now: datetime, user_id: uuid.UUID | None = None) -> tuple[uuid.UUID, bool]:
        """Insert or replace the answer's feedback; returns (feedback id, replaced an earlier one)."""
        with session_scope(self.engine) as s:
            row = s.scalar(select(Feedback).where(Feedback.request_id == request_id))
            if row is None:
                row = Feedback(id=uuid.uuid4(), request_id=request_id, session_id=session_id, user_id=user_id,
                               value=value, category=category, reason=reason, created_at=now, updated_at=now)
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
