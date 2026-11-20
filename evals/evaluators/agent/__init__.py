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
  ran. missed_refusal / false_refusal as in L3. With expect.refuse_parts (T3-38) each sub-question of a multi-query
  turn is graded on its own: its search found no table or its summarize_stat refused (state.refused_parts).
- numbers: expect.calc_qid grades the turn's last summarize_stat answer with the L3 grader as is
  (evaluators.calculation.grade against the table's Parquet cells); an answer from an alternate table (T3-41 rule,
  evaluators.alternates) against that table's cells, and that table counts for state / arguments too (T3-42).
- answer (T3-40): every number in the final answer text must come from a question, a summarize_stat fact (rounding
  allowed), a combine_values result or a default condition of the conversation so far, else
  unsupported_final_number; the answer must cite survey and table name of each answered summarize_stat, else
  missing_source.
- charts (T3-37): every number a make_chart call draws (Vega-Lite data.values) must be a fact value of the turn's
  summarize_stat answers (a chart-only turn: the conversation's latest ones), else chart_data_mismatch; where the user
  named no chart type, expect.auto_chart must be the family of the drawn kind, else wrong_chart_type.
A turn passes with no error; a conversation succeeds when every turn passes; pass^k = share of scenarios whose
k repeated runs all succeeded.

One module per evaluation task (T3-29): tools.py (tool selection, order, arguments), state.py (multi-turn state and
refusal), numbers.py (L3 numbers, multi-query sub-answers and combine), chart.py (chart data and automatic
chart choice, T3-37), answer.py (answer quality: unsupported numbers, missing source). grade_turn joins their errors in that order; aggregate adds by_task (multi-turn MT-* / multi-query MQ-*).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable, Iterable

from kosis_ai.schemas import ChartPoint, ChartSeries, ChartSpec, FactPart, Refusal, Source, StatAnswer, StatResult

from evals.evaluators.agent.answer import Grounds, grade_answer, grounding, missing_sources, unsupported_final_numbers
from evals.evaluators.agent.chart import grade_chart, table_numbers, turn_values
from evals.evaluators import alternates
from evals.evaluators.agent.numbers import AlternateOf, combined_value, grade_numbers, stat_answer, stat_answers, turn_alternates
from evals.evaluators.agent.state import STATE_TOOLS, first_key, grade_state, refused, refused_parts, state_key
from evals.evaluators.agent.tools import final_calls, grade_tools
from evals.schemas import AgentScenario, CalcItem, TurnExpect

__all__ = ["ERRORS", "Grounds", "STATE_TOOLS", "aggregate", "combined_value", "final_calls", "first_key", "grade_conversation",
           "grade_turn", "oracle_records", "oracle_stat", "refused", "refused_parts", "stat_answer", "stat_answers",
           "missing_sources", "state_key", "unsupported_final_numbers"]

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
    "unsupported_final_number": "최종 답 문장에 질문·사실 값·합산 결과에 없는 숫자 (반올림·만·천·억 단위 허용)",
    "missing_source": "답한 summarize_stat의 출처(조사명·통계표명)가 최종 답에 없음",
    "chart_data_mismatch": "차트(Vega-Lite data.values)에 summarize_stat 사실 값이 아닌 숫자가 그려짐",
    "wrong_chart_type": "차트 종류 말이 없는 턴에서 그린 차트 계열(막대·꺾은선·원형)이 기대 종류와 다름",
}
TASK_PREFIXES = {"MT-": "multiturn", "MQ-": "multiquery",  # sid prefixes the builders write (builders/multiturn, multiquery)
                 # T3-46 e2e golden (builders/e2e_golden): one task per failure type
                 "E2E-N-": "e2e_numbers", "E2E-F-": "e2e_formula", "E2E-R-": "e2e_rewrite", "E2E-C-": "e2e_classify"}
TASK_METRICS = ("n_turns", "n_conversations", "conversation_success", "pass^k", "turn_pass", "tool_selection", "param_accuracy",
                "state_accuracy", "numeric_accuracy", "n_numeric", "combine_accuracy", "n_combine", "unsupported_final_number_turns",
                "missing_source_turns", "chart_turn_pass", "n_chart_turns", "auto_chart_agreement", "n_auto_chart")


