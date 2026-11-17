"""T3-38 / KOS-199: per sub-question refusal in a multi-query turn (refuse_parts)."""

from types import SimpleNamespace

import pytest
from kosis_ai.schemas import Fact

from evals.builders import multiquery
from evals.evaluators import agent
from evals.schemas import AgentScenario, CalcItem, TurnExpect

REF = "DT_X|T1|11|2024"
CELLS = {REF: SimpleNamespace(value=100.0)}
ANSWERABLE = CalcItem(qid="c1", question="2024년 서울 출생아 수는?", table_key="101_DT_X", table_sha256="0",
                      kind="answerable",
                      required_facts=[Fact(id="f1", value=100.0, source_ref=REF, unit="명", period="2024")])
REFUSING = CalcItem(qid="r1", question="2003년 충청북도 인구밀도는?", table_key="101_DT_Y", table_sha256="0",
                    kind="not_answerable", allowed_reasons=["no_period"])
CALC = {i.qid: i for i in (ANSWERABLE, REFUSING)}
cells_of = lambda key, refs=None: CELLS  # noqa: E731

SEARCH = {"name": "search_tables"}
STAT_X = {"name": "summarize_stat", "args": {"table_key": "101_DT_X"}}
STAT_Y = {"name": "summarize_stat", "args": {"table_key": "101_DT_Y"}}
QUESTION = f"{ANSWERABLE.question} 그리고 {REFUSING.question}"


def scenario(**expect) -> AgentScenario:
    return AgentScenario.model_validate({"sid": "MQ-R-001", "turns": [{"question": QUESTION, "expect": expect}]})


PARTIAL = scenario(tools=[SEARCH, SEARCH, STAT_X, STAT_Y], calc_qids=["c1"], refuse_parts=[False, True])
OLD_RULE = scenario(tools=[SEARCH, SEARCH, STAT_X, STAT_Y], calc_qids=["c1"])  # the same turn without refuse_parts


def grade(s: AgentScenario, calls: list[dict]) -> dict:
    # the answered part names its source like phase 1's text does (T3-40 missing_source); oracle_stat cites "oracle"
    answer = "출처: KOSIS / oracle / oracle"
    return agent.grade_conversation(s, [{"calls": calls, "answer": answer, "error": None}], CALC, cells_of)[0]


def oracle_calls(s: AgentScenario = PARTIAL) -> list[dict]:
    return agent.oracle_records(s, CALC)[0]["calls"]


def test_oracle_partial_refusal_passes_and_refuses_the_second_part():
    calls = oracle_calls()
    assert [c["result"].get("route") or c["result"].get("outcome") for c in calls] == [
        "found", "found", "answered", "refused"]
    row = grade(PARTIAL, calls)
    assert row["errors"] == [] and row["refused_parts"] == [False, True] and row["sub_calc"][0]["verdict"] == "PASS"


def test_old_rule_calls_the_same_turn_a_false_refusal():
    """Absent refuse_parts the turn-level rule is unchanged: any refusing call refuses the whole turn."""
    row = grade(OLD_RULE, oracle_calls())
    assert row["errors"] == ["false_refusal"] and "refused_parts" not in row


def test_answering_the_refusing_part_is_missed_refusal_with_its_index():
    calls = oracle_calls()
    calls[3] = {**calls[3], "result": {"outcome": "answered"}}
    row = grade(PARTIAL, calls)
    assert row["errors"] == ["missed_refusal"] and row["refusal_errors"] == [{"part": 1, "error": "missed_refusal"}]


def test_refusing_the_answerable_part_is_false_refusal_with_its_index():
    calls = oracle_calls()
    calls[2] = {**calls[2], "result": {"outcome": "refused"}}
    row = grade(PARTIAL, calls)
    assert row["refusal_errors"] == [{"part": 0, "error": "false_refusal"}]
    # known limit: final_calls reads "refused on X, then a call on Y" as an alternate-table attempt (T3-23), so the
    # first call drops out (missing_call) and the turn falls back to the whole-turn refusal; c1 has no answer
    assert row["errors"] == ["missing_call", "wrong_args", "false_refusal", "wrong_numbers"]
    # its search refusing instead keeps one call per part: graded per part, part 1 answering is not an error
    by_search = grade(PARTIAL, [{**calls[0], "result": {"route": "refuse", "picked": []}}, calls[1],
                                {**calls[3], "result": {"outcome": "refused"}}])
    assert by_search["refused_parts"] == [True, True]
    assert by_search["refusal_errors"] == [{"part": 0, "error": "false_refusal"}]


def test_a_search_refusal_counts_for_its_own_part():
    """The graph skips summarize_stat for a sub-question whose search refused (main_graph multi_node)."""
    calls = oracle_calls()
    refused_search = {**calls[1], "result": {"route": "refuse", "picked": []}}
    row = grade(PARTIAL, [calls[0], refused_search, calls[2]])
    assert row["refused_parts"] == [False, True] and row["refusal_errors"] == []
    assert row["sub_calc"][0]["verdict"] == "PASS"  # c1 still graded on the first part's answer
    stop_at_search = scenario(tools=[SEARCH, SEARCH, STAT_X], calc_qids=["c1"], refuse_parts=[False, True])
    assert grade(stop_at_search, oracle_calls(stop_at_search))["errors"] == []


def test_a_turn_that_was_not_split_takes_the_whole_turn_refusal_for_every_part():
    calls = oracle_calls()
    answered_whole = grade(PARTIAL, [calls[0], calls[2]])
    assert answered_whole["refused_parts"] == [False, False] and "missed_refusal" in answered_whole["errors"]
    refused_whole = grade(PARTIAL, [{**calls[0], "result": {"route": "refuse", "picked": []}}])
    assert refused_whole["refused_parts"] == [True, True] and "false_refusal" in refused_whole["errors"]


def test_refuse_parts_is_validated_and_left_out_of_dumps_when_absent():
    with pytest.raises(ValueError):  # one search per sub-question
        TurnExpect.model_validate({"tools": [SEARCH, STAT_X, STAT_Y], "refuse_parts": [False, True]})
    with pytest.raises(ValueError):  # calc_qids only for the answering parts
        TurnExpect.model_validate({"tools": [SEARCH, SEARCH, STAT_X, STAT_Y], "calc_qids": ["c1", "r1"],
                                   "refuse_parts": [False, True]})
    with pytest.raises(ValueError):  # the turn-level flag and the per-part list do not mix
        TurnExpect.model_validate({"tools": [SEARCH, SEARCH, STAT_X, STAT_Y], "refuse": True,
                                   "refuse_parts": [False, True]})
    assert "refuse_parts" not in OLD_RULE.model_dump_json() and '"refuse_parts":[false,true]' in PARTIAL.model_dump_json()


def test_builder_partial_refusal_kind_is_oracle_clean(tmp_path):
    calc = tmp_path / "calc.jsonl"
    calc.write_text("".join(i.model_dump_json() + "\n" for i in CALC.values()), encoding="utf-8")
    rows = multiquery.partial_refusals(calc_path=calc)
    assert [s.sid for s in rows] == ["MQ-R-001"] and rows[0].turns[0].question == QUESTION
    expect = rows[0].turns[0].expect
    assert expect.refuse_parts == [False, True] and expect.calc_qids == ["c1"]
    graded = agent.grade_conversation(rows[0], agent.oracle_records(rows[0], CALC), CALC, cells_of)
    assert agent.aggregate(graded)["turn_pass"] == 1.0
