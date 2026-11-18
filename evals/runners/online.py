"""Online evaluation input (plans/T3-43.md): service turns rebuilt from their traces, or a past agent run in the same shape.

A service turn is one trace (T3-30): root `invoke_agent` with gen_ai.conversation.id, kosis.turn.index, kosis.outcome,
kosis.no_answer_reason and, since T3-43, kosis.turn.question / .answer / .table_key / .resolved_question; under it the
`execute_tool <name>` spans with their arguments and results as JSON. Jaeger returns those spans as JSON; they are
wrapped to look like OTel ReadableSpans so evals.runners.agent.turn_record builds the same record the agent layer grades.

    turns = from_jaeger(JAEGER_UI, lookback="24h", limit=500)   # service traffic (deployment.environment.name=service)
    turns = from_run(Path("evals/reports/<agent run>"))           # a past live agent run: same record shape
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from opentelemetry.trace import StatusCode

from kosis_agent.observability.tracing import ROOT_SPAN, SERVICE

from evals.runners.agent import turn_record

TURN_FIELDS = ("question", "answer", "table_key", "resolved_question")


def _value(tag: dict) -> Any:
    return tag.get("value")


def _span(raw: dict) -> SimpleNamespace:
    """A Jaeger JSON span shaped like the ReadableSpan fields turn_record reads (times in ns, ids as ints)."""
    tags = {t["key"]: _value(t) for t in raw.get("tags") or []}
    error = tags.get("otel.status_code") == "ERROR" or tags.get("error") is True
    parent = next((r["spanID"] for r in raw.get("references") or [] if r.get("refType") == "CHILD_OF"), None)
    start = int(raw["startTime"]) * 1000
    return SimpleNamespace(
        name=raw["operationName"], attributes=tags, start_time=start, end_time=start + int(raw["duration"]) * 1000,
        context=SimpleNamespace(trace_id=int(raw["traceID"], 16), span_id=int(raw["spanID"], 16)),
        parent=SimpleNamespace(span_id=int(parent, 16)) if parent else None,
        status=SimpleNamespace(status_code=StatusCode.ERROR if error else StatusCode.UNSET,
                               description=tags.get("otel.status_description")))


def turn_of_trace(trace: dict) -> dict | None:
    """One turn record from one Jaeger trace; None when it is no agent turn or carries no question (a trace written
    before T3-43, or a /tools/* call)."""
    spans = [_span(s) for s in trace.get("spans") or []]
    root = next((s for s in spans if s.name == ROOT_SPAN), None)
    if root is None or not root.attributes.get("kosis.turn.question"):
        return None
    a = root.attributes
    error = root.status.description or "error" if root.status.status_code == StatusCode.ERROR else None
    record = turn_record(spans, a.get("kosis.turn.answer") or "", error)
    return {**record, **{f: a.get(f"kosis.turn.{f}") for f in TURN_FIELDS},
            "conversation": a.get("gen_ai.conversation.id"), "turn": int(a.get("kosis.turn.index") or 0),
            "outcome": "error" if error else a.get("kosis.outcome"), "no_answer_reason": a.get("kosis.no_answer_reason"),
            "started": root.start_time // 1000}


def fetch(ui: str, lookback: str, limit: int, environment: str = "service") -> list[dict]:
    """Raw traces of the agent service from the Jaeger query API (the UI address serves /api)."""
    query = urllib.parse.urlencode({"service": SERVICE, "lookback": lookback, "limit": limit,
                                    "tags": json.dumps({"deployment.environment.name": environment})})
    with urllib.request.urlopen(f"{ui.rstrip('/')}/api/traces?{query}", timeout=30) as resp:  # noqa: S310 - local Jaeger
        return json.loads(resp.read()).get("data") or []


def from_jaeger(ui: str, lookback: str = "24h", limit: int = 500) -> tuple[list[dict], int]:
    """(turns oldest first, traces that were not readable turns)."""
    raw = fetch(ui, lookback, limit)
    turns = [t for t in map(turn_of_trace, raw) if t is not None]
    return sorted(turns, key=lambda t: (t["conversation"] or "", t["turn"])), len(raw) - len(turns)


def from_run(path: Path) -> tuple[list[dict], int]:
    """Turns of a past agent run (results.jsonl rows: question, answer, calls, error, sid, turn, trace ids)."""
    turns = []
    for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        stats = [c for c in r.get("calls") or [] if c["name"] == "summarize_stat"]
        last = (stats[-1].get("result") or {}) if stats and isinstance(stats[-1].get("result"), dict) else {}
        outcome = ("error" if r.get("error") else last.get("outcome")
                   or ("refused" if r.get("refused") else "no_stat"))  # no_stat: a chart-only / tool-less turn
        turns.append({**r, "conversation": f"{r['sid']}#{r.get('run', 0)}", "outcome": outcome,
                      "no_answer_reason": (last.get("refusal") or {}).get("reason") if outcome == "refused" else None,
                      "resolved_question": None, "table_key": r.get("state_key")})
    return turns, 0
