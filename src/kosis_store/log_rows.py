"""One agent turn -> the values of one request_logs row (plans/T3-36.md, docs/log-schema.md 2.1).

The service already records each turn as OTel spans (kosis_agent.observability.tracing, T3-30~32): a root
`invoke_agent` span with the turn's outcome, node spans, `execute_tool <tool>` spans and `chat <model>` spans with token
counts. row_from_turn reads those finished spans and the turn's ChatResult; it does not trace anything itself. When to
call it and where to write the row is the collector's job (KOS-170). The steps are read to fill failed_stage and the LLM
totals and are not stored: step timings go to Prometheus as metrics (log version 3, docs/log-schema.md 10).

The names below are the tracing module's. They are repeated here so the store does not import the agent package;
tests/test_log_schema.py checks they still match.
"""

from __future__ import annotations

import hashlib
from typing import Any, Iterable

from kosis_store.log_models import LOG_VERSION, UNKNOWN_REASON, clean_spans, failed_stage
from kosis_store.masking import mask

ROOT_SPAN = "invoke_agent"
TOOL_PREFIX = "execute_tool "
CHAT = "chat"  # gen_ai.operation.name of every LLM call span: the eval ops count LLM calls the same way (T3-31)
CONVERSATION_ID_MAX = 64  # request_logs.conversation_id is VARCHAR(64); a sha256 hex digest is exactly 64

# The stage a span belongs to: its own name, else the nearest ancestor's. Main graph nodes and the three tools;
# phase 1 and 2 nodes inside a tool take the tool's stage. T3-67: the ReAct graph's nodes (graph/main_graph.py, T3-03)
# — candidates is the search's candidate share (no LLM, not a tool), understand / rewrite / rewrite_after_search /
# review are agent steps.
STAGES = {
    "search": "search", "search_tables": "search", "candidates": "search",
    "statistics": "stat", "summarize_stat": "stat",
    "chart": "chart", "make_chart": "chart",
    "context": "agent", "multi": "agent", "finalize": "agent",
    "understand": "agent", "rewrite": "agent", "rewrite_after_search": "agent", "review": "agent",
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


def _conversation_id(value) -> str:
    """The thread id as stored. The client may send any string as thread_id, so one longer than the column becomes its
    sha256 hex digest (64 chars): still one value per conversation, and the row is not rejected."""
    text = str(value)
    if len(text) <= CONVERSATION_ID_MAX:
        return text
    return hashlib.sha256(text.encode()).hexdigest()


def row_from_turn(spans: Iterable, result: dict | None, *, aborted: bool = False, first_token_ms: int | None = None,
                  question: str | None = None, session_id=None, is_member: bool = False, is_internal: bool = False,
                  input_method: str = "direct", prev_request_id: str | None = None) -> dict:
    """The request_logs values of one turn.

    spans: the finished spans of the turn (ReadableSpan, e.g. from a SpanProcessor), root `invoke_agent` included.
    result: the turn's ChatResult as a dict (model_dump), None when the turn raised before it had one.
    aborted: the client left before the answer ended (the stream was closed).
    question: the text already masked by the collector; dropped for harness traffic (is_internal).
    Raises ValueError when the spans hold no turn root, or the root has no conversation id or turn index.

    Side effects: none.
    """
    spans = list(spans)
    root = next((s for s in spans if s.name == ROOT_SPAN), None)
    if root is None:
        raise ValueError(f"no {ROOT_SPAN} span: not a service turn")
    a = root.attributes or {}
    for name in ("gen_ai.conversation.id", "kosis.turn.index"):
        if a.get(name) is None:  # a row with a made-up conversation or turn would silently join the wrong question
            raise ValueError(f"{ROOT_SPAN} span has no {name}")
    r = result or {}
    steps = step_spans(spans, root)
    outcome = _outcome(root, result, aborted)

    reason = a.get("kosis.no_answer_reason") or r.get("no_answer_reason")
    if outcome == "error":
        # T3-67: the error code (backend/app/errors.py) the service left on the root; older spans carry only the class
        reason = a.get("kosis.error.code") or a.get("error.type") or reason
    if outcome == "refused" and not reason:
        reason = UNKNOWN_REASON

    # An aborted turn did not fail: closing the stream (GeneratorExit) can end the running node span as an error,
    # which would count the user's leaving as a failed stage.
    stage = None if outcome == "aborted" else failed_stage(steps)
    if stage is None and outcome == "refused":  # nothing raised: the refusal came from the gate or from phase 1
        stage = "stat" if r.get("answer_origin") == "stat" else "search"

    key = r.get("table_key")
    picked = [c for c in r.get("picked") or [] if c.get("key") == key]  # no match: no survey name, not another table's
    llm = [s for s in steps if s["kind"] == "llm"]
    text = None if is_internal else question
    return {
        "request_id": format(root.context.trace_id, "032x"),
        "conversation_id": _conversation_id(a["gen_ai.conversation.id"]),
        "turn_index": int(a["kosis.turn.index"]),
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
        "rewrite_kind": rewrite_kind(result),
        "interpreted_by": r.get("interpreted_by") if result is not None else None,
        "log_version": LOG_VERSION,
    }


# ---------------------------------------------------------------- rewritten questions (T3-50)
def rewrite_kind(result: dict | None) -> str | None:
    """How the agent read the turn's question, from the finished ChatResult (plans/T3-50.md, first match wins).

    - chart_only: answer_origin "chart". Its resolved_question is a copy of the earlier question, not a rewrite.
    - multi: sub_results is set (the sub-questions are the rewrites; resolved_question holds the last one only).
    - carry_over: resolved_question differs from the question. reused_table is not read: a carry-over turn whose earlier
      table refused searches again with the rewritten question and ends with reused_table False.
    - reference: reused_table without a rewrite ("그 표에서 ...").
    - new: everything else. A multi-query turn whose sub-questions all found the same table runs as one question and has
      no sub_results, so it is "new" (the graph does not hand its sub-questions out).
    None when the turn raised before it had a result. Side effects: none.
    """
    if result is None:
        return None
    if result.get("answer_origin") == "chart":
        return "chart_only"
    if result.get("sub_results") is not None:
        return "multi"
    resolved = result.get("resolved_question")
    if resolved and resolved != result.get("question"):
        return "carry_over"
    return "reference" if result.get("reused_table") else "new"


def rewrite_rows(request_id: str, result: dict | None, *, is_internal: bool = False) -> list[dict]:
    """The request_rewrites rows of one turn: the masked standalone question of a carry-over turn, or one row per
    sub-question of a multi-query turn. No rows for other turns, and none for harness traffic (no text is kept for it).

    The ChatResult text is not masked yet, and a rewrite can carry words of the earlier question, so mask() runs here.
    Side effects: none.
    """
    if is_internal:
        return []
    kind = rewrite_kind(result)
    if kind == "carry_over":
        texts = [("resolved", result["resolved_question"])]
    elif kind == "multi":
        texts = [("sub_question", s.get("question")) for s in result["sub_results"] if s.get("question")]
    else:
        return []
    rows = []
    for seq, (text_kind, raw) in enumerate(texts):
        masked = mask(raw)
        rows.append({"request_id": request_id, "seq": seq, "kind": text_kind, "text": masked, "text_len": len(masked)})
    return rows
