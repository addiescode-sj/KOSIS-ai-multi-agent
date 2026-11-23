"""service api: chat_sessions, chat_requests, feedback (T3-51, docs/service/api.md 0, 3, 4)

Conversation ownership and guest expiry (chat_sessions), request owners for feedback (chat_requests), and the last
👍/👎 of each answer (feedback). The conversation state itself is in LangGraph's checkpoint tables, which
PostgresSaver.setup() creates outside Alembic (migrations/env.py leaves them out of autogenerate).

Open PR #37 (T3-49) also adds a revision after 0003_pgvector; whichever merges second moves its down_revision.

Revision ID: 0004_service_api
Revises: 0003_pgvector
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_service_api"
down_revision = "0003_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("thread_id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_sessions_session_id", "chat_sessions", ["session_id"])
    op.create_index("ix_chat_sessions_last_used_at", "chat_sessions", ["last_used_at"])
    op.create_table(
        "chat_requests",
        sa.Column("request_id", sa.String(32), primary_key=True),
        sa.Column("thread_id", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_requests_thread_id", "chat_requests", ["thread_id"])
    op.create_index("ix_chat_requests_created_at", "chat_requests", ["created_at"])
    op.create_table(
        "feedback",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.String(32), nullable=False, unique=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("value", sa.String(8), nullable=False),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("value IN ('up', 'down')", name="ck_feedback_value"),
    )
    op.create_index("ix_feedback_updated_at", "feedback", ["updated_at"])


def downgrade() -> None:
    op.drop_table("feedback")
    op.drop_table("chat_requests")
    op.drop_table("chat_sessions")
