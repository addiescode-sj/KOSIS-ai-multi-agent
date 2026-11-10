"""T3-28 multi-query: split a turn that needs several tables, answer each part, combine numbers in code."""

import json
from pathlib import Path

import pytest

from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.multiquery import LLMSplit, split_by_rules, split_question, verify_split
from kosis_agent.graph.state import chat_result
from kosis_agent.services.combine import combine
from kosis_ai.schemas import Fact, FactPart, StatAnswer, StatResult
from kosis_chat.schemas.io import StatOutput
from kosis_rag.parse.node import parse_query
from kosis_rag.schemas.io import SearchOutput

from tests.test_workflow import STAT_LLM, Stat, answered, refused, source, table

CALC = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "calculation_golden.jsonl"


@pytest.mark.parametrize("question, subs, op", [
    ("2024년 경기도 출생아 수와 외국인주민 수 알려줘", ["2024년 경기도 출생아 수", "2024년 경기도 외국인주민 수 알려줘"], None),
    ("2024년 서울 출생아 수, 사망자 수는?", ["2024년 서울 출생아 수", "2024년 서울 사망자 수는?"], None),
    ("2025년 경기도에서 태어난 아이는 몇 명이에요? 그리고 2020년 부산광역시 인구밀도는?",
     ["2025년 경기도에서 태어난 아이는 몇 명이에요?", "2020년 부산광역시 인구밀도는?"], None),
    ("2024년 경기도 외국인주민 수와 출생아 수를 더하면 몇 명이야?", ["2024년 경기도 외국인주민 수", "2024년 경기도 출생아 수"], "sum"),
])
def test_rules_split_questions_on_different_measures(question, subs, op):
    split = split_by_rules(question, parse_query)
    assert (split.sub_questions, split.op, split.by) == (subs, op, "rule")


@pytest.mark.parametrize("question", [
    "2025년 2인 가구 수, 서울이랑 경기도 비교해줘",  # same measure, two regions: phase 1 compare
    "2024년 부산과 대구 중 조사망률이 더 높은 곳은 어디고 차이는 얼마야?",
    "2025년 1인 가구, 지역별로 다 더하면 몇 가구야?",  # the tail adds a sum but no conditions of its own
    "2023년 서울 일반가구 중 1인 가구 비중은?",
])
def test_rules_keep_one_table_questions_whole(question):
    split = split_by_rules(question, parse_query)
    assert split.sub_questions == [question] and not split.deferred


@pytest.mark.skipif(not CALC.is_file(), reason="calculation golden set not present")
def test_no_golden_single_question_is_split_or_deferred():
    """Single-turn parity guard: none of the 90 golden questions goes down the multi-query path."""
    for line in CALC.read_text(encoding="utf-8").splitlines():
        question = json.loads(line)["question"]
        split = split_by_rules(question, parse_query)
        assert split.sub_questions == [question] and not split.deferred, question


def test_a_connective_join_is_deferred_to_the_llm_and_verified():
    question = "2015년 서울 외국인주민은 몇 명이고 1인 가구는 몇 가구야?"
    assert split_by_rules(question, parse_query).deferred
    good = LLMSplit(sub_questions=["2015년 서울 외국인주민은 몇 명이야?", "2015년 서울 1인 가구는 몇 가구야?"])
    split = split_question(question, parse_query, lambda q: good)
    assert split.by == "llm" and split.sub_questions == good.sub_questions
    # a made-up year, region or word is thrown away: the turn stays whole
    for bad in (["2016년 서울 외국인주민은 몇 명이야?", "2015년 서울 1인 가구는 몇 가구야?"],
                ["2015년 부산 외국인주민은 몇 명이야?", "2015년 서울 1인 가구는 몇 가구야?"],
                ["2015년 서울 외국인 인구는 몇 명이야?", "2015년 서울 1인 가구는 몇 가구야?"]):
        assert not verify_split(LLMSplit(sub_questions=bad), question, parse_query)
        assert split_question(question, parse_query, lambda q, b=bad: LLMSplit(sub_questions=b)).sub_questions == [question]


def test_the_llm_may_add_question_endings_only():
    question = "2025년 전국 주민등록세대수와 인구밀도를 같이 알려줘"
    assert split_by_rules(question, parse_query).deferred
    ok = LLMSplit(sub_questions=["2025년 전국 주민등록세대수는 몇 개인가요?", "2025년 전국 인구밀도는 얼마인가요?"])
    assert verify_split(ok, question, parse_query)
    assert not verify_split(LLMSplit(sub_questions=["2025년 전국 주민등록세대수는 몇 개인가요?", "2025년 전국 인구수는?"]),
                            question, parse_query)


def test_an_llm_failure_keeps_the_turn_whole():
    def broken(question):
        raise RuntimeError("down")
    question = "2015년 서울 외국인주민은 몇 명이고 1인 가구는 몇 가구야?"
    assert split_question(question, parse_query, broken).sub_questions == [question]


