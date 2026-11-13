"""T3-23: summarize_stat refuses a table over max_table_rows without calling phase 1 (the 11/10 e2e froze the PC on
one). Offline: a fake phase 1 call and a fake row count."""

import pytest

from kosis_agent.services.statistics import TABLE_TOO_LARGE_TEXT, make_summarize_stat_tool
from kosis_ai.schemas import Refusal, StatAnswer
from kosis_chat.schemas.io import StatOutput
from tests.test_agent_graph import STAT_LLM

# what the fake phase 1 returns (any valid StatAnswer; a refusal needs no parts)
ANSWER = StatAnswer(outcome="refused", text="1단계 답", refusal=Refusal(reason="out_of_scope", message="1단계 답"))


def _call(rows, limit):
    calls = []

    def summarize(question, table_key, **kw):
        calls.append(table_key)
        return StatOutput(answer=ANSWER, chart=None, spans=[])

    tool = make_summarize_stat_tool(STAT_LLM, summarize=summarize, max_table_rows=limit, count_rows=lambda key: rows)
    msg = tool.invoke({"name": "summarize_stat", "args": {"question": "q", "table_key": "T1"}, "id": "1",
                       "type": "tool_call"})
    return msg.artifact, calls


def test_a_table_over_the_limit_is_refused_without_phase_1():
    stat, calls = _call(rows=1_000_001, limit=1_000_000)
    assert calls == []
    assert stat.answer.outcome == "refused" and stat.answer.text == TABLE_TOO_LARGE_TEXT
    assert stat.answer.refusal.reason == "out_of_scope"
    assert stat.spans[0]["name"] == "table_size" and stat.spans[0]["error"].startswith("TableTooLarge: T1")


@pytest.mark.parametrize("rows, limit", [(1_000_000, 1_000_000), (5, 1_000_000), (None, 1_000_000), (10**9, 0)])
def test_phase_1_runs_at_or_under_the_limit_unknown_size_or_no_limit(rows, limit):
    stat, calls = _call(rows=rows, limit=limit)
    assert calls == ["T1"] and stat.answer == ANSWER
