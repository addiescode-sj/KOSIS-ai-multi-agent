"""operator app tables: feedback reviews and audit (T3-57)

Revision ID: a0001_reviews_audit
Revises:
Create Date: 2026-11-26
"""

import sqlalchemy as sa
from alembic import op

revision = "a0001_reviews_audit"
down_revision = None
branch_labels = None
depends_on = None

S = "admin"


def upgrade() -> None:
    op.create_table(
        "feedback_reviews",
        sa.Column("request_id", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("issue_key", sa.String(16), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('new', 'checking', 'fix_requested', 'no_issue')",
                           name="ck_feedback_reviews_status"),
        sa.CheckConstraint("issue_key IS NULL OR issue_key ~ '^KOS-[0-9]+$'", name="ck_feedback_reviews_issue_key"),
        sa.CheckConstraint("note IS NULL OR char_length(note) <= 200", name="ck_feedback_reviews_note"),
        schema=S,
    )
    op.create_table(
        "audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.CheckConstraint("action IN ('view_question', 'update_review', 'denied')", name="ck_audit_action"),
        schema=S,
    )
    op.create_index("ix_admin_audit_at", "audit", ["at"], schema=S)


def downgrade() -> None:
    op.drop_index("ix_admin_audit_at", table_name="audit", schema=S)
    op.drop_table("audit", schema=S)
    op.drop_table("feedback_reviews", schema=S)
