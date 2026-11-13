"""User request log, draft (plans/T3-36.md, docs/log-schema.md): one row per question.

Draft: `request_logs` has no Alembic migration yet (the number is agreed with the backend task KOS-158 first), so it
sits on its own LogBase and the migration check of T3-18 (Base.metadata) does not see it.

Text as typed lives in `question` only. It is masked before it is stored, never stored for harness requests
(is_internal), and set to NULL after the retention period; every other column is kept. `spans` holds the steps of the
request with the keys of SPAN_KEYS only: exception messages and other free text can carry pieces of the question.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from kosis_store.models import JSONType

OUTCOMES = ("answered", "refused", "clarify", "error", "aborted")  # aborted: the user left before the answer ended
INPUT_METHODS = ("typed", "suggestion")
SPAN_STATUSES = ("ok", "refused", "error")
# stage: search | stat | chart | agent, set by the writer; kind: node | tool | llm
SPAN_KEYS = ("name", "kind", "stage", "ms", "status", "error_type", "input_tokens", "output_tokens")


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
        CheckConstraint("turn >= 1", name="ck_request_logs_turn"),
        Index("ix_request_logs_conversation_turn", "conversation_id", "turn"),  # not unique: a turn can be retried
    )

    request_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)  # made by the server
    trace_id: Mapped[str | None] = mapped_column(String(32))  # OTel trace id, when tracing is on
    # A value, not a foreign key: guest conversations have no conversations row, and a member's log outlives the
    # deleted conversation until the retention period ends
    conversation_id: Mapped[uuid.UUID] = mapped_column()
    turn: Mapped[int] = mapped_column(Integer)
    prev_request_id: Mapped[uuid.UUID | None] = mapped_column()  # the request before this one in the conversation
    session_id: Mapped[uuid.UUID | None] = mapped_column(index=True)  # random browser session id, not a person
    is_member: Mapped[bool] = mapped_column(Boolean, default=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)  # team or eval harness traffic
    input_method: Mapped[str] = mapped_column(String(16), default="typed")

    question: Mapped[str | None] = mapped_column(Text)  # masked text; NULL after retention or when is_internal
    question_len: Mapped[int | None] = mapped_column(Integer)  # kept after the text is gone

    outcome: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(64))  # why it was refused or asked back
    failed_stage: Mapped[str | None] = mapped_column(String(32))  # failed_stage(spans)
    picked_key: Mapped[str | None] = mapped_column(String(64), index=True)  # the table the answer used
    survey_name: Mapped[str | None] = mapped_column(String(200))
    has_numbers: Mapped[bool] = mapped_column(Boolean, default=False)  # denominator of the citation click rate
    has_chart: Mapped[bool] = mapped_column(Boolean, default=False)

    latency_ms: Mapped[int | None] = mapped_column(Integer)  # question received -> answer ended
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_model: Mapped[str | None] = mapped_column(String(64))  # cost = tokens x the unit price of this model
    spans: Mapped[list] = mapped_column(JSONType, default=list)  # clean_spans(...)

    # Filled by the multi-turn quality detection (KOS-176) while the text still exists
    rephrase_of: Mapped[uuid.UUID | None] = mapped_column()
    negative_reaction: Mapped[bool | None] = mapped_column(Boolean)

    log_version: Mapped[int] = mapped_column(Integer, default=1)
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
