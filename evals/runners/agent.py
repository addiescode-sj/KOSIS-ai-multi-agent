"""Agent layer runner (T3-06, KOS-120 / KOS-114): run scenarios turn by turn and read each turn from its live spans.

Any compiled LangGraph graph works (ReAct, Plan-and-Execute, multi-agent: T3-07~10): each turn is
graph.invoke({"messages": [("user", question)]}) under thread_id "<sid>#<run>", so the graph's own checkpointer
carries the conversation. kosis_agent.tracing.LiveSpans opens the OTel spans while it runs; the turn record (tool calls,
args, results, latency, LLM calls, tokens) is read back from those spans, the same ones Jaeger receives when
OTEL_EXPORTER_OTLP_ENDPOINT is set. After grading, link_verdicts adds one `eval.scenario` span per run that links
to the turns' root spans and carries the verdict (KOS-114: tie live traces to scores known only afterwards).
"""

from __future__ import annotations

import importlib
import json
import os
from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, SpanContext, StatusCode, TraceFlags

from kosis_agent.tracing import CHAT_PREFIX, ROOT_SPAN, TOOL_PREFIX, agent_turn

from evals.schemas import AgentScenario

SERVICE = "kosis-agent-evals"


def load_graph(spec: str):
    """`module:function` -> function() (a compiled graph), e.g. kosis_agent.graph:build_agent once T3-03 has it."""
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)()


def tracing() -> tuple[TracerProvider, InMemorySpanExporter]:
    """A provider of its own (no global state): spans to memory, and to OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
    memory = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    return provider, memory


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
    chats = [s for s in mine if s.name.startswith(CHAT_PREFIX)]
    models = sorted({s.attributes.get("gen_ai.request.model") for s in chats} - {None})
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
        "failed": error is not None,
        "trace_id": format(root.context.trace_id, "032x"), "span_id": format(root.context.span_id, "016x"),
    }


def _answer(state: Any) -> str:
    messages = state.get("messages") if isinstance(state, dict) else None
    return str(getattr(messages[-1], "content", "")) if messages else ""


def run(graph, scenarios: list[AgentScenario], repeat: int = 1,
        telemetry: tuple[TracerProvider, InMemorySpanExporter] | None = None) -> dict[tuple[str, int], list[dict]]:
    """{(sid, run): turn records in order}. An exception ends that run (later turns are graded as not run)."""
    provider, memory = telemetry or tracing()
    tracer = provider.get_tracer("kosis-agent")
    out: dict[tuple[str, int], list[dict]] = {}
    for scenario in scenarios:
        for r in range(repeat):
            thread = f"{scenario.sid}#{r}"
            records = out[(scenario.sid, r)] = []
            for i, turn in enumerate(scenario.turns):
                memory.clear()
                answer, error = "", None
                with agent_turn(tracer, thread, i) as (root, spans):
                    try:
                        state = graph.invoke({"messages": [("user", turn.question)]},
                                             config={"configurable": {"thread_id": thread}, "callbacks": [spans]})
                        answer = _answer(state)
                    except Exception as e:  # noqa: BLE001 - a failed turn is graded, not raised
                        error = f"{type(e).__name__}: {e}"
                        root.record_exception(e)
                        root.set_status(StatusCode.ERROR, error)
                records.append({**turn_record(list(memory.get_finished_spans()), answer, error), "run": r})
                if error:
                    break
    provider.force_flush()
    return out


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
