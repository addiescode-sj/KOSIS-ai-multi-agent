"""Answers of a past l3 / e2e run, so a regrade (`--from-run`) calls no model.

Two row formats are read (read_rows picks by the first row's keys):
- this repo's l3 / e2e results.jsonl since T3-32 (`qid`, `answer` = StatAnswer dump, `error`, `spans`, `keys` /
  `top_keys`, `used_table`, the ops fields): rows of evals.tasks.common.grade + e2e.locate, written by run_eval
- the phase 1 harness format (`question_id`, `stat_answer`, `decision_metadata`; scripts/stat_baseline.py), so a
  teammate's phase 1 run can still be graded here (read_run)
Ops fields per row (latency, tokens, model, failed, retried) feed evals.ops.
"""

from __future__ import annotations

import json
from pathlib import Path

from kosis_ai.schemas import Source, StatAnswer
from pydantic import ValidationError

from evals.ops import tokens_of
from evals.schemas import CalcItem

OPS_DEFAULTS = {"latency_ms": 0.0, "llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "model": None,
                "tokens_by_model": {}, "retried": False}


def _answer(data: dict) -> StatAnswer:
    """A saved StatAnswer. A search gate refusal was built without a source (runners.agent._final_answer,
    model_construct), so it does not validate: rebuild it the same way, with the refusal as an object."""
    try:
        return StatAnswer.model_validate(data)
    except ValidationError:
        placeholder = Source(org_id="000", tbl_id="-", survey_nm="-", tbl_nm="-")
        answer = StatAnswer.model_validate({**data, "source": placeholder.model_dump(mode="json")})
        answer.source = None  # no table was opened; the grader reads outcome and refusal.reason only
        return answer


def read_rows(path: Path, items: list[CalcItem]) -> list[dict]:
    """Rows of a past run folder in golden order, shaped like runners.agent.single_turns rows: answer (StatAnswer or
    None), error, keys, used_table, spans, search_spans, ops fields and trace ids. A golden question the run lacks
    is a run failure (graded FAIL run_failure, not skipped, so a regrade covers the same n)."""
    saved = [json.loads(line) for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if saved and "question_id" in saved[0]:
        return read_run(path, items)
    by_id = {r["qid"]: r for r in saved}
    rows = []
    for item in items:
        r = by_id.get(item.qid)
        if r is None:
            rows.append({"qid": item.qid, "answer": None, "error": "not in the run", "keys": [], "used_table": None,
                         "spans": [], "search_spans": [], **OPS_DEFAULTS, "failed": True})
            continue
        answer = _answer(r["answer"]) if r.get("answer") else None
        keys = r.get("keys") or r.get("top_keys") or []  # top_keys (5) in rows written before T3-39
        rows.append({"qid": item.qid, "answer": answer, "error": r.get("error"), "keys": keys,
                     "used_table": r.get("used_table") or r.get("table_key"),
                     "spans": r.get("spans") or [], "search_spans": r.get("search_spans") or [],
                     "retrieve_ms": r.get("retrieve_ms", 0.0), "trace_spans": r.get("trace_spans") or [],
                     **{k: r.get(k, d) for k, d in OPS_DEFAULTS.items()},
                     "trace_id": r.get("trace_id"), "span_id": r.get("span_id"),
                     "failed": answer is None})
    return rows


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
        model = llm[0]["attributes"]["gen_ai.request.model"] if llm else None
        rows.append({
            "qid": item.qid,
            "answer": StatAnswer.model_validate(stat) if stat else None,
            "error": None if stat else (row or {}).get("call_error") or "not in the run",
            "spans": ((row or {}).get("decision_metadata") or {}).get("spans") or [],
            "search_spans": [], "keys": [], "used_table": item.table_key,
            "latency_ms": (row or {}).get("latency_seconds", 0) * 1000,
            "llm_calls": len(llm),
            "input_tokens": tin,
            "output_tokens": tout,
            "model": model,
            "tokens_by_model": {model: {"input": tin, "output": tout}} if model else {},
            "failed": not row or row.get("call_status") != "ok",
            "retried": (row or {}).get("attempts", 1) > 1,
        })
    return rows
