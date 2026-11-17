"""T3-06 / KOS-120: agent metrics (tool order, missing / extra calls, args, multi-turn, numbers) and the span records."""

from types import SimpleNamespace

import pytest

from kosis_ai.schemas import Fact, Source
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import START, MessagesState, StateGraph

from evals.evaluators import agent
from evals.runners import agent as runner
from evals.schemas import AgentScenario, CalcItem

REF = "DT_X|T1|11|2024"
CELLS = {REF: SimpleNamespace(value=100.0)}
CALC = {"c1": CalcItem(qid="c1", question="q", table_key="101_DT_X", table_sha256="0", kind="answerable",
                       required_facts=[Fact(id="f1", value=100.0, source_ref=REF, unit="명", period="2024")])}
cells_of = lambda key, refs=None: CELLS  # noqa: E731
CITE = "출처: KOSIS / oracle / oracle"  # what oracle_stat cites (T3-40: every answer names its source)
ALT_CITE = "출처: KOSIS / 조사 / 표"
table_of = lambda key: frozenset(c.value for c in CELLS.values())  # noqa: E731  (chart context rows, T3-37)


def scenario(*turns, sid="s1") -> AgentScenario:
    return AgentScenario.model_validate({"sid": sid, "turns": [{"question": q, "expect": e} for q, e in turns]})


SEARCH = {"name": "search_tables"}
STAT = {"name": "summarize_stat", "args": {"table_key": "101_DT_X", "regions": ["서울특별시"]}}
CHAT = scenario(
    ("2024년 서울 출생아 수 찾아서 계산해줘", {"tools": [SEARCH, STAT], "table_key": "101_DT_X", "calc_qid": "c1"}),  # multi-query
    ("그 표로 그래프 그려줘", {"tools": [{"name": "make_chart", "args": {"chart_type": "line"}}], "table_key": "101_DT_X"}),  # reference
    ("오늘 날씨 어때?", {"tools": [], "refuse": True}),  # out of scope
)


def grade(s: AgentScenario, records: list[dict]) -> list[dict]:
    return agent.grade_conversation(s, records, CALC, cells_of, table_of)


def test_oracle_self_check_is_all_ones():
    rows = grade(CHAT, agent.oracle_records(CHAT, CALC))
    s = agent.aggregate(rows)
    assert [r["errors"] for r in rows] == [[], [], []]
    for metric in ("conversation_success", "pass^k", "turn_pass", "tool_selection", "tool_order", "param_accuracy",
                   "state_accuracy", "numeric_accuracy"):
        assert s[metric] == 1.0, metric
    assert s["missing_calls"] == s["extra_calls"] == {} and s["n_numeric"] == 1 and s["n_state"] == 2


def _oracle_with(turn: int, **change) -> list[dict]:
    records = agent.oracle_records(CHAT, CALC)
    records[turn] = {**records[turn], **change}
    return records


def test_missing_and_extra_calls_are_counted_apart():
    calls = agent.oracle_records(CHAT, CALC)[0]["calls"]
    missing = grade(CHAT, _oracle_with(0, calls=calls[1:]))[0]  # no search_tables
    assert missing["missing"] == {"search_tables": 1} and missing["extra"] == {} and "missing_call" in missing["errors"]
    extra = grade(CHAT, _oracle_with(0, calls=[calls[0], *calls]))[0]  # searched twice
    assert extra["extra"] == {"search_tables": 1} and extra["order_ok"] and not extra["tools_ok"]
    assert extra["errors"] == ["extra_call"]
    swapped = grade(CHAT, _oracle_with(0, calls=calls[::-1]))[0]
    assert swapped["errors"] == ["wrong_order"] and not swapped["order_ok"]


def test_args_graded_per_field_with_the_l2_rules():
    calls = agent.oracle_records(CHAT, CALC)[0]["calls"]
    wrong = {**calls[1], "args": {"table_key": "101_DT_X", "regions": ["서울특별시", "부산광역시"]}}
    row = grade(CHAT, _oracle_with(0, calls=[calls[0], wrong]))[0]
    assert {a["field"]: a["ok"] for a in row["args"]} == {"table_key": True, "regions": False}
    assert row["errors"] == ["wrong_args"]
    swapped_order = {**calls[1], "args": {"table_key": "101_DT_X", "regions": ["서울특별시"], "query": "free text"}}
    assert grade(CHAT, _oracle_with(0, calls=[calls[0], swapped_order]))[0]["passed"]  # ungraded fields are ignored


