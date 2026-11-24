"""kakao login and member conversations (T3-54, docs/service/api.md 6)

users and login_sessions; conversations get an owner, a title and the time of the last question; chat_sessions and
chat_requests get the member they belong to. Rows from before stay guest rows (user_id NULL).

Revision ID: 0007_kakao_login
Revises: 0006_review_indexes
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_kakao_login"
down_revision = "0006_review_indexes"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kakao_id", sa.String(32), nullable=False, unique=True),
        sa.Column("nickname", sa.String(100), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("last_login_at", TZ, nullable=False),
    )
    op.create_table(
        "login_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", TZ, nullable=False),
        sa.Column("expires_at", TZ, nullable=False),
    )
    op.create_index("ix_login_sessions_user_id", "login_sessions", ["user_id"])
    op.create_index("ix_login_sessions_expires_at", "login_sessions", ["expires_at"])
    op.add_column("conversations", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.add_column("conversations", sa.Column("title", sa.String(100), nullable=True))
    op.add_column("conversations", sa.Column("updated_at", TZ, server_default=sa.func.now(), nullable=False))
    op.execute("UPDATE conversations SET updated_at = created_at")
    op.create_index("ix_conversations_user_updated", "conversations", ["user_id", "updated_at", "id"])
    op.add_column("chat_sessions", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.add_column("chat_sessions", sa.Column("busy_until", TZ, nullable=True))
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])
    op.add_column("chat_requests", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_index("ix_chat_requests_user_id", "chat_requests", ["user_id"])
    for table in ("conversations", "chat_sessions", "chat_requests"):
        op.create_foreign_key(f"fk_{table}_user_id", table, "users", ["user_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    # Member rows go first: without user_id they would look like guest rows anyone could continue. Their checkpoints
    # (LangGraph tables) are not touched here.
    op.execute("DELETE FROM chat_requests WHERE user_id IS NOT NULL")
    op.execute("DELETE FROM chat_sessions WHERE user_id IS NOT NULL")
    op.execute("DELETE FROM conversations WHERE user_id IS NOT NULL")
    for table in ("conversations", "chat_sessions", "chat_requests"):
        op.drop_constraint(f"fk_{table}_user_id", table, type_="foreignkey")
    op.drop_index("ix_chat_requests_user_id", table_name="chat_requests")
    op.drop_column("chat_requests", "user_id")
    op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "busy_until")
    op.drop_column("chat_sessions", "user_id")
    op.drop_index("ix_conversations_user_updated", table_name="conversations")
    for column in ("updated_at", "title", "user_id"):
        op.drop_column("conversations", column)
    op.drop_table("login_sessions")
    op.drop_table("users")
