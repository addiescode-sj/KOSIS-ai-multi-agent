"""ORM models (plans/T3-18.md, docs/store.md).

messages.content and messages.result hold the user's question text as typed: no user identifier is stored, a
conversation is only a random UUID, and conversations are deleted by created_at after the retention period.
eval_runs holds run aggregates only (one evals/reports/ops_history.jsonl line per row): no golden question text,
answer or item id.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on PostgreSQL, plain JSON elsewhere (SQLite in offline tests)
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", passive_deletes=True,
        order_by="Message.turn, Message.id")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "turn", "role", name="uq_messages_turn_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    turn: Mapped[int] = mapped_column(Integer)  # 1, 2, ... one user question and its answer share a turn
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text)  # the question as typed, or the answer text
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # SearchResult as streamed (assistant)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class EvalRun(Base):
    """One eval run aggregate: the fields of an evals/reports/ops_history.jsonl line."""

    __tablename__ = "eval_runs"

    run_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    layer: Mapped[str] = mapped_column(String(32), index=True)
    stage: Mapped[str] = mapped_column(String(200), index=True)
    target: Mapped[str | None] = mapped_column(String(200))
    latency_p50_ms: Mapped[float | None] = mapped_column(Float)
    latency_p95_ms: Mapped[float | None] = mapped_column(Float)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(8))
    tool_failure_rate: Mapped[float | None] = mapped_column(Float)
    retry_rate: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    FIELDS = ("run_id", "layer", "stage", "target", "latency_p50_ms", "latency_p95_ms", "input_tokens",
              "output_tokens", "cost", "currency", "tool_failure_rate", "retry_rate")