def test_mid_conversation_failure_fails_the_conversation_and_pass_k():
    lost = agent.oracle_records(CHAT, CALC)[1]["calls"][0]
    records = _oracle_with(1, calls=[{**lost, "args": {"chart_type": "line", "table_key": "101_DT_Y"}}])  # lost "그 표"
    bad = [{**r, "run": 1} for r in grade(CHAT, records)]
    good = [{**r, "run": 0} for r in grade(CHAT, agent.oracle_records(CHAT, CALC))]
    assert [r["passed"] for r in bad] == [True, False, True] and bad[1]["errors"] == ["lost_state"]
    s = agent.aggregate(good + bad)
    assert s["conversation_success"] == 0.5 and s["pass^k"] == 0.0 and s["repeats"] == 2
    assert s["first_failed_turn"] == {"1": 1} and s["turn_pass"] == 5 / 6


def test_refusal_and_stopped_runs():
    answered = _oracle_with(2, calls=[{"name": "search_tables", "args": {}, "result": {"route": "found", "picked": [{"key": "k"}]}}])
    assert "missed_refusal" in grade(CHAT, answered)[2]["errors"]
    refused = _oracle_with(0, calls=[{"name": "search_tables", "args": {}, "result": {"route": "refuse"}}])
    assert "false_refusal" in grade(CHAT, refused)[0]["errors"]
    stopped = grade(CHAT, agent.oracle_records(CHAT, CALC)[:1])  # the run raised after turn 0
    assert stopped[1]["errors"][0] == "run_failure" and not stopped[2]["passed"]


def test_numbers_use_the_l3_grader_and_the_final_text_must_be_grounded():
    calls = agent.oracle_records(CHAT, CALC)[0]["calls"]
    wrong_value = {**calls[1], "result": {**calls[1]["result"], "result": {"facts": [
        {"id": "f1", "value": 90.0, "source_ref": REF, "unit": "명", "period": "2024"}]}}}
    row = grade(CHAT, _oracle_with(0, calls=[calls[0], wrong_value]))[0]
    assert row["calc"]["verdict"] == "FAIL" and "wrong_numbers" in row["errors"]
    ok = grade(CHAT, _oracle_with(0, answer=f"2024년 서울 출생아는 100명입니다.\n\n{CITE} (101_DT_X)"))[0]
    assert ok["passed"], ok["errors"]
    made_up = grade(CHAT, _oracle_with(0, answer=f"2024년 100명, 전년보다 12.5% 늘었습니다. {CITE}"))[0]
    assert made_up["unsupported_numbers"] == ["12.5"] and made_up["errors"] == ["unsupported_final_number"]
    later = grade(CHAT, _oracle_with(1, answer="2024년 값 100명을 꺾은선으로 그렸습니다."))[1]
    assert later["passed"]  # a number from an earlier turn's tool result is grounded


def test_search_results_cell_refs_and_codes_do_not_ground_a_number():
    """T3-40 (KOS-198): only questions, facts, combine results and the cited source ground a number."""
    search, stat = agent.oracle_records(CHAT, CALC)[0]["calls"]
    noisy = {**search, "result": {"route": "found", "picked": [{"key": "101_DT_1B8000H", "score": 0.64}],
                                  "candidates": [{"key": "101_DT_7777", "period": "년 (1990 ~ 2025)"}]}}
    row = grade(CHAT, _oracle_with(0, calls=[noisy, stat], answer=f"100명, 7777명, 1990년 이후. {CITE}"))[0]
    assert row["unsupported_numbers"] == ["7777", "1990"] and row["errors"] == ["unsupported_final_number"]
    source = Source(org_id="101", tbl_id="DT_1B83A03", survey_nm="인구동향조사", tbl_nm="시도/연령(각세)별 혼인",
                    url="http://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_1B83A03&list_id=A23_1&conn_path=I4")
    grounds = agent.Grounds(facts=CALC["c1"].required_facts, sources=[source])
    text = f"2024년 100명 (표 101_DT_1B83A03).\n\n출처: {source.citation()}"
    assert agent.unsupported_final_numbers(text, grounds) == []  # url and table id digits belong to the cited source
    assert agent.unsupported_final_numbers(text, agent.Grounds(facts=CALC["c1"].required_facts)) != []


@pytest.mark.parametrize("answer, unsupported", [
    ("약 100명", []),
    ("2024년 3천 명", ["3천"]),  # 3 alone is a period digit in no fact here; 3,000 is no fact value
    ("약 10만 명", ["10만"]),
    ("1억 2천만 명", ["1억 2천만"]),
])
def test_korean_magnitudes_are_read_as_one_number(answer, unsupported):
    assert agent.unsupported_final_numbers(answer, agent.Grounds(facts=CALC["c1"].required_facts)) == unsupported


def test_korean_magnitudes_round_to_a_fact_value():
    grounds = agent.Grounds(facts=[Fact(id=f"f{i}", value=v, unit=u, source_ref=f"DT_X|T{i}|00|2024")
                                   for i, (v, u) in enumerate([(103_412.0, "명"), (1_234.0, "억원"), (123_456_789.0, "원")])])
    ok = "약 10만 명, 1,234억 원, 1억 2천만 원, 약 1.2억 원"
    assert agent.unsupported_final_numbers(ok, grounds) == []
    assert agent.unsupported_final_numbers("약 20만 명, 1억 5천만 원", grounds) == ["20만", "1억 5천만"]


