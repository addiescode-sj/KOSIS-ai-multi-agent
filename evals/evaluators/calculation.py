"""L3 calculation evaluator: grade a StatAnswer against a CalcItem and the table's Parquet cells. No LLM.

Rules (plans/T2-03.md, written for this repo; the Phase 1 harness is not used):
- cell facts match on source_ref and value; a missing cell is wrong_period when only the period differs, else
  wrong_dims; a different unit is wrong_unit
- derived facts match on their input cells (any candidate over the same cells) and value within half a unit of
  the golden `round`
- every number in the answer must have support: cell facts equal the Parquet cell, derived facts recompute, and
  connecting text carries no digits except a fact's period or label digits ('1인 가구')
- not_answerable: refused with a reason in allowed_reasons; clarify: outcome clarify with 2+ options;
  default_allowed: the answer states default_applied with the golden keys
Verdict: any FAIL error -> FAIL, no error -> PASS, else PARTIAL.
"""

from __future__ import annotations

import ast
import operator
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from kosis_ai.schemas import MAX_DIMS, Cell, Fact, StatAnswer, TextPart

from evals.schemas import CalcItem

FACT_ERRORS = {"wrong_value", "wrong_unit", "wrong_period", "wrong_dims"}
FAIL = {
    "answerable": FACT_ERRORS | {"unsupported_claim", "false_refusal", "run_failure"},
    "not_answerable": {"missed_refusal", "wrong_reason", "unsupported_claim", "run_failure"},
    "clarify": {"assumed_condition", "missing_clarification", "unsupported_claim", "run_failure"},
    "default_allowed": FACT_ERRORS | {"disallowed_default", "unsupported_claim", "run_failure"},
}
# Error -> what went wrong, for report.html and evals/README.md; the stage evals.trace.blame usually gives it
ERROR_INFO = {
    "wrong_value": "정답 셀은 골랐는데 값이 다르다 (파생값은 반올림 범위 밖)",
    "wrong_unit": "값은 같은데 단위가 다르다 — 보통 같은 숫자를 가진 다른 항목(예: '년' 대신 '%')을 골랐다",
    "wrong_period": "항목·분류는 맞는데 시점이 다른 셀을 썼다",
    "wrong_dims": "필요한 셀이 답에 없다 — 다른 항목·지역·분류 셀을 골랐거나 파생값의 입력 셀이 다르다",
    "missing_aux": "답의 주 숫자는 맞지만 함께 보여야 할 보조 셀(비교 대상·분모 등)이 빠졌다 (PARTIAL)",
    "unsupported_claim": "답에 근거 없는 숫자가 있다 — 표 셀과 다르거나, 파생값이 다시 계산되지 않거나, 연결 문장에 숫자를 지어냈다",
    "false_refusal": "답할 수 있는 질문인데 거절·되묻기를 했다",
    "missed_refusal": "답할 수 없는 질문(기간·범위 밖)인데 답을 했다",
    "wrong_reason": "거절은 맞지만 거절 사유가 정답 사유 목록에 없다",
    "assumed_condition": "되물어야 할 모호한 질문인데 조건을 임의로 정해 답했다",
    "missing_clarification": "되묻기는 했지만 선택지가 2개 미만이다",
    "disallowed_default": "기본값 적용이 허용된 질문에서 답하지 않았거나, 허용되지 않은 기본값을 적용했다",
    "unstated_default": "기본값을 적용했는데 답에 무엇을 적용했는지 밝히지 않았다 (PARTIAL)",
    "missing_source": "답에 출처(KOSIS / 조사명 / 통계표명)가 없다 — source가 비었거나 답 문장에 조사명·통계표명이 없다 (PARTIAL)",
    "run_failure": "답이 없다 — 예외, 모델 호출 실패, 검색 결과 없음",
}
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_IDENT = re.compile(r"[A-Za-z_]\w*")
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow}


class CalcGrade(BaseModel):
    qid: str
    kind: str
    outcome: str  # answered | refused | clarify | error
    verdict: str  # PASS | PARTIAL | FAIL
    errors: list[str] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)


