"""후속 질문 규칙 (11/25 서버 점검): 지표만 바꾼 이월, 시도를 더한 비교, 직전 표에 없는 시도."""

from langchain_core.messages import HumanMessage

from kosis_agent.graph.context import LLMReading
from kosis_multi.agents.followup import carry_conditions, compare_regions, follow_up, region_gap
from kosis_multi.agents.judge import make_judge
from kosis_multi.agents.retriever import make_retriever
from kosis_rag.parse.node import parse_query as parse

BIRTH = "2024년 출생아 수 알려줘"
SEOUL_BIRTH = "2024년 서울 출생아 수 알려줘"


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


def _turn(question: str, previous: str, **judge_args) -> dict:
    judge = make_judge(parse, **judge_args)
    state = {"messages": [HumanMessage(question)], "active_table": "T", "active_question": previous}
    return judge(state, {"configurable": {"thread_id": "t"}})


def test_judge_searches_again_when_table_lacks_region():
    llm = lambda prev, q: LLMReading(standalone_question=SEOUL_BIRTH, use_previous_table=True)  # noqa: E731
    out = _turn("서울은?", BIRTH, llm=llm, regions_of=lambda key: None)
    assert out["resolved_question"] == SEOUL_BIRTH
    assert not out["reused_table"] and out["carried_over"] and out["route"] == "rag" and out["sub_questions"] == []


def test_judge_reuses_table_that_has_region():
    llm = lambda prev, q: LLMReading(standalone_question=SEOUL_BIRTH, use_previous_table=True)  # noqa: E731
    out = _turn("서울은?", BIRTH, llm=llm, regions_of=lambda key: {"서울특별시"})
    assert out["reused_table"] and out["carried_over"]


def test_judge_measure_swap_searches_with_year():
    out = _turn("이혼은?", BIRTH)
    assert out["resolved_question"] == "2024년 이혼은?"
    assert not out["reused_table"] and out["carried_over"] and out["interpreted_by"] == "rule"


class FakeSteps:
    def __init__(self):
        self.asked = []

    def retrieve_candidates(self, question, config):
        self.asked.append(question)


def test_retriever_uses_resolved_question_for_new_search_follow_up():
    steps = FakeSteps()
    state = {"question": "이혼은?", "resolved_question": "2024년 이혼은?", "carried_over": True,
             "reused_table": False, "attempts": []}
    out = make_retriever(steps)(state, {})
    assert steps.asked == ["2024년 이혼은?"] and out["fallback_searched"]


def test_retriever_new_question_uses_original():
    steps = FakeSteps()
    state = {"question": "2024년 이혼 건수", "resolved_question": "2024년 이혼 건수", "carried_over": False,
             "reused_table": False, "attempts": []}
    make_retriever(steps)(state, {})
    assert steps.asked == ["2024년 이혼 건수"]