def test_combine_values_result_grounds_its_rounded_value():
    calls = [{"name": "combine_values", "args": {"op": "ratio"}, "result": {"op": "ratio", "value": 41.2857}, "status": "ok"}]
    assert agent.unsupported_final_numbers("두 값의 계산 결과(비율): 41.3%", agent.grounding("", calls)) == []
    assert agent.unsupported_final_numbers("두 값의 계산 결과(비율): 45.0%", agent.grounding("", calls)) == ["45.0"]


def test_an_answer_must_cite_every_answered_source():
    """T3-40 (KOS-196): survey and table name of each answered summarize_stat, each multi-query sub-answer too."""
    uncited = grade(CHAT, _oracle_with(0, answer="2024년 서울 출생아는 100명입니다."))[0]
    assert uncited["missing_sources"] == ["101_DT_X"] and uncited["errors"] == ["missing_source"]
    s = agent.aggregate(grade(CHAT, _oracle_with(0, answer="100명")))
    assert s["missing_source_turns"] == 1 and s["errors"] == {"missing_source": 1}
    a, b = (agent.oracle_stat(CALC["c1"]) | {"source": {"org_id": "101", "tbl_id": t, "survey_nm": "조사", "tbl_nm": n}}
            for t, n in (("DT_A", "표 A"), ("DT_B", "표 B")))
    calls = [{"name": "summarize_stat", "args": {}, "result": r, "status": "ok"} for r in (a, b)]
    assert agent.missing_sources("KOSIS / 조사 / 표 A", agent.stat_answers(calls)) == ["101_DT_B"]
    assert agent.missing_sources("KOSIS / 조사 / 표 A\n\nKOSIS / 조사 / 표 B", agent.stat_answers(calls)) == []


def test_phase1_code_lines_and_fact_units_are_not_claims():
    """Measured on saved runs (T3-40): caveat lines, the refusal message and digits of a fact's unit are code's text."""
    caveat = "참고: '코로나19 유행 직전'은(는) 사건 달력의 시작 시점 2020년 1월 기준입니다."
    stat = agent.oracle_stat(CALC["c1"])
    stat = {**stat, "text": f"100명\n{caveat}\n\n{CITE}",
            "result": {"facts": [{**stat["result"]["facts"][0], "unit": "가임여자 1명당 명"}]}}
    calls = [{"name": "summarize_stat", "args": {}, "result": stat, "status": "ok"}]
    grounds = agent.grounding("2025년 출생아 수", calls)
    assert agent.unsupported_final_numbers(f"100명 가임여자 1명당 명\n{caveat}\n\n{CITE}", grounds) == []
    assert agent.unsupported_final_numbers("2020년 1월 기준 100명", grounds) == ["2020"]  # not the verbatim line ("1" is a unit digit)
    refusal = {"outcome": "refused", "refusal": {"reason": "no_period", "message": "이 통계표에는 2019년 자료가 없어 답할 수 없습니다."},
               "source": agent.oracle_stat(CALC["c1"])["source"]}
    grounds = agent.grounding("코로나19 직전 해", [{"name": "summarize_stat", "args": {}, "result": refusal, "status": "ok"}])
    assert agent.unsupported_final_numbers("이 통계표에는 2019년 자료가 없어 답할 수 없습니다.", grounds) == []


def test_a_turn_that_raised_is_run_failure_not_missing_source():
    row = grade(CHAT, _oracle_with(0, answer="", error="RuntimeError: model down"))[0]
    assert "run_failure" in row["errors"] and "missing_source" not in row["errors"]


def test_refused_and_clarify_answers_need_no_source():
    refused = {"name": "summarize_stat", "args": {}, "status": "ok",
               "result": {"outcome": "refused", "refusal": {"reason": "no_period", "message": "없음"},
                          "source": agent.oracle_stat(CALC["c1"])["source"]}}
    clarify = {**refused, "result": {**refused["result"], "outcome": "clarify", "refusal": None, "clarify_options": ["서울", "부산"]}}
    assert None not in agent.stat_answers([refused, clarify])
    assert agent.missing_sources("그 시점 자료는 없어요.", agent.stat_answers([refused, clarify])) == []
    assert agent.missing_sources("", []) == []  # no summarize_stat answer in the turn


def _attempt(key: str, status: str = "ok") -> dict:
    """A summarize_stat call on `key` that refused (or failed): what an alternate-table retry moves on from."""
    return {"name": "summarize_stat", "args": {"table_key": key, "regions": ["서울특별시"]},
            "result": {"outcome": "refused", "refusal": {"reason": "no_period", "message": "없음"}}, "status": status}


