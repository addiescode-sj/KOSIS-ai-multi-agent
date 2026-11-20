"""Agent layer runner (T3-06, KOS-120 / KOS-114): run scenarios turn by turn and read each turn from its live spans.

Any compiled LangGraph graph works (ReAct, Plan-and-Execute, multi-agent: T3-07~10): each turn is
graph.invoke({"messages": [("user", question)]}) under thread_id "<sid>#<run>", so the graph's own checkpointer
carries the conversation. kosis_agent.observability.tracing.LiveSpans opens the OTel spans while it runs; the turn record (tool calls,
args, results, latency, LLM calls, tokens) is read back from those spans, the same ones Jaeger receives when
OTEL_EXPORTER_OTLP_ENDPOINT is set. After grading, link_verdicts adds one `eval.scenario` span per run that links
to the turns' root spans and carries the verdict (KOS-114: tie live traces to scores known only afterwards).
LLM calls (T3-31) are the spans with gen_ai.operation.name == "chat": LangChain chat models, phase 1 HCX steps and the
phase 2 rerank2 call. The same callback records gen_ai.client.token.usage; metering() + metric_tokens() read it back
so a run can check the metric totals against the span totals.

T3-32: every layer that traces runs through the agent graph or its tools, so all traces share one service name
(`kosis-agent`, resource deployment.environment.name=eval, kosis.eval.layer). single_turns runs l3 / e2e questions
as one-turn conversations and turns the final graph state into the rows the L3 grader and trace.blame read;
link_questions adds the verdict afterwards as an `eval.question` span linked to each turn's root.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from typing import Any, get_args

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, SpanContext, StatusCode, TraceFlags

from kosis_agent.observability.tracing import CHAT, ROOT_SPAN, TOOL_PREFIX, agent_turn, record_error, resource, short

from evals.evaluators.labels import state_labels
from evals.schemas import AgentScenario

ENVIRONMENT = "eval"  # deployment.environment.name of every eval trace (the service says "service")


def load_graph(spec: str):
    """`module:function` -> function() (a compiled graph), e.g. backend.app.main:build_agent (T3-23)."""
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)()


def tracing(layer: str = "agent") -> tuple[TracerProvider, InMemorySpanExporter]:
    """A provider of its own (no global state): spans to memory, and to OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    provider = TracerProvider(resource=resource(ENVIRONMENT, **{"kosis.eval.layer": layer}))
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    return provider, memory


def metering(layer: str = "agent") -> tuple[MeterProvider, InMemoryMetricReader]:
    """A meter provider of its own (no global state) whose metrics stay in memory."""
    reader = InMemoryMetricReader()
    return MeterProvider(resource=resource(ENVIRONMENT, **{"kosis.eval.layer": layer}), metric_readers=[reader]), reader


def metric_tokens(reader: InMemoryMetricReader) -> dict[str, int]:
    """{"input": n, "output": n}: the gen_ai.client.token.usage histogram sums so far (cumulative)."""
    out = {"input": 0, "output": 0}
    data = reader.get_metrics_data()
    for rm in data.resource_metrics if data else []:
        for sm in rm.scope_metrics:
            for metric in sm.metrics:
                if metric.name != "gen_ai.client.token.usage":
                    continue
                for point in metric.data.data_points:
                    kind = point.attributes.get("gen_ai.token.type")
                    if kind in out:
                        out[kind] += int(point.sum)
    return out


def _is_llm(span: ReadableSpan) -> bool:
    return (span.attributes or {}).get("gen_ai.operation.name") == CHAT


