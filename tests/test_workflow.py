"""T3-23 옵션 A: 결정적 워크플로의 분기 (context → search → statistics ⟲ → chart → finalize).

오프라인 테스트다. 실전 2는 후보 목록을 정해 둔 가짜 검색, 실전 1은 표 키별로 정해 둔 답을 돌려주는 가짜로 바꾼다.
확인하는 것:
- 대체 표 재시도: 표 불일치 거절이면 다음 후보로, 질문 문제·되묻기면 멈춤, 예산(alt_table_budget)을 지킴
- 지시어 턴: 검색을 건너뛰고 직전 표를 쓰며, 그 표를 몰래 바꾸지 않음
- 종료 상태: answered · refused · clarify · failed, degraded 표시
"""

import pytest

from kosis_agent.graph.context import refers_to_previous_table
from kosis_agent.graph.main_graph import NO_TABLE_TEXT, build_graph
from kosis_agent.graph.state import chat_result, new_checkpointer
from kosis_ai.schemas import Fact, FactPart, Refusal, Source, StatAnswer, StatResult
from kosis_chat.schemas.io import StatOutput
from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig
from kosis_rag.schemas.io import SearchOutput, TableCandidate

STAT_LLM = StatLLMConfig(model="mock", provider="mock")


def table(key: str) -> TableCandidate:
    org_id, _, tbl_id = key.partition("_")
    return TableCandidate(key=key, org_id=org_id, tbl_id=tbl_id, table_name=f"표 {tbl_id}", survey_name="조사",
                          organization="국가데이터처", period="년 (2015 ~ 2025)", url="https://kosis.kr")


def source(key: str) -> Source:
    org_id, _, tbl_id = key.partition("_")
    return Source(org_id=org_id, tbl_id=tbl_id, survey_nm="조사", tbl_nm=f"표 {tbl_id}")


def answered(key: str) -> StatOutput:
    fact = Fact(id="f1", value=100.0, source_ref=f"{key.partition('_')[2]}|T1|11|2024", unit="명", period="2024")
    answer = StatAnswer(outcome="answered", text=f"{key}: 100명", result=StatResult(facts=[fact]),
                        parts=[FactPart(fact_id="f1")], source=source(key))
    return StatOutput(answer=answer)


def refused(key: str, reason: str) -> StatOutput:
    text = f"{key}: 거절 ({reason})"
    return StatOutput(answer=StatAnswer(outcome="refused", text=text, refusal=Refusal(reason=reason, message=text),
                                        source=source(key)))


def clarify(key: str) -> StatOutput:
    return StatOutput(answer=StatAnswer(outcome="clarify", text="어느 지역 기준인가요?", clarify_options=["서울", "부산"],
                                        source=source(key)))


class Search:
    """실전 2 대신: 정해 둔 picked / candidates를 돌려준다. 호출된 질문을 기록한다."""

    def __init__(self, picked: list[str], candidates: list[str] | None = None, route: str = "found",
                 spans: list[dict] | None = None):
        self.picked, self.candidates, self.route, self.spans = picked, candidates or picked, route, spans or []
        self.questions: list[str] = []

    def stream(self, inp):
        self.questions.append(inp.question)
        yield "output", SearchOutput(question=inp.question, route=self.route, picked=[table(k) for k in self.picked],
                                     candidates=[table(k) for k in self.candidates], spans=self.spans,
                                     refusal_reason="out_of_scope" if self.route == "refuse" else None,
                                     refusal_text="범위 밖이에요." if self.route == "refuse" else None)


class Stat:
    """실전 1 대신: 표 키별로 정해 둔 결과를 돌려준다(값이 예외면 그 예외를 낸다). 호출된 (질문, 키)를 기록한다."""

    def __init__(self, by_key: dict):
        self.by_key = by_key
        self.calls: list[tuple[str, str]] = []

    def __call__(self, question, table_id, *, llm=None, retry_interval=0.0):
        self.calls.append((question, table_id))
        out = self.by_key[table_id]
        if isinstance(out, Exception):
            raise out
        return out


def run(search, stat, question="2024년 서울 출생아 수", alt_table_budget=1, **kw):
    """재시도 분기를 보려고 기본 예산을 1로 둔다(서비스 기본값은 0: test_the_service_default_is_off)."""
    return build_graph(search, STAT_LLM, summarize=stat, alt_table_budget=alt_table_budget, **kw).invoke(
        {"messages": [("user", question)]})


