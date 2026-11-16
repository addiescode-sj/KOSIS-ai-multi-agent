"""Answer quality of a turn (T3-36, KOS-196 · KOS-198). No LLM.

- unsupported_final_number: every number in the final answer text must be grounded in the conversation so far:
  a number of a question, a summarize_stat fact value (rounding allowed) or a digit connecting text may hold
  (calculation.allowed_text_numbers: periods, label digits), a combine_values result (rounding allowed; values are
  compared by size, as a change is written with a direction word), or a number
  of the conditions summarize_stat applied by default (default_applied), or a number in a fact's unit. The cited
  source (citation, url, survey and table names, table id) and the lines Phase 1 code writes outside the parts
  (caveats '참고: ...', the refusal message) are cut out of the text first, verbatim. Search results, cell refs, item codes and tool arguments do
  not ground a number. Korean magnitudes are read as one number: '약 10만' = 100000 (within 5,000), '1억 2천만'.
- missing_source: the final answer must carry survey_nm and tbl_nm of the source of every summarize_stat that
  answered in the turn (each multi-query sub-answer too). Refused / clarify turns, turns with no answer and turns
  that raised (run_failure, no final message) skip it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kosis_ai.schemas import Fact, Source, StatAnswer

from evals.evaluators import calculation
from evals.evaluators.agent.numbers import combined_value, stat_answers

CAVEAT_PREFIX = "참고: "  # Phase 1 render/text.py final_text: one line per caveat
MAGNITUDES = {"조": 10**12, "억": 10**8, "천만": 10**7, "백만": 10**6, "만": 10**4, "천": 10**3}
_TOKEN = re.compile(r"(\d[\d,]*(?:\.\d+)?)(천만|백만|조|억|만|천)?")


@dataclass
class Grounds:
    """What grounds a number, accumulated over the conversation so far."""

    numbers: set[str] = field(default_factory=set)  # exact numbers (questions, default conditions)
    facts: list[Fact] = field(default_factory=list)
    values: list[float] = field(default_factory=list)  # combine_values results (rounding allowed)
    sources: list[Source] = field(default_factory=list)  # cited sources, cut out of the text before checking
    code_lines: set[str] = field(default_factory=set)  # Phase 1's own lines (caveats, refusal), cut out the same way

    def add(self, other: Grounds) -> None:
        self.numbers |= other.numbers
        self.facts += other.facts
        self.values += other.values
        self.sources += other.sources
        self.code_lines |= other.code_lines


@dataclass(frozen=True)
class Claim:
    """One number as the text writes it. value / digits are the expanded number and its precision (digits after
    the point, negative for a magnitude: '10만' -> -4); raw / unit_word keep the digits before a single magnitude."""

    text: str
    value: float
    digits: int
    raw: str | None = None
    unit_word: str | None = None


def _decimals(num: str) -> int:
    return len(num.split(".")[1]) if "." in num else 0


def _norm(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def claims(text: str) -> list[Claim]:
    """Numbers of a text, with Korean magnitudes joined: '3천 명' -> 3000, '1억 2천만' -> 120000000."""
    out: list[Claim] = []
    chain: list[re.Match] = []

    def flush() -> None:
        if not chain:
            return
        nums = [m.group(1).replace(",", "") for m in chain]
        words = [m.group(2) for m in chain]
        if words[0] is None:
            out.append(Claim(nums[0], float(nums[0]), _decimals(nums[0])))
        else:
            value = sum(float(n) * (MAGNITUDES[w] if w else 1) for n, w in zip(nums, words))
            last_scale = MAGNITUDES[words[-1]] if words[-1] else 1
            digits = _decimals(nums[-1]) - (len(str(last_scale)) - 1)
            single = len(chain) == 1
            out.append(Claim(text[chain[0].start():chain[-1].end()], value, digits,
                             raw=nums[0] if single else None, unit_word=words[0] if single else None))
        chain.clear()

    for m in _TOKEN.finditer(text or ""):
        prev = chain[-1] if chain else None
        joins = (prev is not None and prev.group(2) is not None and text[prev.end():m.start()].strip() == ""
                 and m.start() - prev.end() <= 1
                 and (m.group(2) is None or MAGNITUDES[m.group(2)] < MAGNITUDES[prev.group(2)]))
        if not joins:
            flush()
        chain.append(m)
        if m.group(2) is None:  # a plain number ends a chain ('1억 2천만 5' is unusual; '5' stays alone)
            flush()
    flush()
    return out


def _exact(text: str) -> set[str]:
    """Numbers of a text as exact strings, both as written ('100') and expanded ('100만' -> '1000000')."""
    out = set()
    for c in claims(text):
        out.add(_norm(c.value))
        if c.raw is not None:
            out.add(c.raw)
    return out


def _strip_sources(text: str, grounds: Grounds) -> str:
    pieces = {p for s in grounds.sources for p in (s.citation(), s.url, s.survey_nm, s.tbl_nm, f"{s.org_id}_{s.tbl_id}", s.tbl_id)
              if p and not p.isdigit()}  # a bare number is never cut: it could hide a claim
    pieces |= grounds.code_lines
    for piece in sorted(pieces, key=len, reverse=True):  # the whole citation before its parts
        text = text.replace(piece, " ")
    return text


def unsupported_final_numbers(answer: str, grounds: Grounds) -> list[str]:
    """Numbers in the final answer that nothing in grounds supports (see the module docstring)."""
    units = {n for f in grounds.facts if f.unit for n in calculation._NUMBER.findall(f.unit)}  # '가임여자 1명당 명'
    allowed = {_norm(float(n.replace(",", ""))) for n in grounds.numbers | units | calculation.allowed_text_numbers(grounds.facts)}
    # Phase 1 writes a change as its size and a direction word ('43,156.2건 줄었습니다'), so compare sizes
    values = [abs(v) for v in [f.value for f in grounds.facts if f.value is not None] + grounds.values]
    unit_words = {w for f in grounds.facts if f.unit for w in MAGNITUDES if f.unit.startswith(w)}
    out = []
    for c in claims(_strip_sources(answer or "", grounds)):
        if _norm(c.value) in allowed or any(calculation.close(v, c.value, c.digits) for v in values):
            continue
        if c.raw is not None and c.unit_word in unit_words:  # '1,234억 원' on a fact in 억원: the digits are the value
            raw = float(c.raw)
            if _norm(raw) in allowed or any(calculation.close(v, raw, _decimals(c.raw)) for v in values):
                continue
        out.append(c.text.replace(",", ""))
    return out


def grounding(question: str, calls: list[dict]) -> Grounds:
    """What this turn adds to the grounds: the question's numbers and the facts, default conditions and sources of
    every summarize_stat answer (T3-28: each sub-question), and the combine_values result."""
    g = Grounds(numbers=_exact(question))
    for answer in stat_answers(calls):
        if answer is None:
            continue
        g.facts += answer.result.facts
        g.numbers |= {n for v in answer.default_applied.values() for n in _exact(v)}
        if answer.source is not None:
            g.sources.append(answer.source)
        # lines code writes outside the parts: caveats ('참고: ...', S6 Caveat.text) and the refusal message
        g.code_lines |= {line for line in answer.text.splitlines() if line.startswith(CAVEAT_PREFIX)}
        if answer.refusal is not None:
            g.code_lines.add(answer.refusal.message)
    if (value := combined_value(calls)) is not None:
        g.values.append(value)
    return g


def missing_sources(answer: str, answers: list[StatAnswer | None]) -> list[str]:
    """'org_tbl' of each answered summarize_stat whose survey and table names the final answer lacks
    ('?' for an answer with no source at all)."""
    out = []
    for a in answers:
        if a is None or a.outcome != "answered":
            continue
        if a.source is None:
            out.append("?")
        elif a.source.survey_nm not in answer or a.source.tbl_nm not in answer:
            out.append(f"{a.source.org_id}_{a.source.tbl_id}")
    return out


def grade_answer(record: dict, calls: list[dict], grounds: Grounds) -> tuple[dict, list[str]]:
    """calls = the turn's final calls (alternate-table attempts set aside); grounds = the conversation so far."""
    text = record.get("answer") or ""
    unsupported = unsupported_final_numbers(text, grounds)
    missing = [] if record.get("error") else missing_sources(text, stat_answers(calls))  # a crash is run_failure only
    errors = ["unsupported_final_number"] * bool(unsupported) + ["missing_source"] * bool(missing)
    return {"unsupported_numbers": unsupported, "missing_sources": missing}, errors
