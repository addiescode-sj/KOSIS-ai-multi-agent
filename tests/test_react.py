"""T3-03 옵션 B: 단계별 ReAct 판단 (understand → rewrite ⟲1 → search·검수 → statistics → chart → review → finalize).

오프라인 테스트다. 실전 1·2는 tests/test_workflow.py의 가짜를, Qwen 판단은 정해 둔 답을 돌려주는 FakeJudge를 쓴다.
확인하는 것:
- understand는 매 턴 Qwen이 먼저 판단한다(규칙 판단 없음). 템플릿 즉시 답변이면 툴을 하나도 부르지 않는다
- 되묻기 → 다음 턴 답으로 재작성 → 재작성 문장으로 검색
- 재작성 충분성: 불충분이면 1회만 더 쓰고, 그래도 불충분이면 되묻기로 끝난다
- 재작성 문장이 원문에 없는 숫자를 쓰면 버린다(grounded)
- 검색 거절(no_item)이면 재작성 → 재검색 1회, out_of_scope면 재검색하지 않는다
- 분해 검토(merge), 검수(출처 줄), 판단 끔(off), 판단 장애 시 규칙 경로
"""

from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.multiquery import split_by_rules
from kosis_agent.graph.react import RewriteStep, SplitStep, SufficiencyStep, UnderstandStep, grounded
from kosis_agent.graph.state import chat_result, new_checkpointer
from kosis_chat.schemas.io import StatOutput
from kosis_rag.parse.node import parse_query
from kosis_rag.schemas.io import SearchOutput
from tests.test_workflow import STAT_LLM, Search, Stat, answered, table


class FakeJudge:
    """Qwen 대신: 메서드별로 정해 둔 결과를 차례로 돌려준다(값이 예외면 낸다). 호출을 기록한다."""

    def __init__(self, understand=None, rewrite=(), sufficiency=(), split=None):
        self.answers = {"understand": [understand], "rewrite": list(rewrite), "sufficiency": list(sufficiency),
                        "split": [split]}
        self.calls: list[str] = []

    def _next(self, name):
        self.calls.append(name)
        out = self.answers[name].pop(0) if len(self.answers[name]) > 1 else self.answers[name][0]
        if isinstance(out, Exception):
            raise out
        return out

    def understand(self, question, previous):
        return self._next("understand")

    def rewrite(self, source, feedback):
        return self._next("rewrite")

    def sufficiency(self, question):
        return self._next("sufficiency")

    def split(self, question, subs):
        return self._next("split")


class RefuseThenFind(Search):
    """첫 검색은 gate 거절(reason), 그다음은 found."""

    def __init__(self, reason: str, picked: list[str]):
        super().__init__(picked)
        self.reason = reason

    def stream(self, inp):
        self.questions.append(inp.question)
        if len(self.questions) == 1:
            yield "output", SearchOutput(question=inp.question, route="refuse", picked=[], candidates=[table("101_X")],
                                         refusal_reason=self.reason, refusal_text="맞는 표가 없어요.")
        else:
            yield "output", SearchOutput(question=inp.question, route="found", picked=[table(k) for k in self.picked],
                                         candidates=[table(k) for k in self.picked])


class AlwaysRefuse(Search):
    """매번 gate 거절(no_item)."""

    def stream(self, inp):
        self.questions.append(inp.question)
        yield "output", SearchOutput(question=inp.question, route="refuse", refusal_reason="no_item",
                                     refusal_text="없어요.")


def graph(search, stat, judge=None, steps="b", **kw):
    return build_graph(search, STAT_LLM, summarize=stat, judge=judge, steps=steps, **kw)


def ask(g, question, thread="t"):
    return g.invoke({"messages": [("user", question)]}, {"configurable": {"thread_id": thread}})


# ---------------------------------------------------------------- understand
def test_a_greeting_ends_with_the_template_and_no_tool_call():
    judge = FakeJudge(understand=UnderstandStep(thought="인사", action="template", kind="greeting"))
    search, stat = Search(["101_A"]), Stat({})
    out = graph(search, stat, judge).invoke({"messages": [("user", "안녕! 넌 뭘 할 수 있어?")]})
    r = chat_result(out)
    assert search.questions == [] and stat.calls == []
    assert r.outcome == "refused" and r.route == "skip" and r.intent == "template" and "KOSIS" in r.answer
    assert out["refusal_reason"] == "out_of_scope" and out["trace"][0]["by"] == "llm"