def grade_turn(expect: TurnExpect, record: dict, calc: dict[str, CalcItem], cells_of: Callable[[str, frozenset[str]], dict],
               grounds: Grounds, chart_facts: list[float] | None = None,
               values_of: Callable[[str], Iterable[float]] = table_numbers, carried: Iterable[str] = (),
               alternate_of: AlternateOf = alternates.alternate_item) -> dict:
    """One turn's metrics and errors. grounds = what grounds a number in the conversation so far (this turn's
    question and tool results included). chart_facts = the fact values this turn's charts may draw (default: this
    turn's summarize_stat answers); values_of(table_key) = every number of a table, for the context rows phase 1
    draws beside the facts (default: the Parquet value column). Graded on final_calls: alternate-table attempts are
    set aside and counted in alt_attempts, not as errors.

    T3-42: tables that hold every golden cell value of the turn's calc_qid(s) (evaluators.alternates), plus `carried`
    (accepted earlier in the conversation for the same expected table), count as right for state and tool arguments,
    as they do for the numbers. value_alternates lists them."""
    every = record.get("calls") or []
    calls = final_calls(every)
    found = sorted(set(turn_alternates(expect, calls, calc, alternate_of)) | set(carried))
    if found:
        expect = expect.model_copy(update={"alt_table_keys": sorted(set(expect.alt_table_keys) | set(found))})
    tools, tool_errors = grade_tools(expect, calls)
    state, state_errors = grade_state(expect, calls)
    numbers, number_errors = grade_numbers(expect, record, calls, calc, cells_of, alternate_of)
    chart, chart_errors = grade_chart(expect, calls, chart_facts if chart_facts is not None else turn_values(calls) or [],
                                      values_of)
    answer, answer_errors = grade_answer(record, calls, grounds)
    errors = (["run_failure"] * bool(record.get("error")) + tool_errors + state_errors + number_errors + chart_errors
              + answer_errors)
    return {**tools, **state, **numbers, "expected_table": expect.table_key, "alt_attempts": len(every) - len(calls),
            "value_alternates": found,
            **chart, **answer, "errors": errors, "passed": not errors}


def grade_conversation(scenario: AgentScenario, records: list[dict], calc: dict[str, CalcItem],
                       cells_of: Callable[[str, frozenset[str]], dict],
                       values_of: Callable[[str], Iterable[float]] = table_numbers,
                       alternate_of: AlternateOf = alternates.alternate_item) -> list[dict]:
    """Graded rows, one per scenario turn, for one run of the scenario (records in turn order; a missing turn —
    the run stopped — is a run_failure). Each row keeps its record (the transcript) for --from-run.
    values_of: every number of a table (chart context rows, T3-37; see grade_turn). alternate_of: the T3-41 rule
    (T3-42); an alternate found on one turn stays right on later turns that expect the same table ("그 표로 그려줘")."""
    grounds = Grounds()
    latest: list[float] = []  # fact values of the latest turn that called summarize_stat (a chart-only turn draws them)
    accepted: dict[str, set[str]] = defaultdict(set)  # expected table -> alternates accepted so far in the conversation
    rows = []
    for i, turn in enumerate(scenario.turns):
        record = records[i] if i < len(records) else {"calls": [], "answer": "", "error": "turn not run"}
        grounds.add(grounding(turn.question, final_calls(record.get("calls") or [])))
        own = turn_values(final_calls(record.get("calls") or []))
        latest = own if own is not None else latest
        expected = turn.expect.table_key
        g = grade_turn(turn.expect, record, calc, cells_of, grounds, latest, values_of,
                       accepted.get(expected, ()) if expected else (), alternate_of)
        if expected:
            accepted[expected].update(g["value_alternates"])
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
    charts = [r for r in rows if r.get("chart_turn")]
    auto = [r for r in rows if r.get("auto_chart_ok") is not None]
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
        "missing_source_turns": sum(bool(r.get("missing_sources")) for r in rows),
        # T3-37: turns that expect make_chart, their drawn data vs the facts, and the automatic chart choice
        "chart_turn_pass": rate([r["passed"] for r in charts]), "n_chart_turns": len(charts),
        "chart_data_accuracy": rate([r["chart_data_ok"] for r in rows if r.get("chart_data_ok") is not None]),
        "n_chart_data": sum(r.get("chart_data_ok") is not None for r in rows),
        "auto_chart_agreement": rate([r["auto_chart_ok"] for r in auto]), "n_auto_chart": len(auto),
        "auto_chart_confusion": dict(sorted(Counter(f"{r['auto_chart']}→{r['chart_kind'] or '-'}" for r in auto).items())),
        "errors": dict(Counter(e for r in rows for e in r["errors"])),
        "first_failed_turn": {str(k): v for k, v in sorted(first_fail.items())},
    }


def _oracle_source(table_key: str | None) -> Source:
    org_id, _, tbl_id = (table_key or "000_oracle").partition("_")
    return Source(org_id=org_id, tbl_id=tbl_id or "oracle", survey_nm="oracle", tbl_nm="oracle")


