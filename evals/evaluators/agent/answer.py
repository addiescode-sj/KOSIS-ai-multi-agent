"""Answer quality of a turn: every number in the final answer text must come from a tool result or a question of the
conversation so far (rounding of a fact value allowed); a number the LLM wrote itself is unsupported_final_number."""

from __future__ import annotations

import json

from kosis_ai.schemas import Fact

from evals.evaluators import calculation
from evals.evaluators.agent.numbers import stat_answers


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


def grounding(question: str, calls: list[dict]) -> tuple[set[str], list[Fact]]:
    numbers = set(_numbers(question))
    facts: list[Fact] = []
    for call in calls:
        result = call.get("result")
        numbers |= set(_numbers(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)))
        numbers |= {n for v in (call.get("args") or {}).values() for n in _numbers(json.dumps(v, ensure_ascii=False))}
    for answer in stat_answers(calls):  # every sub-question's facts (T3-28); one call = the last answer as before
        if answer is not None:
            facts += answer.result.facts
    return numbers, facts


def grade_answer(record: dict, grounded: set[str], facts: list[Fact]) -> tuple[dict, list[str]]:
    unsupported = unsupported_final_numbers(record.get("answer") or "", grounded, facts)
    return {"unsupported_numbers": unsupported}, ["unsupported_final_number"] if unsupported else []