def test_the_judge_decides_every_turn_even_a_clear_statistics_question():
    judge = FakeJudge(understand=UnderstandStep(thought="지표·지역·기간 있음", action="search"))
    search, stat = Search(["101_A"]), Stat({"101_A": answered("101_A")})
    out = graph(search, stat, judge).invoke({"messages": [("user", "2024년 서울 출생아 수")]})
    assert judge.calls == ["understand"] and search.questions == ["2024년 서울 출생아 수"]  # 새 질문은 원문으로 검색
    assert out["outcome"] == "answered" and [s["step"] for s in out["trace"]] == ["understand", "verify", "answer", "review"]


def test_without_a_judge_a_greeting_goes_to_search_like_before_react():
    search = Search(["101_A"])
    out = graph(search, Stat({"101_A": answered("101_A")})).invoke({"messages": [("user", "안녕하세요")]})
    assert search.questions == ["안녕하세요"] and out["trace"][0]["by"] == "none"


def test_a_follow_up_turn_is_judged_then_reuses_the_previous_table():
    judge = FakeJudge(understand=UnderstandStep(thought="이어 묻기", action="search"))
    search, stat = Search(["101_A"]), Stat({"101_A": answered("101_A")})
    g = graph(search, stat, judge, checkpointer=new_checkpointer())
    ask(g, "2024년 서울 출생아 수")
    second = ask(g, "그 표에서 부산은?")
    assert judge.calls == ["understand", "understand"] and search.questions == ["2024년 서울 출생아 수"]
    assert [s["step"] for s in second["trace"]][:2] == ["understand", "context"] and second["reused_table"]


def test_clarify_then_the_reply_is_rewritten_and_searched():
    judge = FakeJudge(understand=UnderstandStep(thought="무엇인지 모름", action="clarify", missing=["지표"]),
                      rewrite=[RewriteStep(thought="답을 채움", question="서울 출생아 수")],
                      sufficiency=[SufficiencyStep(thought="지표 분명", action="sufficient")])
    search, stat = Search(["101_A"]), Stat({"101_A": answered("101_A")})
    g = graph(search, stat, judge, checkpointer=new_checkpointer())
    first = ask(g, "그거 알려줘")
    assert first["outcome"] == "clarify" and first["pending_clarify"] == "그거 알려줘" and search.questions == []
    assert "지표" in first["messages"][-1].content and chat_result(first).route == "skip"
    second = ask(g, "서울 출생아 수")
    assert search.questions == ["서울 출생아 수"] and second["outcome"] == "answered"
    assert second["pending_clarify"] is None and second["trace"][0]["thought"] == "되묻기에 대한 답"


def test_an_insufficient_rewrite_is_retried_once_then_clarifies():
    judge = FakeJudge(understand=UnderstandStep(thought="다듬어야 함", action="rewrite"),
                      rewrite=[RewriteStep(thought="1", question="그거")],
                      sufficiency=[SufficiencyStep(thought="지표 없음", action="retry", missing=["지표"])])
    search, stat = Search(["101_A"]), Stat({})
    out = graph(search, stat, judge).invoke({"messages": [("user", "그거")]})
    assert judge.calls.count("rewrite") == 2 and judge.calls.count("sufficiency") == 2
    assert search.questions == [] and out["outcome"] == "clarify" and out["rewrite_tries"] == 2


def test_a_rewrite_with_an_invented_number_is_dropped():
    assert not grounded("2024년 서울 출생아 수", "질문: 서울 출생아 수", parse_query)
    assert grounded("서울 출생아 수", "질문: 서울 출생아 수", parse_query)
    judge = FakeJudge(understand=UnderstandStep(thought="다듬기", action="rewrite"),
                      rewrite=[RewriteStep(thought="연도 추가", question="2024년 그거 수")],
                      sufficiency=[SufficiencyStep(thought="ok", action="sufficient")])
    search, stat = Search(["101_A"]), Stat({"101_A": answered("101_A")})
    out = graph(search, stat, judge).invoke({"messages": [("user", "그거 수")]})
    assert search.questions == ["그거 수"]  # LLM 문장을 버리고 원문으로
    assert out["trace"][1] == {"step": "rewrite", "action": "rewrite", "by": "rule", "thought": "원문(·되묻기 답)을 그대로 이어 씀"}


