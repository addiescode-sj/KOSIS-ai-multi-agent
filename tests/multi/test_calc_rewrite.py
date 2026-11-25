"""S4b: ③과 동시에 도는 계산용 질문 다듬기. 오프라인(가짜 검색·실전 1·Qwen)."""

from datetime import date

import pytest

from kosis_multi.agents.rewrite import RewriteReply, parse_rewrite_reply, safe_rewrite, system_prompt
from kosis_multi.orchestrator import build_graph
from kosis_rag.parse.node import parse_query
from tests.test_workflow import STAT_LLM, Search, Stat, answered


class FakeRewrite:
    def __init__(self, reply: RewriteReply | Exception):
        self.reply, self.questions = reply, []

    def __call__(self, question: str) -> RewriteReply:
        self.questions.append(question)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def run(question, rewrite, search=None):
    search = search or Search(["101_A"])
    stat = Stat({"101_A": answered("101_A")})
    graph = build_graph(search, STAT_LLM, summarize=stat, rewrite_llm=rewrite)
    out = graph.invoke({"messages": [("user", question)]}, config={"configurable": {"thread_id": "t"}})
    return out, stat


# ---------------------------------------------------------------- 코드 검사

@pytest.mark.parametrize("original, rewrite", [
    ("작년 서울 출생아 수", "2025년 서울 출생아수"),  # 상대 시점 → 연도
    ("10년 전 대구 인구", "2016년 대구 인구"),  # "10"은 상대 시점의 숫자라 사라져도 됨
    ("시울 인구 알려줘", "서울 인구 알려줘"),  # 원문에 시도가 없으면 새 시도 허용(오타)
    ("2023년 부산에서 태어난 아기는 몇 명이야", "2023년 부산 출생아수는 몇 명이야"),
])
def test_a_rewrite_that_keeps_the_conditions_passes(original, rewrite):
    assert safe_rewrite(original, rewrite, parse_query) == rewrite


@pytest.mark.parametrize("original, rewrite", [
    ("2024년 서울 출생아 수", "2023년 서울 출생아수"),  # 명시 연도를 바꿈
    ("서울 출생아 수", "2025년 서울 출생아수"),  # 상대 시점이 없는데 연도를 만듦
    ("2024년 서울 출생아 수", "2024년 부산 출생아수"),  # 인식된 시도를 바꿈
    ("최근 5년 부산 혼인 건수", "최근 3년 부산 혼인건수"),  # 숫자를 바꿈
    ("2024년 서울 출생아 수", "2024년 서울 출생아 수"),  # 같음
    ("2024년 서울 출생아 수", "  "),
    ("2024년 서울 출생아 수", None),
])
def test_a_rewrite_that_changes_a_condition_is_dropped(original, rewrite):
    assert safe_rewrite(original, rewrite, parse_query) is None


def test_the_prompt_has_todays_years_and_no_placeholder_left():
    text = system_prompt(date(2026, 11, 24))
    assert "2026-11-24" in text and "2025년 서울 출생아수" in text and "2016년 대구 인구" in text
    assert "{last_year}" not in text and "{today}" not in text


def test_the_reply_is_read_from_its_first_json_object():
    got = parse_rewrite_reply('```json\n{"reason": "작년은 2025년", "rewrite": "2025년 서울 인구"}\n```')
    assert got.rewrite == "2025년 서울 인구" and got.reason
    assert parse_rewrite_reply('{"reason": "고칠 것 없음", "rewrite": null}').rewrite is None


# ---------------------------------------------------------------- 그래프

def test_calculation_uses_the_rewrite_and_the_answer_says_so():
    rewrite = FakeRewrite(RewriteReply(reason="작년 = 2025년", rewrite="2025년 서울 출생아수"))
    out, stat = run("작년 서울 출생아 수", rewrite)
    assert rewrite.questions == ["작년 서울 출생아 수"]
    assert stat.calls == [("2025년 서울 출생아수", "101_A")]
    assert out["calc_question"] == "2025년 서울 출생아수"
    assert out["messages"][-1].content.endswith('(질문을 "2025년 서울 출생아수"(으)로 읽고 답했어요.)')
    assert out["active_question"] == "작년 서울 출생아 수"  # 대화에는 사용자 원 질문을 남긴다


@pytest.mark.parametrize("reply", [RewriteReply(reason="고칠 것 없음", rewrite=None), RuntimeError("down"),
                                   RewriteReply(reason="연도를 바꿈", rewrite="2023년 서울 출생아수")])
def test_no_rewrite_a_failure_or_a_bad_rewrite_keeps_the_original(reply):
    out, stat = run("2024년 서울 출생아 수", FakeRewrite(reply))
    assert stat.calls == [("2024년 서울 출생아 수", "101_A")] and out["calc_question"] is None
    assert "읽고 답했어요" not in out["messages"][-1].content


def test_a_refused_search_does_not_calculate_with_the_rewrite():
    rewrite = FakeRewrite(RewriteReply(reason="작년 = 2025년", rewrite="2025년 서울 출생아수"))
    out, stat = run("작년 서울 출생아 수", rewrite, search=Search([], route="refuse"))
    assert stat.calls == [] and out["outcome"] == "refused"


def test_without_the_rewriter_nothing_changes():
    out, stat = run("작년 서울 출생아 수", None)
    assert stat.calls == [("작년 서울 출생아 수", "101_A")] and out["calc_question"] is None
