"""combine_values 툴: 서로 다른 표에서 받은 하위 답 두 개의 숫자를 코드가 합친다 (T3-28 멀티쿼리).

"2024년 경기도 외국인주민 수와 출생아 수를 더하면?"처럼 표가 다른 두 값을 계산해야 할 때 쓴다. 숫자는 실전 1이
원자료에서 계산한 사실(Fact) 값에서만 가져오고, 계산도 여기서 코드가 한다(LLM이 만든 숫자 없음).

계산하지 않는 경우 (value None + 이유, 지어내지 않음):
- 하위 답이 답하지 않았거나(거절·되묻기), 답한 숫자가 하나가 아니다(어느 숫자를 쓸지 코드가 고를 근거가 없음)
- 두 값의 단위가 다르다("명"과 "가구"를 더하지 않음)
- 나눗셈(ratio)에서 분모가 0이다

툴 계약 (evals/evaluators/agent.py가 읽는 형태):
- 인자: op (diff | sum | ratio, 채점 필드 evals/schemas.ARG_FIELDS) + answers (StatAnswer JSON 두 개, 질문 순서)
- content·artifact: {"op", "value", "unit", "inputs": [셀 키…], "reason"}. 하네스는 value를 기대값과 비교하고,
  이 결과에 든 숫자를 "툴 결과에 있는 숫자"로 인정한다(답 문장의 계산값이 근거 없는 숫자로 잡히지 않음).
"""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import BaseTool, tool

from kosis_ai.schemas import Fact, StatAnswer

Op = Literal["diff", "sum", "ratio"]
OP_NAMES = {"diff": "차이", "sum": "합", "ratio": "비율"}


def single_fact(answer: StatAnswer) -> Fact | None:
    """하위 답의 숫자 하나. 답하지 않았거나, 값이 있는 사실이 하나가 아니면 None.

    context_fact_ids(묻지 않았는데 곁들인 사실)는 뺀다. 실전 1 값(value) 질문의 답은 사실 하나다.
    """
    if answer.outcome != "answered":
        return None
    facts = [f for f in answer.result.facts if f.value is not None and f.id not in set(answer.context_fact_ids)]
    return facts[0] if len(facts) == 1 else None


def combine(op: Op, answers: list[StatAnswer]) -> dict:
    """두 하위 답의 숫자를 op로 합친다. diff = 앞 − 뒤, ratio = 앞 ÷ 뒤 × 100 (%)."""
    out = {"op": op, "value": None, "unit": None, "inputs": [], "reason": None}
    if len(answers) != 2:
        return {**out, "reason": "two_answers_needed"}
    facts = [single_fact(a) for a in answers]
    if any(f is None for f in facts):
        return {**out, "reason": "not_single_value"}
    a, b = facts
    out["inputs"] = [a.source_ref, b.source_ref]
    if (a.unit or "") != (b.unit or "") and op != "ratio":
        return {**out, "reason": "unit_mismatch"}
    if op == "diff":
        value, unit = a.value - b.value, a.unit
    elif op == "sum":
        value, unit = a.value + b.value, a.unit
    else:
        if b.value == 0:
            return {**out, "reason": "zero_denominator"}
        value, unit = round(a.value / b.value * 100, 1), "%"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return {**out, "value": value, "unit": unit}


def combine_text(result: dict) -> str:
    """계산 결과를 코드 고정 문장으로. 계산하지 않았으면 그 이유를 말한다."""
    name = OP_NAMES[result["op"]]
    if result["value"] is None:
        why = "단위가 달라서" if result["reason"] == "unit_mismatch" else "값이 하나로 정해지지 않아서"
        return f"두 값의 계산({name})은 {why} 하지 않았어요."
    value = f"{result['value']:,}" if isinstance(result["value"], int) else f"{result['value']:,.1f}"
    return f"두 값의 계산 결과({name}): {value}{result['unit'] or ''}"


def make_combine_values_tool() -> BaseTool:
    @tool("combine_values", response_format="content_and_artifact")
    def combine_values(op: Op, answers: list[dict]) -> tuple[dict, dict]:
        """서로 다른 표에서 받은 summarize_stat 답 두 개(answers, 질문 순서)의 숫자를 코드로 합친다.
        op: diff(앞 − 뒤) | sum(합) | ratio(앞 ÷ 뒤 × 100, %). 숫자가 하나가 아니거나 단위가 다르면 계산하지 않는다."""
        result = combine(op, [StatAnswer.model_validate(a) for a in answers])
        return result, result

    return combine_values
