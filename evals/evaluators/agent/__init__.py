"""Agent layer (T3-06, KOS-120): grade an agent's run record against a multi-turn scenario. No LLM.

Input is architecture-free: per turn, the tool calls in order ({name, args, result, status}), the final answer text
and the error, as runners.agent reads them from the turn's OTel spans (or a past run's results.jsonl).

Per turn (on final_calls: a summarize_stat that refused or failed and was retried right away on another table is an
alternate-table attempt, set aside and counted in alt_attempts; T3-23):
- tools: the called names must equal the expected names in order (tools_ok). missing / extra calls are counted
  per tool (multiset difference), and order_ok says whether the expected calls appear in order (extra calls allowed).
- args: each graded field of an expected call vs the same tool's k-th call, with the L2 slot rule
  (evaluators.query.field_correct: lists as sets, the rest exactly). Predicted scalars are compared as text.
- state: the table the turn ends on (state_key) vs expect.table_key. This is how "이거" / "그 표" are graded.
- refusal: a turn refuses when a tool said so (search route "refuse", summarize_stat outcome "refused") or no tool
  ran. missed_refusal / false_refusal as in L3.
- numbers: expect.calc_qid grades the turn's last summarize_stat answer with the L3 grader as is
  (evaluators.calculation.grade against the table's Parquet cells). Every number in the final answer text must
  also come from a tool result or a question of the conversation so far (rounding allowed against fact values),
  so a number the LLM wrote itself is unsupported_final_number.
A turn passes with no error; a conversation succeeds when every turn passes; pass^k = share of scenarios whose
k repeated runs all succeeded.

One module per evaluation task (T3-29): tools.py (tool selection, order, arguments), state.py (multi-turn state and
refusal), numbers.py (L3 numbers, multi-query sub-answers and combine), answer.py (answer quality: unsupported
numbers). grade_turn joins their errors in that order; aggregate adds by_task (multi-turn MT-* / multi-query MQ-*).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable

from kosis_ai.schemas import Fact, FactPart, Refusal, Source, StatAnswer, StatResult

from evals.evaluators.agent.answer import grade_answer, grounding, unsupported_final_numbers
from evals.evaluators.agent.numbers import combined_value, grade_numbers, same_values, stat_answer, stat_answers
from evals.evaluators.agent.state import STATE_TOOLS, first_key, grade_state, refused, state_key
from evals.evaluators.agent.tools import final_calls, grade_tools
from evals.schemas import AgentScenario, CalcItem, TurnExpect

__all__ = ["ERRORS", "STATE_TOOLS", "aggregate", "combined_value", "final_calls", "first_key", "grade_conversation",
           "grade_turn", "oracle_records", "oracle_stat", "refused", "same_values", "stat_answer", "stat_answers",
           "state_key", "unsupported_final_numbers"]

ERRORS = {  # error -> what it means (report legend)
    "run_failure": "턴 실행 실패 (예외)",
    "missing_call": "정답에 있는 툴을 부르지 않음",
    "extra_call": "정답에 없는 툴을 부름 (불필요 호출)",
    "wrong_order": "툴은 맞는데 순서가 다름",
    "wrong_args": "툴 인자 필드가 정답과 다름",
    "lost_state": "턴이 끝난 표가 정답 표와 다름 (지시어·조건 이월 실패 포함)",
    "missed_refusal": "거절해야 할 턴에 답함",
    "false_refusal": "답할 수 있는 턴을 거절함",
    "wrong_numbers": "summarize_stat 답이 L3 채점에서 PASS가 아님",
    "unsupported_final_number": "최종 답 문장에 툴 결과·질문에 없는 숫자",
}
TASK_PREFIXES = {"MT-": "multiturn", "MQ-": "multiquery"}  # sid prefixes the builders write (builders/multiturn, multiquery)
TASK_METRICS = ("n_turns", "n_conversations", "conversation_success", "pass^k", "turn_pass", "tool_selection", "param_accuracy",
                "state_accuracy", "numeric_accuracy", "n_numeric", "combine_accuracy", "n_combine", "unsupported_final_number_turns")


def grade_turn(expect: TurnExpect, record: dict, calc: dict[str, CalcItem], cells_of: Callable[[str], dict],
               grounded: set[str], facts: list[Fact]) -> dict:
    """One turn's metrics and errors. grounded / facts = numbers and facts of the conversation so far (this turn's
    question and tool results included). Graded on final_calls: alternate-table attempts are set aside and counted
    in alt_attempts, not as errors."""
    every = record.get("calls") or []
    calls = final_calls(every)
    tools, tool_errors = grade_tools(expect, calls)
    state, state_errors = grade_state(expect, calls)
    numbers, number_errors = grade_numbers(expect, record, calls, state["state_key"], calc, cells_of)
    answer, answer_errors = grade_answer(record, grounded, facts)
    errors = ["run_failure"] * bool(record.get("error")) + tool_errors + state_errors + number_errors + answer_errors
    return {**tools, **state, **numbers, "expected_table": expect.table_key, "alt_attempts": len(every) - len(calls),
            **answer, "errors": errors, "passed": not errors}


def grade_conversation(scenario: AgentScenario, records: list[dict], calc: dict[str, CalcItem],
                       cells_of: Callable[[str], dict]) -> list[dict]:
    """Graded rows, one per scenario turn, for one run of the scenario (records in turn order; a missing turn —
    the run stopped — is a run_failure). Each row keeps its record (the transcript) for --from-run."""
    grounded: set[str] = set()
    facts: list[Fact] = []
    rows = []
    for i, turn in enumerate(scenario.turns):
        record = records[i] if i < len(records) else {"calls": [], "answer": "", "error": "turn not run"}
        numbers, new_facts = grounding(turn.question, record.get("calls") or [])
        grounded |= numbers
        facts += new_facts
        g = grade_turn(turn.expect, record, calc, cells_of, grounded, facts)
        rows.append({**record, "sid": scenario.sid, "split": scenario.split, "turn": i, "question": turn.question, **g})
    return rows


def task_of(sid: str) -> str:
    return next((name for prefix, name in TASK_PREFIXES.items() if sid.startswith(prefix)), "other")


def aggregate(rows: list[dict]) -> dict:
    """Run-level metrics over graded turn rows, and the same headline metrics per evaluation task (by_task, T3-29)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[task_of(r["sid"])].append(r)
    by_task = {name: {k: m[k] for k in TASK_METRICS} for name, part in sorted(groups.items()) for m in [_metrics(part)]}
    return {**_metrics(rows), "by_task": by_task}