# ---------------------------------------------------------------- 검색 검수 · 재검색
def test_a_no_item_refusal_is_rewritten_and_searched_once_more():
    judge = FakeJudge(rewrite=[RewriteStep(thought="표현 바꿈", question="서울 출생아 수")],
                      sufficiency=[SufficiencyStep(thought="ok", action="sufficient")])
    search, stat = RefuseThenFind("no_item", ["101_A"]), Stat({"101_A": answered("101_A")})
    out = graph(search, stat, judge).invoke({"messages": [("user", "서울에서 태어난 아기 출생아 수")]})
    assert len(search.questions) == 2 and search.questions[1] == "서울 출생아 수"
    assert out["outcome"] == "answered" and out["researched"]


def test_a_second_refusal_ends_without_more_tools():
    judge = FakeJudge(rewrite=[RewriteStep(thought="t", question="서울 출생아 수")],
                      sufficiency=[SufficiencyStep(thought="ok", action="sufficient")])
    search, stat = AlwaysRefuse([]), Stat({})
    out = graph(search, stat, judge).invoke({"messages": [("user", "서울 출생아 수 알려줘")]})
    assert len(search.questions) == 2 and stat.calls == [] and out["outcome"] == "refused"


def test_an_out_of_scope_refusal_is_not_searched_again():
    judge = FakeJudge(rewrite=[AssertionError("no rewrite")])
    search, stat = RefuseThenFind("out_of_scope", ["101_A"]), Stat({})
    out = graph(search, stat, judge).invoke({"messages": [("user", "비트코인 가격 알려줘")]})
    assert len(search.questions) == 1 and stat.calls == [] and out["outcome"] == "refused"
    assert out["trace"][-1]["action"] == "unanswerable"


# ---------------------------------------------------------------- 분해 검토 · 검수 · 끔 · 장애
def test_the_judge_merges_an_over_split_comparison():
    question = "2024년 출생아 수는? 그리고 2024년 사망자 수는?"
    assert len(split_by_rules(question, parse_query).sub_questions) == 2  # 규칙은 둘로 나눈다
    judge = FakeJudge(split=SplitStep(thought="같은 지표 비교", action="merge"))
    search, stat = Search(["101_A"]), Stat({"101_A": answered("101_A")})
    out = graph(search, stat, judge).invoke({"messages": [("user", question)]})
    assert judge.calls == ["understand", "split"] and out["sub_questions"] == [] and search.questions == [question]
    kept = Search(["101_A"])  # keep이면 하위 질문마다 검색한다(같은 표면 multi가 원문 한 번으로 되돌림, T3-28)
    graph(kept, Stat({"101_A": answered("101_A")}), FakeJudge(split=SplitStep(thought="다른 지표", action="keep"))).invoke(
        {"messages": [("user", question)]})
    assert len(kept.questions) == 2


def test_review_adds_the_source_line_when_phase1_left_it_out():
    bare = StatOutput(answer=answered("101_A").answer.model_copy(update={"text": "101_A: 100명"}))
    out = graph(Search(["101_A"]), Stat({"101_A": bare})).invoke({"messages": [("user", "2024년 서울 출생아 수")]})
    assert out["messages"][-1].content.endswith("출처: KOSIS / 조사 / 표 A") and not out["review"]["source"]
    assert chat_result(out).answer == out["messages"][-1].content


def test_steps_off_never_calls_the_judge():
    judge = FakeJudge(understand=AssertionError("off"))
    out = graph(Search(["101_A"]), Stat({"101_A": answered("101_A")}), judge, steps="off").invoke(
        {"messages": [("user", "요즘 어때?")]})
    assert judge.calls == [] and out["trace"][0]["by"] == "none"


def test_a_judge_failure_falls_back_to_search():
    judge = FakeJudge(understand=RuntimeError("server down"))
    search = Search(["101_A"])
    out = graph(search, Stat({"101_A": answered("101_A")}), judge).invoke({"messages": [("user", "요즘 어때?")]})
    assert search.questions == ["요즘 어때?"] and out["intent"] == "search"


def test_a_null_or_unknown_missing_slot_keeps_the_judgment():
    from kosis_agent.graph.react import SufficiencyStep, parse_step

    got = parse_step('{"thought": "t", "action": "sufficient", "missing": null}', SufficiencyStep)
    assert got.action == "sufficient" and got.missing == []
    assert parse_step('{"thought": "t", "action": "retry", "missing": ["연도", "지표"]}', SufficiencyStep).missing == ["지표"]
