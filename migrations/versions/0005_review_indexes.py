"""data review indexes (T3-52): one outcome / one user over a period

Revision ID: 0005_review_indexes
Revises: 0004_request_rewrites
Create Date: 2026-11-24

(session_id, created_at) replaces the single-column session index: its leading column serves session_id alone.
No feedback index: measured on made-up bulk rows, a (request_id, client_ts DESC, ...) feedback index made the T3-49
metric queries slower (the planner took it for their DISTINCT ON over all feedback, 76 -> 163 ms), while the review
lookup is already fast on ix_ui_events_request_id (plans/T3-52.md Result).
Plain CREATE INDEX (it locks writes while it builds): the log tables are small today. On a large table, build them
with CREATE INDEX CONCURRENTLY outside a transaction instead.
"""

from alembic import op

revision = "0005_review_indexes"
down_revision = "0004_request_rewrites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_request_logs_outcome_created", "request_logs", ["outcome", "created_at"])
    op.create_index("ix_request_logs_session_created", "request_logs", ["session_id", "created_at"])
    op.drop_index("ix_request_logs_session_id", table_name="request_logs")


def downgrade() -> None:
    op.create_index("ix_request_logs_session_id", "request_logs", ["session_id"])
    op.drop_index("ix_request_logs_session_created", table_name="request_logs")
    op.drop_index("ix_request_logs_outcome_created", table_name="request_logs")
