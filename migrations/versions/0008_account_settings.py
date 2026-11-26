"""account settings (T3-54 Change 1, the settings screen of PR #46)

What a member chooses on the settings screen, and the member of a feedback so that withdrawal removes it.

Revision ID: 0008_account_settings
Revises: 0007_kakao_login
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_account_settings"
down_revision = "0007_kakao_login"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("display_name", sa.String(20), nullable=True))
    op.add_column("users", sa.Column("color", sa.String(8), server_default="blue", nullable=False))
    op.add_column("users", sa.Column("save_history", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("users", sa.Column("retention_days", sa.Integer(), server_default="90", nullable=False))
    op.create_check_constraint("ck_users_color", "users", "color IN ('blue', 'green', 'amber', 'rose', 'slate')")
    op.create_check_constraint("ck_users_retention_days", "users", "retention_days IN (30, 90)")
    op.add_column("feedback", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.execute("UPDATE feedback f SET user_id = r.user_id FROM chat_requests r WHERE r.request_id = f.request_id")
    op.create_index("ix_feedback_user_id", "feedback", ["user_id"])
    op.create_foreign_key("fk_feedback_user_id", "feedback", "users", ["user_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint("fk_feedback_user_id", "feedback", type_="foreignkey")
    op.drop_index("ix_feedback_user_id", table_name="feedback")
    op.drop_column("feedback", "user_id")
    op.drop_constraint("ck_users_retention_days", "users", type_="check")
    op.drop_constraint("ck_users_color", "users", type_="check")
    for column in ("retention_days", "save_history", "color", "display_name"):
        op.drop_column("users", column)
