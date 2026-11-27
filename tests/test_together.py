"""T3-61: 2차 리랭커의 멀티표 판단(실전 2 T2-47 stage2_together) → 실전 1에 표 2~3개.

오프라인 테스트다. 실전 2는 rerank2 span에 판단을 담아 돌려주는 가짜 검색, 실전 1은 표 키(들)별 가짜로 바꾼다.
확인하는 것:
- together_keys: found이고 2~3개·서로 다름·모두 picked 안일 때만 받는다
- 그래프: together가 있으면 summarize_stat(table_keys)로 한 번, 멀티 답이 답함이 아니면 첫 표 하나로 다시 묻는다,
  together가 없으면(v4) 예전과 같은 인자로 한 번
"""

from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.state import chat_result
from kosis_agent.retrieval.search import TOGETHER_FIELD, together_keys
from kosis_agent.services.statistics import make_summarize_stat_tool
from kosis_rag.schemas.io import SearchOutput
from tests.test_workflow import STAT_LLM, Search, Stat, answered, refused, table


def found(picked, together=None, route="found"):
    span = {"node": "rerank2", "stage2_picked_n": len(picked)}
    if together is not None:
        span[TOGETHER_FIELD] = together
    return SearchOutput(question="q", route=route, picked=[table(k) for k in picked],
                        candidates=[table(k) for k in picked], spans=[span])


def test_together_keys_checks_the_search_output_again():
    assert together_keys(found(["101_A", "101_B"], ["101_A", "101_B"])) == ["101_A", "101_B"]
    assert together_keys(found(["101_A", "101_B"])) == []  # v4: no verdict
    assert together_keys(found(["101_A"], ["101_A", "101_X"])) == []  # outside picked
    assert together_keys(found(["101_A", "101_B"], ["101_A"])) == []  # one table is not a set
    assert together_keys(found(["101_A", "101_B"], ["101_A", "101_B"], route="refuse")) == []
    assert together_keys(None) == []


class Many:
    """실전 1 summarize_stats_run 대신: 표 키 묶음별 결과."""

    def __init__(self, by_keys: dict):
        self.by_keys, self.calls = by_keys, []

    def __call__(self, question, table_ids, *, llm=None, retry_interval=0.0):
        self.calls.append((question, tuple(table_ids)))
        return self.by_keys[tuple(table_ids)]


def together_search(picked, together):
    return Search(picked, spans=[{"node": "rerank2", TOGETHER_FIELD: together}])


def run(search, stat, many, question="2023년 서울 혼인 100건당 이혼 건수"):
    graph = build_graph(search, STAT_LLM, summarize=stat, summarize_many=many)
    return graph.invoke({"messages": [("user", question)]})


def test_a_together_verdict_asks_phase_1_once_with_every_table():
    stat, many = Stat({}), Many({("101_A", "101_B"): answered("101_A")})
    r = chat_result(run(together_search(["101_A", "101_B", "101_C"], ["101_A", "101_B"]), stat, many))
    assert many.calls == [("2023년 서울 혼인 100건당 이혼 건수", ("101_A", "101_B"))] and stat.calls == []
    assert r.outcome == "answered" and r.table_key == "101_A" and r.attempted_keys == ["101_A+101_B"]


def test_a_refused_multi_answer_asks_the_first_table_alone():
    stat = Stat({"101_A": answered("101_A")})
    many = Many({("101_A", "101_B"): refused("101_A", "no_item")})
    r = chat_result(run(together_search(["101_A", "101_B"], ["101_A", "101_B"]), stat, many))
    assert len(many.calls) == 1 and [k for _, k in stat.calls] == ["101_A"]
    assert r.outcome == "answered" and r.table_key == "101_A" and r.attempted_keys == ["101_A+101_B", "101_A"]


def test_without_a_verdict_the_one_table_path_is_unchanged():
    stat, many = Stat({"101_A": answered("101_A")}), Many({})
    r = chat_result(run(Search(["101_A", "101_B"], spans=[{"node": "rerank2"}]), stat, many, "2024년 서울 출생아 수"))
    assert many.calls == [] and [k for _, k in stat.calls] == ["101_A"] and r.attempted_keys == ["101_A"]


def test_the_one_table_tool_call_has_no_table_keys_argument():
    """한 표 회귀 0: 하네스가 채점하는 summarize_stat 인자가 예전과 같다."""
    seen = []
    tool = make_summarize_stat_tool(STAT_LLM, summarize=lambda q, k, **kw: seen.append(k) or answered(k),
                                    summarize_many=lambda q, ks, **kw: seen.append(tuple(ks)) or answered(ks[0]))
    tool.invoke({"question": "q", "table_key": "101_A"})
    tool.invoke({"question": "q", "table_key": "101_A", "table_keys": ["101_A", "101_B"]})
    tool.invoke({"question": "q", "table_key": "101_A", "table_keys": ["101_A"]})  # 하나면 한 표 경로
    assert seen == ["101_A", ("101_A", "101_B"), "101_A"]