def test_an_answer_from_an_alternate_table_is_graded_on_the_final_call_only():
    """T3-23: refused on 101_DT_W, then answered on the expected 101_DT_X -> no extra_call, no false_refusal."""
    search, stat = agent.oracle_records(CHAT, CALC)[0]["calls"]
    row = grade(CHAT, _oracle_with(0, calls=[search, _attempt("101_DT_W"), stat]))[0]
    assert row["passed"], row["errors"]
    assert row["alt_attempts"] == 1 and row["extra"] == {} and not row["refused"] and row["calc"]["verdict"] == "PASS"
    chain = grade(CHAT, _oracle_with(0, calls=[search, _attempt("101_DT_V"), _attempt("101_DT_W"), stat]))[0]
    assert chain["passed"] and chain["alt_attempts"] == 2


def test_an_alternate_table_still_fails_when_the_final_table_is_wrong():
    search, stat = agent.oracle_records(CHAT, CALC)[0]["calls"]
    elsewhere = {**stat, "args": {**stat["args"], "table_key": "101_DT_W"}}
    row = grade(CHAT, _oracle_with(0, calls=[search, _attempt("101_DT_X"), elsewhere]))[0]
    assert row["state_key"] == "101_DT_W" and "lost_state" in row["errors"]
    assert "extra_call" not in row["errors"] and "false_refusal" not in row["errors"]


def test_a_failed_tool_call_retried_on_another_table_is_an_attempt():
    search, stat = agent.oracle_records(CHAT, CALC)[0]["calls"]
    row = grade(CHAT, _oracle_with(0, calls=[search, _attempt("101_DT_W", status="error"), stat]))[0]
    assert row["passed"] and row["alt_attempts"] == 1


def test_a_repeated_call_on_the_same_table_or_a_final_refusal_is_not_set_aside():
    search, stat = agent.oracle_records(CHAT, CALC)[0]["calls"]
    same = grade(CHAT, _oracle_with(0, calls=[search, _attempt("101_DT_X"), stat]))[0]
    assert same["alt_attempts"] == 0 and "extra_call" in same["errors"]  # same table again: a call of its own
    gave_up = grade(CHAT, _oracle_with(0, calls=[search, stat, _attempt("101_DT_W")]))[0]
    assert gave_up["alt_attempts"] == 0 and "extra_call" in gave_up["errors"]  # an answer is never set aside


def test_alternate_attempts_are_counted_in_the_summary():
    search, stat = agent.oracle_records(CHAT, CALC)[0]["calls"]
    rows = grade(CHAT, _oracle_with(0, calls=[search, _attempt("101_DT_W"), stat]))
    s = agent.aggregate(rows)
    assert s["alt_attempts"] == 1 and s["alt_attempt_turns"] == 1 and s["turn_pass"] == 1.0


# T3-37: chart data vs the facts, and the automatic chart choice


def _chart(*points: tuple[float, str | None], kind: str = "line", chart_type: str | None = "line") -> dict:
    """A make_chart call as the span records it: the phase 1 ChartSpec in args, the tool's content as the result."""
    spec = {"kind": kind, "title": "t", "fact_ids": ["f1"], "series": [{"name": "s", "points": [
        {"label": f"p{n}", "value": v, "display": str(v), "fact_id": fid} for n, (v, fid) in enumerate(points)]}]}
    return {"name": "make_chart", "status": "ok", "args": {"table_key": "101_DT_X", "chart": spec, "chart_type": chart_type},
            "result": {"table_key": "101_DT_X", "chart_type": chart_type, "kind": kind, "drawn": True, "note": None}}


def test_oracle_chart_draws_the_latest_facts_and_its_spec_is_checked():
    rows = grade(CHAT, agent.oracle_records(CHAT, CALC))
    assert rows[1]["chart_turn"] and rows[1]["chart_data_ok"] is True and rows[1]["chart_kind"] == "line"
    s = agent.aggregate(rows)
    assert s["chart_turn_pass"] == 1.0 and s["n_chart_turns"] == 1 and s["chart_data_accuracy"] == 1.0
    assert s["by_task"]["other"]["n_chart_turns"] == 1 and s["auto_chart_agreement"] is None


