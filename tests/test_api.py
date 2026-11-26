"""T3-30 (KOS-114 / KOS-182): service traces per request, kept apart from eval tracing, and the graph / tool endpoints.
Offline: the fake search module and phase 1 call of test_agent_graph."""

import json

import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from backend.app import telemetry
from backend.app.main import app, chat_stream, ChatRequest, get_graph, get_table_keys, get_tools, tokens
from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.state import new_checkpointer
from kosis_agent.retrieval.search import make_search_tables_tool
from kosis_agent.services.chart import make_chart_tool
from kosis_agent.services.statistics import make_summarize_stat_tool
from kosis_ai.schemas import Refusal, StatAnswer
from kosis_chat.schemas.io import StatOutput
from tests.test_agent_graph import STAT_LLM, FakeSearch

ANSWER = "이 표에는 2030년 자료가 없어요."
STEPS = [{"name": "analyze.llm_plan", "start_ms": 0.0, "duration_ms": 30.0, "status": "ok",
          "attributes": {"gen_ai.request.model": "HCX-DASH-002", "gen_ai.usage.input_tokens": 120}},
         {"name": "compose", "start_ms": 30.0, "duration_ms": 20.0, "status": "ok", "attributes": {}}]


def summarize(question, table_id, **kw):
    return StatOutput(answer=StatAnswer(outcome="refused", text=ANSWER,
                                         refusal=Refusal(reason="out_of_scope", message=ANSWER)), chart=None, spans=STEPS)


@pytest.fixture
def client():
    """The app with the fake graph / tools and an in-memory service provider."""
    provider, memory = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    graph = build_graph(FakeSearch("found"), STAT_LLM, summarize=summarize, checkpointer=new_checkpointer())
    tools = {t.name: t for t in (make_search_tables_tool(FakeSearch("found")),
                                 make_summarize_stat_tool(STAT_LLM, summarize=summarize), make_chart_tool())}
    app.dependency_overrides.update({get_graph: lambda: graph, get_tools: lambda: tools,
                                     get_table_keys: lambda: frozenset({"101_DT_1B8000H"}),
                                     telemetry.get_tracer: lambda: provider.get_tracer("test")})
    try:
        yield TestClient(app), memory
    finally:
        app.dependency_overrides.clear()


def _events(text: str) -> list[tuple[str, dict]]:
    names = [l.removeprefix("event: ") for l in text.splitlines() if l.startswith("event: ")]
    data = [json.loads(l.removeprefix("data: ")) for l in text.splitlines() if l.startswith("data: ")]
    return list(zip(names, data))


def test_tokens_rebuild_the_answer_exactly():
    for text in [ANSWER, "  앞 공백과\n줄바꿈  ", "", "한단어"]:
        assert "".join(tokens(text)) == text
    assert tokens("a b") == ["a ", "b"]


def test_chat_stream_is_one_trace_with_node_tool_and_phase1_step_spans(client):
    http, memory = client
    events = _events(http.post("/chat/stream", json={"question": "2030년 출생아 수", "thread_id": "t1"}).text)
    assert events[0][0] == "meta" and events[-1][0] == "result"
    assert "".join(d["delta"] for e, d in events if e == "token") == events[-1][1]["answer"] == ANSWER

    spans = memory.get_finished_spans()
    assert len({s.context.trace_id for s in spans}) == 1  # one request, one trace
    root = next(s for s in spans if s.name == "invoke_agent")
    assert format(root.context.trace_id, "032x") == events[0][1]["trace_id"]
    assert root.attributes["gen_ai.conversation.id"] == "t1" and root.attributes["kosis.turn.index"] == 0
    # T3-43: what online evaluation reads back (question, final answer = the streamed result)
    assert root.attributes["kosis.turn.question"] == "2030년 출생아 수" and root.attributes["kosis.turn.answer"] == ANSWER
    by_name = {s.name: s for s in spans}
    assert {"context", "search", "statistics", "execute_tool search_tables", "execute_tool summarize_stat"} <= set(by_name)
    # phase 1 steps sit under the summarize_stat tool span, at phase 1's own offsets
    tool = by_name["execute_tool summarize_stat"]
    plan, compose = by_name["analyze.llm_plan"], by_name["compose"]
    assert plan.parent.span_id == tool.context.span_id == compose.parent.span_id
    assert plan.attributes["gen_ai.request.model"] == "HCX-DASH-002"
    assert (compose.start_time - tool.start_time, compose.end_time - compose.start_time) == (30_000_000, 20_000_000)