# ---------------------------------------------------------------- 대체 표 재시도

def test_a_table_mismatch_refusal_tries_the_next_candidate():
    stat = Stat({"101_A": refused("101_A", "no_period"), "101_B": answered("101_B")})
    out = run(Search(["101_A"], ["101_A", "101_B"]), stat)
    assert [k for _, k in stat.calls] == ["101_A", "101_B"]  # picked 다음 candidates 순서
    r = chat_result(out)
    assert r.outcome == "answered" and not r.no_answer and r.table_key == "101_B" and r.answer == "101_B: 100명"
    assert r.attempted_keys == ["101_A", "101_B"] and out["active_table"] == "101_B"


def test_budget_zero_keeps_the_stage_1_path():
    stat = Stat({"101_A": refused("101_A", "no_period"), "101_B": answered("101_B")})
    r = chat_result(run(Search(["101_A"], ["101_A", "101_B"]), stat, alt_table_budget=0))
    assert [k for _, k in stat.calls] == ["101_A"]
    assert r.outcome == "refused" and r.no_answer_reason == "no_period" and r.table_key == "101_A"


def test_the_budget_caps_the_attempts():
    stat = Stat({k: refused(k, "no_item") for k in ("101_A", "101_B", "101_C")})
    r = chat_result(run(Search(["101_A", "101_B", "101_C"]), stat, alt_table_budget=1))
    assert [k for _, k in stat.calls] == ["101_A", "101_B"]
    # 턴은 마지막으로 시도한 표에서 끝난다 (평가 하네스의 "마지막 summarize_stat 호출" 기준과 같음)
    assert r.outcome == "refused" and r.table_key == "101_B" and r.answer == "101_B: 거절 (no_item)"


@pytest.mark.parametrize("reason", ["causal", "unit_mismatch", "not_aggregatable", "missing_value"])
def test_a_question_problem_is_not_retried_on_another_table(reason):
    stat = Stat({"101_A": refused("101_A", reason), "101_B": answered("101_B")})
    r = chat_result(run(Search(["101_A", "101_B"]), stat))
    assert [k for _, k in stat.calls] == ["101_A"] and r.no_answer_reason == reason


def test_clarify_ends_the_turn_without_a_retry_or_a_chart():
    stat = Stat({"101_A": clarify("101_A"), "101_B": answered("101_B")})
    r = chat_result(run(Search(["101_A", "101_B"]), stat))
    assert [k for _, k in stat.calls] == ["101_A"]
    assert r.outcome == "clarify" and r.no_answer and r.chart is None and r.answer == "어느 지역 기준인가요?"


def test_a_key_missing_from_tables_csv_moves_on_to_the_next_candidate():
    stat = Stat({"101_A": LookupError("101_A is not in tables.csv"), "101_B": answered("101_B")})
    r = chat_result(run(Search(["101_A", "101_B"]), stat))
    assert r.attempted_keys == ["101_A", "101_B"] and r.outcome == "answered" and r.table_key == "101_B"


def test_a_model_failure_is_not_retried():
    stat = Stat({"101_A": RuntimeError("HCX call failed (429)"), "101_B": answered("101_B")})
    with pytest.raises(RuntimeError):
        run(Search(["101_A", "101_B"]), stat)
    assert [k for _, k in stat.calls] == ["101_A"]


# ---------------------------------------------------------------- 지시어 턴 (멀티턴)

