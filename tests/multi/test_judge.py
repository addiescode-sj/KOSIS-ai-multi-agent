"""S2: ① 판단의 경로(rag | direct)와 즉시 답변 안전장치. 오프라인(가짜 검색·실전 1·Qwen)."""

import pytest

from kosis_agent.graph.state import new_checkpointer
from kosis_multi.agents.judge import (DIRECT_FALLBACK, JudgeReply, decide_route, parse_judge_reply, route_by_rules,
                                      safe_reply)
from kosis_multi.orchestrator import build_graph
from kosis_rag.parse.node import parse_query
from tests.test_workflow import STAT_LLM, Search, Stat, answered


class FakeQwen:
    """정해 둔 JudgeReply(또는 예외)를 돌려주고, 받은 질문을 기록한다."""

    def __init__(self, reply: JudgeReply | Exception):
        self.reply, self.questions = reply, []

    def __call__(self, question: str) -> JudgeReply:
        self.questions.append(question)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def run(question, qwen=None, search=None, stat=None, graph=None, thread="t"):
    search = search or Search(["101_A"])
    stat = stat or Stat({"101_A": answered("101_A")})
    graph = graph or build_graph(search, STAT_LLM, summarize=stat, judge_llm=qwen)
    out = graph.invoke({"messages": [("user", question)]}, config={"configurable": {"thread_id": thread}})
    return out, search, stat


# ---------------------------------------------------------------- 규칙 경로

@pytest.mark.parametrize("question", ["2024년 서울 출생아 수", "부산 인구", "최근 5년 혼인 건수 추이"])
def test_a_question_with_statistics_conditions_is_rag_by_rule(question):
    assert route_by_rules(question, parse_query(question)) == "rag"


@pytest.mark.parametrize("question", ["안녕하세요", "고마워요!", "넌 누구야?", "뭘 물어볼 수 있어?", "사용법 알려줘"])
def test_small_talk_with_nothing_else_is_direct_by_rule(question):
    assert route_by_rules(question, parse_query(question)) == "direct"


@pytest.mark.parametrize("question", ["안녕하세요 서울 인구 알려줘", "심심해", "this table"])
def test_anything_else_is_not_direct_by_rule(question):
    assert route_by_rules(question, parse_query(question)) != "direct"


# ---------------------------------------------------------------- Qwen을 부르는 때

def test_qwen_is_not_asked_when_the_rules_say_rag():
    qwen = FakeQwen(JudgeReply(route="direct", reply="안녕하세요"))
    out, search, stat = run("2024년 서울 출생아 수", qwen)
    assert qwen.questions == [] and out["route"] == "rag" and out["route_by"] == "rule"
    assert search.questions == ["2024년 서울 출생아 수"] and out["outcome"] == "answered"


def test_an_undecided_turn_asks_qwen_and_rag_searches():
    qwen = FakeQwen(JudgeReply(route="rag"))
    out, search, _ = run("심심해", qwen)
    assert qwen.questions == ["심심해"] and out["route"] == "rag" and out["route_by"] == "llm"
    assert search.questions == ["심심해"]


def test_an_undecided_turn_that_qwen_calls_direct_answers_without_searching():
    qwen = FakeQwen(JudgeReply(route="direct", reply="편하게 물어보세요."))
    out, search, stat = run("심심해", qwen)
    assert out["route"] == "direct" and out["outcome"] == "answered"
    assert out["messages"][-1].content == "편하게 물어보세요."
    assert search.questions == [] and stat.calls == []


def test_a_rule_direct_turn_uses_qwens_wording():
    qwen = FakeQwen(JudgeReply(route="direct", reply="안녕하세요! 궁금한 통계를 물어봐 주세요."))
    out, search, _ = run("안녕하세요", qwen)
    assert out["messages"][-1].content == "안녕하세요! 궁금한 통계를 물어봐 주세요." and out["route_by"] == "rule"
    assert search.questions == []


