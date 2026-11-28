"""User request log, draft (plans/T3-36.md, docs/log-schema.md): one row per question (one agent turn).

The row key is the OTel trace id of the turn (32 hex): the service already sends it in the first SSE event (`meta`),
so screen events and feedback attach to it without a second id. kosis_store.log_rows.row_from_turn builds a row from
the turn's spans and its ChatResult.

T3-48: the tables have the migration `0002_log_tables` and sit on the store's Base (LogBase is the same object), so the
T3-18 migration check covers them. The service writes them through kosis_store.log_collector.

Free text lives in three places only: `request_logs.question` (the question as typed), `request_rewrites.text` (the
question the agent rewrote, T3-50) and `ui_events.reason` (the feedback reason). Each is masked before it is stored,
never stored for harness requests (is_internal), and set to NULL after the retention period; every other column is kept.

Log version 3: the row has no per-step column. Node, tool and LLM step timings are OpenTelemetry metrics sent to
Prometheus (docs/log-schema.md 10). The steps of a turn are still read once at write time, with the keys of SPAN_KEYS
only (exception messages and other free text can carry pieces of the question), to fill failed_stage and the LLM totals.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String,
                        Text, func)
from sqlalchemy.orm import Mapped, mapped_column

from kosis_store.models import Base, JSONType

# the four ChatResult outcomes, error (an exception) and aborted (the user left before the answer ended)
OUTCOMES = ("answered", "refused", "clarify", "failed", "error", "aborted")
# direct: typed by the user; clarify_option: a choice button of a clarifying question (FEAT-016/018).
# Suggested questions are out of phase 1 (11/18), so there is no suggestion value.
INPUT_METHODS = ("direct", "clarify_option")
SPAN_STATUSES = ("ok", "refused", "error")
# the keys of one step read at write time (not stored); stage: search | stat | chart | agent; kind: node | tool | llm
SPAN_KEYS = ("name", "kind", "stage", "ms", "status", "error_type", "input_tokens", "output_tokens", "model")
UNKNOWN_REASON = "unknown"  # a refusal the agent gave no reason for (multi-query turns, a failed reason lookup)
NO_TRACE = "0" * 32  # trace id of a no-op tracer: tracing was off, the row would have no usable key
# T3-50: how the agent read the question (kosis_store.log_rows.rewrites_from_turn), first match wins:
# chart_only (redraw the last chart), multi (split into sub-questions), carry_over (rewritten with the earlier turn's
# conditions, "부산은?"), reference ("그 표에서 ..." asked as typed on the earlier table), new
REWRITE_KINDS = ("new", "carry_over", "reference", "chart_only", "multi")
RETRY_KINDS = ("user", "auto")  # T3-68: who sent retry_of (the 다시 시도 button / the front server reconnecting)
INTERPRETERS = ("rule", "llm", "none")  # ChatResult.interpreted_by: who read the turn
# one request_rewrites row: the standalone question of a carry-over turn, or one sub-question of a multi-query turn
REWRITE_TEXT_KINDS = ("resolved", "sub_question")
LOG_VERSION = 3  # 2: trace id key, string conversation id, 0-based turn index, failed outcome; 3: no steps column


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# T3-48: the log tables now have a migration (0002_log_tables), so they sit on the store's Base and the T3-18 migration
# check sees them. LogBase stays as a name for the T3-36 tests.
LogBase = Base


class RequestLog(LogBase):
    __tablename__ = "request_logs"
    __table_args__ = (
        CheckConstraint(_in("outcome", OUTCOMES), name="ck_request_logs_outcome"),
        CheckConstraint(_in("input_method", INPUT_METHODS), name="ck_request_logs_input_method"),
        # golden questions sent by the eval harness never reach the log as text
        CheckConstraint("NOT is_internal OR question IS NULL", name="ck_request_logs_internal_no_text"),
        CheckConstraint("outcome <> 'refused' OR reason_code IS NOT NULL", name="ck_request_logs_refusal_reason"),
        CheckConstraint("turn_index >= 0", name="ck_request_logs_turn_index"),
        CheckConstraint(f"length(request_id) = 32 AND request_id <> '{NO_TRACE}'", name="ck_request_logs_request_id"),
        Index("ix_request_logs_conversation_turn", "conversation_id", "turn_index"),  # not unique: a turn can be retried
        CheckConstraint(_in("rewrite_kind", REWRITE_KINDS), name="ck_request_logs_rewrite_kind"),
        CheckConstraint(_in("retry_kind", RETRY_KINDS), name="ck_request_logs_retry_kind"),  # T3-68
        CheckConstraint(_in("interpreted_by", INTERPRETERS), name="ck_request_logs_interpreted_by"),
        # T3-52 data review (kosis_store.log_metrics.review_turns): one outcome over a period, one user over a period.
        # The (session_id, created_at) index also serves session_id alone, so it replaced the single-column one.
        Index("ix_request_logs_outcome_created", "outcome", "created_at"),
        Index("ix_request_logs_session_created", "session_id", "created_at"),
    )

    request_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # OTel trace id of the turn (Jaeger search)
    # thread_id = gen_ai.conversation.id. A value, not a foreign key: guest conversations have no conversations row,
    # and a member's log outlives the deleted conversation until the retention period ends
    conversation_id: Mapped[str] = mapped_column(String(64))
    turn_index: Mapped[int] = mapped_column(Integer)  # kosis.turn.index: 0 for the first question, as in the trace
    prev_request_id: Mapped[str | None] = mapped_column(String(32))  # the request before this one in the conversation
    # T3-68: the request_id the screen asked to retry ("다시 시도" / "다시 받기"), from the root span kosis.turn.retry_of.
    # A value the client sent, not a foreign key: the retried turn may have no row (its log write failed).
    retry_of: Mapped[str | None] = mapped_column(String(32))
    retry_kind: Mapped[str | None] = mapped_column(String(8))  # user: the 다시 시도 button, auto: the front server's reconnect
    session_id: Mapped[uuid.UUID | None] = mapped_column()  # random browser session id, not a person
    is_member: Mapped[bool] = mapped_column(Boolean, default=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)  # team or eval harness traffic
    input_method: Mapped[str] = mapped_column(String(16), default="direct")

    question: Mapped[str | None] = mapped_column(Text)  # masked text; NULL after retention or when is_internal
    question_len: Mapped[int | None] = mapped_column(Integer)  # kept after the text is gone

    outcome: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(64))  # why it was refused or asked back
    failed_stage: Mapped[str | None] = mapped_column(String(32))  # failed_stage(steps), worked out at write time
    picked_key: Mapped[str | None] = mapped_column(String(64), index=True)  # the table the answer used
    survey_name: Mapped[str | None] = mapped_column(String(200))
    has_numbers: Mapped[bool] = mapped_column(Boolean, default=False)  # denominator of the citation click rate
    has_chart: Mapped[bool] = mapped_column(Boolean, default=False)

    latency_ms: Mapped[int | None] = mapped_column(Integer)  # question received -> answer ended
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # T3-50: how the question was read; NULL when the turn raised before it had a result. The rewritten text itself is in
    # request_rewrites (one row per rewritten question, so the sub-questions of a multi-query turn are rows)
    rewrite_kind: Mapped[str | None] = mapped_column(String(16))
    interpreted_by: Mapped[str | None] = mapped_column(String(8))

    # Filled by the multi-turn quality detection (KOS-176) while the text still exists
    rephrase_of: Mapped[str | None] = mapped_column(String(32))
    negative_reaction: Mapped[bool | None] = mapped_column(Boolean)

    log_version: Mapped[int] = mapped_column(Integer, default=LOG_VERSION)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class RequestRewrite(Base):
    """A question the agent rewrote in one turn (T3-50, plans/T3-50.md option B): the standalone question of a
    carry-over turn (seq 0), or each sub-question of a multi-query turn (seq 0, 1, ...). Turns that kept the question as
    typed have no row. Harness turns have no row either (they would carry no text)."""

    __tablename__ = "request_rewrites"
    __table_args__ = (
        CheckConstraint(_in("kind", REWRITE_TEXT_KINDS), name="ck_request_rewrites_kind"),
        CheckConstraint("seq >= 0", name="ck_request_rewrites_seq"),
    )

    request_id: Mapped[str] = mapped_column(String(32), ForeignKey("request_logs.request_id", ondelete="CASCADE"),
                                            primary_key=True)
    seq: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str | None] = mapped_column(Text)  # masked; NULL after retention
    text_len: Mapped[int | None] = mapped_column(Integer)  # kept after the text is gone
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


def clean_spans(spans: list[dict]) -> list[dict]:
    """The spans with the allowed keys only (SPAN_KEYS), in order; a status outside SPAN_STATUSES becomes "error".

    Side effects: none.
    """
    out = []
    for span in spans:
        kept = {k: span[k] for k in SPAN_KEYS if span.get(k) is not None}
        if kept.get("status") not in SPAN_STATUSES:
            kept["status"] = "error" if "status" in kept else "ok"
        out.append(kept)
    return out


def failed_stage(spans: list[dict]) -> str | None:
    """The stage of the first span that did not end ok, or None. No gold answer is needed (unlike evals.trace.blame).

    Side effects: none.
    """
    for span in spans:
        if span.get("status", "ok") != "ok":
            return span.get("stage") or "unknown"
    return None


# Screen events (docs/log-schema.md 4, T3-48): what only the browser knows: clicks, leaving the stream, feedback. props hold
# the allowed keys of each event type (kosis_store.log_collector.EVENT_PROPS); the only free text is the feedback reason,
# masked into its own column so the 90-day purge reaches it. The question, the first token and the answer end are not
# screen events; the server writes them to request_logs. Page views and chart views were dropped (11/23).
UI_EVENT_TYPES = ("citation_click", "copy_answer", "stream_abort", "feedback")
# Feedback choices (POL-007): one per value; "other" needs a reason
FEEDBACK_CHOICES = {
    "up": ("accurate", "found_table", "easy", "chart_helpful", "other"),
    "down": ("wrong_number", "wrong_table", "hard", "other"),
}
FEEDBACK_REASON_MAX = 200


class UiEvent(Base):
    __tablename__ = "ui_events"
    __table_args__ = (
        CheckConstraint(_in("event_type", UI_EVENT_TYPES), name="ck_ui_events_event_type"),
        Index("ix_ui_events_received_at", "received_at"),
        Index("ix_ui_events_request_id", "request_id"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)  # made by the browser; a resent copy is ignored
    event_type: Mapped[str] = mapped_column(String(32))
    client_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # the browser's clock, or the server's (adjusted)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ts_adjusted: Mapped[bool] = mapped_column(Boolean, default=False)  # the browser clock was off by more than 5 minutes
    session_id: Mapped[uuid.UUID | None] = mapped_column()
    request_id: Mapped[str | None] = mapped_column(String(32))  # the turn it belongs to (trace id from the meta event)
    screen_id: Mapped[str | None] = mapped_column(String(16))
    feature_id: Mapped[str | None] = mapped_column(String(16))
    props: Mapped[dict] = mapped_column(JSONType, default=dict)
    reason: Mapped[str | None] = mapped_column(Text)  # feedback only: masked, NULL after the retention period
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    log_version: Mapped[int] = mapped_column(Integer, default=LOG_VERSION)




class IngestReject(Base):
    """Screen events that failed the intake checks: counted with the reason, never stored as text (docs/log-schema.md 4)."""

    __tablename__ = "ingest_rejects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str] = mapped_column(String(64))  # e.g. unknown_event_type, bad_props, missing_field, unknown_version
    event_type: Mapped[str | None] = mapped_column(String(32))  # as sent, cut to 32 chars
    n: Mapped[int] = mapped_column(Integer)


class DailyUsage(Base):
    """One KST day of usage counts over all users (kosis_store.daily_usage). Harness traffic is left out. Rebuilt whole
    for a day, so running the same day again gives the same row."""

    __tablename__ = "daily_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)  # KST date
    questions: Mapped[int] = mapped_column(Integer)  # request_logs rows
    sessions: Mapped[int] = mapped_column(Integer)  # distinct browser session ids (rows without one are not counted)
    conversations: Mapped[int] = mapped_column(Integer)  # distinct conversation ids
    avg_turns: Mapped[float] = mapped_column(Float)  # questions per conversation on that day, 0 when no question
    multi_turn_conversations: Mapped[int] = mapped_column(Integer)  # conversations with 2+ questions that day
    aborted: Mapped[int] = mapped_column(Integer)  # turns the user left before the answer ended
    citation_clicks: Mapped[int] = mapped_column(Integer)
    copy_answers: Mapped[int] = mapped_column(Integer)
    feedback_up: Mapped[int] = mapped_column(Integer)  # answers whose last feedback was up
    feedback_down: Mapped[int] = mapped_column(Integer)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DailySessionUsage(Base):
    """One KST day of one user's usage (kosis_store.daily_usage). The user is the anonymous browser session_id: the log
    keeps no account id (POL-006). Questions without a session id are only in DailyUsage. Rebuilt with DailyUsage."""

    __tablename__ = "daily_session_usage"

    day: Mapped[date] = mapped_column(Date, primary_key=True)  # KST date
    session_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    is_member: Mapped[bool] = mapped_column(Boolean)  # any question of the day was asked logged in
    questions: Mapped[int] = mapped_column(Integer)
    conversations: Mapped[int] = mapped_column(Integer)
    avg_turns: Mapped[float] = mapped_column(Float)
    multi_turn_conversations: Mapped[int] = mapped_column(Integer)
    aborted: Mapped[int] = mapped_column(Integer)
    citation_clicks: Mapped[int] = mapped_column(Integer)
    copy_answers: Mapped[int] = mapped_column(Integer)
    feedback_up: Mapped[int] = mapped_column(Integer)
    feedback_down: Mapped[int] = mapped_column(Integer)
    built_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