def _metrics(rows: list[dict]) -> dict:
    """Run-level metrics over graded turn rows (rows carry sid, run, turn, passed and the turn metrics)."""
    n = len(rows)
    rate = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    convs: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        convs[(r["sid"], r.get("run", 0))].append(r)
    success = {k: all(t["passed"] for t in v) for k, v in convs.items()}
    by_sid: dict[str, list[bool]] = defaultdict(list)
    for (sid, _), ok in success.items():
        by_sid[sid].append(ok)
    fields: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        for a in r["args"]:
            fields[a["field"]].append(a["ok"])
    calc = [r["calc"]["verdict"] for r in rows if r["calc"]]
    calc += [g["verdict"] for r in rows for g in r.get("sub_calc") or []]  # T3-28: each sub-question's L3 grade
    combine = [r["combine_ok"] for r in rows if r.get("combine_ok") is not None]
    first_fail = Counter(next(t["turn"] for t in v if not t["passed"]) for k, v in convs.items() if not success[k])
    return {
        "n_turns": n, "n_conversations": len(convs), "n_scenarios": len(by_sid),
        "repeats": max((len(v) for v in by_sid.values()), default=0),
        "conversation_success": rate(list(success.values())),
        "pass^k": rate([all(v) for v in by_sid.values()]),
        "turn_pass": rate([r["passed"] for r in rows]),
        "tool_selection": rate([r["tools_ok"] for r in rows]),
        "tool_order": rate([r["order_ok"] for r in rows]),
        "missing_calls": dict(sum((Counter(r["missing"]) for r in rows), Counter())),
        "extra_calls": dict(sum((Counter(r["extra"]) for r in rows), Counter())),
        "alt_attempts": sum(r.get("alt_attempts", 0) for r in rows),  # alternate-table attempts set aside (T3-23)
        "alt_attempt_turns": sum(bool(r.get("alt_attempts")) for r in rows),
        "param_accuracy": rate([ok for v in fields.values() for ok in v]),
        "param_by_field": {f: {"acc": rate(v), "n": len(v)} for f, v in sorted(fields.items())},
        "state_accuracy": rate([r["state_ok"] for r in rows if r["state_ok"] is not None]),
        "n_state": sum(r["state_ok"] is not None for r in rows),
        "numeric_accuracy": rate([v == "PASS" for v in calc]), "n_numeric": len(calc),
        "numeric_pass_via_alternate": sum(bool(r["calc"] and r["calc"].get("via_alternate")) for r in rows),
        "state_via_alternate": sum(bool(r["state_ok"] and r["state_key"] != r.get("expected_table")) for r in rows
                                   if r.get("expected_table")),
        "combine_accuracy": rate(combine), "n_combine": len(combine),
        "unsupported_final_number_turns": sum(bool(r["unsupported_numbers"]) for r in rows),
        "errors": dict(Counter(e for r in rows for e in r["errors"])),
        "first_failed_turn": {str(k): v for k, v in sorted(first_fail.items())},
    }