def test_a_follow_up_turn_never_asks_qwen():
    qwen = FakeQwen(JudgeReply(route="direct", reply="안녕"))
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=Stat({"101_A": answered("101_A")}),
                        checkpointer=new_checkpointer(), judge_llm=qwen)
    run("2024년 서울 출생아 수", graph=graph)
    out, _, _ = run("그 표에서 부산은?", graph=graph)
    assert qwen.questions == [] and out["route"] == "rag" and out["reused_table"]


# ---------------------------------------------------------------- 안전장치

@pytest.mark.parametrize("reply", ["서울 인구는 9,400,000명이에요.", "작년보다 3% 늘었어요.", "약 삼만 명이에요.", "", None])
def test_a_direct_reply_with_a_number_or_nothing_becomes_the_fixed_sentence(reply):
    assert safe_reply(reply) == DIRECT_FALLBACK


def test_a_direct_reply_with_a_number_is_not_sent():
    qwen = FakeQwen(JudgeReply(route="direct", reply="서울 인구는 940만 명이에요."))
    out, search, _ = run("심심해", qwen)
    assert out["messages"][-1].content == DIRECT_FALLBACK and search.questions == []


def test_a_qwen_failure_falls_back_to_search_or_the_fixed_sentence():
    assert decide_route("심심해", parse_query("심심해"), FakeQwen(RuntimeError("down")))[:2] == ("rag", None)
    assert decide_route("안녕하세요", parse_query("안녕하세요"), FakeQwen(RuntimeError("down")))[:2] == (
        "direct", DIRECT_FALLBACK)


def test_without_qwen_undecided_turns_search_and_small_talk_gets_the_fixed_sentence():
    out, search, _ = run("심심해")
    assert out["route"] == "rag" and out["route_by"] == "none" and search.questions == ["심심해"]
    out, search, _ = run("안녕하세요")
    assert out["messages"][-1].content == DIRECT_FALLBACK and search.questions == []


def test_a_direct_turn_keeps_the_conversation_table():
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=Stat({"101_A": answered("101_A")}),
                        checkpointer=new_checkpointer(), judge_llm=FakeQwen(JudgeReply(route="direct", reply="천만에요.")))
    first, _, _ = run("2024년 서울 출생아 수", graph=graph)
    out, _, _ = run("고마워요", graph=graph)
    assert out["messages"][-1].content == "천만에요."
    assert out["active_table"] == first["active_table"] == "101_A"


def test_the_qwen_reply_is_read_from_its_first_json_object():
    got = parse_judge_reply('```json\n{"route": "direct", "reply": "안녕하세요"}\n``` 끝')
    assert got.route == "direct" and got.reply == "안녕하세요"
    with pytest.raises(ValueError):
        parse_judge_reply("direct")


class NeverRead:
    """해석기 LLM 자리. 부르면 실패한다(인사 턴은 read_turn을 건너뛰어야 함)."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        raise AssertionError("small talk must not reach the interpreter LLM")


def test_small_talk_in_a_conversation_skips_the_interpreter_llm():
    reader = NeverRead()
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=Stat({"101_A": answered("101_A")}),
                        checkpointer=new_checkpointer(), llm=reader,
                        judge_llm=FakeQwen(JudgeReply(route="direct", reply="천만에요.")))
    run("2024년 서울 출생아 수", graph=graph)
    out, _, _ = run("고마워요", graph=graph)
    assert reader.calls == [] and out["route"] == "direct" and out["messages"][-1].content == "천만에요."


def test_an_undecided_turn_in_a_conversation_does_not_ask_qwen():
    qwen = FakeQwen(JudgeReply(route="direct", reply="안녕"))
    graph = build_graph(Search(["101_A"]), STAT_LLM, summarize=Stat({"101_A": answered("101_A")}),
                        checkpointer=new_checkpointer(), judge_llm=qwen)
    run("2024년 서울 출생아 수", graph=graph)
    out, _, _ = run("심심해", graph=graph)
    assert qwen.questions == [] and out["route"] == "rag"
