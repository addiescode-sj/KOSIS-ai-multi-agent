"""S3: ② 후보 찾기(parse → … → rerank1)와 ③ 고르기(rerank2 → gate, search_tables 툴)를 나눠 도는지. 오프라인 가짜 노드."""

import pytest

from kosis_multi import search_steps
from kosis_multi.orchestrator import build_graph
from kosis_multi.search_steps import SearchSteps
from tests.test_workflow import STAT_LLM, Stat, answered


class FakeResources:
    sigungu = None
    stage2_llm = None


class SplitSearch:
    """SearchModule 자리: resources와 k만 있다. 노드는 graph_nodes를 바꿔 끼운 가짜."""

    def __init__(self):
        self.resources = FakeResources()

    def k(self, requested):
        return requested or 5


def fake_nodes(log: list, route: str = "refuse"):
    def node(name):
        def run(state):
            log.append((name, state.question))
            update = {"trace": {name: {"seconds": 0.0}}}
            if name == "gate":
                update.update(route=route, refusal_reason="no_table" if route == "refuse" else None)
            return update
        return run

    names = ["parse", "condition", "embed", "retrieve_dense", "retrieve_bm25", "fuse", "rerank1", "rerank2", "gate"]
    return {name: node(name) for name in names}


@pytest.fixture
def log(monkeypatch):
    calls: list = []
    monkeypatch.setattr(search_steps, "graph_nodes", lambda resources, k: fake_nodes(calls))
    return calls


def names(log):
    return [name for name, _ in log]


def test_retrieve_runs_up_to_rerank1_and_the_tool_runs_rerank2_and_gate(log):
    steps = SearchSteps(SplitSearch())
    steps.retrieve_candidates("서울 인구")
    assert set(names(log)) == {"parse", "condition", "embed", "retrieve_dense", "retrieve_bm25", "fuse", "rerank1"}
    assert names(log)[0] == "parse" and names(log)[-1] == "rerank1"
    log.clear()
    out = steps.rerank_and_gate("서울 인구")
    assert names(log) == ["rerank2", "gate"] and out.route == "refuse"
    assert [s["node"] for s in out.spans][-2:] == ["rerank2", "gate"] and out.spans[0]["node"] == "parse"


def test_the_tool_without_candidates_runs_the_whole_search(log):
    out = SearchSteps(SplitSearch()).rerank_and_gate("서울 인구")
    assert names(log)[0] == "parse" and names(log)[-2:] == ["rerank2", "gate"] and out.question == "서울 인구"


def test_candidates_are_kept_per_conversation(log):
    steps = SearchSteps(SplitSearch())
    steps.retrieve_candidates("서울 인구", {"configurable": {"thread_id": "a"}})
    assert steps.take("서울 인구", {"configurable": {"thread_id": "b"}}) is None
    assert steps.take("서울 인구", {"configurable": {"thread_id": "a"}}) is not None


def test_the_graph_searches_in_retriever_and_closes_search_tables_in_feasibility(log):
    graph = build_graph(SplitSearch(), STAT_LLM, summarize=Stat({"101_A": answered("101_A")}))
    out = graph.invoke({"messages": [("user", "2024년 서울 출생아 수")]}, config={"configurable": {"thread_id": "t"}})
    assert names(log).count("parse") == 1 and names(log)[-2:] == ["rerank2", "gate"]  # 한 번만 찾고 한 번만 닫음
    assert out["outcome"] == "refused" and out["search"].route == "refuse" and out["search_questions"] is None
    assert set(out["timings"]) >= {"judge", "retriever", "feasibility"}


# ---------------------------------------------------------------- S4 미리 검색 (①∥②)

def test_a_new_question_uses_the_prefetched_candidates(log):
    graph = build_graph(SplitSearch(), STAT_LLM, summarize=Stat({"101_A": answered("101_A")}), prefetch=True)
    out = graph.invoke({"messages": [("user", "2024년 서울 출생아 수")]}, config={"configurable": {"thread_id": "t"}})
    assert names(log).count("parse") == 1  # ①과 동시에 한 번 찾고, ②는 그 결과를 씀
    assert names(log)[-2:] == ["rerank2", "gate"] and out["outcome"] == "refused"


def test_small_talk_does_not_prefetch(log):
    graph = build_graph(SplitSearch(), STAT_LLM, summarize=Stat({}), prefetch=True)
    out = graph.invoke({"messages": [("user", "안녕하세요")]}, config={"configurable": {"thread_id": "t"}})
    assert log == [] and out["route"] == "direct"


def test_an_unused_prefetch_is_settled_by_the_end_of_the_turn(log):
    steps = SearchSteps(SplitSearch())
    config = {"configurable": {"thread_id": "a"}}
    steps.prefetch("심심해", config)
    steps.settle(config)
    assert names(log)[-1] == "rerank1" and steps._prefetched == {}
    steps.retrieve_candidates("심심해", config)  # 버린 뒤에는 다시 찾는다
    assert names(log).count("parse") == 2


def test_a_failed_prefetch_is_searched_again(monkeypatch):
    calls: list = []
    nodes = fake_nodes(calls)
    first = nodes["parse"]
    state = {"n": 0}

    def flaky(s):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("vector store down")
        return first(s)

    nodes["parse"] = flaky
    monkeypatch.setattr(search_steps, "graph_nodes", lambda resources, k: nodes)
    steps = SearchSteps(SplitSearch())
    steps.prefetch("서울 인구")
    steps.retrieve_candidates("서울 인구")
    assert names(calls)[-1] == "rerank1" and steps.take("서울 인구") is not None
