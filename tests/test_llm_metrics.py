"""T3-31 (KOS-115): every LLM call is a gen_ai chat span, and the token metric matches the raw usage the
phase 1 / phase 2 records carry (independent source, not the span attributes the metric is written from)."""

from types import SimpleNamespace

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from evals.runners.agent import metering, metric_tokens, turn_record
from kosis_agent.observability.tracing import CHAT, agent_turn
from kosis_agent.retrieval.search import make_search_tables_tool
from kosis_agent.services.statistics import make_summarize_stat_tool
from kosis_ai.schemas import Refusal, StatAnswer
from kosis_chat.schemas.io import StatOutput
from kosis_rag.schemas.io import SearchOutput
from tests.test_agent_graph import STAT_LLM, TABLE

# phase 2 trace in node order: retrieve ran in parallel, rerank2 then gate finished last
SEARCH_SPANS = [{"node": "parse", "seconds": 0.01}, {"node": "retrieve_bm25", "seconds": 0.30},
                {"node": "retrieve_dense", "seconds": 0.40}, {"node": "fuse", "seconds": 0.01},
                {"node": "rerank2", "seconds": 0.52, "stage2": "clova", "stage2_calls": 2, "stage2_input_tokens": 900,
                 "stage2_output_tokens": 40, "stage2_seconds": 0.5, "stage2_error": None},
                {"node": "gate", "seconds": 0.2}]
STEPS = [{"name": "analyze.llm_plan", "start_ms": 0.0, "duration_ms": 30.0, "status": "ok",
          "attributes": {"gen_ai.request.model": "HCX-005", "gen_ai.usage.input_tokens": 120,
                         "gen_ai.usage.output_tokens": 30, "llm.attempts": 1}},
         {"name": "compute", "start_ms": 30.0, "duration_ms": 5.0, "status": "ok", "attributes": {}},
         {"name": "compose", "start_ms": 35.0, "duration_ms": 20.0, "status": "ok",
          "attributes": {"gen_ai.request.model": "HCX-005", "gen_ai.usage.input_tokens": 200,
                         "gen_ai.usage.output_tokens": 60}}]


class Search:
    """A SearchModule with a stage 2 LLM: resources.stage2_llm.model, rerank2 in the trace."""

    resources = SimpleNamespace(stage2_llm=SimpleNamespace(model="HCX-RERANK"))

    def __init__(self, spans=SEARCH_SPANS):
        self.spans = spans

    def stream(self, inp):
        yield "output", SearchOutput(question=inp.question, route="found", picked=[TABLE], candidates=[TABLE],
                                     spans=self.spans)


def summarize(question, table_id, **kw):
    text = "자료가 없어요."
    return StatOutput(answer=StatAnswer(outcome="refused", text=text, refusal=Refusal(reason="out_of_scope", message=text)),
                      chart=None, spans=STEPS)


def _turn(search=None):
    provider, memory = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    meters, reader = metering()
    chat = GenericFakeChatModel(messages=iter([AIMessage("{}", usage_metadata={
        "input_tokens": 50, "output_tokens": 5, "total_tokens": 55})]))
    with agent_turn(provider.get_tracer("t"), "t1", 0, meters.get_meter("t")) as (_, spans):
        config = {"callbacks": [spans]}
        chat.invoke("해석", config=config)
        make_search_tables_tool(search or Search()).invoke(
            {"name": "search_tables", "args": {"question": "q"}, "id": "1", "type": "tool_call"}, config=config)
        make_summarize_stat_tool(STAT_LLM, summarize=summarize).invoke(
            {"name": "summarize_stat", "args": {"question": "q", "table_key": "k"}, "id": "2", "type": "tool_call"},
            config=config)
    return list(memory.get_finished_spans()), reader


def test_all_three_llm_paths_are_chat_spans():
    spans, _ = _turn()
    llm = {s.name: s for s in spans if s.attributes.get("gen_ai.operation.name") == CHAT}
    assert set(llm) == {"chat unknown", "chat HCX-RERANK", "analyze.llm_plan", "compose"}  # compute is not an LLM call
    rerank = llm["chat HCX-RERANK"]
    tool = next(s for s in spans if s.name == "execute_tool search_tables")
    assert rerank.parent.span_id == tool.context.span_id
    assert rerank.attributes["llm.attempts"] == 2 and rerank.attributes["gen_ai.usage.input_tokens"] == 900
    # dated back from the tool's end: ends gate.seconds (0.2 s) before it, lasts stage2_seconds (0.5 s)
    assert rerank.end_time - rerank.start_time == 500_000_000
    assert 200_000_000 <= tool.end_time - rerank.end_time < 300_000_000


def test_token_metric_matches_the_raw_usage_and_the_eval_ops():
    spans, reader = _turn()
    raw_in = 50 + SEARCH_SPANS[4]["stage2_input_tokens"] + sum(
        s["attributes"].get("gen_ai.usage.input_tokens", 0) for s in STEPS)
    raw_out = 5 + SEARCH_SPANS[4]["stage2_output_tokens"] + sum(
        s["attributes"].get("gen_ai.usage.output_tokens", 0) for s in STEPS)
    assert metric_tokens(reader) == {"input": raw_in, "output": raw_out} == {"input": 1270, "output": 135}
    record = turn_record(spans, "", None)
    assert (record["llm_calls"], record["input_tokens"], record["output_tokens"]) == (4, raw_in, raw_out)


