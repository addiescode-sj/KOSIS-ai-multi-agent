"""T3-06 / KOS-120: agent metrics (tool order, missing / extra calls, args, multi-turn, numbers) and the span records."""

from types import SimpleNamespace

from kosis_ai.schemas import Fact
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
cells_of = lambda key: CELLS  # noqa: E731


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
    return agent.grade_conversation(s, records, CALC, cells_of)


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
    ok = grade(CHAT, _oracle_with(0, answer="2024년 서울 출생아는 100명입니다. (KOSIS)"))[0]
    assert ok["passed"], ok["errors"]
    made_up = grade(CHAT, _oracle_with(0, answer="2024년 100명, 전년보다 12.5% 늘었습니다."))[0]
    assert made_up["unsupported_numbers"] == ["12.5"] and made_up["errors"] == ["unsupported_final_number"]
    later = grade(CHAT, _oracle_with(1, answer="2024년 값 100명을 꺾은선으로 그렸습니다."))[1]
    assert later["passed"]  # a number from an earlier turn's tool result is grounded


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
    model = FakeMessagesListChatModel(responses=[
        AIMessage("2024년 100명", usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})] * 4)

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
    assert (first["llm_calls"], first["input_tokens"], first["output_tokens"]) == (1, 7, 3) and first["answer"] == "2024년 100명"
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

    from evals.run_eval import run_agent

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