def test_a_chart_number_that_is_no_fact_is_chart_data_mismatch():
    """A chart-only turn draws the previous answer's facts: 100 is the fact, 999 is from nowhere."""
    ok = grade(CHAT, _oracle_with(1, calls=[_chart((100.0, "f1"))]))[1]
    assert ok["passed"], ok["errors"]
    rows = grade(CHAT, _oracle_with(1, calls=[_chart((100.0, "f1"), (999.0, None))]))
    wrong = rows[1]
    assert wrong["errors"] == ["chart_data_mismatch"] and wrong["chart_unmatched"] == [999.0]
    assert wrong["chart_data_ok"] is False and agent.aggregate(rows)["chart_turn_pass"] == 0.0
    # a result that carries its own Vega-Lite is read as is (data.values of the quantitative y field)
    vega = {"data": {"values": [{"값": 5.0}]}, "mark": "bar", "encoding": {"y": {"field": "값", "type": "quantitative"}}}
    own = {**_chart((100.0, "f1")), "result": {"kind": "bar", "drawn": True, "vega_lite": vega}}
    assert grade(CHAT, _oracle_with(1, calls=[own]))[1]["chart_unmatched"] == [5.0]
    # the facts of the same turn count, and a turn with its own summarize_stat does not draw an older turn's facts
    search, stat = agent.oracle_records(CHAT, CALC)[0]["calls"]
    same_turn = grade(CHAT, _oracle_with(0, calls=[search, stat, _chart((100.0, "f1"))]))[0]
    assert same_turn["chart_data_ok"] is True and same_turn["errors"] == ["extra_call"]
    empty = {**stat, "result": {"outcome": "answered"}}
    records = _oracle_with(0, calls=[search, empty])
    no_cells = agent.grade_conversation(CHAT, records, CALC, cells_of, lambda key: ())  # 100: no fact, no cell then
    assert no_cells[1]["errors"] == ["chart_data_mismatch"]  # turn 0 had no facts to draw
    assert grade(CHAT, records)[1]["passed"]  # with the table read, 100 is one of its cells (context row)


def test_context_rows_phase_1_draws_beside_the_facts_pass_when_they_are_table_data():
    """ChartPoint without fact_id: another period of the table (a cell) or a pie's rest slice (total fact - parts)."""
    table = lambda key: frozenset({100.0, 80.0})  # noqa: E731  (the 2024 cell and a 2023 cell of 101_DT_X)
    context = agent.grade_conversation(CHAT, _oracle_with(1, calls=[_chart((80.0, None), (100.0, "f1"))]), CALC,
                                       cells_of, table)[1]
    assert context["passed"] and context["chart_context_values"] == 1
    other = agent.grade_conversation(CHAT, _oracle_with(1, calls=[_chart((77.0, None), (100.0, "f1"))]), CALC,
                                     cells_of, table)[1]
    assert other["errors"] == ["chart_data_mismatch"]
    pie = {**_chart((60.0, "f1"), (40.0, None), kind="pie", chart_type="line"), "result": {"kind": "pie", "drawn": True,
                                                                                         "note": "fell back"}}
    total = {"id": "f1", "value": 100.0, "source_ref": REF, "unit": "명", "period": "2024"}
    part = {"id": "f2", "value": 60.0, "source_ref": REF, "unit": "명", "period": "2024"}
    search, stat = agent.oracle_records(CHAT, CALC)[0]["calls"]
    parts = {**stat, "result": {**stat["result"], "result": {"facts": [total, part]}}}
    rows = agent.grade_conversation(CHAT, [{"calls": [search, parts], "answer": "", "error": None},
                                           {"calls": [pie], "answer": "", "error": None}], CALC, cells_of, lambda key: ())
    assert rows[1]["chart_data_ok"] is True and rows[1]["chart_context_values"] == 1  # 40 = 100 - 60


AUTO = scenario(
    ("2015년부터 2024년까지 서울 출생아 수 추이", {"tools": [SEARCH, STAT], "table_key": "101_DT_X", "calc_qid": "c1"}),
    ("그래프로 보여줘", {"tools": [{"name": "make_chart", "args": {"table_key": "101_DT_X"}}], "table_key": "101_DT_X",
                   "auto_chart": "line"}), sid="MT-C-A001")


def test_automatic_chart_choice_is_graded_on_the_family_of_the_drawn_kind():
    oracle = agent.oracle_records(AUTO, CALC)
    rows = grade(AUTO, oracle)
    assert rows[1]["passed"] and rows[1]["auto_chart_ok"] and oracle[1]["calls"][0]["args"].get("chart_type") is None
    as_lines = grade(AUTO, [oracle[0], {"calls": [_chart((100.0, "f1"), kind="multi_line", chart_type=None)],
                                         "answer": "", "error": None}])[1]
    assert as_lines["passed"] and as_lines["chart_kind"] == "line"  # one line per series is still a line chart
    as_bars = grade(AUTO, [oracle[0], {"calls": [_chart((100.0, "f1"), kind="timeline", chart_type=None)],
                                        "answer": "", "error": None}])
    assert as_bars[1]["errors"] == ["wrong_chart_type"] and as_bars[1]["chart_kind"] == "bar"
    s = agent.aggregate([*rows, *[{**r, "run": 1} for r in as_bars]])
    assert s["auto_chart_agreement"] == 0.5 and s["n_auto_chart"] == 2 and s["chart_turn_pass"] == 0.5
    assert s["auto_chart_confusion"] == {"line→bar": 1, "line→line": 1}
    assert s["by_task"]["multiturn"]["auto_chart_agreement"] == 0.5
    missing = grade(AUTO, [oracle[0], {"calls": [], "answer": "", "error": None}])[1]
    assert missing["auto_chart_ok"] is False and "wrong_chart_type" not in missing["errors"]  # missing_call says it