def test_the_turn_index_counts_questions_in_the_thread(client):
    http, memory = client
    for q in ["2030년 출생아 수", "2031년은?"]:
        http.post("/chat", json={"question": q, "thread_id": "t2"})
    roots = [s for s in memory.get_finished_spans() if s.name == "invoke_agent"]
    assert [r.attributes["kosis.turn.index"] for r in roots] == [0, 1]
    assert [r.attributes["kosis.turn.question"] for r in roots] == ["2030년 출생아 수", "2031년은?"]  # /chat records them too
    assert all(r.attributes.get("kosis.turn.answer") for r in roots)


def test_chat_answers_like_the_stream(client):
    http, _ = client
    body = http.post("/chat", json={"question": "2030년 출생아 수"}).json()
    streamed = _events(http.post("/chat/stream", json={"question": "2030년 출생아 수"}).text)[-1][1]
    # T3-51: each answer carries its own request_id (= its trace id); everything else is the same
    assert body["result"]["request_id"] == body["trace_id"] and streamed["request_id"] != body["trace_id"]
    assert {**body["result"], "request_id": None} == {**streamed, "request_id": None}
    assert len(body["trace_id"]) == 32 and body["thread_id"]


def test_the_root_span_ends_when_the_client_goes_away():
    provider, memory = TracerProvider(), InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    graph = build_graph(FakeSearch("found"), STAT_LLM, summarize=summarize)
    stream = chat_stream(ChatRequest(question="q"), graph, provider.get_tracer("test"))
    assert next(stream).event == "meta"
    stream.close()  # what Starlette does on disconnect (GeneratorExit)
    assert [s.name for s in memory.get_finished_spans()] == ["invoke_agent"]


def test_tool_endpoints_call_the_tools_and_leave_a_trace_each(client):
    http, memory = client
    found = http.post("/tools/search_tables", json={"question": "출생아 수"}).json()
    assert found["route"] == "found" and found["picked"][0]["key"] == "101_DT_1B8000H"
    stat = http.post("/tools/summarize_stat", json={"question": "q", "table_key": "101_DT_1B8000H"}).json()
    assert stat["answer"]["text"] == ANSWER and "vega_lite" not in stat
    chart = http.post("/tools/make_chart", json={"table_key": "101_DT_1B8000H"}).json()
    assert chart["chart"] is None and chart["note"]
    roots = [s for s in memory.get_finished_spans() if s.parent is None]
    assert [s.name for s in roots] == ["POST /dev/tools/search_tables", "POST /dev/tools/summarize_stat",
                                       "POST /dev/tools/make_chart"]  # T3-51: developer paths under /dev/
    assert len({s.context.trace_id for s in roots}) == 3


def test_an_unregistered_table_key_is_422_not_a_refusal(client):
    """api.md 0 (T3-55): a key outside the registry is an input error; no tool runs, no trace is left."""
    http, memory = client
    for path, body in [("/dev/tools/summarize_stat", {"question": "q", "table_key": "999_NOPE"}),
                       ("/tools/summarize_stat", {"question": "q", "table_key": "999_NOPE"}),
                       ("/dev/tools/make_chart", {"table_key": "999_NOPE"})]:
        res = http.post(path, json=body)
        assert res.status_code == 422 and res.json()["error"]["code"] == "unknown_table"
    assert memory.get_finished_spans() == ()
    assert http.post("/dev/tools/summarize_stat", json={"question": "q", "table_key": "101_DT_1B8000H"}).status_code == 200