def test_no_stage2_call_no_span_and_unknown_tokens_are_not_counted():
    empty = [{"node": "rerank2", "seconds": 0.001}, {"node": "gate", "seconds": 0.01}]  # docs were empty
    spans, reader = _turn(Search(empty))
    assert not any(s.name == "chat HCX-RERANK" for s in spans)
    no_usage = [{**SEARCH_SPANS[4], "stage2_input_tokens": None, "stage2_output_tokens": None}]
    spans, reader = _turn(Search(no_usage))
    rerank = next(s for s in spans if s.name == "chat HCX-RERANK")
    assert "gen_ai.usage.input_tokens" not in rerank.attributes
    assert metric_tokens(reader) == {"input": 50 + 320, "output": 5 + 90}


def _metric_points(reader, name):
    data = reader.get_metrics_data()
    return [(dict(p.attributes), p.value) for rm in data.resource_metrics for sm in rm.scope_metrics
            for m in sm.metrics if m.name == name for p in m.data.data_points]


def test_finish_reasons_and_llm_failures_on_chat_spans():
    failed = [{**STEPS[0], "status": "error", "attributes": {**STEPS[0]["attributes"], "llm.status": "timeout"}}]
    provider, memory = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    chat = GenericFakeChatModel(messages=iter([AIMessage("{", response_metadata={"finish_reason": "length"})]))
    error = [{**SEARCH_SPANS[4], "stage2_error": "RuntimeError: CLOVA reranker call failed (code 500, 4 calls)"}]
    with agent_turn(provider.get_tracer("t"), "t1", 0) as (_, spans):
        chat.invoke("해석", config={"callbacks": [spans]})
        make_search_tables_tool(Search(error)).invoke(
            {"name": "search_tables", "args": {"question": "q"}, "id": "1", "type": "tool_call"},
            config={"callbacks": [spans]})
        make_summarize_stat_tool(STAT_LLM, summarize=lambda *a, **k: summarize(*a).model_copy(
            update={"spans": failed})).invoke(
            {"name": "summarize_stat", "args": {"question": "q", "table_key": "k"}, "id": "2", "type": "tool_call"},
            config={"callbacks": [spans]})
    by_name = {s.name: s for s in memory.get_finished_spans()}
    assert by_name["chat unknown"].attributes["gen_ai.response.finish_reasons"] == ("length",)
    assert by_name["analyze.llm_plan"].attributes["error.type"] == "timeout"
    assert by_name["chat HCX-RERANK"].attributes["error.type"] == "RuntimeError"


def test_the_turn_root_says_how_the_turn_ended():
    from kosis_agent.graph.main_graph import build_graph
    from kosis_agent.graph.state import new_checkpointer
    from tests.test_agent_graph import FakeSearch

    provider, memory = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    meters, reader = metering()
    tracer, meter = provider.get_tracer("t"), meters.get_meter("t")
    graph = build_graph(FakeSearch("refuse"), STAT_LLM, summarize=summarize, checkpointer=new_checkpointer())
    with agent_turn(tracer, "t1", 0, meter) as (root, spans):  # search refuses: a normal end, with its reason
        list(graph.stream({"messages": [("user", "화성 인구")]},
                          {"configurable": {"thread_id": "t1"}, "callbacks": [spans]}, stream_mode="values"))
    assert root.attributes["kosis.outcome"] == "refused"
    assert root.attributes["kosis.no_answer_reason"] == "out_of_scope"

    class Broken:
        def stream(self, inp):
            raise ConnectionError("chroma down")
            yield

    broken = build_graph(Broken(), STAT_LLM, summarize=summarize, checkpointer=new_checkpointer())
    try:
        with agent_turn(tracer, "t2", 0, meter) as (root, spans):
            broken.invoke({"messages": [("user", "출생아 수")]}, {"configurable": {"thread_id": "t2"}, "callbacks": [spans]})
    except ConnectionError:
        pass
    assert (root.attributes["kosis.outcome"], root.attributes["error.type"]) == ("error", "ConnectionError")
    counts = {(a["kosis.outcome"], a.get("kosis.no_answer_reason") or a.get("error.type")): v
              for a, v in _metric_points(reader, "kosis.agent.turns")}
    assert counts == {("refused", "out_of_scope"): 1, ("error", "ConnectionError"): 1}


def test_a_phase1_step_already_live_as_a_node_is_not_drawn_twice():
    """T3-32: phase 1's own graph nodes open live under the tool; the step record of the same name is skipped,
    while an LLM step (tokens only in the record) stays."""
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    class S(TypedDict, total=False):
        n: int

    inner = StateGraph(S)
    inner.add_node("compute", lambda s: {"n": 1})
    inner.add_edge(START, "compute")
    inner.add_edge("compute", END)
    inner = inner.compile()

    def live(question, table_id, **kw):
        inner.invoke({"n": 0})  # phase 1 runs its graph inside the tool, as the real module does
        return summarize(question, table_id)

    provider, memory = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with agent_turn(provider.get_tracer("t"), "t1", 0) as (_, spans):
        make_summarize_stat_tool(STAT_LLM, summarize=live).invoke(
            {"name": "summarize_stat", "args": {"question": "q", "table_key": "k"}, "id": "2", "type": "tool_call"},
            config={"callbacks": [spans]})
    names = [s.name for s in memory.get_finished_spans()]
    assert names.count("compute") == 1 and "analyze.llm_plan" in names and "compose" in names
