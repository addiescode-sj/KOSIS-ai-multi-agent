"""T3-60 후속 질문 규칙 (11/25 서버 점검): 지표만 바꾼 이월, 시도를 더한 비교, 직전 표에 없는 시도.

규칙 함수(graph/followup.py)와, understand가 그 규칙을 read_turn 앞에서 써서 검색·직전 표 재사용을 정하는지 본다.
"""

from kosis_agent.graph.context import LLMReading
from kosis_agent.graph.followup import carry_conditions, compare_regions, follow_up, region_gap
from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.state import new_checkpointer
from kosis_rag.parse.node import parse_query as parse
from tests.test_workflow import STAT_LLM, Search, Stat, answered

BIRTH = "2024년 출생아 수 알려줘"
SEOUL_BIRTH = "2024년 서울 출생아 수 알려줘"
# "서울은?"을 직전 표 이월로 읽는 해석기 (규칙이 미루는 턴을 LLM이 읽은 결과)
SEOUL_CARRY = lambda previous, question: LLMReading(standalone_question=SEOUL_BIRTH, use_previous_table=True)  # noqa: E731


def test_measure_swap_keeps_year():
    assert carry_conditions("이혼은?", BIRTH, parse) == "2024년 이혼은?"


def test_measure_swap_keeps_year_and_region():
    assert carry_conditions("이혼 건수는?", "2023년 부산 혼인 건수는?", parse) == "2023년 부산광역시 이혼 건수는?"


def test_measure_swap_not_when_own_period_or_same_measure_or_nothing_to_carry():
    assert carry_conditions("이혼 추이는?", BIRTH, parse) is None
    assert carry_conditions("출생아는?", BIRTH, parse) is None
    assert carry_conditions("이혼은?", "출생아 수 알려줘", parse) is None
    assert carry_conditions("부산은?", BIRTH, parse) is None  # 지표 없음: 기존 이월 규칙


def test_compare_adds_region():
    assert compare_regions("부산이랑 비교해줘", SEOUL_BIRTH, parse) == "2024년 서울과 부산광역시 출생아 수 비교해줘"
    assert compare_regions("부산하고 비교하면?", "2024년 경기 출생아 수는?", parse) == "2024년 경기와 부산광역시 출생아 수 비교해줘"


def test_compare_not_for_plain_swap():
    assert compare_regions("부산은?", SEOUL_BIRTH, parse) is None
    assert compare_regions("부산이랑 비교해줘", BIRTH, parse) is None  # 직전 시도 없음


def test_follow_up_readings():
    compare = follow_up("부산이랑 비교해줘", SEOUL_BIRTH, parse)
    assert compare.use_previous_table and compare.by == "rule"
    swap = follow_up("이혼은?", BIRTH, parse)
    assert not swap.use_previous_table and swap.resolved_question == "2024년 이혼은?"
    assert follow_up("부산은?", SEOUL_BIRTH, parse) is None


def test_region_gap():
    national = lambda key: None  # noqa: E731 - 시도 칸 없는 전국 표
    sido = lambda key: {"서울특별시", "부산광역시"}  # noqa: E731
    assert region_gap(SEOUL_BIRTH, "T", parse, national)
    assert not region_gap(SEOUL_BIRTH, "T", parse, sido)
    assert region_gap("2024년 서울과 대구 출생아 수 비교해줘", "T", parse, sido)
    assert not region_gap(BIRTH, "T", parse, national)  # 시도를 묻지 않음
    assert not region_gap(SEOUL_BIRTH, "T", parse, None)  # 표 정보 없음: 기존 동작


# ---------------------------------------------------------------- 메인 그래프 (understand → candidates)

def two_turns(first: str, second: str, search: Search, stat: Stat, **kw) -> dict:
    graph = build_graph(search, STAT_LLM, summarize=stat, checkpointer=new_checkpointer(), parse=parse, **kw)
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", first)]}, config)
    return graph.invoke({"messages": [("user", second)]}, config)


def test_measure_swap_searches_again_with_the_previous_year():
    search, stat = Search(["101_A"]), Stat({"101_A": answered("101_A")})
    out = two_turns(BIRTH, "이혼은?", search, stat)
    assert search.questions == [BIRTH, "2024년 이혼은?"]  # 원문 "이혼은?"이 아니라 연도를 붙인 문장으로 찾음
    assert stat.calls[-1] == ("2024년 이혼은?", "101_A") and out["outcome"] == "answered"
    assert not out["reused_table"] and out["carried_over"] and out["sub_questions"] == []
    assert any(t["action"] == "search_again" for t in out["trace"])


def test_compare_asks_the_previous_table_with_both_regions():
    search, stat = Search(["101_A"]), Stat({"101_A": answered("101_A")})
    out = two_turns(SEOUL_BIRTH, "부산이랑 비교해줘", search, stat)
    assert search.questions == [SEOUL_BIRTH]  # 둘째 턴은 검색하지 않음
    assert stat.calls[-1] == ("2024년 서울과 부산광역시 출생아 수 비교해줘", "101_A") and out["reused_table"]


def test_a_region_the_previous_table_lacks_is_searched_again():
    search, stat = Search(["101_A"]), Stat({"101_A": answered("101_A")})
    out = two_turns(BIRTH, "서울은?", search, stat, llm=SEOUL_CARRY, regions_of=lambda key: None)  # 시도 칸 없는 전국 표
    assert search.questions == [BIRTH, SEOUL_BIRTH] and stat.calls[-1] == (SEOUL_BIRTH, "101_A")
    assert not out["reused_table"] and out["fallback_searched"]


def test_a_region_the_previous_table_holds_reuses_it():
    search, stat = Search(["101_A"]), Stat({"101_A": answered("101_A")})
    out = two_turns(BIRTH, "서울은?", search, stat, llm=SEOUL_CARRY, regions_of=lambda key: {"서울특별시"})
    assert search.questions == [BIRTH] and out["reused_table"]