def test_a_reference_reuses_the_previous_table_without_searching():
    search = Search(["101_A", "101_B"])
    stat = Stat({"101_A": answered("101_A"), "101_B": answered("101_B")})
    graph = build_graph(search, STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", "2024년 서울 출생아 수")]}, config)
    second = graph.invoke({"messages": [("user", "그 표에서 부산은?")]}, config)
    assert search.questions == ["2024년 서울 출생아 수"]  # 두 번째 턴은 검색하지 않음
    assert stat.calls[-1] == ("그 표에서 부산은?", "101_A")  # 직전 턴에 답한 표, 이번 턴 질문
    r = chat_result(second)
    assert r.reused_table and r.question == "그 표에서 부산은?" and r.table_key == "101_A"


def test_a_reference_turn_does_not_swap_the_table_the_user_pointed_at():
    search = Search(["101_A", "101_B"])
    stat = Stat({"101_A": answered("101_A"), "101_B": answered("101_B")})
    graph = build_graph(search, STAT_LLM, summarize=stat, checkpointer=new_checkpointer(), alt_table_budget=1)
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", "2024년 서울 출생아 수")]}, config)
    stat.by_key["101_A"] = refused("101_A", "no_period")
    r = chat_result(graph.invoke({"messages": [("user", "그 표에서 1990년은?")]}, config))
    assert stat.calls[-1][1] == "101_A" and len(stat.calls) == 2  # 101_B로 몰래 바꾸지 않음
    assert r.outcome == "refused" and r.no_answer_reason == "no_period"


def test_a_new_question_searches_again():
    search = Search(["101_A"])
    stat = Stat({"101_A": answered("101_A")})
    graph = build_graph(search, STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", "2024년 서울 출생아 수")]}, config)
    r = chat_result(graph.invoke({"messages": [("user", "2024년 부산 사망자 수")]}, config))
    assert search.questions == ["2024년 서울 출생아 수", "2024년 부산 사망자 수"] and not r.reused_table


def test_a_reference_without_a_previous_table_searches():
    search = Search(["101_A"])
    r = chat_result(run(search, Stat({"101_A": answered("101_A")}), question="그 표에서 서울은?"))
    assert search.questions == ["그 표에서 서울은?"] and not r.reused_table


@pytest.mark.parametrize("question", ["그 표에서 2020년만 보여줘", "이 표로 막대 그려줘", "같은 표에서 부산은?",
                                      "아까 그 자료로 비교해줘", "거기서 서울만", "위 표의 2023년 값"])
def test_reference_phrases(question):
    assert refers_to_previous_table(question)


@pytest.mark.parametrize("question", ["2023년 서울 출생아 수", "이 표준인구는 어떻게 계산해?", "높이 표로 정리",
                                      "인구 통계표 찾아줘", "표본 조사 결과", "여기저기 인구"])
def test_not_reference_phrases(question):
    assert not refers_to_previous_table(question)


# ---------------------------------------------------------------- 종료 상태

def test_a_search_refusal_is_refused_with_the_gate_text():
    stat = Stat({})
    r = chat_result(run(Search([], route="refuse"), stat))
    assert stat.calls == [] and r.outcome == "refused" and r.answer == "범위 밖이에요." and r.answer_origin == "refusal"


def test_found_without_any_table_is_failed():
    r = chat_result(run(Search([], []), Stat({})))
    assert r.outcome == "failed" and r.no_answer and r.answer == NO_TABLE_TEXT


def test_a_stage2_error_marks_the_answer_degraded():
    spans = [{"node": "rerank2", "seconds": 0.1, "stage2_error": "TimeoutError: HCX"}]
    r = chat_result(run(Search(["101_A"], spans=spans), Stat({"101_A": answered("101_A")})))
    assert r.degraded and r.outcome == "answered"


def test_the_service_default_is_off():
    """기본 예산 0: 재시도하지 않는다(재생 측정에서 틀린 표로 답한 사례가 나와 기본을 끔, plans/T3-23.md Result)."""
    stat = Stat({"101_A": refused("101_A", "no_period"), "101_B": answered("101_B")})
    build_graph(Search(["101_A", "101_B"]), STAT_LLM, summarize=stat).invoke({"messages": [("user", "q")]})
    assert [k for _, k in stat.calls] == ["101_A"]


def test_a_negative_budget_is_rejected():
    with pytest.raises(ValueError):
        build_graph(Search(["101_A"]), STAT_LLM, summarize=Stat({}), alt_table_budget=-1)


# ---------------------------------------------------------------- 평가 하네스와 함께 (그래프 → span → 채점기)

def _graded(graph, *turns):
    """turns = (질문, 기대) 목록을 한 대화로 돌리고, agent 계층 채점 결과를 턴별로 돌려준다."""
    from evals.evaluators import agent as grader
    from evals.runners import agent as runner
    from evals.schemas import AgentScenario

    scenario = AgentScenario.model_validate({"sid": "s", "turns": [{"question": q, "expect": e} for q, e in turns]})
    records = runner.run(graph, [scenario])[("s", 0)]
    return grader.grade_conversation(scenario, records, {}, lambda key: {}), records


def test_the_harness_grades_an_alternate_table_answer_on_its_final_table():
    """대체 표로 답한 턴: 기대 표가 대체 표(101_B)면 통과, 시도는 감점 없이 alt_attempts로만 남는다."""
    stat = Stat({"101_A": refused("101_A", "no_period"), "101_B": answered("101_B")})
    graph = build_graph(Search(["101_A"], ["101_A", "101_B"]), STAT_LLM, summarize=stat,
                        checkpointer=new_checkpointer(), alt_table_budget=1)
    expect = {"tools": [{"name": "search_tables"}, {"name": "summarize_stat", "args": {"table_key": "101_B"}}],
              "table_key": "101_B"}
    (row,), _ = _graded(graph, ("2024년 서울 출생아 수", expect))
    assert row["passed"], row["errors"]
    assert row["alt_attempts"] == 1 and row["extra"] == {} and not row["refused"]


def test_the_harness_still_fails_an_alternate_table_that_is_not_the_expected_one():
    stat = Stat({"101_A": refused("101_A", "no_period"), "101_B": answered("101_B")})
    graph = build_graph(Search(["101_A"], ["101_A", "101_B"]), STAT_LLM, summarize=stat,
                        checkpointer=new_checkpointer(), alt_table_budget=1)
    (row,), _ = _graded(graph, ("2024년 서울 출생아 수", {"tools": [{"name": "search_tables"}, {"name": "summarize_stat"}],
                                                       "table_key": "101_A"}))
    assert row["errors"] == ["lost_state"]  # 최종 답의 표가 정답 표와 다를 때만 감점


def test_summarize_stat_args_carry_the_conditions_of_each_turn():
    """조건 인자(기간·시도·분류)는 실전 2 parse_query로 이번 질문에서 읽는다. 지시어 턴은 표는 그대로, 조건은 새 질문 것."""
    stat = Stat({"101_A": answered("101_A")})
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    seoul = {"tools": [{"name": "search_tables"},
                       {"name": "summarize_stat", "args": {"table_key": "101_A", "period_start": "2024",
                                                           "period_end": "2024", "regions": ["서울특별시"]}}]}
    busan = {"tools": [{"name": "summarize_stat", "args": {"table_key": "101_A", "regions": ["부산광역시"]}}],
             "table_key": "101_A"}
    rows, records = _graded(graph, ("2024년 서울 출생아 수", seoul), ("그 표에서 부산은?", busan))
    assert [r["passed"] for r in rows] == [True, True], [r["errors"] for r in rows]
    assert [c["name"] for c in records[1]["calls"]] == ["summarize_stat"]  # 지시어 턴은 검색하지 않음
    assert records[1]["calls"][0]["args"]["question"] == "그 표에서 부산은?"


# ---------------------------------------------------------------- T3-27: 이월 해석 (graph/context.py)

from kosis_agent.graph.context import LLMReading, carry_over, leftover, read_turn, verify_reading  # noqa: E402
from kosis_agent.services.chart import NO_CHART_TEXT, draw, phase1_kind  # noqa: E402
from kosis_ai.schemas import ChartPoint, ChartSeries, ChartSpec  # noqa: E402
from kosis_rag.parse.node import parse_query  # noqa: E402

PREV = "2025년 경기도에서 태어난 아이는 몇 명이에요?"


def rules(question, previous=PREV, table="101_A", **kw):
    return read_turn(question, active_table=table, active_question=previous, parse=parse_query, mode="rules", **kw)


@pytest.mark.parametrize("question, expected", [
    ("부산은?", "2025년 부산광역시에서 태어난 아이는 몇 명이에요?"),
    ("세종은요?", "2025년 세종특별자치시에서 태어난 아이는 몇 명이에요?"),
    ("그럼 부산 쪽은?", "2025년 부산광역시에서 태어난 아이는 몇 명이에요?"),
    ("아니 2023년으로", "2023년 경기도에서 태어난 아이는 몇 명이에요?"),
    ("2024년은?", "2024년 경기도에서 태어난 아이는 몇 명이에요?"),
])
def test_an_elliptical_follow_up_carries_the_previous_conditions(question, expected):
    r = rules(question)
    assert r.use_previous_table and r.by == "rule" and r.resolved_question == expected


@pytest.mark.parametrize("question", ["2024년 부산의 자연증가건수는 몇 명이야?", "2020년 부산광역시 인구밀도는?",
                                      "2023년 부산 출생 통계 알려줘"])
def test_a_full_question_is_a_new_question_even_without_a_measure_word(question):
    """실전 2가 지표 낱말을 못 읽는 질문("자연증가건수")도 남는 내용이 길면 새 질문이다."""
    r = rules(question)
    assert not r.use_previous_table and r.resolved_question == question


@pytest.mark.parametrize("question, kind", [("그 표로 막대그래프 그려줘", "bar"), ("꺾은선 그래프로 보여줘", "line"),
                                            ("이걸 막대 차트로 그려줘", "bar")])
def test_a_chart_only_request(question, kind):
    r = rules(question)
    assert r.chart_only and r.use_previous_table and r.chart_type == kind


@pytest.mark.parametrize("question, expected", [
    ("작년은?", "2024년 경기도에서 태어난 아이는 몇 명이에요?"),
    ("전년도는?", "2024년 경기도에서 태어난 아이는 몇 명이에요?"),
    ("직전 해 기준으로는?", "2024년 경기도에서 태어난 아이는 몇 명이에요?"),
    ("재작년은?", "2023년 경기도에서 태어난 아이는 몇 명이에요?"),
    ("3년 전 수치는?", "2022년 경기도에서 태어난 아이는 몇 명이에요?"),
    ("작년 부산은?", "2024년 부산광역시에서 태어난 아이는 몇 명이에요?"),
])
def test_a_relative_year_is_computed_from_the_previous_year(question, expected):
    """상대 연도는 코드가 직전 질문 연도에서 계산한다(LLM이 만든 연도는 verify_reading이 버림)."""
    r = rules(question)
    assert r.use_previous_table and r.by == "rule" and r.resolved_question == expected


def test_a_relative_year_keeps_the_previous_month():
    r = rules("재작년은?", previous="2025년 3월 전국 혼인 건수는?")
    assert r.by == "rule" and r.resolved_question == "2023년 3월 전국 혼인 건수는?"


def test_a_relative_year_without_a_single_previous_year_is_not_guessed():
    assert rules("작년은?", previous="2015년부터 2020년까지 합계출산율은?").by == "none"  # 직전이 기간 범위
    assert rules("전년 대비는?").by == "none"  # 비교 표현: 연도를 바꾸지 않는다


@pytest.mark.parametrize("question", ["남자는?"])
def test_rules_do_not_guess(question):
    """규칙이 판단하지 못하면 원문으로 새 검색(rules) — hybrid에서는 이 턴만 LLM에 묻는다."""
    r = rules(question)
    assert not r.use_previous_table and r.by == "none" and r.resolved_question == question


def test_no_carry_over_without_a_surface_to_replace():
    assert carry_over("2015년부터 2020년까지 합계출산율은?", parse_query("2015년부터 2020년까지 합계출산율은?"),
                      parse_query("2023년은?")) is None  # 직전 질문이 기간 범위: 바꿔 끼우지 않는다
    assert carry_over("합계출산율 최근 값 알려줘", parse_query("합계출산율 최근 값 알려줘"), parse_query("부산은?")) is None
    assert leftover("그럼 부산 쪽은?") == ""


def test_no_previous_table_means_a_new_question():
    r = read_turn("부산은?", active_table=None, active_question=None, parse=parse_query)
    assert not r.use_previous_table and r.by == "none"


def test_the_llm_reading_is_checked_against_both_questions():
    ok = LLMReading(standalone_question="2025년 경기도에서 태어난 남자 아이는 몇 명이에요?", use_previous_table=True)
    reworded = LLMReading(standalone_question="2025년 경기도 남자 출생아 수는?", use_previous_table=True)
    assert verify_reading(LLMReading(standalone_question="2025년 부산광역시에서 태어난 아이는 몇 명이에요?",
                                     use_previous_table=True), PREV, "부산은?", parse_query)
    assert not verify_reading(LLMReading(standalone_question="2019년 경기도에서 태어난 아이는 몇 명이에요?",
                                         use_previous_table=True), PREV, "작년은?", parse_query)  # 2019: 어디에도 없는 숫자
    assert not verify_reading(LLMReading(standalone_question="2025년 대구광역시에서 태어난 아이는 몇 명이에요?",
                                         use_previous_table=True), PREV, "부산은?", parse_query)  # 대구: 지어낸 지역
    assert verify_reading(ok, PREV, "남자는?", parse_query)
    assert not verify_reading(reworded, PREV, "남자는?", parse_query)  # "출생아": 두 질문 어디에도 없는 낱말


class FakeLLM:
    def __init__(self, reading=None, error=None):
        self.reading, self.error, self.calls = reading, error, []

    def __call__(self, previous, question):
        self.calls.append((previous, question))
        if self.error:
            raise self.error
        return self.reading


def test_hybrid_asks_the_llm_only_when_the_rules_do_not_decide():
    llm = FakeLLM(LLMReading(standalone_question="2025년 경기도에서 태어난 남자 아이는 몇 명이에요?", use_previous_table=True))
    decided = read_turn("부산은?", active_table="T", active_question=PREV, parse=parse_query, mode="hybrid", llm=llm)
    assert decided.by == "rule" and llm.calls == []
    asked = read_turn("남자는?", active_table="T", active_question=PREV, parse=parse_query, mode="hybrid", llm=llm)
    assert asked.by == "llm" and asked.use_previous_table and llm.calls == [(PREV, "남자는?")]


def test_a_rejected_or_failed_llm_reading_falls_back_to_a_new_search():
    made_up = FakeLLM(LLMReading(standalone_question="2019년 경기도에서 태어난 남자 아이는 몇 명이에요?", use_previous_table=True))
    r = read_turn("남자는?", active_table="T", active_question=PREV, parse=parse_query, mode="hybrid", llm=made_up)
    assert not r.use_previous_table and r.resolved_question == "남자는?"
    broken = FakeLLM(error=TimeoutError("HCX"))
    r = read_turn("남자는?", active_table="T", active_question=PREV, parse=parse_query, mode="hybrid", llm=broken)
    assert not r.use_previous_table and r.by == "none"


def test_a_new_question_from_the_llm_searches_with_the_user_wording():
    llm = FakeLLM(LLMReading(standalone_question="2025년 전국 혼인 건수", use_previous_table=False))
    r = read_turn("결혼은?", active_table="T", active_question=PREV, parse=parse_query, mode="llm", llm=llm)
    assert not r.use_previous_table and r.resolved_question == "결혼은?"  # LLM 문장이 아니라 원문


def test_llm_mode_asks_the_llm_on_every_follow_up():
    llm = FakeLLM(LLMReading(standalone_question="2025년 부산광역시에서 태어난 아이는 몇 명이에요?", use_previous_table=True))
    r = read_turn("부산은?", active_table="T", active_question=PREV, parse=parse_query, mode="llm", llm=llm)
    assert r.by == "llm" and len(llm.calls) == 1


# ---------------------------------------------------------------- T3-27: 그래프에서 이월·차트

def bar_chart(key: str) -> ChartSpec:
    return ChartSpec(kind="bar", title="출생아 수", series=[ChartSeries(name=key, points=[
        ChartPoint(label="경기도", value=100, display="100명", fact_id="f1")])], fact_ids=["f1"])


def charted(key: str) -> StatOutput:
    out = answered(key)
    return out.model_copy(update={"chart": bar_chart(key)})


def test_an_elliptical_turn_asks_phase1_the_carried_over_question():
    search = Search(["101_A"])
    stat = Stat({"101_A": answered("101_A")})
    graph = build_graph(search, STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", PREV)]}, config)
    r = chat_result(graph.invoke({"messages": [("user", "부산은?")]}, config))
    assert search.questions == [PREV]  # 이월 턴은 검색하지 않음
    assert stat.calls[-1] == ("2025년 부산광역시에서 태어난 아이는 몇 명이에요?", "101_A")
    assert r.reused_table and r.interpreted_by == "rule" and r.resolved_question == stat.calls[-1][0]
    third = chat_result(graph.invoke({"messages": [("user", "아니 2023년으로")]}, config))
    assert stat.calls[-1][0] == "2023년 부산광역시에서 태어난 아이는 몇 명이에요?"  # 이월은 직전에 해석된 질문 위에 쌓인다
    assert third.table_key == "101_A"


def test_a_chart_only_turn_redraws_the_previous_answer_without_phase1():
    stat = Stat({"101_A": charted("101_A")})
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", PREV)]}, config)
    r = chat_result(graph.invoke({"messages": [("user", "꺾은선 그래프로 보여줘")]}, config))
    assert len(stat.calls) == 1  # 숫자는 직전 답 그대로
    assert r.answer_origin == "chart" and r.outcome == "answered" and r.chart is not None and r.chart_type == "line"
    assert r.table_key == "101_A"


def test_a_chart_only_turn_without_chart_data_says_so():
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=Stat({"101_A": answered("101_A")}),
                        checkpointer=new_checkpointer())
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", PREV)]}, config)
    r = chat_result(graph.invoke({"messages": [("user", "막대그래프로 그려줘")]}, config))
    assert r.outcome == "failed" and r.answer == NO_CHART_TEXT and r.chart is None


def test_the_harness_grades_chart_requests_through_make_chart():
    """차트 요청 턴은 make_chart(chart_type)를 부르고, 요청 없는 턴은 부르지 않는다(extra_call 방지)."""
    stat = Stat({"101_A": charted("101_A")})
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    chart = {"name": "make_chart", "args": {"table_key": "101_A", "chart_type": "bar"}}
    rows, records = _graded(graph,
                            (PREV, {"tools": [{"name": "search_tables"}, {"name": "summarize_stat"}], "table_key": "101_A"}),
                            ("그 표로 막대그래프 그려줘", {"tools": [chart], "table_key": "101_A"}))
    assert [r["passed"] for r in rows] == [True, True], [r["errors"] for r in rows]
    graph2 = build_graph(Search(["101_A"]), STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    rows, _ = _graded(graph2, (PREV + " 막대그래프로도 그려줘",
                               {"tools": [{"name": "search_tables"}, {"name": "summarize_stat"}, chart]}))
    assert rows[0]["passed"], rows[0]["errors"]


def test_phase1_chart_kinds():
    assert phase1_kind("bar", "line") == "timeline" and phase1_kind("bar", "multi_line") == "grouped_bar"
    assert phase1_kind("line", "timeline") == "line" and phase1_kind("line", "grouped_bar") == "multi_line"
    assert phase1_kind("pie", "bar") == "pie" and phase1_kind("bar", "bar") == "bar"
    out = draw(bar_chart("101_A"), "bar")
    assert out["vega_lite"] is not None and out["kind"] == "bar" and out["note"] is None
    assert draw(None, "bar")["note"] == NO_CHART_TEXT


def test_a_verified_llm_rewrite_that_borrows_the_previous_question_is_a_follow_up():
    """HCX-005 실측: 독립 질문은 직전 질문을 이어받아 맞게 썼는데 use_previous_table=false로 답했다 → 문장을 믿는다."""
    llm = FakeLLM(LLMReading(standalone_question="2025년 경기도에서 태어난 남자 아이는 몇 명이에요?",
                             use_previous_table=False))
    r = read_turn("남자는?", active_table="T", active_question=PREV, parse=parse_query, mode="hybrid", llm=llm)
    assert r.by == "llm" and r.use_previous_table and r.resolved_question.startswith("2025년 경기도")
    same = FakeLLM(LLMReading(standalone_question="남자는?", use_previous_table=False))  # 빌린 낱말 없음 = 새 질문
    assert not read_turn("남자는?", active_table="T", active_question=PREV, parse=parse_query, mode="hybrid",
                         llm=same).use_previous_table


@pytest.mark.parametrize("question", ["대구는 어떻게 나와?", "울산 쪽 숫자도 알려줘", "2022년 수치로 다시 알려줘"])
def test_a_short_follow_up_with_a_slot_is_carried_over_by_rules(question):
    """조건 표현을 지우고 남는 말이 6자보다 짧고 시도·연도가 있으면 이월 (골든 문항 중 가장 짧은 것이 8자)."""
    r = rules(question)
    assert r.use_previous_table and r.by == "rule" and r.resolved_question != question


@pytest.mark.parametrize("question, kind", [("선으로 그려줄 수 있어?", "line"), ("막대로 보여줘", "bar"),
                                            ("꺾은선으로 시각화해줘", "line")])
def test_a_chart_request_with_a_polite_ending_is_still_chart_only(question, kind):
    r = rules(question)
    assert r.chart_only and r.chart_type == kind


def test_the_llm_reply_is_read_from_its_first_json_object():
    from kosis_agent.graph.context import parse_llm_reading

    got = parse_llm_reading('답: {"standalone_question": "q", "use_previous_table": true} {"x": 1}')
    assert got.standalone_question == "q" and got.use_previous_table
    with pytest.raises(ValueError):
        parse_llm_reading("JSON이 없는 답")


# ---------------------------------------------------------------- 이월 턴의 다시 검색 (2026-11-09 실측 후 추가)

class KeyedSearch(Search):
    """질문마다 다른 후보를 돌려주는 가짜 검색: {질문에 들어 있는 말: [표 키]}."""

    def __init__(self, routes: dict[str, list[str]]):
        super().__init__([])
        self.routes = routes

    def stream(self, inp):
        self.questions.append(inp.question)
        keys = next((v for k, v in self.routes.items() if k in inp.question), [])
        yield "output", SearchOutput(question=inp.question, route="found" if keys else "refuse",
                                     picked=[table(k) for k in keys], candidates=[table(k) for k in keys],
                                     refusal_reason=None if keys else "out_of_scope",
                                     refusal_text=None if keys else "범위 밖이에요.")


def _two_turns(search, stat, second):
    graph = build_graph(search, STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", PREV)]}, config)
    return chat_result(graph.invoke({"messages": [("user", second)]}, config))


def test_a_carried_over_turn_searches_with_the_rewritten_question_when_the_previous_table_cannot_answer():
    """"부산은?" → 직전 표(경기도만 있는 표)가 no_item으로 거절 → "2025년 부산광역시에서 …"로 검색 → 그 표로 답한다."""
    search = KeyedSearch({"경기도": ["101_GG"], "부산광역시": ["101_BS"]})
    stat = Stat({"101_GG": answered("101_GG"), "101_BS": answered("101_BS")})
    graph = build_graph(search, STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", PREV)]}, config)
    stat.by_key["101_GG"] = refused("101_GG", "no_item")
    r = chat_result(graph.invoke({"messages": [("user", "부산은?")]}, config))
    assert search.questions[-1] == "2025년 부산광역시에서 태어난 아이는 몇 명이에요?"  # "부산은?"이 아니라 다시 쓴 문장
    assert [k for _, k in stat.calls[-2:]] == ["101_GG", "101_BS"]
    assert r.outcome == "answered" and r.table_key == "101_BS" and r.attempted_keys == ["101_GG", "101_BS"]


def test_a_reference_turn_does_not_search_again():
    search = KeyedSearch({"경기도": ["101_GG"], "부산": ["101_BS"]})
    stat = Stat({"101_GG": answered("101_GG")})
    graph = build_graph(search, STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", PREV)]}, config)
    stat.by_key["101_GG"] = refused("101_GG", "no_item")
    r = chat_result(graph.invoke({"messages": [("user", "그 표에서 부산은?")]}, config))
    assert len(search.questions) == 1 and r.outcome == "refused" and r.table_key == "101_GG"


def test_the_search_again_that_finds_only_the_same_table_keeps_its_refusal():
    search = KeyedSearch({"태어난": ["101_GG"]})  # 다시 써도 같은 표만 나옴
    stat = Stat({"101_GG": answered("101_GG")})
    graph = build_graph(search, STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", PREV)]}, config)
    stat.by_key["101_GG"] = refused("101_GG", "no_period")
    r = chat_result(graph.invoke({"messages": [("user", "아니 2001년으로")]}, config))
    assert len(search.questions) == 2 and r.outcome == "refused" and r.no_answer_reason == "no_period"
    assert [k for _, k in stat.calls].count("101_GG") == 2  # 같은 표를 두 번 묻지 않음 (턴마다 한 번)


def test_a_question_problem_on_a_carried_over_turn_does_not_search_again():
    search = KeyedSearch({"경기도": ["101_GG"], "부산광역시": ["101_BS"]})
    stat = Stat({"101_GG": answered("101_GG"), "101_BS": answered("101_BS")})
    graph = build_graph(search, STAT_LLM, summarize=stat, checkpointer=new_checkpointer())
    config = {"configurable": {"thread_id": "t"}}
    graph.invoke({"messages": [("user", PREV)]}, config)
    stat.by_key["101_GG"] = refused("101_GG", "missing_value")
    r = chat_result(graph.invoke({"messages": [("user", "부산은?")]}, config))
    assert len(search.questions) == 1 and r.no_answer_reason == "missing_value"
