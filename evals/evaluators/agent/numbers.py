"""Numbers of a turn: the summarize_stat answer graded by L3 (calc_qid), each multi-query sub-answer (calc_qids)
and the combine_values result (combine_value). No LLM: evaluators.calculation against the table's Parquet cells.

T3-42: an answer read from another table that holds every golden cell value (evaluators.alternates, the T3-41 rule)
is graded against that table's cells, as e2e does; it replaced T3-27's same_values (any equal value anywhere passed)."""

from __future__ import annotations

from typing import Callable

from kosis_ai.schemas import StatAnswer

from evals.evaluators import alternates, calculation
from evals.evaluators.agent.state import sub_calls
from evals.schemas import CalcItem, TurnExpect

# (golden item, table key, refs the answer cited) -> the item moved to that table, or None when it is no alternate
AlternateOf = Callable[[CalcItem, "str | None", frozenset], "CalcItem | None"]


def _as_answer(call: dict) -> StatAnswer | None:
    if call.get("status", "ok") != "ok":
        return None
    try:
        return StatAnswer.model_validate(call.get("result"))
    except ValueError:
        return None


def stat_answer(calls: list[dict]) -> StatAnswer | None:
    """The last successful summarize_stat result as a StatAnswer; None when there is none or it does not validate."""
    for call in reversed(calls):
        if call["name"] == "summarize_stat" and call.get("status", "ok") == "ok":
            return _as_answer(call)
    return None


def stat_answers(calls: list[dict]) -> list[StatAnswer | None]:
    """Every summarize_stat result in call order (None where it failed or does not validate). T3-28 calc_qids."""
    return [_as_answer(c) for c in calls if c["name"] == "summarize_stat"]


def combined_value(calls: list[dict]) -> float | None:
    """The value of the last combine_values call (T3-28), None when it did not compute one."""
    for call in reversed(calls):
        if call["name"] == "combine_values" and isinstance(call.get("result"), dict):
            return call["result"].get("value")
    return None



def table_key(call: dict | None) -> str | None:
    """The table a summarize_stat answer's numbers came from: its source (tables.csv keys are '<org_id>_<tbl_id>'),
    else the call's table_key argument. Source first: the argument can name one table while the facts cite another
    (the oracle answers an L1 alternate turn with the golden table's facts)."""
    if not call:
        return None
    src = (call.get("result") or {}).get("source") if isinstance(call.get("result"), dict) else None
    if src and src.get("org_id") and src.get("tbl_id"):
        return f"{src['org_id']}_{src['tbl_id']}"
    return (call.get("args") or {}).get("table_key")


def grade_one(item: CalcItem, call: dict | None, error: str | None, cells_of: Callable[[str, frozenset[str]], dict],
              alternate_of: AlternateOf = alternates.alternate_item) -> dict:
    """L3 grade of one summarize_stat call: against the golden table, or against the table it answered from when
    that table is an alternate (via_alternate = passed there; graded_table names it)."""
    answer = _as_answer(call) if call else None
    cited = frozenset(f.source_ref for f in answer.result.facts if f.source_ref) if answer is not None else frozenset()
    alt = alternate_of(item, table_key(call), cited) if answer is not None else None
    graded = alt or item
    g = calculation.grade(graded, answer, cells_of(graded.table_key, calculation.grade_refs(graded, answer)), error, cite=False)
    out = {"verdict": g.verdict, "errors": g.errors}
    if alt is not None:
        out |= {"graded_table": alt.table_key, "via_alternate": g.verdict == "PASS"}
    return out


def grade_numbers(expect: TurnExpect, record: dict, calls: list[dict], calc: dict[str, CalcItem],
                  cells_of: Callable[[str, frozenset[str]], dict],
                  alternate_of: AlternateOf = alternates.alternate_item) -> tuple[dict, list[str]]:
    """One wrong_numbers per failing check (calc_qid, calc_qids, combine_value), so a turn can carry it more than once
    and the summary error counts stay as they were before the split (T3-29). cells_of(table_key, refs) returns the
    cells of those refs (calculation.read_cells); alternate_of decides alternate tables (tests inject it)."""
    errors = []
    calc_grade = None
    stats = [c for c in calls if c["name"] == "summarize_stat"]
    if expect.calc_qid:
        last_ok = next((c for c in reversed(stats) if c.get("status", "ok") == "ok"), None)  # = stat_answer(calls)
        calc_grade = grade_one(calc[expect.calc_qid], last_ok, record.get("error"), cells_of, alternate_of)
        if calc_grade["verdict"] != "PASS":
            errors.append("wrong_numbers")
    sub_grades = []
    if expect.calc_qids:  # T3-28: one L3 grade per sub-question, in summarize_stat call order
        parts: list[dict | None] = stats
        if expect.refuse_parts is not None and (subs := sub_calls(calls, len(expect.refuse_parts))) is not None:
            # T3-38: a sub-question that must refuse has no calc_qid; calc_qids are the answering sub-questions' items
            parts = [s for s, refuse in zip(subs, expect.refuse_parts) if not refuse]
        for i, qid in enumerate(expect.calc_qids):
            g = grade_one(calc[qid], parts[i] if i < len(parts) else None, record.get("error"), cells_of, alternate_of)
            sub_grades.append({"qid": qid, **g})
        if any(g["verdict"] != "PASS" for g in sub_grades):
            errors.append("wrong_numbers")
    combine_ok = None
    if expect.combine_value is not None:
        got_value = combined_value(calls)
        combine_ok = got_value is not None and calculation.close(expect.combine_value, got_value, 1)
        if not combine_ok:
            errors.append("wrong_numbers")
    return {"calc": calc_grade, "sub_calc": sub_grades, "combine_ok": combine_ok}, errors


def turn_alternates(expect: TurnExpect, calls: list[dict], calc: dict[str, CalcItem],
                    alternate_of: AlternateOf = alternates.alternate_item) -> list[str]:
    """The tables this turn's summarize_stat calls answered from that are alternates of its calc_qid / calc_qids
    (T3-41 rule): grade_turn adds them to the expected tables, so state and tool-argument grading accept the same
    tables the numbers are graded on."""
    qids = [q for q in [expect.calc_qid, *expect.calc_qids] if q and q in calc]
    keys = {table_key(c) for c in calls if c["name"] == "summarize_stat"} - {None, expect.table_key}
    return sorted(k for k in keys if any(alternate_of(calc[q], k, frozenset()) for q in qids))