def _loads(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    try:
        return json.loads(text)
    except ValueError:
        return text


def turn_record(spans: list[ReadableSpan], answer: str, error: str | None) -> dict:
    """One turn's record from its finished spans: calls in start order, latency of the root, LLM calls and tokens."""
    root = next(s for s in spans if s.name == ROOT_SPAN)
    mine = [s for s in spans if s.context.trace_id == root.context.trace_id]
    tools = sorted((s for s in mine if s.name.startswith(TOOL_PREFIX)), key=lambda s: s.start_time)
    chats = [s for s in mine if _is_llm(s)]
    models = sorted({s.attributes.get("gen_ai.request.model") for s in chats} - {None})
    by_model: dict[str, dict[str, int]] = {}  # per model, so ops can price a turn that calls two models (T3-39)
    for s in chats:
        t = by_model.setdefault(s.attributes.get("gen_ai.request.model") or "unknown", {"input": 0, "output": 0})
        t["input"] += int(s.attributes.get("gen_ai.usage.input_tokens") or 0)
        t["output"] += int(s.attributes.get("gen_ai.usage.output_tokens") or 0)
    return {
        "calls": [{"name": s.attributes.get("gen_ai.tool.name"),
                   "args": _loads(s.attributes.get("gen_ai.tool.call.arguments")) or {},
                   "result": _loads(s.attributes.get("gen_ai.tool.call.result")),
                   "status": "error" if s.status.status_code == StatusCode.ERROR else "ok"} for s in tools],
        "answer": answer, "error": error,
        "latency_ms": (root.end_time - root.start_time) / 1e6,
        "llm_calls": len(chats),
        "input_tokens": sum(int(s.attributes.get("gen_ai.usage.input_tokens") or 0) for s in chats),
        "output_tokens": sum(int(s.attributes.get("gen_ai.usage.output_tokens") or 0) for s in chats),
        "model": ",".join(models) or None,
        "tokens_by_model": by_model,
        "failed": error is not None,
        "retried": any(int(s.attributes.get("llm.attempts") or 1) > 1 for s in chats),  # an HTTP retry (429/5xx)
        "trace_id": format(root.context.trace_id, "032x"), "span_id": format(root.context.span_id, "016x"),
        "spans": span_rows(root, mine),
    }


def span_rows(root: ReadableSpan, spans: list[ReadableSpan]) -> list[dict]:
    """The turn's spans for the report waterfall: name, kind, start and length (ms from the root's start), status, depth."""
    parent = {s.context.span_id: s.parent.span_id if s.parent else None for s in spans}

    def depth(span_id: int) -> int:
        n = 0
        while (span_id := parent.get(span_id)) in parent:
            n += 1
        return n

    kind = lambda s: ("turn" if s.name == ROOT_SPAN else "tool" if s.name.startswith(TOOL_PREFIX)  # noqa: E731
                      else "llm" if _is_llm(s) else "node")
    return [{"name": s.name, "kind": kind(s), "start_ms": (s.start_time - root.start_time) / 1e6,
             "duration_ms": (s.end_time - s.start_time) / 1e6, "depth": depth(s.context.span_id),
             "status": "error" if s.status.status_code == StatusCode.ERROR else "ok"}
            for s in sorted(spans, key=lambda s: s.start_time)]


def _answer(state: Any) -> str:
    messages = state.get("messages") if isinstance(state, dict) else None
    return str(getattr(messages[-1], "content", "")) if messages else ""


def run(graph, scenarios: list[AgentScenario], repeat: int = 1,
        telemetry: tuple[TracerProvider, InMemorySpanExporter] | None = None,
        meters: MeterProvider | None = None) -> dict[tuple[str, int], list[dict]]:
    """{(sid, run): turn records in order}. An exception ends that run (later turns are graded as not run)."""
    provider, memory = telemetry or tracing()
    tracer = provider.get_tracer("kosis-agent")
    meter = meters.get_meter("kosis-agent") if meters is not None else None
    out: dict[tuple[str, int], list[dict]] = {}
    total, done = len(scenarios) * repeat, 0
    for scenario in scenarios:
        for r in range(repeat):
            thread = f"{scenario.sid}#{r}"
            records = out[(scenario.sid, r)] = []
            done += 1
            active = None  # the table the conversation ended the previous turn on (T3-46 labels: rule 1 vs rule 6)
            for i, turn in enumerate(scenario.turns):
                memory.clear()
                answer, error, started, state = "", None, time.perf_counter(), {}
                with agent_turn(tracer, thread, i, meter) as (root, spans):
                    try:
                        state = graph.invoke({"messages": [("user", turn.question)]},
                                             config={"configurable": {"thread_id": thread}, "callbacks": [spans]})
                        answer = _answer(state)
                    except Exception as e:  # noqa: BLE001 - a failed turn is graded, not raised
                        error = f"{type(e).__name__}: {e}"
                        record_error(root, e)  # T3-44: span gets a cut copy, the record keeps the full error
                labels = state_labels(state, turn.question, had_table=active is not None) if isinstance(state, dict) and state else None
                active = state.get("active_table") if isinstance(state, dict) else active
                records.append({**turn_record(list(memory.get_finished_spans()), answer, error), "run": r,
                                "labels": labels.model_dump(exclude_none=True) if labels else None})
                progress(done, total, f"{thread} turn {i}", started, error)
                if error:
                    break
    provider.force_flush()
    return out


def progress(n: int, total: int, label: str, started: float, error: str | None = None) -> None:
    """One stderr line per question / turn, so a long live run shows how far it is (T3-33; run_eval prints the rest at the end)."""
    print(f"[{n}/{total}] {label} {time.perf_counter() - started:.1f}s" + (f" ERROR {error[:80]}" if error else ""),
          file=sys.stderr, flush=True)


def _final_answer(state: dict, error: str | None):
    """(StatAnswer | None, error): phase 1's answer, or the search gate's refusal as a refused StatAnswer.

    The L3 grader reads a StatAnswer. A turn the search gate refused never reaches phase 1, so its refusal (reason
    out_of_scope | no_item, both phase 1 RefusalReason values) is graded as phase 1's refusal would be (T3-32)."""
    from kosis_ai.schemas import Refusal, RefusalReason, StatAnswer

    stat, search = state.get("stat"), state.get("search")
    if stat is not None:
        return stat.answer, error
    if error is None and state.get("outcome") == "refused" and search is not None:
        reason = search.refusal_reason if search.refusal_reason in get_args(RefusalReason) else "out_of_scope"
        text = _answer(state) or search.refusal_text or "답할 수 없어요."
        # model_construct: StatAnswer requires a source for every refusal but out_of_scope, and the gate refused
        # before any table was opened; inventing a citation would be wrong. This record only feeds the grader
        # (outcome + refusal.reason), it is never shown to a user.
        return StatAnswer.model_construct(outcome="refused", text=text,
                                          refusal=Refusal(reason=reason, message=text)), None
    return None, error or f"no phase 1 answer (outcome {state.get('outcome')})"


def single_turns(graph, questions: list[tuple[str, str]], telemetry: tuple[TracerProvider, InMemorySpanExporter],
                 meters: MeterProvider | None = None, interval: float = 0.0) -> list[dict]:
    """l3 / e2e rows: each (qid, question) as a new one-turn conversation of the agent graph (T3-32, option C).

    A row has what the L3 grader, e2e.locate and trace.blame read: answer (StatAnswer), error, spans (phase 1 step
    records of the summarize_stat output), search_spans (search graph nodes, for blame's lost_at), keys (search
    candidates in rank order), used_table (the table phase 1 answered from), plus the turn record's ops fields
    (LLM calls and tokens from the chat spans, the same rule as the agent layer) and its trace / span ids."""
    from evals.trace import NodeSpans, restore_search

    provider, memory = telemetry
    tracer = provider.get_tracer("kosis-agent")
    meter = meters.get_meter("kosis-agent") if meters is not None else None
    rows = []
    for n, (qid, question) in enumerate(questions):
        if n and interval:
            time.sleep(interval)
        memory.clear()
        started = time.perf_counter()
        state, error, nodes, thread = {}, None, NodeSpans(), f"{qid}#0"
        with agent_turn(tracer, thread, 0, meter) as (root, spans):
            root.set_attribute("kosis.eval.qid", qid)
            try:
                state = graph.invoke({"messages": [("user", question)]},
                                     config={"configurable": {"thread_id": thread}, "callbacks": [spans, nodes]})
            except Exception as e:  # noqa: BLE001 - a failed question is graded (run_failure), not raised
                error = f"{type(e).__name__}: {e}"
                record_error(root, e)  # T3-44: span gets a cut copy, the record keeps the full error
        record = turn_record(list(memory.get_finished_spans()), _answer(state), error)
        answer, error = _final_answer(state, error)
        search, stat = state.get("search"), state.get("stat")
        keys = [c.key for c in search.candidates] if search is not None else []
        search_ms = sum(s["duration_ms"] for s in record["spans"] if s["name"] == TOOL_PREFIX + "search_tables")
        rows.append({"qid": qid, "answer": answer, "error": error, "keys": keys,
                     "used_table": state.get("table_key") or (keys[0] if keys else None),
                     "spans": list(stat.spans) if stat is not None else [], "search_spans": nodes.spans,
                     "retrieve_ms": search_ms, "trace_spans": record["spans"],  # the live trace, for report.html
                     **{k: record[k] for k in ("latency_ms", "llm_calls", "input_tokens", "output_tokens", "model",
                                               "tokens_by_model", "retried", "trace_id", "span_id")},
                     "failed": error is not None})
        progress(n + 1, len(questions), qid, started, error)
    provider.force_flush()
    restore_search(rows)  # T3-45: a turn that raised after search keeps its candidates (from the node spans)
    return rows


def link_questions(provider: TracerProvider, rows: list[dict], layer: str, name: str = "eval.question",
                   attributes=None) -> None:
    """One `eval.question` span per graded row, linked to the row's turn root, with the verdict and failure (T3-32).
    T3-19: the ragas layer names it `eval.ragas` and adds its scores through attributes(row) -> dict."""
    tracer = provider.get_tracer("kosis-evals")
    for r in rows:
        if not r.get("trace_id") or not r.get("span_id"):
            continue
        f = r.get("failure") or {}
        link = Link(SpanContext(int(r["trace_id"], 16), int(r["span_id"], 16), is_remote=True,
                                trace_flags=TraceFlags(TraceFlags.SAMPLED)))
        with tracer.start_as_current_span(name, links=[link], attributes={
                "kosis.eval.qid": r["qid"], "kosis.eval.layer": layer, "kosis.verdict": r.get("verdict") or "",
                "kosis.errors": ",".join(r.get("errors") or []),
                # T3-44: a run_failure reason carries the whole error (2 MB SchemaError): cut, results.jsonl keeps it
                **{f"kosis.failure.{k}": short(v) if isinstance(v, str) else v for k, v in f.items() if v is not None and k != "label"},
                **(attributes(r) if attributes else {})}) as span:
            if f:
                span.set_status(StatusCode.ERROR, short(f"{f['label']}: {f['reason']}"))
    provider.force_flush()


def link_verdicts(provider: TracerProvider, rows: list[dict]) -> None:
    """One `eval.scenario` span per (sid, run), linked to its turns' root spans, with the verdict and first failure."""
    tracer = provider.get_tracer("kosis-evals")
    runs: dict[tuple, list[dict]] = {}
    for r in rows:
        runs.setdefault((r["sid"], r.get("run", 0)), []).append(r)
    for (sid, run_no), turns in runs.items():
        links = [Link(SpanContext(int(t["trace_id"], 16), int(t["span_id"], 16), is_remote=True,
                                  trace_flags=TraceFlags(TraceFlags.SAMPLED))) for t in turns if t.get("trace_id")]
        failed = next((t for t in turns if not t["passed"]), None)
        with tracer.start_as_current_span("eval.scenario", links=links, attributes={
                "kosis.scenario.id": sid, "kosis.scenario.run": run_no,
                "kosis.verdict": "PASS" if failed is None else "FAIL",
                "kosis.failure.turn": -1 if failed is None else failed["turn"],
                "kosis.failure.errors": ",".join(failed["errors"]) if failed else ""}) as span:
            if failed is not None:
                span.set_status(StatusCode.ERROR, ",".join(failed["errors"]))
    provider.force_flush()