# Live run: a real LangGraph graph with a checkpointer, tools and a chat model, read back from the spans


@tool
def search_tables(query: str) -> dict:
    """Find tables."""
    return {"route": "found", "picked": [{"key": "101_DT_X"}]}


@tool
def summarize_stat(question: str, table_key: str) -> dict:
    """Compute."""
    return agent.oracle_stat(CALC["c1"])


def fake_agent():
    # one message object per turn: add_messages gives a message an id, so a reused object would replace the last one
    model = FakeMessagesListChatModel(responses=[
        AIMessage(f"2024년 100명\n\n{CITE}", usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})
        for _ in range(4)])

    def step(state: MessagesState, config) -> dict:
        question = state["messages"][-1].content
        if len(state["messages"]) == 1:  # first turn: search then compute; later turns: compute on the same table
            search_tables.invoke({"name": "search_tables", "args": {"query": question}, "id": "c0", "type": "tool_call"}, config)
        summarize_stat.invoke({"question": question, "table_key": "101_DT_X"}, config)
        return {"messages": [model.invoke(state["messages"], config)]}

    g = StateGraph(MessagesState)
    g.add_node("agent", step)
    g.add_edge(START, "agent")
    return g.compile(checkpointer=MemorySaver())


def test_live_run_reads_turns_from_spans_and_links_verdicts():
    s = scenario(("2024년 서울 출생아 수", {"tools": [SEARCH, {"name": "summarize_stat", "args": {"table_key": "101_DT_X"}}],
                                     "table_key": "101_DT_X", "calc_qid": "c1"}),
                 ("그 표로 다시", {"tools": [{"name": "summarize_stat"}], "table_key": "101_DT_X", "calc_qid": "c1"}))
    telemetry = runner.tracing()
    records = runner.run(fake_agent(), [s], repeat=2, telemetry=telemetry)
    first = records[("s1", 0)][0]
    assert [c["name"] for c in first["calls"]] == ["search_tables", "summarize_stat"]
    assert first["calls"][0]["args"] == {"query": "2024년 서울 출생아 수"} and first["calls"][0]["result"]["route"] == "found"
    assert first["calls"][1]["result"]["outcome"] == "answered"
    assert (first["llm_calls"], first["input_tokens"], first["output_tokens"]) == (1, 7, 3) and first["answer"] == f"2024년 100명\n\n{CITE}"
    assert first["latency_ms"] > 0 and len(first["trace_id"]) == 32
    kinds = {s["kind"]: s for s in first["spans"]}  # T3-20 waterfall: root, graph node, tools, chat model
    assert set(kinds) == {"turn", "node", "tool", "llm"} and kinds["turn"]["depth"] == 0 and kinds["tool"]["depth"] == 2
    rows = [{**row, "run": run_no} for (_, run_no), recs in records.items() for row in agent.grade_conversation(s, recs, CALC, cells_of)]
    assert all(r["passed"] for r in rows), [r["errors"] for r in rows]
    assert agent.aggregate(rows)["pass^k"] == 1.0
    _, memory = telemetry
    memory.clear()
    runner.link_verdicts(telemetry[0], rows)
    links = [span for span in memory.get_finished_spans() if span.name == "eval.scenario"]
    assert len(links) == 2 and all(len(span.links) == 2 and span.attributes["kosis.verdict"] == "PASS" for span in links)


def test_failed_turn_is_recorded_not_raised():
    class Broken:
        def invoke(self, *args, **kwargs):
            raise RuntimeError("model down")

    records = runner.run(Broken(), [CHAT], telemetry=runner.tracing())[("s1", 0)]
    assert len(records) == 1 and records[0]["error"] == "RuntimeError: model down" and records[0]["failed"]
    rows = grade(CHAT, records)
    assert rows[0]["errors"][0] == "run_failure" and agent.aggregate(rows)["conversation_success"] == 0.0


