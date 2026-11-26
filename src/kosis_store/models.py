"""ORM models (plans/T3-18.md, docs/store.md).

users / login_sessions (T3-54) hold a Kakao member number, a nickname and hashed login tokens. conversations and
messages are a member's saved turns (questions as typed): deleted by the member, on withdrawal, or 90 days after the
last question (kosis_store.cleanup). Guests have no rows here.
eval_runs holds run aggregates only (one evals/reports/ops_history.jsonl line per row): no golden question text,
answer or item id.
vector_collection / vector_item are the vector store (T3-47, PostgreSQL + pgvector): table metadata and its
embeddings, no statistic values. Phase 2 (kosis_rag.adapters.vectorstore) reads and writes them with SQL; the models
are here so the migration and the metadata stay one definition (tests/test_store.py).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (DDL, JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, event, func, true)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on PostgreSQL, plain JSON elsewhere (SQLite in offline tests)
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


# T3-47: vector_item has a pgvector column, so Base.metadata.create_all on PostgreSQL (tests, T3-48 log checks) needs
# the extension first; alembic 0003_pgvector creates it the same way. SQLite (offline tests) skips it.
event.listen(Base.metadata, "before_create",
             DDL("CREATE EXTENSION IF NOT EXISTS vector").execute_if(dialect="postgresql"))


class User(Base):
    """A member (T3-54): Kakao's member number and nickname, and what the member chose on the settings screen. Deleted
    with everything it owns on withdrawal."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("color IN ('blue', 'green', 'amber', 'rose', 'slate')", name="ck_users_color"),
        CheckConstraint("retention_days IN (30, 90)", name="ck_users_retention_days"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kakao_id: Mapped[str] = mapped_column(String(32), unique=True)
    nickname: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    display_name: Mapped[str | None] = mapped_column(String(20))  # NULL: the Kakao nickname is shown
    color: Mapped[str] = mapped_column(String(8), default="blue", server_default="blue")
    save_history: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    retention_days: Mapped[int] = mapped_column(Integer, default=90, server_default="90")


class LoginSession(Base):
    """One signed-in browser (T3-54). Only the SHA-256 of the cookie's token is stored."""

    __tablename__ = "login_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Conversation(Base):
    """A member's conversation (T3-54). user_id is NULL only for rows made before members existed."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at", "id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(String(100))  # the start of the first question
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

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


class VectorCollection(Base):
    """One collection: a table index build, the region chunks or a question index (name = the build's name)."""

    __tablename__ = "vector_collection"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    meta: Mapped[dict] = mapped_column("metadata", JSONType)  # embed_model, doc_format, dim, index_version ...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VectorItem(Base):
    """One row of a collection: id, the table key it belongs to, the embedded text, its metadata and vector."""

    __tablename__ = "vector_item"
    __table_args__ = (Index("vector_item_key", "collection", "key"),)

    collection: Mapped[str] = mapped_column(Text, ForeignKey("vector_collection.name", ondelete="CASCADE"),
                                            primary_key=True)
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str | None] = mapped_column(Text)
    document: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column("metadata", JSONType)
    embedding: Mapped[list[float]] = mapped_column(Vector())
