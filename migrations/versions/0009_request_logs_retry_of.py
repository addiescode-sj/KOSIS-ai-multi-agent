"""request_logs.retry_of (T3-68)

The request_id the screen asked to retry, so the retry rate is counted from the log (plans/T3-68.md option B).
Nullable: rows written before this have none.

Revision ID: 0009_request_logs_retry_of
Revises: 0008_account_settings
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_request_logs_retry_of"
down_revision = "0008_account_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("request_logs", sa.Column("retry_of", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("request_logs", "retry_of")
