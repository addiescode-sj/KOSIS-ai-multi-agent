"""Service API tables (T3-51, docs/service/api.md sections 0, 3, 4): who owns a conversation, which request belongs to
whom, and the 👍/👎 feedback.

- chat_sessions: one row per conversation (thread_id = the LangGraph checkpointer key). `session_id` is the random UUID
  the browser sends in X-Session-Id (no user identifier). `last_used_at` decides the one-hour expiry of a guest
  conversation (PL-BIWRBV). The conversation state itself is in the checkpointer tables (PostgresSaver).
- chat_requests: one row per question, written before the `meta` event so a feedback sent right after the answer
  finds its owner (the request_logs row of T3-48 is written later, off the request thread).
- feedback: the last 👍/👎 of each answer (`request_id` is unique; a second press replaces it). `reason` is masked
  free text and is cleared after the log retention period like the other raw text (kosis_store.cleanup).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from kosis_store.models import Base

FEEDBACK_VALUES = ("up", "down")
# docs/service/api.md 3: the category codes of each value; "other" needs a reason
FEEDBACK_CATEGORIES = {
    "up": ("accurate_number", "right_table", "easy", "chart_helpful", "other"),
    "down": ("wrong_number", "wrong_table", "hard", "other"),
}


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(String(64), index=True)  # NULL: a caller that sent no session
    # T3-54: the member who owns it (thread_id = conversations.id as str(UUID)); NULL = a guest conversation
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    busy_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # a member turn is running until then
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ChatRequestRow(Base):
    __tablename__ = "chat_requests"

    request_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # = OTel trace id = meta.trace_id
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)  # T3-54
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint("value IN ('up', 'down')", name="ck_feedback_value"),
        Index("ix_feedback_updated_at", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[str] = mapped_column(String(32), unique=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(String(8))
    category: Mapped[str | None] = mapped_column(String(32))  # NULL: closed without a choice (api.md 10, proposal 3)
    reason: Mapped[str | None] = mapped_column(Text)  # masked; NULL after the retention period
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
