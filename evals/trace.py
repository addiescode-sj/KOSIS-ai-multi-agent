"""Stage-level tracing for L1 / L3 / e2e runs (plans/T2-15.md, T2-36): which span a question failed in, and why.

T3-32: the traces themselves are live (kosis_agent.observability.tracing.LiveSpans; l3 / e2e run the agent graph,
evals.runners.agent.single_turns), one service `kosis-agent` with deployment.environment.name=eval. This module keeps
the deterministic rules over the span *records* a row carries, which are the same names the live trace shows:

    invoke_agent                              l3·e2e: one turn of the agent graph
    ├─ execute_tool search_tables             e2e (l3: the oracle search); graph nodes parse, retrieve_*, rerank1, rerank2, gate
    └─ execute_tool summarize_stat            phase 1: judge, analyze.*, compute, compose, verify, template, fallback_chatbot

row["spans"] = phase 1 StatOutput.spans; row["search_spans"] = the search graph nodes as NodeSpans records them (with
the ranked "keys" lost_at reads). blame() / blame_search() name the failing stage; run_eval links the verdict to
the live trace (evals.runners.agent.link_questions). Tokens and cost stay in evals.ops (ops.json).
"""

from __future__ import annotations

import math
import os
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from kosis_agent.observability.tracing import TOOL_PREFIX

from evals.evaluators.calculation import FACT_ERRORS

STAT_TOOL, SEARCH_TOOL = TOOL_PREFIX + "summarize_stat", TOOL_PREFIX + "search_tables"  # blame's fallback span names

JAEGER_UI = os.environ.get("JAEGER_UI_URL", "http://localhost:16686")
STAGES = {  # stage -> label, in blame order
    "retrieval": "검색 실패",
    "tool_call": "도구 호출 실패",
    "tool_skipped": "계산 모듈 건너뜀",
    "compute": "계산 거절·실패",
    "understanding": "질문 이해 실패",
    "compose": "문장 생성 실패",
    "unknown": "미분류",
}
NO_ANSWER_LABEL = "답 없음 판정 오류"  # L1 only (blame_search): not a stage of the e2e rule order
JUDGE_ERRORS = {"false_refusal", "missed_refusal", "assumed_condition", "wrong_reason", "disallowed_default",
                "unstated_default", "missing_clarification"}
PLAN_ERRORS = FACT_ERRORS | {"missing_aux"}


class NodeSpans(BaseCallbackHandler):
    """The LangGraph node runs of one search as span dicts shaped like the Phase 1 ones; one handler per question.

    LangGraph passes the node name (metadata langgraph_node) down to the runs inside a node (channel writes, the gate
    branch), so a run is a node only when its name is its langgraph_node and its parent is not a
    node. A search that is not a graph records nothing. Each node span also keeps "keys", the whole ranked pool its
    output docs had (search nodes only), for lost_at.
    """

    def __init__(self) -> None:
        self.spans: list[dict] = []
        self._t0: float | None = None
        self._open: dict[UUID, dict] = {}
        self._nodes: set[UUID] = set()

    def on_chain_start(self, serialized, inputs, *, run_id: UUID, parent_run_id: UUID | None = None,
                       metadata: dict | None = None, **kwargs: Any) -> None:
        now = time.perf_counter()
        self._t0 = now if self._t0 is None else self._t0
        node = (metadata or {}).get("langgraph_node")
        if node is None or kwargs.get("name") != node or parent_run_id in self._nodes:
            return
        self._nodes.add(run_id)
        self._open[run_id] = {"name": node, "start_ms": (now - self._t0) * 1000, "_t": now, "status": "ok",
                              "attributes": {}}

    def _close(self, run_id: UUID, **fields: Any) -> dict | None:
        span = self._open.pop(run_id, None)
        if span is not None:
            span["duration_ms"] = (time.perf_counter() - span.pop("_t")) * 1000
            span.update(fields)
            self.spans.append(span)
        return span

    def on_chain_end(self, outputs, *, run_id: UUID, **kwargs: Any) -> None:
        if (span := self._close(run_id)) is None or not isinstance(outputs, dict):
            return
        # the graph's own per-node record (seconds, n_docs, route, stage2_*, origin), minus the time we measure
        span["attributes"] = {k: v for k, v in ((outputs.get("trace") or {}).get(span["name"]) or {}).items()
                              if k != "seconds" and v is not None}
        if outputs.get("refusal_reason"):
            span["attributes"]["refusal_reason"] = outputs["refusal_reason"]
        if "docs" in outputs:
            span["keys"] = [d.metadata.get("key") for d in outputs["docs"]]
            span["attributes"]["top_keys"] = span["keys"][:5]

    def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._close(run_id, status="error", attributes={"error": f"{type(error).__name__}: {error}"})