def test_every_non_sse_error_has_the_contract_shape(client):
    """api.md 0 (T3-55): {"error": {"code", "message"}} with the status kept: 404 of a thread or an unknown path,
    405, the 503 of an agent that cannot load. Input validation keeps FastAPI's 422 {"detail": [...]}."""
    http, _ = client
    assert http.get("/dev/threads/nope").json()["error"]["code"] == "not_found"
    unknown = http.get("/no/such/path")
    assert unknown.status_code == 404 and unknown.json() == {"error": {"code": "not_found", "message": "Not Found"}}
    wrong = http.put("/health")
    assert wrong.status_code == 405 and wrong.json()["error"]["code"] == "method_not_allowed"
    assert "detail" in http.post("/dev/chat", json={}).json()  # validation: FastAPI's body, still 422

    def broken():
        from backend.app.main import ApiError
        raise ApiError(503, "unavailable", "x")
    app.dependency_overrides[get_graph] = broken
    down = http.post("/dev/chat", json={"question": "q"})
    assert down.status_code == 503 and down.json()["error"]["code"] == "unavailable"


def test_the_agent_load_failure_is_a_503_in_the_contract_shape(monkeypatch):
    from backend.app import main

    def fail(checkpointer=None):
        raise RuntimeError("no vector store")
    monkeypatch.setattr(main, "_build", fail)
    monkeypatch.setattr(main, "_agent", None)
    monkeypatch.setattr(main, "get_checkpointer", lambda: None)
    res = TestClient(app).post("/dev/tools/search_tables", json={"question": "q"})
    assert res.status_code == 503 and res.json()["error"]["code"] == "unavailable"
    assert "RuntimeError" in res.json()["error"]["message"]


def test_a_dev_chat_failure_is_a_500_in_the_contract_shape(client):
    http, _ = client

    class Broken:
        def get_state(self, config):
            raise ValueError("no checkpointer")

        def invoke(self, *a, **kw):
            raise RuntimeError("boom")
    app.dependency_overrides[get_graph] = lambda: Broken()
    res = http.post("/dev/chat", json={"question": "q"})
    assert res.status_code == 500 and res.json()["error"]["code"] == "internal_error"
    assert "RuntimeError: boom" in res.json()["error"]["message"]


def test_thread_state_after_a_turn_and_404_before(client):
    http, _ = client
    assert http.get("/threads/nope").status_code == 404
    http.post("/chat", json={"question": "2030년 출생아 수", "thread_id": "t3"})
    state = http.get("/threads/t3").json()
    assert state["turns"] == 1 and state["active_table"] == "101_DT_1B8000H"
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"]


def test_service_tracing_never_touches_the_global_provider():
    """Eval runners make their own provider; the service lifespan must not install one globally."""
    before = trace.get_tracer_provider()
    with TestClient(app) as http:
        assert http.get("/health").status_code == 200
        assert telemetry._provider is not None
        attributes = telemetry._provider.resource.attributes  # T3-32: one service name, the environment tells them apart
        assert (attributes["service.name"], attributes["deployment.environment.name"]) == ("kosis-agent", "service")
    assert trace.get_tracer_provider() is before and telemetry._provider is None


def test_the_openapi_docs_list_every_endpoint():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    dev = {"/dev/chat", "/dev/tools/search_tables", "/dev/tools/summarize_stat", "/dev/tools/make_chart",
           "/dev/threads/{thread_id}"}
    legacy = {"/chat", "/tools/search_tables", "/tools/summarize_stat", "/tools/make_chart", "/threads/{thread_id}"}
    assert set(paths) == {"/health", "/chat/stream", "/events", "/ops/log-counts",  # T3-48 log intake
                          "/feedback", "/chat/{thread_id}",  # T3-51 contract (docs/service/api.md)
                          "/auth/kakao/login", "/auth/kakao/callback", "/auth/logout", "/auth/me"} | dev | legacy  # T3-54
    assert all(op.get("deprecated") for p in legacy for m, op in paths[p].items() if (p, m) != ("/chat", "get"))
    assert "text/event-stream" in json.dumps(paths["/chat/stream"])
