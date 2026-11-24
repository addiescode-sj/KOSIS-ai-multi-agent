"""T3-51 옵션 B: 2차 리랭커의 멀티표 판단(v4m together) → 실전 1에 표 2~3개.

오프라인 테스트다. 2차 LLM은 정해 둔 답을 돌려주는 가짜 client, 실전 1은 표 키(들)별 가짜로 바꾼다.
확인하는 것:
- together 파서: 2~3개·서로 다름·모두 picked 안일 때만 받고, 하나라도 틀리면 통째로 버림
- apply: v4m이면 실전 2 등록부의 hcx·gpt가 TogetherSelector, v4면 원래대로
- 그래프: together가 있으면 summarize_stat(table_keys)로 한 번, 멀티 답이 거절이면 첫 표 하나로 다시 물음,
  together가 없으면(v4) 예전과 같은 인자로 한 번
"""

from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.state import chat_result
from kosis_agent.retrieval.together import (TOGETHER_FIELD, TogetherSelector, apply, parse_together, prompt_text,
                                            together_keys, with_together)
from kosis_rag.llm.base import LLMResult
from kosis_rag.rerank import selectors
from kosis_rag.rerank.hcx import system_prompt
from kosis_rag.schemas.io import SearchOutput
from tests.test_workflow import STAT_LLM, Search, Stat, answered, refused, table


# ---------------------------------------------------------------- 파서

def test_together_is_read_only_inside_picked():
    assert parse_together('{"picked": [3, 1], "reason": null, "together": [3, 1]}', [3, 1]) == [3, 1]
    assert parse_together('{"picked": [3, 1, 2], "together": ["2", "3"]}', [3, 1, 2]) == [2, 3]


def test_a_wrong_together_falls_back_to_one_table():
    for reply, picked in [
        ('{"picked": [3, 1], "together": null}', [3, 1]),
        ('{"picked": [3, 1], "together": [3]}', [3, 1]),  # 하나는 묶음이 아님
        ('{"picked": [3, 1], "together": [3, 3]}', [3, 1]),  # 중복
        ('{"picked": [3, 1], "together": [3, 2]}', [3, 1]),  # picked 밖 번호
        ('{"picked": [1, 2, 3, 4], "together": [1, 2, 3, 4]}', [1, 2, 3, 4]),  # 실전 1은 최대 3표
        ('{"picked": [1, 2], "together": [1.5, 2]}', [1, 2]),
        ('{"picked": [1, 2]}', [1, 2]),  # v4 형식 답
        ("no json", [1, 2]),
    ]:
        assert parse_together(reply, picked) is None, reply


# ---------------------------------------------------------------- 프롬프트·등록

def test_v4m_keeps_the_v4_rules_byte_for_byte():
    v4, v4m = system_prompt("stage2-hcx-v4.md"), prompt_text("v4m")
    head = v4.split("# 출력 형식")[0]
    assert v4m.startswith(head)  # 1~4절은 그대로, 5절·출력 형식·예시만 다름
    assert '"together"' in v4m and "# 5. 함께 읽어야 하는 표" in v4m


def test_apply_swaps_and_restores_the_phase_2_selectors():
    original = dict(selectors.LLM_RERANKERS)
    try:
        assert apply("v4m") == "v4m"
        assert isinstance(selectors.LLM_RERANKERS["hcx"]({}, None), TogetherSelector)
        assert selectors.LLM_RERANKERS["clova"] is original["clova"]  # 번호를 고르지 않는 reranker는 그대로
        assert apply(None) == "v4" and selectors.LLM_RERANKERS == original
    finally:
        selectors.LLM_RERANKERS.update(original)


def test_unknown_prompt_version_is_an_error():
    try:
        apply("v9")
    except ValueError as error:
        assert "STAGE2_PROMPT" in str(error)
    else:
        raise AssertionError("expected ValueError")
    finally:
        apply(None)


class FakeClient:
    def __init__(self, text: str):
        self.text, self.prompts = text, []

    def generate(self, system, user):
        self.prompts.append(system)
        return LLMResult(text=self.text)


def test_the_selector_returns_picked_and_remembers_together_once():
    client = FakeClient('{"picked": [2, 1], "reason": null, "together": [2, 1]}')
    select = TogetherSelector(text_of={"A": "a", "B": "b", "C": "c"}, client=client, prompt="P")
    out = select("질문", ["A", "B", "C"])
    assert out.keys == ["B", "A"] and client.prompts == ["P"]
    assert select.take_together() == ["B", "A"] and select.take_together() == []


# ---------------------------------------------------------------- 검색 결과에 싣기

