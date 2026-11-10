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
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any, Callable

from kosis_ai.schemas import Fact, FactPart, Refusal, Source, StatAnswer, StatResult

from evals.evaluators import calculation
from evals.evaluators.query import field_correct
from evals.schemas import AgentScenario, CalcItem, TurnExpect

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
STATE_TOOLS = ("summarize_stat", "make_chart")  # calls whose table_key is the table the turn works on


def _norm(value: Any) -> Any:
    if value is None:
        return None
    return [str(v) for v in value] if isinstance(value, (list, tuple, set)) else str(value)


def _gave_up(call: dict) -> bool:
    """A summarize_stat call that did not answer from its table: refused, or the tool failed."""
    result = call.get("result")
    return call.get("status") == "error" or (isinstance(result, dict) and result.get("outcome") == "refused")


def final_calls(calls: list[dict]) -> list[dict]:
    """The calls a turn is graded on (T3-23): an alternate-table attempt is not a call of its own.

    A summarize_stat that refused (or failed) and is followed right away by a summarize_stat on another table_key is
    an attempt the agent moved on from; the later call stands for it (A refused -> B refused -> C answered leaves C).
    So a turn that answers from an alternate table is graded only on its final table and answer: lost_state when that
    table is not the expected one, wrong_numbers when the answer is wrong, never extra_call / false_refusal for the
    attempts. A repeated summarize_stat on the same table is not an attempt and still counts as a call."""
    out: list[dict] = []
    for call in calls:
        prev = out[-1] if out else None
        if (prev is not None and prev["name"] == call["name"] == "summarize_stat" and _gave_up(prev)
                and (prev.get("args") or {}).get("table_key") != (call.get("args") or {}).get("table_key")):
            out[-1] = call
        else:
            out.append(call)
    return out


def first_key(result: Any) -> str | None:
    """Top table key of a search_tables result (phase 2 SearchOutput: picked, else candidates)."""
    if isinstance(result, dict):
        for field in ("picked", "candidates"):
            if (rows := result.get(field)) and isinstance(rows[0], dict):
                return rows[0].get("key")
    return None


def state_key(calls: list[dict]) -> str | None:
    """The table a turn ends on: the last summarize_stat / make_chart table_key, else the last search's top key.

    ponytail: read from the calls, not from the graph state, so it does not depend on T3-04's state field names.
    """
    for call in reversed(calls):
        if call["name"] in STATE_TOOLS and (call.get("args") or {}).get("table_key"):
            return call["args"]["table_key"]
    for call in reversed(calls):
        if call["name"] == "search_tables" and (key := first_key(call.get("result"))):
            return key
    return None


def refused(calls: list[dict]) -> bool:
    if not calls:
        return True
    for call in calls:
        result = call.get("result")
        if isinstance(result, dict) and (result.get("route") == "refuse" or result.get("outcome") == "refused"):
            return True
    return False


def stat_answer(calls: list[dict]) -> StatAnswer | None:
    """The last successful summarize_stat result as a StatAnswer; None when there is none or it does not validate."""
    for call in reversed(calls):
        if call["name"] == "summarize_stat" and call.get("status", "ok") == "ok":
            try:
                return StatAnswer.model_validate(call.get("result"))
            except ValueError:
                return None
    return None


def _subsequence(want: list[str], got: list[str]) -> bool:
    it = iter(got)
    return all(name in it for name in want)


def _numbers(text: str) -> list[str]:
    return [n.replace(",", "") for n in calculation._NUMBER.findall(text or "")]


def unsupported_final_numbers(answer: str, grounded: set[str], facts: list[Fact]) -> list[str]:
    """Numbers in the final answer that no tool result or question holds, and that are no rounding of a fact value."""
    allowed = grounded | calculation.allowed_text_numbers(facts)
    values = [f.value for f in facts if f.value is not None]
    out = []
    for num in _numbers(answer):
        digits = len(num.split(".")[1]) if "." in num else 0
        if num not in allowed and not any(calculation.close(v, float(num), digits) for v in values):
            out.append(num)
    return out


def same_values(item: CalcItem, answer: StatAnswer | None) -> bool:
    """Every required fact's value is among the answer's facts (rounded as the golden fact says)."""
    if answer is None or answer.outcome != "answered":
        return False
    got = [f.value for f in answer.result.facts if f.value is not None]
    for fact in item.required_facts:
        if fact.value is None:
            return False
        digits = fact.round if fact.round is not None else (len(str(fact.value).split(".")[1].rstrip("0"))
                                                            if "." in str(fact.value) else 0)
        if not any(calculation.close(fact.value, v, digits) for v in got):
            return False
    return True


