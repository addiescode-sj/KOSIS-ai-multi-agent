"""L3 runner: summarize_stat(question, table key) per CalcItem (the oracle table), or answers read from a past run.

A past run is a results.jsonl with `question_id` and `stat_answer` per line (scripts/stat_baseline.py, Phase 1
run_eval --pipeline stat), so a teammate's run can be re-graded here with no model call. Ops fields per row
(latency, tokens, model, failed, retried) feed evals.ops.
"""

from __future__ import annotations

import json
from pathlib import Path

from kosis_ai.schemas import StatAnswer

from evals.ops import tokens_of
from evals.schemas import CalcItem


def _llm_spans(row: dict) -> list[dict]:
    spans = ((row or {}).get("decision_metadata") or {}).get("spans") or []
    return [s for s in spans if "gen_ai.request.model" in (s.get("attributes") or {})]


def read_run(path: Path, items: list[CalcItem]) -> list[dict]:
    """Answers of a past run folder (results.jsonl) in golden order; a question the run lacks is a run failure."""
    by_id = {}
    for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            by_id[row["question_id"]] = row
    rows = []
    for item in items:
        row = by_id.get(item.qid)
        stat = row.get("stat_answer") if row else None
        llm = _llm_spans(row)
        # The row's usage is the last model call only; spans carry every call (compose retries included).
        if llm:
            tin = sum(int(s["attributes"].get("gen_ai.usage.input_tokens") or 0) for s in llm)
            tout = sum(int(s["attributes"].get("gen_ai.usage.output_tokens") or 0) for s in llm)
        else:
            tin, tout = tokens_of((row or {}).get("usage"))
        rows.append({
            "qid": item.qid,
            "answer": StatAnswer.model_validate(stat) if stat else None,
            "error": None if stat else (row or {}).get("call_error") or "not in the run",
            "spans": ((row or {}).get("decision_metadata") or {}).get("spans") or [],
            "latency_ms": (row or {}).get("latency_seconds", 0) * 1000,
            "llm_calls": len(llm),
            "input_tokens": tin,
            "output_tokens": tout,
            "model": llm[0]["attributes"]["gen_ai.request.model"] if llm else None,
            "failed": not row or row.get("call_status") != "ok",
            "retried": (row or {}).get("attempts", 1) > 1,
        })
    return rows
