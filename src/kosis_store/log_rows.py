"""One agent turn -> the values of one request_logs row (plans/T3-36.md, docs/log-schema.md 2.1).

The service already records each turn as OTel spans (kosis_agent.observability.tracing, T3-30~32): a root
`invoke_agent` span with the turn's outcome, node spans, `execute_tool <tool>` spans and `chat <model>` spans with token
counts. row_from_turn reads those finished spans and the turn's ChatResult; it does not trace anything itself. When to
call it and where to write the row is the collector's job (KOS-170).

The names below are the tracing module's. They are repeated here so the store does not import the agent package;
tests/test_log_schema.py checks they still match.
"""

from __future__ import annotations

from typing import Any, Iterable

from kosis_store.log_models import LOG_VERSION, UNKNOWN_REASON, clean_spans, failed_stage

ROOT_SPAN = "invoke_agent"
TOOL_PREFIX = "execute_tool "
CHAT = "chat"  # gen_ai.operation.name of every LLM call span: the eval ops count LLM calls the same way (T3-31)

# The stage a span belongs to: its own name, else the nearest ancestor's. Main graph nodes and the three tools;
# phase 1 and 2 nodes inside a tool take the tool's stage.
STAGES = {
    "search": "search", "search_tables": "search",
    "statistics": "stat", "summarize_stat": "stat",
    "chart": "chart", "make_chart": "chart",
    "context": "agent", "multi": "agent", "finalize": "agent",
}


def _key(span) -> str:
    return span.name[len(TOOL_PREFIX):] if span.name.startswith(TOOL_PREFIX) else span.name


def _kind(span) -> str:
    op = (span.attributes or {}).get("gen_ai.operation.name")
    return "llm" if op == CHAT else "tool" if op == "execute_tool" else "node"


def _ms(span) -> int | None:
    if span.start_time is None or span.end_time is None:
        return None
    return round((span.end_time - span.start_time) / 1e6)


def _stage(span, by_id: dict[int, Any]) -> str:
    while span is not None:
        if (stage := STAGES.get(_key(span))) is not None:
            return stage
        span = by_id.get(span.parent.span_id) if span.parent is not None else None
    return "agent"


def step_spans(spans: Iterable, root) -> list[dict]:
    """The turn's spans under `root` as log steps (SPAN_KEYS only), in start order.

    Side effects: none.
    """
    by_id = {s.context.span_id: s for s in spans}
    steps = []
    for span in sorted(by_id.values(), key=lambda s: s.start_time or 0):
        if span is root or span.context.trace_id != root.context.trace_id:
            continue
        a = span.attributes or {}
        steps.append({
            "name": span.name, "kind": _kind(span), "stage": _stage(span, by_id), "ms": _ms(span),
            "status": "error" if span.status.status_code.name == "ERROR" else "ok",
            "error_type": a.get("error.type"), "model": a.get("gen_ai.request.model"),
            "input_tokens": a.get("gen_ai.usage.input_tokens"), "output_tokens": a.get("gen_ai.usage.output_tokens"),
        })
    return clean_spans(steps)


def _outcome(root, result: dict | None, aborted: bool) -> str:
    if aborted:
        return "aborted"
    if outcome := (root.attributes or {}).get("kosis.outcome"):
        return outcome
    if root.status.status_code.name == "ERROR":
        return "error"
    return (result or {}).get("outcome") or "error"


def row_from_turn(spans: Iterable, result: dict | None, *, aborted: bool = False, first_token_ms: int | None = None,
                  question: str | None = None, session_id=None, is_member: bool = False, is_internal: bool = False,
                  input_method: str = "typed", prev_request_id: str | None = None) -> dict:
    """The request_logs values of one turn.

    spans: the finished spans of the turn (ReadableSpan, e.g. from a SpanProcessor), root `invoke_agent` included.
    result: the turn's ChatResult as a dict (model_dump), None when the turn raised before it had one.
    aborted: the client left before the answer ended (the stream was closed).
    question: the text already masked by the collector; dropped for harness traffic (is_internal).
    Raises ValueError when the spans hold no turn root.

    Side effects: none.
    """
    spans = list(spans)
    root = next((s for s in spans if s.name == ROOT_SPAN), None)
    if root is None:
        raise ValueError(f"no {ROOT_SPAN} span: not a service turn")
    a = root.attributes or {}
    r = result or {}
    steps = step_spans(spans, root)
    outcome = _outcome(root, result, aborted)

    reason = a.get("kosis.no_answer_reason") or r.get("no_answer_reason")
    if outcome == "error":
        reason = a.get("error.type") or reason
    if outcome == "refused" and not reason:
        reason = UNKNOWN_REASON

    stage = failed_stage(steps)
    if stage is None and outcome == "refused":  # nothing raised: the refusal came from the gate or from phase 1
        stage = "stat" if r.get("answer_origin") == "stat" else "search"

    key = r.get("table_key")
    picked = [c for c in r.get("picked") or [] if c.get("key") == key] or r.get("picked") or []
    llm = [s for s in steps if s["kind"] == "llm"]
    text = None if is_internal else question
    return {
        "request_id": format(root.context.trace_id, "032x"),
        "conversation_id": str(a.get("gen_ai.conversation.id")),
        "turn_index": int(a.get("kosis.turn.index", 0)),
        "prev_request_id": prev_request_id,
        "session_id": session_id,
        "is_member": is_member,
        "is_internal": is_internal,
        "input_method": input_method,
        "question": text,
        "question_len": len(question) if question is not None else None,
        "outcome": outcome,
        "reason_code": reason,
        "failed_stage": stage,
        "picked_key": key,
        "survey_name": picked[0].get("survey_name") if key and picked else None,
        "has_numbers": outcome == "answered" and r.get("stat_answer") is not None,
        "has_chart": r.get("chart") is not None,
        "latency_ms": _ms(root),
        "first_token_ms": first_token_ms,
        "llm_calls": len(llm),
        "input_tokens": sum(s.get("input_tokens", 0) for s in llm),
        "output_tokens": sum(s.get("output_tokens", 0) for s in llm),
        "spans": steps,
        "log_version": LOG_VERSION,
    }