def grade_turn(expect: TurnExpect, record: dict, calc: dict[str, CalcItem], cells_of: Callable[[str], dict],
               grounded: set[str], facts: list[Fact]) -> dict:
    """One turn's metrics and errors. grounded / facts = numbers and facts of the conversation so far (this turn's
    question and tool results included). Graded on final_calls: alternate-table attempts are set aside and counted
    in alt_attempts, not as errors."""
    every = record.get("calls") or []
    calls = final_calls(every)
    want, got = [c.name for c in expect.tools], [c["name"] for c in calls]
    missing, extra = Counter(want) - Counter(got), Counter(got) - Counter(want)
    seen: Counter = Counter()
    args = []
    for call in expect.tools:
        same = [c for c in calls if c["name"] == call.name]
        pred = same[seen[call.name]] if seen[call.name] < len(same) else None
        seen[call.name] += 1
        for field, gold in call.args.items():
            value = _norm((pred.get("args") or {}).get(field)) if pred else None
            ok = pred is not None and (field_correct(field, gold, value)
                                       or (field == "table_key" and value in expect.alt_table_keys))
            args.append({"tool": call.name, "field": field, "ok": ok})
    key = state_key(calls)
    did_refuse = refused(calls)
    errors = []
    if record.get("error"):
        errors.append("run_failure")
    if missing:
        errors.append("missing_call")
    if extra:
        errors.append("extra_call")
    if not missing and not extra and want != got:
        errors.append("wrong_order")
    if any(not a["ok"] for a in args):
        errors.append("wrong_args")
    state_ok = None if expect.table_key is None else (key == expect.table_key or key in expect.alt_table_keys)
    if state_ok is False:
        errors.append("lost_state")
    if expect.refuse and not did_refuse:
        errors.append("missed_refusal")
    if not expect.refuse and did_refuse:
        errors.append("false_refusal")
    calc_grade = None
    if expect.calc_qid:
        item = calc[expect.calc_qid]
        answer = stat_answer(calls)
        g = calculation.grade(item, answer, cells_of(item.table_key), record.get("error"))
        calc_grade = {"verdict": g.verdict, "errors": g.errors}
        if g.verdict != "PASS" and key in expect.alt_table_keys and same_values(item, answer):
            # an alternate gold table: the L3 grader checks cells of the golden table only, so the same numbers read
            # from another table fail on dims / support. Equal values pass, counted apart (T3-27).
            calc_grade = {"verdict": "PASS", "errors": [], "via_alternate": True, "l3": g.verdict}
        if calc_grade["verdict"] != "PASS":
            errors.append("wrong_numbers")
    unsupported = unsupported_final_numbers(record.get("answer") or "", grounded, facts)
    if unsupported:
        errors.append("unsupported_final_number")
    return {"tools_ok": want == got, "order_ok": _subsequence(want, got), "missing": dict(missing), "extra": dict(extra),
            "args": args, "state_key": key, "state_ok": state_ok, "refused": did_refuse, "calc": calc_grade,
            "expected_table": expect.table_key,
            "alt_attempts": len(every) - len(calls),
            "unsupported_numbers": unsupported, "errors": errors, "passed": not errors}


def _grounding(question: str, calls: list[dict]) -> tuple[set[str], list[Fact]]:
    numbers = set(_numbers(question))
    facts: list[Fact] = []
    for call in calls:
        result = call.get("result")
        numbers |= set(_numbers(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)))
        numbers |= {n for v in (call.get("args") or {}).values() for n in _numbers(json.dumps(v, ensure_ascii=False))}
    if (answer := stat_answer(calls)) is not None:
        facts = list(answer.result.facts)
    return numbers, facts


def grade_conversation(scenario: AgentScenario, records: list[dict], calc: dict[str, CalcItem],
                       cells_of: Callable[[str], dict]) -> list[dict]:
    """Graded rows, one per scenario turn, for one run of the scenario (records in turn order; a missing turn —
    the run stopped — is a run_failure). Each row keeps its record (the transcript) for --from-run."""
    grounded: set[str] = set()
    facts: list[Fact] = []
    rows = []
    for i, turn in enumerate(scenario.turns):
        record = records[i] if i < len(records) else {"calls": [], "answer": "", "error": "turn not run"}
        numbers, new_facts = _grounding(turn.question, record.get("calls") or [])
        grounded |= numbers
        facts += new_facts
        g = grade_turn(turn.expect, record, calc, cells_of, grounded, facts)
        rows.append({**record, "sid": scenario.sid, "split": scenario.split, "turn": i, "question": turn.question, **g})
    return rows


def aggregate(rows: list[dict]) -> dict:
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
        for call in e.tools:
            args = dict(call.args)
            if call.name in STATE_TOOLS and e.table_key and "table_key" not in args:
                args["table_key"] = e.table_key
            if call.name == "search_tables":
                key = e.table_key or args.get("table_key")
                result: Any = {"route": "refuse", "picked": []} if e.refuse else {"route": "found", "picked": [{"key": key}]}
            elif call.name == "summarize_stat":
                result = oracle_stat(calc[e.calc_qid]) if e.calc_qid else {"outcome": "answered"}
            else:
                result = {"chart_type": args.get("chart_type")}
            calls.append({"name": call.name, "args": args, "result": result, "status": "ok"})
        records.append({"calls": calls, "answer": "", "error": None})
    return records