@lru_cache(maxsize=16)
def read_cells(path: Path, refs: frozenset[str] | None = None) -> dict[str, Cell]:
    """Cells of one Parquet table keyed by Cell.ref() (the source_ref format).

    With `refs`, only those cells are built: the file is scanned batch by batch and the ref of every row is made
    from its key columns with pyarrow, so a 10 MB table (2M rows, 4 GB and 70 s as Python objects) costs a few
    hundred MB and a few seconds. Grading needs only the golden facts' cells and the answer's cells (grade_refs).
    Without `refs`, every cell is built (small tables, the builders)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if refs is None:
        return {c.ref(): c for c in map(Cell.from_row, pq.read_table(path).to_pylist())}
    out: dict[str, Cell] = {}
    if not refs:
        return out
    wanted = pa.array(sorted(refs))
    file = pq.ParquetFile(path)
    dims = [f"dim{i}_code" for i in range(1, MAX_DIMS + 1) if f"dim{i}_code" in file.schema_arrow.names]
    for batch in file.iter_batches(batch_size=200_000):
        parts = [batch.column("tbl_id"), batch.column("item_id"), *(batch.column(d) for d in dims), batch.column("period")]
        # a missing dim code must leave no empty field: join skips nulls (null_handling='skip')
        ref = pc.binary_join_element_wise(*(pc.cast(p, pa.string()) for p in parts), "|", null_handling="skip")
        hit = pc.is_in(ref, value_set=wanted)
        if pc.any(hit).as_py():
            out.update((c.ref(), c) for c in map(Cell.from_row, batch.filter(hit).to_pylist()))
    return out


def grade_refs(item: CalcItem, answer: StatAnswer | None) -> frozenset[str]:
    """The cells grade() reads: the golden facts' and the answer's source_refs (read_cells(path, refs))."""
    facts = [*item.required_facts, *item.aux_facts]
    if answer is not None and answer.result is not None:
        facts += answer.result.facts
    return frozenset(f.source_ref for f in facts if f.source_ref)


def item_cells(item: CalcItem, answer: StatAnswer | None, cells_of) -> dict[str, Cell]:
    """grade()'s cells over every table of the item (T3-56): cells_of(table_key, refs) per table, merged.
    A ref names its table by tbl_id, so each table returns only its own refs."""
    refs = grade_refs(item, answer)
    return {ref: cell for key in item.tables() for ref, cell in cells_of(key, refs).items()}


def merge_answers(answers: list[StatAnswer]) -> StatAnswer | None:
    """The answered summarize_stat answers of one turn as one answer (T3-56: a multi-table item graded against the
    per-table sub-answers). Fact ids get a per-answer prefix (derivations rewritten) so f1 of two answers stay apart."""
    answered = [a for a in answers if a is not None and a.outcome == "answered" and a.result is not None]
    if len(answered) < 2:
        return answered[0] if answered else next((a for a in answers if a is not None), None)
    facts, parts = [], []
    for i, a in enumerate(answered):
        ids = {f.id for f in a.result.facts}
        rename = lambda m, ids=ids, i=i: f"s{i}_{m.group(0)}" if m.group(0) in ids else m.group(0)  # noqa: E731
        facts += [f.model_copy(update={"id": f"s{i}_{f.id}",
                                       "derivation": _IDENT.sub(rename, f.derivation) if f.derivation else None})
                  for f in a.result.facts]
        parts += [p.model_copy(update={"fact_id": f"s{i}_{p.fact_id}"}) if p.kind == "fact" else p for p in a.parts]
    first = answered[0]
    return first.model_copy(update={"result": first.result.model_copy(update={"facts": facts}), "parts": parts,
                                    "text": "\n\n".join(a.text for a in answered if a.text)})


def evaluate(expr: str, values: dict[str, float]) -> float:
    """A derivation such as '(f2 / f1 - 1) * 100' over fact values: + - * / ** and parentheses only."""

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            return values[node.id]
        raise ValueError(f"unsupported expression {ast.dump(node)}")

    return ev(ast.parse(expr, mode="eval").body)


def inputs(fact: Fact, by_id: dict[str, Fact]) -> frozenset[str]:
    """source_refs of the cells a fact is built from (itself for a cell fact)."""
    if fact.source_ref:
        return frozenset([fact.source_ref])
    names = _IDENT.findall(fact.derivation or "")
    return frozenset().union(*(inputs(by_id[n], by_id) for n in names if n in by_id))


def close(expected: float | None, actual: float | None, digits: int | None) -> bool:
    if expected is None or actual is None:
        return expected is actual
    tol = 0.5 * 10**-digits if digits is not None else 1e-9 * max(1.0, abs(expected))
    return abs(expected - actual) <= tol + 1e-12


def _no_period(ref: str) -> str:
    return ref.rsplit("|", 1)[0]


def fact_errors(item: CalcItem, answer: StatAnswer, details: list[str]) -> set[str]:
    errors: set[str] = set()
    gold_by_id = {f.id: f for f in item.required_facts + item.aux_facts}
    got_by_id = {f.id: f for f in answer.result.facts}
    got_cells = {f.source_ref: f for f in answer.result.facts if f.source_ref}
    got_derived = [(f, inputs(f, got_by_id)) for f in answer.result.facts if f.derivation]
    for want in item.required_facts:
        if want.source_ref:
            got = got_cells.get(want.source_ref)
            if got is None:
                same_cell = any(_no_period(r) == _no_period(want.source_ref) for r in got_cells)
                errors.add("wrong_period" if same_cell else "wrong_dims")
                details.append(f"{want.id}: cell {want.source_ref} missing")
            elif not close(want.value, got.value, None):
                errors.add("wrong_value")
                details.append(f"{want.id}: value {got.value} != {want.value}")
            elif want.unit and got.unit and want.unit != got.unit:
                errors.add("wrong_unit")
                details.append(f"{want.id}: unit {got.unit!r} != {want.unit!r}")
        elif want.derivation:
            key = inputs(want, gold_by_id)
            candidates = [f for f, ins in got_derived if ins == key]
            if not candidates:
                same_cells = any({_no_period(r) for r in ins} == {_no_period(r) for r in key} for _, ins in got_derived)
                errors.add("wrong_period" if same_cells else "wrong_dims")
                details.append(f"{want.id}: no derived fact over {sorted(key)}")
            elif not any(close(want.value, f.value, want.round) for f in candidates):
                errors.add("wrong_value")
                details.append(f"{want.id}: derived {[f.value for f in candidates]} != {want.value}")
    for want in item.aux_facts:
        if want.source_ref and want.source_ref not in got_cells:
            errors.add("missing_aux")
            details.append(f"{want.id}: aux cell {want.source_ref} missing")
    return errors


def allowed_text_numbers(facts: list[Fact]) -> set[str]:
    """Digits connecting text may hold without being a claim: a fact's period ('2025', '3' for 202503, '1' for Q1)
    and digits inside a fact's labels ('1인 가구', '1985년생')."""
    allowed: set[str] = set()
    for f in facts:
        if f.period:
            allowed |= {f.period, f.period[:4]}
            if len(f.period) == 6 and f.period.isdigit():
                month = int(f.period[4:])
                allowed |= {str(month), str((month - 1) // 3 + 1)}
        for label in (f.dims or {}).values():
            allowed |= set(_NUMBER.findall(label))
    return allowed


def unsupported_numbers(answer: StatAnswer, cells: dict[str, Cell], details: list[str]) -> int:
    count = 0
    by_id = {f.id: f for f in answer.result.facts}
    for f in answer.result.facts:
        if f.source_ref:
            cell = cells.get(f.source_ref)
            if cell is None or not close(cell.value, f.value, None):
                count += 1
                details.append(f"unsupported {f.id}: {f.source_ref} = {f.value}")
        elif f.derivation:
            try:
                expected = round(evaluate(f.derivation, {k: v.value for k, v in by_id.items() if v.value is not None}), f.round)
            except (KeyError, ValueError, ZeroDivisionError, SyntaxError, TypeError):
                expected = None
            if not close(expected, f.value, f.round):
                count += 1
                details.append(f"unsupported {f.id}: {f.derivation} does not recompute to {f.value}")
    allowed = allowed_text_numbers(answer.result.facts)
    for part in answer.parts:
        if isinstance(part, TextPart):
            for num in _NUMBER.findall(part.text):
                if num not in allowed:
                    count += 1
                    details.append(f"unsupported text number {num!r}")
    return count


def missing_source(answer: StatAnswer) -> bool:
    """An answered StatAnswer that does not cite its source: no source, or survey / table name not in its text."""
    if answer.outcome != "answered":
        return False
    source = answer.source
    return source is None or source.survey_nm not in (answer.text or "") or source.tbl_nm not in (answer.text or "")


def grade(item: CalcItem, answer: StatAnswer | None, cells: dict[str, Cell], error: str | None = None,
          cite: bool = True) -> CalcGrade:
    """answer None means the call failed (error says why): run_failure, never a refusal. cite=False skips the source
    check (the agent layer checks the final message, evaluators.agent.answer)."""
    if answer is None:
        return CalcGrade(qid=item.qid, kind=item.kind, outcome="error", verdict="FAIL", errors=["run_failure"],
                         details=[error or "no answer"])
    details: list[str] = []
    errors: set[str] = set()
    if unsupported_numbers(answer, cells, details):
        errors.add("unsupported_claim")
    if cite and missing_source(answer):
        errors.add("missing_source")
        details.append("source missing" if answer.source is None else f"text does not cite {answer.source.citation()!r}")
    if item.kind in ("answerable", "default_allowed"):
        if answer.outcome != "answered":
            errors.add("false_refusal" if item.kind == "answerable" else "disallowed_default")
            details.append(f"outcome {answer.outcome} on a {item.kind} question")
        else:
            errors |= fact_errors(item, answer, details)
            if item.kind == "default_allowed":
                if not answer.default_applied:
                    errors.add("unstated_default")
                elif answer.default_applied.keys() != item.default_applied.keys():
                    errors.add("disallowed_default")
    elif item.kind == "not_answerable":
        if answer.outcome != "refused":
            errors.add("missed_refusal")
        elif answer.refusal is None or answer.refusal.reason not in item.allowed_reasons:
            errors.add("wrong_reason")
            details.append(f"reason {answer.refusal.reason if answer.refusal else None} not in {item.allowed_reasons}")
    elif item.kind == "clarify":
        if answer.outcome != "clarify":
            errors.add("assumed_condition")
        elif len(answer.clarify_options) < 2:
            errors.add("missing_clarification")
    verdict = "FAIL" if errors & FAIL[item.kind] else "PASS" if not errors else "PARTIAL"
    return CalcGrade(qid=item.qid, kind=item.kind, outcome=answer.outcome, verdict=verdict, errors=sorted(errors), details=details)
