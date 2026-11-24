"""rewritten questions: request_rewrites, request_logs.rewrite_kind / interpreted_by (T3-50)

Revision ID: 0004_request_rewrites
Revises: 0003_pgvector
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_request_rewrites"
down_revision = "0003_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("rewrite_kind", sa.String(16), nullable=True))
    op.add_column("request_logs", sa.Column("interpreted_by", sa.String(8), nullable=True))
    op.create_check_constraint(
        "ck_request_logs_rewrite_kind", "request_logs",
        "rewrite_kind IN ('new', 'carry_over', 'reference', 'chart_only', 'multi')")
    op.create_check_constraint(
        "ck_request_logs_interpreted_by", "request_logs", "interpreted_by IN ('rule', 'llm', 'none')")
    op.create_table(
        "request_rewrites",
        sa.Column("request_id", sa.String(32), sa.ForeignKey("request_logs.request_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("seq", sa.SmallInteger(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("text_len", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('resolved', 'sub_question')", name="ck_request_rewrites_kind"),
        sa.CheckConstraint("seq >= 0", name="ck_request_rewrites_seq"),
    )
    op.create_index("ix_request_rewrites_created_at", "request_rewrites", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_request_rewrites_created_at", table_name="request_rewrites")
    op.drop_table("request_rewrites")
    op.drop_constraint("ck_request_logs_interpreted_by", "request_logs", type_="check")
    op.drop_constraint("ck_request_logs_rewrite_kind", "request_logs", type_="check")
    op.drop_column("request_logs", "interpreted_by")
    op.drop_column("request_logs", "rewrite_kind")
