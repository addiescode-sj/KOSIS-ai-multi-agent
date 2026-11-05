"""T3-00: phase 1 / phase 2 import from the submodules; question -> supervisor -> phase 2 search graph -> SSE runs offline."""

import json

from fastapi.testclient import TestClient
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from backend.app.main import app, get_graph
from kosis_agent.graph import build_graph
from kosis_rag.graph import SearchState
from kosis_rag.schemas import SearchResult


def fake_search_graph():
    """Stand-in for kosis_rag.graph.build_graph: parse -> refuse over the real SearchState, one streamed piece."""

    def parse(state: SearchState) -> dict:
        return {"trace": {**state.trace, "parse": {"seconds": 0.01}}}

    def refuse(state: SearchState) -> dict:
        get_stream_writer()({"delta": "맞는 표가 "})
        result = SearchResult(question=state.question, no_answer=True, answer="맞는 표가 없습니다.", answer_origin="refusal")
        return {"result": result, "trace": {**state.trace, "refuse": {"seconds": 0.02}}}

    g = StateGraph(SearchState)
    g.add_node("parse", parse)
    g.add_node("refuse", refuse)
    g.add_edge(START, "parse")
    g.add_edge("parse", "refuse")
    g.add_edge("refuse", END)
    return g.compile()


def test_phase1_contract_imports():
    from kosis_ai.schemas import StatAnswer  # phase 1 shared contract

    assert StatAnswer


def test_graph_routes_to_search_and_keeps_its_result():
    out = build_graph(fake_search_graph()).invoke({"messages": [("user", "비트코인 가격")]})
    assert out["messages"][-1].content == "맞는 표가 없습니다."
    assert out["search"].no_answer and out["search"].answer_origin == "refusal"


def test_graph_streams_the_phase2_node_events():
    chunks = list(build_graph(fake_search_graph()).stream({"messages": [("user", "q")]}, stream_mode="custom"))
    assert chunks == [("parse", {"seconds": 0.01}), ("token", {"delta": "맞는 표가 "}), ("refuse", {"seconds": 0.02})]


def test_chat_stream_sends_node_events_then_the_search_result():
    app.dependency_overrides[get_graph] = lambda: build_graph(fake_search_graph())
    try:
        res = TestClient(app).post("/chat/stream", json={"question": "비트코인 가격"})
    finally:
        app.dependency_overrides.clear()
    lines = res.text.splitlines()
    events = [line.removeprefix("event: ") for line in lines if line.startswith("event: ")]
    data = [json.loads(line.removeprefix("data: ")) for line in lines if line.startswith("data: ")]
    assert res.status_code == 200
    assert events == ["parse", "token", "refuse", "result"]
    assert data[-1]["no_answer"] is True and data[-1]["answer"] == "맞는 표가 없습니다."


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}
