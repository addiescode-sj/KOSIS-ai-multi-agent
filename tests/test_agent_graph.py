"""Phase 3 main graph: question -> search_tables (phase 2 SearchModule) -> (refuse: its refusal_text | found:
summarize_stat (phase 1 summarize_stat_run with picked[0].key)) -> SSE. Offline: a fake search module and a fake
phase 1 call. T3-23: the nodes call phase 1 / 2 through LangChain tools, so the agent eval layer can grade the graph."""

import json
from typing import TypedDict

from fastapi.testclient import TestClient
from langgraph.graph import END, START, StateGraph

from backend.app.main import app, get_graph
from evals.evaluators import agent as grader
from evals.runners import agent as runner
from evals.schemas import AgentScenario
from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.state import ChatResult, chat_result, new_checkpointer
from kosis_agent.services.statistics import NOT_IN_CATALOG_TEXT
from kosis_ai.schemas import Refusal, StatAnswer
from kosis_chat.schemas.io import StatOutput
from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig
from kosis_rag.schemas.io import SearchOutput, TableCandidate

STAT_LLM = StatLLMConfig(model="mock", provider="mock")
TABLE = TableCandidate(key="101_DT_1B8000H", org_id="101", tbl_id="DT_1B8000H", table_name="출생아수",
                       survey_name="인구동향조사", organization="국가데이터처", period="년 (2015 ~ 2025)", url="https://kosis.kr")


class Counter(TypedDict):
    n: int


def _inner_graph():
    """A tiny compiled graph, standing in for the phase 1 / phase 2 graphs the tools run inside them."""
    g = StateGraph(Counter)
    g.add_node("inc", lambda s: {"n": s["n"] + 1})
    g.add_edge(START, "inc")
    g.add_edge("inc", END)
    return g.compile()


class FakeSearch:
    """Stand-in for kosis_rag.api.SearchModule: node events, then ("output", SearchOutput). Runs a compiled graph
    inside, as the real module does, so the checkpointer isolation (kosis_agent/runtime.py) is exercised."""

    def __init__(self, route: str):
        self.route = route
        self.questions: list[str] = []
        self.inner = _inner_graph()

    def stream(self, inp):
        self.questions.append(inp.question)
        self.inner.invoke({"n": 0})
        yield "parse", {"seconds": 0.01}
        yield "gate", {"seconds": 0.02, "route": self.route}
        if self.route == "refuse":
            yield "output", SearchOutput(question=inp.question, route="refuse", refusal_reason="out_of_scope",
                                         refusal_text="제공된 통계표 범위 밖의 질문이에요.", candidates=[TABLE])
        else:
            yield "output", SearchOutput(question=inp.question, route="found", picked=[TABLE], candidates=[TABLE])


class FakeSummarize:
    """Stand-in for kosis_chat.api.summarize_stat_run: records its arguments, answers refused (no chart)."""

    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, question, table_id, *, llm=None, retry_interval=0.0):
        self.calls.append((question, table_id, llm, retry_interval))
        answer = StatAnswer(outcome="refused", text="이 표에는 2030년 자료가 없어요.",
                            refusal=Refusal(reason="out_of_scope", message="이 표에는 2030년 자료가 없어요."))
        return StatOutput(answer=answer, chart=None, spans=[{"name": "judge", "status": "ok"}])


def _sse(graph, question: str):
    app.dependency_overrides[get_graph] = lambda: graph
    try:
        res = TestClient(app).post("/chat/stream", json={"question": question})
    finally:
        app.dependency_overrides.clear()
    events = [line.removeprefix("event: ") for line in res.text.splitlines() if line.startswith("event: ")]
    data = [json.loads(line.removeprefix("data: ")) for line in res.text.splitlines() if line.startswith("data: ")]
    return res, events, data


def test_refused_search_ends_with_its_refusal_text_and_skips_phase1():
    summarize = FakeSummarize()
    out = build_graph(FakeSearch("refuse"), STAT_LLM, summarize=summarize).invoke({"messages": [("user", "비트코인 가격")]})
    assert out["messages"][-1].content == "제공된 통계표 범위 밖의 질문이에요."
    assert summarize.calls == []
    result = chat_result(out)
    assert result.no_answer and result.answer_origin == "refusal" and result.no_answer_reason == "out_of_scope"
    assert result.stat_answer is None and result.table_key is None


def test_found_search_hands_the_tables_csv_key_to_phase1():
    summarize = FakeSummarize()
    search = FakeSearch("found")
    out = build_graph(search, STAT_LLM, retry_interval=2.0, summarize=summarize).invoke(
        {"messages": [("user", "2030년 출생아 수")]})
    assert search.questions == ["2030년 출생아 수"]
    assert summarize.calls == [("2030년 출생아 수", "101_DT_1B8000H", STAT_LLM, 2.0)]  # key, not tbl_id
    assert out["messages"][-1].content == "이 표에는 2030년 자료가 없어요."
    result = chat_result(out)
    assert result.answer_origin == "stat" and result.table_key == "101_DT_1B8000H"
    assert result.no_answer and result.no_answer_reason == "out_of_scope" and result.chart is None


