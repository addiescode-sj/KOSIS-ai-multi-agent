"""Stage-level tracing for L1 / L3 / e2e runs (plans/T2-15.md, T2-36): which span a question failed in, and why;
Jaeger export.

One question = one trace:

    eval.question            qid, kind, layer, verdict, failure.stage / failure.span / failure.reason
    ├─ retrieve              L1·e2e, a plain retriever: retriever, top_keys, gold_rank, right_table
    ├─ search_tables         L1·e2e, the LangGraph search (T2-10), same attributes
    │  └─ parse, retrieve, rerank1, rerank2, gate, refuse | guide           (graph nodes, NodeSpans)
    └─ tool.summarize_stat   L3·e2e: table_key, outcome, numbers_origin
       └─ judge, analyze.*, compute, compose, verify, template, fallback_chatbot   (Phase 1 spans, T1-22)

The Phase 1 pipeline already records OTel-shaped span dicts in Answer.decision_metadata["spans"]; summarize() keeps
them (summarize_stat drops them). NodeSpans records the graph nodes as span dicts of the same shape from LangChain
callbacks (row["search_spans"], kept apart from the Phase 1 row["spans"]). blame() / blame_search() name the failing
stage with deterministic rules, and export() replays the dicts as OTel spans over OTLP when
OTEL_EXPORTER_OTLP_ENDPOINT is set (docker compose --profile obs up jaeger).
Tokens and cost stay in evals.ops (ops.json); the gen_ai.* span attributes are only the per-call record.
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from evals.evaluators.calculation import FACT_ERRORS

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


def summarize(question: str, table_key: str, client, retry_interval: float = 0.0):
    """summarize_stat that also returns the pipeline's spans: (StatAnswer | None, spans, error)."""
    from kosis_chat import api
    from kosis_chat.contract import to_stat_answer

    # ponytail: private _build, since summarize_stat returns no spans; drop when Phase 1 exposes them (KOS-95 follow-up)
    bot = api._build(api.table_path(table_key), None, client, retry_interval)
    answer = bot.ask(question)
    spans = (answer.decision_metadata or {}).get("spans") or []
    stat = to_stat_answer(question, answer, org_id=bot.org_id)
    return stat, spans, None if stat else f"RuntimeError: {table_key}: the model call failed ({answer.status}: {answer.error})"


class NodeSpans(BaseCallbackHandler):
    """The LangGraph node runs of one search as span dicts shaped like the Phase 1 ones; one handler per question.

    LangGraph passes the node name (metadata langgraph_node) down to the runs inside a node (channel writes, the gate
    branch, the guide chain), so a run is a node only when its name is its langgraph_node and its parent is not a
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
    if row["no_answer_pred"] == row["answerable"]:  # refused an answerable one, or guided an unanswerable one
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
        span, node = "retrieve", ""
        if lost := lost_at(row.get("search_spans") or [], set(row.get("gold_keys") or []), 1):
            span, node = lost[0], f" — {lost[1]}"
        return _failure("retrieval", span, f"검색 1위 {row.get('used_table') or '없음'} ≠ 정답 표 "
                        f"(정답 순위 {rank or '밖'}){node}", errors)
    if "run_failure" in errors:
        span = last(lambda s: s["status"] == "error") or "tool.summarize_stat"
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
        span = analyze[-1]["name"] if analyze else "tool.summarize_stat"
        return _failure("understanding", span, f"계획이 고른 셀이 틀림: {', '.join(sorted(hit))}", errors - hit)
    if "unsupported_claim" in errors:
        return _failure("compose", last(lambda s: s["name"] == "compose") or "tool.summarize_stat",
                        "근거 없는 숫자를 문장에 씀 (unsupported_claim)", errors - {"unsupported_claim"})
    return _failure("unknown", None, "규칙에 안 맞음", errors)


def _attr(v):
    return v if isinstance(v, (str, bool, int, float)) else json.dumps(v, ensure_ascii=False, default=str)


def _replay(tracer, ctx, spans: list[dict], start: int, failure: dict) -> None:
    """Child OTel spans for span dicts under ctx, offset from start (ns); the blamed one and error ones get ERROR."""
    from opentelemetry.trace import Status, StatusCode

    ms = 1_000_000
    for sp in spans:
        begin = start + int(sp["start_ms"] * ms)
        child = tracer.start_span(sp["name"], context=ctx, start_time=begin,
                                  attributes={k: _attr(v) for k, v in sp["attributes"].items()})
        if sp["status"] == "error" or failure.get("span") == sp["name"]:
            child.set_status(Status(StatusCode.ERROR, failure["reason"] if failure.get("span") == sp["name"] else "error"))
        child.end(end_time=begin + int(sp["duration_ms"] * ms))


def export(rows: list[dict], layer: str, retriever: str | None) -> bool:
    """Replay each row as one trace over OTLP and set row["trace_id"]; no endpoint -> nothing sent, False.

    Spans are laid out back to back ending now (rows hold durations, not wall-clock times), so durations and order
    are real while absolute times are not. L1 rows are the search alone; L3 rows the tool alone; e2e both.
    """
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return False
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Status, StatusCode

    provider = TracerProvider(resource=Resource.create({"service.name": "kosis-evals"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    tracer = provider.get_tracer("evals.trace")
    ms = 1_000_000
    t = time.time_ns() - int(sum(r.get("latency_ms", 0) for r in rows) * ms)
    for r in rows:
        f = r.get("failure") or {}
        end = t + int(r.get("latency_ms", 0) * ms)
        root = tracer.start_span("eval.question", start_time=t, attributes={
            "qid": r["qid"], "layer": layer,
            **{k: r[k] for k in ("kind", "verdict", "errors", "qtype", "answerable") if r.get(k) is not None},
            **{f"failure.{k}": v for k, v in f.items() if v is not None and k != "label"}})
        ctx = trace.set_span_in_context(root)
        tool_start = t
        if layer == "l1" or "right_table" in r:  # the search step: the whole row in L1, the first part in e2e
            tool_start = end if layer == "l1" else t + int(r.get("retrieve_ms", 0) * ms)
            nodes = r.get("search_spans") or []
            s = tracer.start_span("search_tables" if nodes else "retrieve", context=ctx, start_time=t, attributes={
                "retriever": retriever or "", "top_keys": r.get("top_keys") or (r.get("keys") or [])[:5],
                "gold_rank": r.get("gold_rank") or r.get("rank") or 0,
                **({"right_table": r["right_table"]} if "right_table" in r else {})})
            if f.get("span") == "retrieve" and not nodes:
                s.set_status(Status(StatusCode.ERROR, f["reason"]))
            _replay(tracer, trace.set_span_in_context(s), nodes, t, f)
            s.end(end_time=tool_start)
        if layer != "l1":
            answer = r.get("answer") or {}
            tool = tracer.start_span("tool.summarize_stat", context=ctx, start_time=tool_start, attributes={
                "table_key": r.get("used_table") or r.get("table_key") or "", "outcome": r.get("outcome") or "",
                "numbers_origin": answer.get("numbers_origin") or ""})
            _replay(tracer, trace.set_span_in_context(tool), r.get("spans") or [], tool_start, f)
            if f.get("stage") == "tool_call":
                tool.set_status(Status(StatusCode.ERROR, f["reason"]))
            tool.end(end_time=end)
        if f:
            root.set_status(Status(StatusCode.ERROR, f"{f['label']}: {f['reason']}"))
        root.end(end_time=end)
        r["trace_id"] = format(root.get_span_context().trace_id, "032x")
        t = end
    provider.shutdown()  # flushes the batch
    return True
