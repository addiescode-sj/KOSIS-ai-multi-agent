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
    # 모두 거절하면 검색 1순위 표의 거절을 보여 준다
    assert r.outcome == "refused" and r.table_key == "101_A" and r.answer == "101_A: 거절 (no_item)"


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