def found(picked, together_of=None):
    spans = [{"node": "rerank2", "stage2_picked_n": len(picked)}]
    out = SearchOutput(question="q", route="found", picked=[table(k) for k in picked],
                       candidates=[table(k) for k in picked], spans=spans)
    return with_together(out, together_of) if together_of else out


def test_together_keys_checks_the_search_output_again():
    assert together_keys(found(["101_A", "101_B"], ["101_A", "101_B"])) == ["101_A", "101_B"]
    assert together_keys(found(["101_A", "101_B"])) == []  # v4: 판단 없음
    assert together_keys(found(["101_A"], ["101_A", "101_X"])) == []  # picked 밖
    refused_out = found(["101_A", "101_B"], ["101_A", "101_B"]).model_copy(update={"route": "refuse"})
    assert together_keys(refused_out) == []


class TogetherSearch(Search):
    """실전 2 대신: 2차 selector(resources.select)가 together 판단을 들고 있는 검색."""

    def __init__(self, picked, together_of):
        super().__init__(picked, spans=[{"node": "rerank2"}])
        select = TogetherSelector(text_of={}, client=FakeClient("{}"), prompt="")
        self.resources = type("R", (), {"select": select, "stage2_llm": None})()
        self.together_of = together_of

    def stream(self, inp):
        self.resources.select._remember(list(self.together_of))  # 실전 2 rerank2가 selector를 부른 것처럼
        yield from super().stream(inp)


class Many:
    """실전 1 summarize_stats_run 대신: 표 키 묶음별 결과."""

    def __init__(self, by_keys: dict):
        self.by_keys, self.calls = by_keys, []

    def __call__(self, question, table_ids, *, llm=None, retry_interval=0.0):
        self.calls.append((question, tuple(table_ids)))
        return self.by_keys[tuple(table_ids)]


def run(search, stat, many, question="2023년 서울 혼인 100건당 이혼 건수"):
    graph = build_graph(search, STAT_LLM, summarize=stat, summarize_many=many)
    return graph.invoke({"messages": [("user", question)]})


def test_a_together_verdict_asks_phase_1_once_with_every_table():
    stat, many = Stat({}), Many({("101_A", "101_B"): answered("101_A")})
    out = run(TogetherSearch(["101_A", "101_B", "101_C"], ["101_A", "101_B"]), stat, many)
    r = chat_result(out)
    assert many.calls == [("2023년 서울 혼인 100건당 이혼 건수", ("101_A", "101_B"))] and stat.calls == []
    assert r.outcome == "answered" and r.table_key == "101_A" and r.attempted_keys == ["101_A+101_B"]


def test_a_refused_multi_answer_asks_the_first_table_alone():
    stat = Stat({"101_A": answered("101_A")})
    many = Many({("101_A", "101_B"): refused("101_A", "no_item")})
    r = chat_result(run(TogetherSearch(["101_A", "101_B"], ["101_A", "101_B"]), stat, many))
    assert len(many.calls) == 1 and [k for _, k in stat.calls] == ["101_A"]
    assert r.outcome == "answered" and r.table_key == "101_A" and r.attempted_keys == ["101_A+101_B", "101_A"]


def test_without_a_verdict_the_tool_arguments_are_unchanged():
    stat, many = Stat({"101_A": answered("101_A")}), Many({})
    search = TogetherSearch(["101_A", "101_B"], [])
    graph = build_graph(search, STAT_LLM, summarize=stat, summarize_many=many)
    out = graph.invoke({"messages": [("user", "2024년 서울 출생아 수")]})
    assert many.calls == [] and [k for _, k in stat.calls] == ["101_A"]
    assert all(TOGETHER_FIELD not in s for s in out["search"].spans)  # v4와 같은 span


def test_the_one_table_tool_call_has_no_table_keys_argument():
    """한 표 회귀 0: 하네스가 채점하는 summarize_stat 인자가 예전과 같다."""
    from kosis_agent.services.statistics import make_summarize_stat_tool

    seen = []
    tool = make_summarize_stat_tool(STAT_LLM, summarize=lambda q, k, **kw: seen.append(k) or answered(k),
                                    summarize_many=lambda q, ks, **kw: seen.append(tuple(ks)) or answered(ks[0]))
    tool.invoke({"question": "q", "table_key": "101_A"})
    tool.invoke({"question": "q", "table_key": "101_A", "table_keys": ["101_A", "101_B"]})
    tool.invoke({"question": "q", "table_key": "101_A", "table_keys": ["101_A"]})  # 하나면 한 표 경로
    assert seen == ["101_A", ("101_A", "101_B"), "101_A"]
