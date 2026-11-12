"""Numbers of a turn: the summarize_stat answer graded by L3 (calc_qid), each multi-query sub-answer (calc_qids)
and the combine_values result (combine_value). No LLM: evaluators.calculation against the table's Parquet cells."""

from __future__ import annotations

from typing import Callable

from kosis_ai.schemas import StatAnswer

from evals.evaluators import calculation
from evals.schemas import CalcItem, TurnExpect


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


def grade_numbers(expect: TurnExpect, record: dict, calls: list[dict], key: str | None, calc: dict[str, CalcItem],
                  cells_of: Callable[[str], dict]) -> tuple[dict, list[str]]:
    """One wrong_numbers per failing check (calc_qid, calc_qids, combine_value), so a turn can carry it more than once
    and the summary error counts stay as they were before the split (T3-29)."""
    errors = []
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
    sub_grades = []
    if expect.calc_qids:  # T3-28: one L3 grade per sub-question, in summarize_stat call order
        answers = stat_answers(calls)
        for i, qid in enumerate(expect.calc_qids):
            item = calc[qid]
            answer = answers[i] if i < len(answers) else None
            g = calculation.grade(item, answer, cells_of(item.table_key), record.get("error"))
            verdict = g.verdict
            if verdict != "PASS" and same_values(item, answer):  # alternate table holding the same numbers
                verdict = "PASS"
            sub_grades.append({"qid": qid, "verdict": verdict, "errors": g.errors})
        if any(g["verdict"] != "PASS" for g in sub_grades):
            errors.append("wrong_numbers")
    combine_ok = None
    if expect.combine_value is not None:
        got_value = combined_value(calls)
        combine_ok = got_value is not None and calculation.close(expect.combine_value, got_value, 1)
        if not combine_ok:
            errors.append("wrong_numbers")
    return {"calc": calc_grade, "sub_calc": sub_grades, "combine_ok": combine_ok}, errors