def oracle_stat(item: CalcItem) -> dict:
    """A summarize_stat answer that is right by construction for an L3 item (the oracle's self-check)."""
    source = _oracle_source(item.table_key)
    cite = f"출처: {source.citation()}"  # the line Phase 1 final_text ends every answer with (T3-40)
    if item.kind == "not_answerable":
        answer = StatAnswer(outcome="refused", refusal=Refusal(reason=item.allowed_reasons[0], message="oracle"), source=source,
                            text=cite)
    elif item.kind == "clarify":
        answer = StatAnswer(outcome="clarify", clarify_options=item.clarify_options, source=source, text=cite)
    else:
        facts = [*item.required_facts, *item.aux_facts]
        answer = StatAnswer(outcome="answered", result=StatResult(facts=facts), parts=[FactPart(fact_id=f.id) for f in facts],
                            default_applied=item.default_applied, source=source, text=cite)
    return answer.model_dump(mode="json")


def oracle_chart(args: dict, auto_chart: str | None, facts: list[Fact]) -> tuple[dict, dict]:
    """make_chart args and result a perfect agent leaves (T3-37): the result has the tool's content shape
    (services/chart.py) and the `chart` arg is a phase 1 ChartSpec drawing exactly the latest answer's facts, so the
    grader's rebuilt Vega-Lite holds fact values only. No facts (a structure run): nothing to draw from, no spec."""
    kind = args.get("chart_type") or auto_chart or "bar"
    drawn = [f for f in facts if f.value is not None]
    if drawn:
        points = [ChartPoint(label=f.id, value=f.value, display=str(f.value), fact_id=f.id, period=f.period) for f in drawn]
        spec = ChartSpec(kind=kind, title="oracle", series=[ChartSeries(name="oracle", points=points)],
                         fact_ids=[f.id for f in drawn])
        args = {**args, "chart": spec.model_dump(mode="json")}
    return args, {"table_key": args.get("table_key"), "chart_type": args.get("chart_type"), "kind": kind, "drawn": True,
                  "note": None}


def oracle_records(scenario: AgentScenario, calc: dict[str, CalcItem]) -> list[dict]:
    """The run record a perfect agent would leave: expected calls with expected args and consistent results."""
    records = []
    latest: list[Fact] = []  # facts of the latest oracle summarize_stat answer (what its charts draw, T3-37)
    for turn in scenario.turns:
        e = turn.expect
        calls = []
        sub_qids = iter(e.calc_qids)  # T3-28: one L3 item per summarize_stat, in call order
        stat_keys = iter([c.args.get("table_key") for c in e.tools if c.name == "summarize_stat"])
        next_key = None
        parts = e.refuse_parts
        if parts is not None:  # T3-38: sub-question k = k-th search; refusing ones stop at search unless they have a stat
            keys = [c.args.get("table_key") for c in e.tools if c.name == "summarize_stat"]
            stat_parts = list(range(len(parts))) if len(keys) == len(parts) else [k for k, r in enumerate(parts) if not r]
            part_key, searches, stat_part = dict(zip(stat_parts, keys)), iter(range(len(parts))), iter(stat_parts)
        for call in e.tools:
            args = dict(call.args)
            if call.name in STATE_TOOLS and e.table_key and "table_key" not in args:
                args["table_key"] = e.table_key
            if parts is not None and call.name == "search_tables":
                k = next(searches)
                result = ({"route": "found", "picked": [{"key": part_key[k] or e.table_key}]} if k in part_key
                          else {"route": "refuse", "picked": []})
            elif parts is not None and call.name == "summarize_stat":
                k = next(stat_part)
                qid = None if parts[k] else next(sub_qids, None)
                result = {"outcome": "refused"} if parts[k] else (oracle_stat(calc[qid]) if qid else {"outcome": "answered"})
            elif call.name == "search_tables":
                next_key = next(stat_keys, None)  # the table of the summarize_stat this search leads to
                key = (next_key if e.calc_qids else None) or e.table_key or args.get("table_key")
                result: Any = {"route": "refuse", "picked": []} if e.refuse else {"route": "found", "picked": [{"key": key}]}
            elif call.name == "summarize_stat":
                qid = e.calc_qid or next(sub_qids, None)
                source = _oracle_source(args.get("table_key"))
                result = oracle_stat(calc[qid]) if qid else {"outcome": "answered", "source": source.model_dump(mode="json"),
                                                             "text": f"출처: {source.citation()}"}
                latest = StatAnswer.model_validate(result).result.facts if qid else []
            elif call.name == "combine_values":
                result = {"op": args.get("op"), "value": e.combine_value}
            else:
                args, result = oracle_chart(args, e.auto_chart, latest)
            calls.append({"name": call.name, "args": args, "result": result, "status": "ok"})
        texts = [c["result"].get("text") for c in calls if c["name"] == "summarize_stat" and isinstance(c["result"], dict)]
        records.append({"calls": calls, "answer": "\n\n".join(t for t in texts if t), "error": None})  # as finalize joins them
    return records