def test_graph_streams_search_nodes_then_stat():
    chunks = list(build_graph(FakeSearch("found"), STAT_LLM, summarize=FakeSummarize()).stream(
        {"messages": [("user", "q")]}, stream_mode="custom"))
    assert [name for name, _ in chunks] == ["parse", "gate", "stat"]
    assert chunks[-1][1]["key"] == "101_DT_1B8000H" and chunks[-1][1]["spans"] == [{"name": "judge", "status": "ok"}]
    assert "seconds" in chunks[-1][1]


def test_chat_stream_sends_node_events_then_the_result():
    res, events, data = _sse(build_graph(FakeSearch("found"), STAT_LLM, summarize=FakeSummarize()), "2030년 출생아 수")
    assert res.status_code == 200
    assert events == ["parse", "gate", "stat", "result"]
    result = ChatResult.model_validate(data[-1])
    assert result.answer == "이 표에는 2030년 자료가 없어요." and result.picked[0].key == "101_DT_1B8000H"


def test_a_key_missing_from_tables_csv_is_a_refusal_not_an_error():
    """T3-23 behaviour change: phase 1's LookupError used to end the stream with `error`; now it is a refused answer."""
    def missing(question, table_id, **kw):
        raise LookupError(f"{table_id} is not in tables.csv")

    _, events, data = _sse(build_graph(FakeSearch("found"), STAT_LLM, summarize=missing), "q")
    assert events == ["parse", "gate", "stat", "result"]
    result = ChatResult.model_validate(data[-1])
    assert result.no_answer and result.no_answer_reason == "out_of_scope" and result.answer == NOT_IN_CATALOG_TEXT


def test_chat_stream_reports_a_phase1_model_failure_as_an_error_event():
    def failing(question, table_id, **kw):
        raise RuntimeError("HCX-DASH-002 call failed (429)")

    res, events, _ = _sse(build_graph(FakeSearch("found"), STAT_LLM, summarize=failing), "q")
    assert events[-1] == "error" and "RuntimeError" in res.text


def test_tool_spans_carry_what_the_agent_harness_grades():
    """The agent eval layer reads each turn from its tool spans: names, args (table_key) and results (route,
    StatAnswer). Graded with the harness's own functions, so a contract drift fails here."""
    graph = build_graph(FakeSearch("found"), STAT_LLM, summarize=FakeSummarize(), checkpointer=new_checkpointer())
    s = AgentScenario.model_validate({"sid": "s1", "turns": [{"question": "2030년 출생아 수", "expect": {}}]})
    (record,) = runner.run(graph, [s])[("s1", 0)]
    calls = record["calls"]
    assert [c["name"] for c in calls] == ["search_tables", "summarize_stat"]
    assert calls[0]["args"] == {"question": "2030년 출생아 수"} and calls[0]["result"]["route"] == "found"
    # question + table_key + the conditions phase 2's parse_query reads (the harness's ARG_FIELDS names)
    assert calls[1]["args"] == {"question": "2030년 출생아 수", "table_key": "101_DT_1B8000H", "period_start": "2030",
                                "period_end": "2030", "regions": [], "dimensions": []}
    assert grader.state_key(calls) == "101_DT_1B8000H"
    assert grader.stat_answer(calls).refusal.reason == "out_of_scope"  # the result validates as a StatAnswer
    assert grader.refused(calls) and record["error"] is None


def test_a_search_refusal_is_graded_as_a_refusal():
    graph = build_graph(FakeSearch("refuse"), STAT_LLM, summarize=FakeSummarize(), checkpointer=new_checkpointer())
    s = AgentScenario.model_validate({"sid": "s1", "turns": [{"question": "비트코인 가격", "expect": {}}]})
    (record,) = runner.run(graph, [s])[("s1", 0)]
    assert [c["name"] for c in record["calls"]] == ["search_tables"] and grader.refused(record["calls"])


def test_the_checkpointer_keeps_the_conversation_but_not_the_inner_graphs(caplog):
    """P7 (plans/agent-graph-refactor.md): a graph run inside a node inherits the parent's checkpointer, so phase 1 /
    phase 2 state would pile up in the conversation's checkpoints. kosis_agent.runtime.without_checkpointer stops it.
    The state's models are read back without the serializer's "unregistered type" warning (CHECKPOINT_TYPES)."""
    saver = new_checkpointer()
    graph = build_graph(FakeSearch("found"), STAT_LLM, summarize=FakeSummarize(), checkpointer=saver)
    config = {"configurable": {"thread_id": "t1"}}
    graph.invoke({"messages": [("user", "2030년 출생아 수")]}, config)
    second = graph.invoke({"messages": [("user", "2031년은?")]}, config)
    assert [m.content for m in second["messages"]] == ["2030년 출생아 수", "이 표에는 2030년 자료가 없어요.",
                                                       "2031년은?", "이 표에는 2030년 자료가 없어요."]
    namespaces = {c.config["configurable"].get("checkpoint_ns", "") for c in saver.list(None)}
    assert namespaces == {""}  # only the main graph's own checkpoints
    assert type(graph.get_state(config).values["stat"]) is StatOutput
    assert not [r for r in caplog.records if "unregistered type" in r.getMessage()]


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}
