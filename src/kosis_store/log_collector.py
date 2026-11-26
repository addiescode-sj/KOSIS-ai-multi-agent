"""Collect the user log of the service and write it off the request thread (plans/T3-48.md, KOS-170).

Turns (docs/log-schema.md 2, 10): TurnLogCollector is an OTel span processor on the service provider. It only keeps the
finished spans of each trace. The request handler knows when a turn is over (answered, refused, error, the client gone),
so after `root.end()` it calls `finish(root, result, ...)` once: the kept spans become one request_logs row
(kosis_store.log_rows.row_from_turn, the same source as the traces) and a single writer thread inserts it. A failed
write is dropped and counted; it never reaches the answer. Traces whose root is not a turn (`POST /tools/...`) are
dropped when their root ends.

Screen events (docs/log-schema.md 4): `check_events` validates a batch from the browser (clicks, leaving the stream,
feedback). Passing events go to ui_events
(a resent event_id is ignored), failing ones are counted by reason in ingest_rejects, never stored as text.

Text: the handler passes the question already masked (kosis_store.masking). The span attributes are not read for text.
T3-50: a turn whose question the agent rewrote (carry-over, multi-query) also gets request_rewrites rows, built from the
ChatResult and masked by kosis_store.log_rows.rewrite_rows; they are queued right after the turn row.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

from kosis_store.log_models import (FEEDBACK_CHOICES, FEEDBACK_REASON_MAX, LOG_VERSION, UI_EVENT_TYPES, DailyUsage,
                                    IngestReject, RequestLog, RequestRewrite, UiEvent)
from kosis_store.log_rows import ROOT_SPAN, rewrite_rows, row_from_turn
from kosis_store.masking import mask

Writer = Callable[[str, list[dict]], None]  # (table name, rows) -> None; raises on failure

MAX_PENDING_TRACES = 256  # spans kept for turns not finished yet; the oldest go first past this (counted)
MAX_EVENTS_PER_BATCH = 100
CLOCK_SKEW = timedelta(minutes=5)  # a browser clock further off than this is replaced by the server's (flagged)
MAX_PROP_TEXT = 64

# The props each screen event may carry: name -> type. Nothing else is stored, so no free text gets in.
EVENT_PROPS: dict[str, dict[str, type]] = {
    "citation_click": {"table_key": str, "rank": int},
    "copy_answer": {},
    "stream_abort": {"elapsed_ms": int},
    "feedback": {"value": str, "choice": str},  # the reason text is a field of its own (masked, purged after 90 days)
}
assert set(EVENT_PROPS) == set(UI_EVENT_TYPES)

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")
_SHORT_ID = re.compile(r"^[A-Za-z0-9_-]{1,16}$")  # screen S-xx, feature F-xx


class TurnLogCollector(SpanProcessor):
    """Keeps finished spans per trace; `finish` turns one into a request_logs row written on its own thread."""

    def __init__(self, write: Writer, max_pending: int = MAX_PENDING_TRACES) -> None:
        self._write = write
        self._max_pending = max_pending
        self._spans: OrderedDict[int, list[ReadableSpan]] = OrderedDict()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kosis-log-writer")
        self.counts: Counter[str] = Counter()  # written / failed / skipped, by kind (shown by the service)

    # -- SpanProcessor
    def on_start(self, span, parent_context=None) -> None:  # noqa: D401 - SpanProcessor API
        return None

    def on_end(self, span: ReadableSpan) -> None:
        trace_id = span.context.trace_id
        if not trace_id:
            return
        with self._lock:
            if span.parent is None and span.name != ROOT_SPAN:  # the root of a non-turn trace (POST /tools/...)
                self._spans.pop(trace_id, None)
                return
            self._spans.setdefault(trace_id, []).append(span)
            while len(self._spans) > self._max_pending:
                self._spans.popitem(last=False)
                self.counts["dropped_pending_trace"] += 1

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)  # the last rows are written before the service stops

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        try:
            self._pool.submit(lambda: None).result(timeout=timeout_millis / 1000)
            return True
        except Exception:  # noqa: BLE001 - flushing is best effort (pool already shut down)
            return False

    # -- turns
    def finish(self, root, result: dict | None, **row: Any) -> None:
        """Call once per turn, after `root.end()`. row: keyword arguments of row_from_turn (aborted, question, ...).

        Never raises. Side effects: removes the kept spans of the trace, queues one insert.
        """
        try:
            trace_id = root.get_span_context().trace_id
            if not trace_id:  # a no-op tracer: tracing is off, there is no key for the row
                self.counts["skipped_no_trace"] += 1
                return
            with self._lock:
                spans = self._spans.pop(trace_id, [])
            values = row_from_turn(spans, result, **row)
            rewrites = rewrite_rows(values["request_id"], result, is_internal=bool(row.get("is_internal")))
        except Exception:  # noqa: BLE001 - logging must not break the answer
            self.counts["skipped_bad_turn"] += 1
            return
        self.submit("request_logs", [values])
        if rewrites:  # after the turn row (one writer thread keeps the order): the foreign key needs it first
            self.submit("request_rewrites", rewrites)

    # -- screen events
    def add_events(self, events: Iterable[Any], *, is_internal: bool = False,
                   now: datetime | None = None) -> tuple[int, int]:
        """Check a batch of screen events and queue the inserts; returns (accepted, rejected). Never raises."""
        received = now or datetime.now(timezone.utc)
        rows, rejects = check_events(list(events), received, is_internal=is_internal)
        if rows:
            self.submit("ui_events", rows)
        if rejects:
            self.submit("ingest_rejects", [{"received_at": received, "reason": reason, "event_type": kind, "n": n}
                                           for (reason, kind), n in sorted(rejects.items(), key=str)])
        return len(rows), sum(rejects.values())

    def submit(self, table: str, rows: list[dict]) -> None:
        try:
            self._pool.submit(self._write_counted, table, rows)
        except RuntimeError:  # the pool is shut down (service stopping)
            self.counts[f"failed_{table}"] += len(rows)

    def _write_counted(self, table: str, rows: list[dict]) -> None:
        try:
            self._write(table, rows)
            self.counts[f"written_{table}"] += len(rows)
        except Exception:  # noqa: BLE001 - a failed write is dropped and counted, never retried in the request
            self.counts[f"failed_{table}"] += len(rows)


def check_events(events: list[Any], received_at: datetime, *,
                 is_internal: bool = False) -> tuple[list[dict], Counter[tuple[str, str | None]]]:
    """Screen events that pass the intake checks as ui_events rows, and the rest counted by (reason, event type).

    Checks: object shape, known event type, log version, event id (UUID), client time (ISO 8601 with zone), optional ids
    (session UUID, request trace id, short screen / feature ids), props (allowed keys and types only, short strings),
    feedback (request id, up / down with a choice of that value, reason up to 200 chars and needed for "other", masked).
    A client time more than CLOCK_SKEW away from `received_at` is replaced by `received_at` and flagged. A repeated
    event_id inside the batch is kept once. Side effects: none.
    """
    rows: list[dict] = []
    rejects: Counter[tuple[str, str | None]] = Counter()
    seen: set[uuid.UUID] = set()
    for event in events[:MAX_EVENTS_PER_BATCH]:
        kind = event.get("event_type") if isinstance(event, dict) else None
        kind_label = str(kind)[:32] if kind is not None else None
        row, reason = _check_event(event, received_at, is_internal)
        if reason:
            rejects[(reason, kind_label)] += 1
            continue
        if row["event_id"] in seen:
            continue
        seen.add(row["event_id"])
        rows.append(row)
    if len(events) > MAX_EVENTS_PER_BATCH:
        rejects[("batch_too_large", None)] += len(events) - MAX_EVENTS_PER_BATCH
    return rows, rejects


def _check_event(event: Any, received_at: datetime, is_internal: bool) -> tuple[dict | None, str | None]:
    if not isinstance(event, dict):
        return None, "not_an_object"
    kind = event.get("event_type")
    if kind not in EVENT_PROPS:
        return None, "unknown_event_type"
    if event.get("log_version") != LOG_VERSION:
        return None, "unknown_version"
    try:
        event_id = uuid.UUID(str(event["event_id"]))
        client_ts = datetime.fromisoformat(str(event["ts"]))
    except KeyError:
        return None, "missing_field"
    except ValueError:
        return None, "bad_field"
    if client_ts.tzinfo is None:
        return None, "bad_field"
    try:
        session_id = uuid.UUID(str(event["session_id"])) if event.get("session_id") else None
    except ValueError:
        return None, "bad_field"
    request_id = event.get("request_id")
    if request_id is not None and not (isinstance(request_id, str) and _TRACE_ID.match(request_id)):
        return None, "bad_field"
    for key in ("screen_id", "feature_id"):
        value = event.get(key)
        if value is not None and not (isinstance(value, str) and _SHORT_ID.match(value)):
            return None, "bad_field"
    props = event.get("props") or {}
    allowed = EVENT_PROPS[kind]
    if not isinstance(props, dict) or set(props) - set(allowed):
        return None, "bad_props"
    for key, value in props.items():
        expected = allowed[key]
        if type(value) is not expected or (expected is str and len(value) > MAX_PROP_TEXT):  # bool is not an int here
            return None, "bad_props"
    reason_text = None
    if kind == "feedback":
        if request_id is None or props.get("value") not in FEEDBACK_CHOICES:
            return None, "bad_feedback"  # feedback is about one answer, and is up or down
        if props.get("choice") not in FEEDBACK_CHOICES[props["value"]]:
            return None, "bad_feedback"  # a choice of the other value, or none
        reason_text = event.get("reason")
        if reason_text is not None and (not isinstance(reason_text, str) or len(reason_text) > FEEDBACK_REASON_MAX):
            return None, "bad_feedback"
        reason_text = (reason_text or "").strip() or None
        if props["choice"] == "other" and reason_text is None:
            return None, "bad_feedback"  # POL-007: "other" needs a reason
        reason_text = mask(reason_text) if not is_internal else None
    elif event.get("reason") is not None:
        return None, "bad_field"  # free text only on feedback
    adjusted = abs(client_ts - received_at) > CLOCK_SKEW
    return {
        "event_id": event_id, "event_type": kind,
        "client_ts": received_at if adjusted else client_ts, "received_at": received_at, "ts_adjusted": adjusted,
        "session_id": session_id, "request_id": request_id,
        "screen_id": event.get("screen_id"), "feature_id": event.get("feature_id"),
        "props": props, "reason": reason_text, "is_internal": is_internal, "log_version": LOG_VERSION,
    }, None


_MODELS = {"request_logs": RequestLog, "request_rewrites": RequestRewrite, "ui_events": UiEvent,
           "ingest_rejects": IngestReject, "daily_usage": DailyUsage}
_KEYS = {"request_logs": ["request_id"], "request_rewrites": ["request_id", "seq"], "ui_events": ["event_id"]}  # a resent row with the same key is ignored


def db_writer(engine) -> Writer:
    """A Writer that inserts into the database of `engine` (PostgreSQL or SQLite). Rows whose key is already stored are
    ignored (ON CONFLICT DO NOTHING), so a resent screen event or a repeated turn row never doubles."""
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif engine.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - the service runs on PostgreSQL, the tests on SQLite
        from sqlalchemy import insert

    def write(table: str, rows: list[dict]) -> None:
        stmt = insert(_MODELS[table])
        if table in _KEYS and hasattr(stmt, "on_conflict_do_nothing"):
            stmt = stmt.on_conflict_do_nothing(index_elements=_KEYS[table])
        with engine.begin() as conn:
            conn.execute(stmt, rows)

    return write