def oracle_stat(item: CalcItem) -> dict:
    """A summarize_stat answer that is right by construction for an L3 item (the oracle's self-check)."""
    org_id, _, tbl_id = item.table_key.partition("_")
    source = Source(org_id=org_id, tbl_id=tbl_id, survey_nm="oracle", tbl_nm="oracle")
    if item.kind == "not_answerable":
        answer = StatAnswer(outcome="refused", refusal=Refusal(reason=item.allowed_reasons[0], message="oracle"), source=source)
    elif item.kind == "clarify":
        answer = StatAnswer(outcome="clarify", clarify_options=item.clarify_options, source=source)
    else:
        facts = [*item.required_facts, *item.aux_facts]
        answer = StatAnswer(outcome="answered", result=StatResult(facts=facts), parts=[FactPart(fact_id=f.id) for f in facts],
                            default_applied=item.default_applied, source=source)
    return answer.model_dump(mode="json")


def oracle_records(scenario: AgentScenario, calc: dict[str, CalcItem]) -> list[dict]:
    """The run record a perfect agent would leave: expected calls with expected args and consistent results."""
    records = []
    for turn in scenario.turns:
        e = turn.expect
        calls = []
        sub_qids = iter(e.calc_qids)  # T3-28: one L3 item per summarize_stat, in call order
        stat_keys = iter([c.args.get("table_key") for c in e.tools if c.name == "summarize_stat"])
        next_key = None
        for call in e.tools:
            args = dict(call.args)
            if call.name in STATE_TOOLS and e.table_key and "table_key" not in args:
                args["table_key"] = e.table_key
            if call.name == "search_tables":
                next_key = next(stat_keys, None)  # the table of the summarize_stat this search leads to
                key = (next_key if e.calc_qids else None) or e.table_key or args.get("table_key")
                result: Any = {"route": "refuse", "picked": []} if e.refuse else {"route": "found", "picked": [{"key": key}]}
            elif call.name == "summarize_stat":
                qid = e.calc_qid or next(sub_qids, None)
                result = oracle_stat(calc[qid]) if qid else {"outcome": "answered"}
            elif call.name == "combine_values":
                result = {"op": args.get("op"), "value": e.combine_value}
            else:
                result = {"chart_type": args.get("chart_type")}
            calls.append({"name": call.name, "args": args, "result": result, "status": "ok"})
        records.append({"calls": calls, "answer": "", "error": None})
    return records
