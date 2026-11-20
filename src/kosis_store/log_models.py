"""User request log, draft (plans/T3-36.md, docs/log-schema.md): one row per question (one agent turn).

The row key is the OTel trace id of the turn (32 hex): the service already sends it in the first SSE event (`meta`),
so screen events and feedback attach to it without a second id. kosis_store.log_rows.row_from_turn builds a row from
the turn's spans and its ChatResult.

Draft: `request_logs` has no Alembic migration yet (the number is agreed with the backend task KOS-158 first), so it
sits on its own LogBase and the migration check of T3-18 (Base.metadata) does not see it.

Text as typed lives in `question` only. It is masked before it is stored, never stored for harness requests
(is_internal), and set to NULL after the retention period; every other column is kept.

Log version 3: the row has no per-step column. Node, tool and LLM step timings are OpenTelemetry metrics sent to
Prometheus (docs/log-schema.md 10). The steps of a turn are still read once at write time, with the keys of SPAN_KEYS
only (exception messages and other free text can carry pieces of the question), to fill failed_stage and the LLM totals.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
LOG_VERSION = 3  # 2: trace id key, string conversation id, 0-based turn index, failed outcome; 3: no steps column


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class LogBase(DeclarativeBase):
    pass


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
    )

    request_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # OTel trace id of the turn (Jaeger search)
    # thread_id = gen_ai.conversation.id. A value, not a foreign key: guest conversations have no conversations row,
    # and a member's log outlives the deleted conversation until the retention period ends
    conversation_id: Mapped[str] = mapped_column(String(64))
    turn_index: Mapped[int] = mapped_column(Integer)  # kosis.turn.index: 0 for the first question, as in the trace
    prev_request_id: Mapped[str | None] = mapped_column(String(32))  # the request before this one in the conversation
    session_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # random browser session id, not a person
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

    # Filled by the multi-turn quality detection (KOS-176) while the text still exists
    rephrase_of: Mapped[str | None] = mapped_column(String(32))
    negative_reaction: Mapped[bool | None] = mapped_column(Boolean)

    log_version: Mapped[int] = mapped_column(Integer, default=LOG_VERSION)
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