def answer(value, unit="명", n=1) -> StatAnswer:
    facts = [Fact(id=f"f{i}", value=value + i, unit=unit, source_ref=f"T|I|11|202{i}") for i in range(n)]
    return StatAnswer(outcome="answered", text="x", result=StatResult(facts=facts),
                      parts=[FactPart(fact_id=f.id) for f in facts], source=source("101_T"))


def test_combine_computes_in_code_and_refuses_what_it_cannot_pick():
    assert combine("sum", [answer(100), answer(30)])["value"] == 130
    assert combine("diff", [answer(100), answer(30)])["value"] == 70
    assert combine("ratio", [answer(30), answer(120)]) | {"inputs": None} == {
        "op": "ratio", "value": 25.0, "unit": "%", "inputs": None, "reason": None}
    assert combine("sum", [answer(100), answer(30, unit="가구")])["reason"] == "unit_mismatch"
    assert combine("sum", [answer(100, n=2), answer(30)])["reason"] == "not_single_value"
    assert combine("sum", [answer(100), refused("101_T", "no_period").answer])["value"] is None


class KeyedSearch:
    """Phase 2 stand-in: the table depends on a word in the question."""

    def __init__(self, by_word: dict[str, str]):
        self.by_word, self.questions = by_word, []

    def stream(self, inp):
        self.questions.append(inp.question)
        key = next((k for w, k in self.by_word.items() if w in inp.question), None)
        picked = [table(key)] if key else []
        yield "output", SearchOutput(question=inp.question, route="found" if key else "refuse", picked=picked,
                                     candidates=picked, refusal_reason=None if key else "out_of_scope",
                                     refusal_text=None if key else "범위 밖이에요.")


def run(search, stat, question):
    return build_graph(search, STAT_LLM, summarize=stat).invoke({"messages": [("user", question)]})


def test_each_sub_question_gets_its_own_table_and_answer():
    search = KeyedSearch({"외국인": "110_TX_FOREIGN", "출생": "101_DT_BIRTH"})
    stat = Stat({"110_TX_FOREIGN": answered("110_TX_FOREIGN"), "101_DT_BIRTH": answered("101_DT_BIRTH")})
    state = run(search, stat, "2024년 경기도 출생아 수와 외국인주민 수 알려줘")
    assert search.questions == ["2024년 경기도 출생아 수", "2024년 경기도 외국인주민 수 알려줘"]
    assert [k for _, k in stat.calls] == ["101_DT_BIRTH", "110_TX_FOREIGN"]
    result = chat_result(state)
    assert result.outcome == "answered" and result.answer_origin == "multi"
    assert result.answer == "101_DT_BIRTH: 100명\n\n110_TX_FOREIGN: 100명"
    assert state["active_table"] == "110_TX_FOREIGN"  # the next turn's "그 표" = the last answered part


def test_parts_on_the_same_table_go_back_to_one_question():
    search = KeyedSearch({"출생": "101_DT_VITAL", "사망": "101_DT_VITAL"})
    stat = Stat({"101_DT_VITAL": answered("101_DT_VITAL")})
    question = "2024년 출생아 수와 사망자 수 알려줘"
    state = run(search, stat, question)
    assert stat.calls == [(question, "101_DT_VITAL")]  # original wording, once
    assert chat_result(state).answer_origin == "stat" and state["outcome"] == "answered"


def test_one_refused_part_keeps_the_other_answer():
    search = KeyedSearch({"출생": "101_DT_BIRTH"})  # 외국인 → search refuses
    stat = Stat({"101_DT_BIRTH": answered("101_DT_BIRTH")})
    state = run(search, stat, "2024년 경기도 출생아 수와 외국인주민 수 알려줘")
    result = chat_result(state)
    assert result.outcome == "answered" and "범위 밖이에요." in result.answer
    assert [r["outcome"] for r in result.sub_results] == ["answered", "refused"]


def test_all_parts_refused_is_refused():
    search = KeyedSearch({"출생": "101_DT_BIRTH", "외국인": "110_TX_FOREIGN"})
    stat = Stat({"101_DT_BIRTH": refused("101_DT_BIRTH", "no_period"),
                 "110_TX_FOREIGN": refused("110_TX_FOREIGN", "no_period")})
    assert run(search, stat, "2024년 경기도 출생아 수와 외국인주민 수 알려줘")["outcome"] == "refused"


def test_a_sum_request_calls_combine_values():
    search = KeyedSearch({"외국인": "110_TX_FOREIGN", "출생": "101_DT_BIRTH"})

    def out(key, value):
        fact = Fact(id="f1", value=value, unit="명", source_ref=f"{key}|I|11|2024")
        return StatOutput(answer=StatAnswer(outcome="answered", text=f"{value}명", result=StatResult(facts=[fact]),
                                            parts=[FactPart(fact_id="f1")], source=source(key)))

    stat = Stat({"110_TX_FOREIGN": out("110_TX_FOREIGN", 1000), "101_DT_BIRTH": out("101_DT_BIRTH", 234)})
    state = run(search, stat, "2024년 경기도 외국인주민 수와 출생아 수를 더하면 몇 명이야?")
    assert state["combined"]["value"] == 1234
    assert chat_result(state).answer.endswith("두 값의 계산 결과(합): 1,234명")