def test_layer_agent_oracle_and_from_run(tmp_path):
    import json
    from argparse import Namespace

    from evals.tasks.agent import run as run_agent

    chat = scenario(*[(t.question, t.expect.model_dump()) for t in CHAT.turns[1:]], sid="s2")  # no calc_qid: no Parquet
    path = tmp_path / "scenarios.jsonl"
    path.write_text(chat.model_dump_json() + "\n", encoding="utf-8")
    base = dict(scenarios=path, split="all", calc_golden=[], agent=None, repeat=1, stage="t")
    rows, summary, report = run_agent(Namespace(**base, oracle=True, from_run=None))
    assert summary["conversation_success"] == 1.0 and "| 대화 성공률 (모든 턴 통과) | 1.000 |" in report
    rows[0]["calls"] = []  # the past run forgot the chart call
    (tmp_path / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    _, summary, report = run_agent(Namespace(**base, oracle=False, from_run=tmp_path))
    assert summary["missing_calls"] == {"make_chart": 1} and summary["conversation_success"] == 0.0 and "s2#0 | 0 |" in report


def _alt_scenario():
    return AgentScenario.model_validate({"sid": "s1", "turns": [{"question": "2024년 서울 출생아 수", "expect": {
        "tools": [SEARCH, {"name": "summarize_stat", "args": {"table_key": "101_DT_X"}}], "table_key": "101_DT_X",
        "alt_table_keys": ["101_DT_ALT"], "calc_qid": "c1"}}]})


def _answer_from(key: str, value: float) -> dict:
    """A summarize_stat call on `key` whose answer holds one fact with `value` (another table's cell)."""
    return {"name": "summarize_stat", "args": {"table_key": key}, "status": "ok", "result": {
        "outcome": "answered", "text": f"{value:g}명", "result": {"facts": [
            {"id": "f1", "value": value, "source_ref": f"{key.split('_', 1)[1]}|T9|11|2024", "unit": "명",
             "period": "2024"}]}, "parts": [{"kind": "fact", "fact_id": "f1"}],
        "source": {"org_id": "101", "tbl_id": key.split("_", 1)[1], "survey_nm": "조사", "tbl_nm": "표"}}}


ALT_REF = "DT_ALT|T9|11|2024"
ALT_CELLS = {**CELLS, ALT_REF: SimpleNamespace(value=100.0)}
alt_cells_of = lambda key, refs=None: ALT_CELLS  # noqa: E731


def holds_golden(*keys):
    """An alternate judgement for tests (the real one reads Parquet, evaluators.alternates): `keys` hold every golden
    cell of c1 at ALT_REF-shaped refs; other tables do not."""
    def alternate_of(item, key, cited):
        if key not in keys:
            return None
        ref = f"{key.split('_', 1)[1]}|T9|11|2024"
        return item.model_copy(update={"table_key": key, "required_facts": [f.model_copy(update={"source_ref": ref})
                                                                          for f in item.required_facts]})
    return alternate_of


def test_an_alternate_gold_table_is_graded_on_its_own_cells_and_counted_apart():
    """T3-42 (was T3-27 same_values): an answer from a table holding the golden values is graded against that table's
    cells; a wrong number there still fails; a table that is no alternate fails state, arguments and numbers."""
    s = _alt_scenario()
    search = {"name": "search_tables", "args": {}, "result": {"route": "found", "picked": [{"key": "101_DT_ALT"}]}}
    run = lambda value, key="101_DT_ALT": agent.grade_conversation(  # noqa: E731
        s, [{"calls": [search, _answer_from(key, value)], "answer": f"{value:g}명 {ALT_CITE}", "error": None}],
        CALC, alt_cells_of, alternate_of=holds_golden("101_DT_ALT"))[0]
    same = run(100.0)
    assert same["passed"], same["errors"]
    assert same["state_ok"] and same["calc"]["via_alternate"] and same["calc"]["graded_table"] == "101_DT_ALT"
    assert agent.aggregate([same])["numeric_pass_via_alternate"] == 1
    other = run(90.0)
    assert other["state_ok"] and "wrong_numbers" in other["errors"] and not other["calc"]["via_alternate"]
    outside = run(100.0, "101_DT_ZZZ")  # equal value, but the table does not hold the golden cells: same_values passed it
    assert {"lost_state", "wrong_args", "wrong_numbers"} <= set(outside["errors"])


def test_a_value_alternate_counts_for_state_and_carries_to_the_next_turn():
    """T3-42: a table outside the scenario's alt_table_keys that the T3-41 rule accepts is right for state and tool
    arguments, and stays right on a later turn that expects the same table without a calc_qid ("그 표로 그려줘")."""
    s = AgentScenario.model_validate({"sid": "s1", "turns": [
        {"question": "2024년 서울 출생아 수", "expect": {"tools": [SEARCH, {"name": "summarize_stat", "args": {"table_key": "101_DT_X"}}],
                                                   "table_key": "101_DT_X", "calc_qid": "c1"}},
        {"question": "그 표로 그려줘", "expect": {"tools": [{"name": "make_chart", "args": {"table_key": "101_DT_X"}}],
                                             "table_key": "101_DT_X"}}]})
    chart = {"name": "make_chart", "status": "ok", "args": {"table_key": "101_DT_NEW"}, "result": {"table_key": "101_DT_NEW"}}
    records = [{"calls": [SEARCH, _answer_from("101_DT_NEW", 100.0)], "answer": f"100명 {ALT_CITE}", "error": None},
               {"calls": [chart], "answer": "", "error": None}]
    cells = lambda key, refs=None: {**CELLS, "DT_NEW|T9|11|2024": SimpleNamespace(value=100.0)}  # noqa: E731
    first, second = agent.grade_conversation(s, records, CALC, cells, alternate_of=holds_golden("101_DT_NEW"))
    assert first["value_alternates"] == ["101_DT_NEW"] and first["state_ok"] and "wrong_args" not in first["errors"]
    assert second["value_alternates"] == ["101_DT_NEW"] and second["state_ok"]
    first, second = agent.grade_conversation(s, records, CALC, cells, alternate_of=holds_golden())
    assert not first["state_ok"] and not second["state_ok"]  # without the rule both turns lose the state


def test_numbers_are_graded_on_the_table_the_facts_came_from():
    """T3-42: the call may name an alternate table while its answer cites the golden table (the oracle on an L1
    alternate turn): the answer's source decides, so the golden cells grade it (oracle self-check stays 1.000)."""
    from evals.evaluators.agent.numbers import table_key

    call = {**_answer_from("101_DT_X", 100.0), "args": {"table_key": "101_DT_ALT"}}
    call["result"]["result"]["facts"][0]["source_ref"] = REF
    assert table_key(call) == "101_DT_X" and table_key({"args": {"table_key": "101_DT_ALT"}}) == "101_DT_ALT"
    (row,) = agent.grade_conversation(_alt_scenario(), [{"calls": [SEARCH, call], "answer": f"100명 {ALT_CITE}", "error": None}],
                                      CALC, alt_cells_of, alternate_of=holds_golden("101_DT_ALT"))
    assert row["calc"]["verdict"] == "PASS" and "graded_table" not in row["calc"]


def test_multi_query_sub_answers_need_an_alternate_table_too():
    """T3-42: a sub-answer (calc_qids) with the golden value from a table that is no alternate used to pass on the
    value alone (same_values, no table check); now it passes only from the golden table or an alternate."""
    s = AgentScenario.model_validate({"sid": "MQ-1", "turns": [{"question": "q", "expect": {
        "tools": [SEARCH, {"name": "summarize_stat", "args": {}}], "calc_qids": ["c1"]}}]})
    rec = lambda key: [{"calls": [SEARCH, _answer_from(key, 100.0)], "answer": f"100명 {ALT_CITE}", "error": None}]  # noqa: E731
    (no,) = agent.grade_conversation(s, rec("101_DT_ZZZ"), CALC, alt_cells_of, alternate_of=holds_golden("101_DT_ALT"))
    assert no["sub_calc"][0]["verdict"] != "PASS" and "wrong_numbers" in no["errors"]
    (yes,) = agent.grade_conversation(s, rec("101_DT_ALT"), CALC, alt_cells_of, alternate_of=holds_golden("101_DT_ALT"))
    assert yes["sub_calc"][0]["verdict"] == "PASS" and yes["sub_calc"][0]["via_alternate"]


def test_layer_agent_by_task_over_several_scenario_files(tmp_path):
    """T3-29: multi-turn (MT-*) and multi-query (MQ-*) files in one run, success per task; repeated sids are refused."""
    from argparse import Namespace

    import pytest

    from evals.tasks.agent import run as run_agent

    turns = [(t.question, t.expect.model_dump()) for t in CHAT.turns[1:]]  # no calc_qid: no Parquet
    mt, mq = tmp_path / "mt.jsonl", tmp_path / "mq.jsonl"
    mt.write_text(scenario(*turns, sid="MT-R-001").model_dump_json() + "\n", encoding="utf-8")
    mq.write_text(scenario(*turns[:1], sid="MQ-B-001").model_dump_json() + "\n", encoding="utf-8")
    base = dict(split="all", calc_golden=[], agent=None, repeat=1, stage="t", oracle=True, from_run=None)
    rows, summary, report = run_agent(Namespace(**base, scenarios=[mt, mq]))
    assert {r["sid"] for r in rows} == {"MT-R-001", "MQ-B-001"}
    by_task = summary["by_task"]
    assert set(by_task) == {"multiturn", "multiquery"} and by_task["multiturn"]["n_turns"] == 2
    assert by_task["multiquery"]["n_turns"] == 1 and by_task["multiquery"]["conversation_success"] == 1.0
    assert "| 멀티턴 (MT-*) | 1 | 1.000 |" in report and "| 멀티쿼리 (MQ-*) | 1 | 1.000 |" in report
    next(r for r in rows if r["sid"] == "MT-R-001")["calls"] = []  # the multi-turn run forgot the chart call: only its task fails
    (tmp_path / "results.jsonl").write_text("".join(__import__("json").dumps(r, ensure_ascii=False) + "\n" for r in rows))
    _, summary, _ = run_agent(Namespace(**{**base, "oracle": False, "from_run": tmp_path}, scenarios=[mt, mq]))
    assert summary["by_task"]["multiturn"]["conversation_success"] == 0.0
    assert summary["by_task"]["multiquery"]["conversation_success"] == 1.0
    with pytest.raises(SystemExit, match="duplicate sids"):
        run_agent(Namespace(**base, scenarios=[mt, mt]))
