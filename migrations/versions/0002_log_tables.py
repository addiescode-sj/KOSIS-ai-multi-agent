"""log tables: request_logs, ui_events, ingest_rejects, daily_usage, daily_session_usage (T3-36, T3-48 KOS-170)

Revision ID: 0002_log_tables
Revises: 0001
Create Date: 2026-11-23

Merge order: PR #34 (0002_pgvector, KOS-138) and KOS-158 (feedback, users) also start from 0001. Whichever merges
second changes its own `down_revision` to the revision merged first, so the chain stays one line (alembic heads = 1).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_log_tables"
down_revision = "0001"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
NO_TRACE = "0" * 32


def upgrade() -> None:
    op.create_table(
        "request_logs",
        sa.Column("request_id", sa.String(32), primary_key=True),
        sa.Column("conversation_id", sa.String(64), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("prev_request_id", sa.String(32), nullable=True),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("is_member", sa.Boolean(), nullable=False),
        sa.Column("is_internal", sa.Boolean(), nullable=False),
        sa.Column("input_method", sa.String(16), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("question_len", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("failed_stage", sa.String(32), nullable=True),
        sa.Column("picked_key", sa.String(64), nullable=True),
        sa.Column("survey_name", sa.String(200), nullable=True),
        sa.Column("has_numbers", sa.Boolean(), nullable=False),
        sa.Column("has_chart", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("first_token_ms", sa.Integer(), nullable=True),
        sa.Column("llm_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("rephrase_of", sa.String(32), nullable=True),
        sa.Column("negative_reaction", sa.Boolean(), nullable=True),
        sa.Column("log_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("outcome IN ('answered', 'refused', 'clarify', 'failed', 'error', 'aborted')",
                           name="ck_request_logs_outcome"),
        sa.CheckConstraint("input_method IN ('direct', 'clarify_option')", name="ck_request_logs_input_method"),
        sa.CheckConstraint("NOT is_internal OR question IS NULL", name="ck_request_logs_internal_no_text"),
        sa.CheckConstraint("outcome <> 'refused' OR reason_code IS NOT NULL", name="ck_request_logs_refusal_reason"),
        sa.CheckConstraint("turn_index >= 0", name="ck_request_logs_turn_index"),
        sa.CheckConstraint(f"length(request_id) = 32 AND request_id <> '{NO_TRACE}'", name="ck_request_logs_request_id"),
    )
    op.create_index("ix_request_logs_conversation_turn", "request_logs", ["conversation_id", "turn_index"])
    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])
    op.create_index("ix_request_logs_picked_key", "request_logs", ["picked_key"])
    op.create_index("ix_request_logs_session_id", "request_logs", ["session_id"])

    op.create_table(
        "ui_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("client_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ts_adjusted", sa.Boolean(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(32), nullable=True),
        sa.Column("screen_id", sa.String(16), nullable=True),
        sa.Column("feature_id", sa.String(16), nullable=True),
        sa.Column("props", JSONType, nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("is_internal", sa.Boolean(), nullable=False),
        sa.Column("log_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("event_type IN ('citation_click', 'copy_answer', 'stream_abort', 'feedback')",
                           name="ck_ui_events_event_type"),
    )
    op.create_index("ix_ui_events_received_at", "ui_events", ["received_at"])
    op.create_index("ix_ui_events_request_id", "ui_events", ["request_id"])

    op.create_table(
        "ingest_rejects",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=True),
        sa.Column("n", sa.Integer(), nullable=False),
    )
    op.create_index("ix_ingest_rejects_received_at", "ingest_rejects", ["received_at"])

    op.create_table(
        "daily_usage",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("questions", sa.Integer(), nullable=False),
        sa.Column("sessions", sa.Integer(), nullable=False),
        sa.Column("conversations", sa.Integer(), nullable=False),
        sa.Column("avg_turns", sa.Float(), nullable=False),
        sa.Column("multi_turn_conversations", sa.Integer(), nullable=False),
        sa.Column("aborted", sa.Integer(), nullable=False),
        sa.Column("citation_clicks", sa.Integer(), nullable=False),
        sa.Column("copy_answers", sa.Integer(), nullable=False),
        sa.Column("feedback_up", sa.Integer(), nullable=False),
        sa.Column("feedback_down", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
    )


    op.create_table(
        "daily_session_usage",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), primary_key=True),
        sa.Column("is_member", sa.Boolean(), nullable=False),
        sa.Column("questions", sa.Integer(), nullable=False),
        sa.Column("conversations", sa.Integer(), nullable=False),
        sa.Column("avg_turns", sa.Float(), nullable=False),
        sa.Column("multi_turn_conversations", sa.Integer(), nullable=False),
        sa.Column("aborted", sa.Integer(), nullable=False),
        sa.Column("citation_clicks", sa.Integer(), nullable=False),
        sa.Column("copy_answers", sa.Integer(), nullable=False),
        sa.Column("feedback_up", sa.Integer(), nullable=False),
        sa.Column("feedback_down", sa.Integer(), nullable=False),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
    )

def downgrade() -> None:
    op.drop_table("daily_session_usage")
    op.drop_table("daily_usage")
    op.drop_table("ingest_rejects")
    op.drop_table("ui_events")
    op.drop_table("request_logs")
