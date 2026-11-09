"""Phase 3 connection: question -> phase 2 SearchModule -> (refuse: its refusal_text | found: phase 1 summarize_stat_run
with picked[0].key) -> SSE. Offline: a fake search module and a fake phase 1 call."""

import json

from fastapi.testclient import TestClient

from backend.app.main import app, get_graph
from kosis_agent.graph import ChatResult, build_graph, chat_result
from kosis_ai.schemas import Refusal, StatAnswer
from kosis_chat.schemas.io import StatOutput
from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig
from kosis_rag.schemas.io import SearchOutput, TableCandidate

STAT_LLM = StatLLMConfig(model="mock", provider="mock")
TABLE = TableCandidate(key="101_DT_1B8000H", org_id="101", tbl_id="DT_1B8000H", table_name="출생아수",
                       survey_name="인구동향조사", organization="국가데이터처", period="년 (2015 ~ 2025)", url="https://kosis.kr")


class FakeSearch:
    """Stand-in for kosis_rag.api.SearchModule: node events, then ("output", SearchOutput)."""

    def __init__(self, route: str):
        self.route = route
        self.questions: list[str] = []

    def stream(self, inp):
        self.questions.append(inp.question)
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
    app.dependency_overrides[get_graph] = lambda: build_graph(FakeSearch("found"), STAT_LLM, summarize=FakeSummarize())
    try:
        res = TestClient(app).post("/chat/stream", json={"question": "2030년 출생아 수"})
    finally:
        app.dependency_overrides.clear()
    lines = res.text.splitlines()
    events = [line.removeprefix("event: ") for line in lines if line.startswith("event: ")]
    data = [json.loads(line.removeprefix("data: ")) for line in lines if line.startswith("data: ")]
    assert res.status_code == 200
    assert events == ["parse", "gate", "stat", "result"]
    result = ChatResult.model_validate(data[-1])
    assert result.answer == "이 표에는 2030년 자료가 없어요." and result.picked[0].key == "101_DT_1B8000H"


def test_chat_stream_reports_a_phase1_failure_as_an_error_event():
    def failing(question, table_id, **kw):
        raise LookupError(f"{table_id} is not in tables.csv")

    app.dependency_overrides[get_graph] = lambda: build_graph(FakeSearch("found"), STAT_LLM, summarize=failing)
    try:
        res = TestClient(app).post("/chat/stream", json={"question": "q"})
    finally:
        app.dependency_overrides.clear()
    events = [line.removeprefix("event: ") for line in res.text.splitlines() if line.startswith("event: ")]
    assert events[-1] == "error" and "LookupError" in res.text


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}