def lost_at(spans: list[dict], gold: set[str], k: int) -> tuple[str, str] | None:
    """(node, reason) for the search node that left the gold tables outside the top k (plans/T2-36.md Idea 3).

    Per node, the best rank of any gold key in its whole output pool (positions, so no ties). The node is the last
    one that moved it from <= k to > k; retrieve when no node ever had it within k. None without node spans.
    """
    ranks = [(s["name"], next((n for n, key in enumerate(s["keys"], 1) if key in gold), math.inf))
             for s in spans if "keys" in s]
    if not ranks:
        return None
    show = lambda r: "밖" if r == math.inf else f"{r}위"  # noqa: E731
    culprit = None
    for (_, before), (name, after) in zip(ranks, ranks[1:]):
        if before <= k < after:
            culprit = (name, f"{name}이(가) 정답 표를 {show(before)} → {show(after)}로 내림 (기준 top {k})")
    if culprit:
        return culprit
    name, first = ranks[0]
    if first == math.inf:
        return name, f"{name}: 정답 표가 후보 풀({len(spans[0].get('keys') or [])}개)에 없음"
    return name, f"{name}: 정답 표 {show(first)}, 이후 노드도 top {k} 안으로 못 올림 (최종 {show(ranks[-1][1])})"


def blame_search(row: dict, gold: set[str], k: int) -> dict | None:
    """L1 counterpart of blame() for one scored row (keys, answerable, no_answer_pred, search_spans): the node a
    Hit@k miss is lost at, or gate for a wrong no-answer call; None when the row is right.

    The L1 no-answer call is the evaluator's tau on the top score, not the graph's gate, so the reason shows both.
    """
    nodes = row.get("search_spans") or []
    if row["answerable"] and not any(key in gold for key in row["keys"][:k]):
        node, reason = lost_at(nodes, gold, k) or ("retrieve", f"정답 표가 top {k} 밖")
        return {"stage": "retrieval", "label": STAGES["retrieval"], "span": node, "reason": reason}
    if row["no_answer_pred"] == row["answerable"]:  # refused an answerable one, or found tables for an unanswerable one
        gate = next((s for s in nodes if s["name"] == "gate"), None)
        route = gate["attributes"].get("route") if gate else None
        what = "답 있는 질문을 거절" if row["answerable"] else "답 없는 질문에 표 안내"
        return {"stage": "no_answer", "label": NO_ANSWER_LABEL, "span": "gate" if gate else None,
                "reason": f"{what} (tau 판정 no_answer={row['no_answer_pred']}, 그래프 gate route={route})"}
    return None


def _failure(stage: str, span: str | None, reason: str, others: set[str]) -> dict:
    extra = f" (같이 나온 오류: {', '.join(sorted(others))})" if others else ""
    return {"stage": stage, "label": STAGES[stage], "span": span, "reason": reason + extra}


def blame(row: dict) -> dict | None:
    """First matching rule (plans/T2-15.md table); None for a PASS row. row: verdict, kind, errors, spans, ...;
    e2e rows also carry right_table, used_table, gold_rank, error."""
    if row["verdict"] == "PASS":
        return None
    errors, spans = set(row["errors"]), row.get("spans") or []
    names = [s["name"] for s in spans]
    analyze = [s for s in spans if s["name"].startswith("analyze.")]
    last = lambda pred: next((s["name"] for s in reversed(spans) if pred(s)), None)  # noqa: E731

    if row.get("right_table") is False:
        rank = row.get("gold_rank")
        span, node = SEARCH_TOOL, ""
        if lost := lost_at(row.get("search_spans") or [], set(row.get("gold_keys") or []), 1):
            span, node = lost[0], f" — {lost[1]}"
        return _failure("retrieval", span, f"검색 1위 {row.get('used_table') or '없음'} ≠ 정답 표 "
                        f"(정답 순위 {rank or '밖'}){node}", errors)
    if "run_failure" in errors:
        span = last(lambda s: s["status"] == "error") or STAT_TOOL
        return _failure("tool_call", span, row.get("error") or "답 없음", errors - {"run_failure"})
    if "fallback_chatbot" in names:
        tried = ", ".join(f"{s['name']}={s['status']}" for s in analyze) or "analyze 없음"
        return _failure("tool_skipped", "fallback_chatbot",
                        f"계산 모듈(compute)을 거치지 않고 LLM이 숫자를 썼다 — 계획을 못 만든 앞 단계: {tried}", errors)
    compute = next((s for s in spans if s["name"] == "compute"), None)
    if compute and row["kind"] in ("answerable", "default_allowed") and (
            compute["status"] == "error" or compute["attributes"].get("outcome") == "refused"):
        return _failure("compute", "compute", f"계산 모듈이 거절함 (reason={compute['attributes'].get('reason')})", errors)
    if hit := errors & JUDGE_ERRORS:
        judge = next((s for s in spans if s["name"] == "judge"), None)
        span = "judge" if judge is None or judge["attributes"].get("final") or not analyze else analyze[-1]["name"]
        outcome = judge["attributes"].get("outcome") if judge else None
        return _failure("understanding", span, f"판정 오류 {', '.join(sorted(hit))} (judge outcome={outcome})", errors - hit)
    if hit := errors & PLAN_ERRORS:
        span = analyze[-1]["name"] if analyze else STAT_TOOL
        return _failure("understanding", span, f"계획이 고른 셀이 틀림: {', '.join(sorted(hit))}", errors - hit)
    if "unsupported_claim" in errors:
        return _failure("compose", last(lambda s: s["name"] == "compose") or STAT_TOOL,
                        "근거 없는 숫자를 문장에 씀 (unsupported_claim)", errors - {"unsupported_claim"})
    return _failure("unknown", None, "규칙에 안 맞음", errors)
