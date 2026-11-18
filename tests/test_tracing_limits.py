"""T3-44: a huge error message (phase 1 SchemaError listing every duplicate cell, 2 MB) must not make a turn's spans
exceed the OTLP / Jaeger limit (4 MB per batch), while tool results the graders read stay whole."""

import json
import uuid

from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from kosis_agent.observability.tracing import MAX_ERROR_TEXT, LiveSpans, record_error, short

HUGE = "SchemaError: " + "; ".join(f"cells[{73323 + i}].source_ref: duplicate of cells[{i}]" for i in range(40000))
OTLP_LIMIT = 4 * 1024 * 1024


def test_short_keeps_the_head_and_says_how_much_was_cut():
    assert short("abc") == "abc"
    cut = short(HUGE)
    assert cut.startswith(HUGE[:MAX_ERROR_TEXT]) and f"전체 {len(HUGE):,}자" in cut and len(cut) < MAX_ERROR_TEXT + 60


def test_a_huge_error_through_tool_node_and_root_stays_far_under_the_limit():
    provider, memory = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = provider.get_tracer("t")
    root = tracer.start_span("invoke_agent")
    spans = LiveSpans(tracer, root)
    graph, node, tool = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    spans.on_chain_start({}, {}, run_id=graph, name="LangGraph")
    spans.on_chain_start({}, {}, run_id=node, parent_run_id=graph, name="statistics", metadata={"langgraph_node": "statistics"})
    spans.on_tool_start({"name": "summarize_stat"}, "{}", run_id=tool, parent_run_id=node, inputs={"q": "x"})
    error = ValueError(HUGE)
    spans.on_tool_error(error, run_id=tool)
    spans.on_chain_error(error, run_id=node)
    record_error(root, error)  # the service's _fail / the eval runners' turn root
    root.end()
    finished = memory.get_finished_spans()
    assert len(HUGE) > 1_000_000
    size = encode_spans(finished).ByteSize()
    assert size < 100_000 < OTLP_LIMIT, size  # was 18.7 MB for the same error on three spans (plans/T3-44.md)
    for s in finished:
        if s.status.description:
            assert len(s.status.description) < MAX_ERROR_TEXT + 60
        for e in s.events:
            assert all(len(str(v)) < MAX_ERROR_TEXT + 60 for v in e.attributes.values())


def test_tool_results_the_graders_read_are_not_cut():
    provider, memory = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = provider.get_tracer("t")
    root = tracer.start_span("invoke_agent")
    spans = LiveSpans(tracer, root)
    tool = uuid.uuid4()
    result = {"text": "가" * (MAX_ERROR_TEXT * 5)}  # a long but normal tool result
    spans.on_tool_start({"name": "summarize_stat"}, "{}", run_id=tool, inputs={})
    spans.on_tool_end(result, run_id=tool)
    root.end()
    span = next(s for s in memory.get_finished_spans() if s.name == "execute_tool summarize_stat")
    assert json.loads(span.attributes["gen_ai.tool.call.result"]) == result
