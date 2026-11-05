"""Golden set contracts per evaluation layer (T2-03), one jsonl file each under evals/datasets/ (gitignored).

    python -m evals.schemas                      # check the L1 golden set and print its distribution

L1 RetrievalItem (T2-02): a question with up to 3 required table slots (compound questions use at most 3 tables).
A slot is a set of interchangeable table keys ("<org_id>_<tbl_id>"); a hit on any key of a slot covers that slot.
Unanswerable items have no slots and a reason from Phase 1 RefusalReason, so L4 grading can reuse them as is.
L2 QueryItem and L4 E2EItem are fixed here and filled when their targets exist (T2-14 parser, T2-10 search API).
L3 CalcItem is the Phase 1 golden set converted as data (evals/builders/phase1.py); no Phase 1 eval code is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from kosis_ai.schemas import Fact, QuestionKind, QuestionType, RefusalReason

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "evals" / "datasets"
GOLDEN = DATASETS / "retrieval_golden.jsonl"
QUERY_GOLDEN = DATASETS / "query_golden.jsonl"  # L2 slot labels, same qids as GOLDEN (T2-14 draft, T2-28 labels)
TABLES = ROOT / "data" / "tables.jsonl"
MAX_SLOTS = 3  # AI engineering decision (10/29): a compound question uses at most 3 tables

QType = Literal["exact_name", "concept", "sibling", "multi_condition", "period_cycle", "multi_table", "ambiguous", "no_answer"]
QTYPES: tuple[str, ...] = get_args(QType)
# Subset of kosis_ai.schemas.RefusalReason; a frequency mismatch is no_period with qtype period_cycle.
NoAnswerReason = Literal["out_of_scope", "no_period", "no_item"]


class GoldenError(ValueError):
    pass


class RetrievalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qid: str = Field(min_length=1)
    split: Literal["dev", "test"]
    qtype: QType
    source: Literal["scenario", "mentor_poc", "phase1", "v2"]  # v2: T2-32, one sampled table per question
    question: str = Field(min_length=1)
    answerable: bool = True
    required: list[list[str]] = Field(default_factory=list)  # slots; each slot = interchangeable keys
    no_answer_reason: NoAnswerReason | None = None
    constraints: dict[str, str] = Field(default_factory=dict)  # e.g. {"period": "2023", "cycle": "Y"} for filter tests
    phase1_id: str | None = None  # Phase 1 golden id when the question comes from there (L4 bridge)
    label_version: int = 1  # bumped when a test slot gains a key after adjudication (keys are only ever added)
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> RetrievalItem:
        if self.answerable:
            if not 1 <= len(self.required) <= MAX_SLOTS or any(not slot for slot in self.required):
                raise ValueError(f"answerable item needs 1-{MAX_SLOTS} non-empty slots")
            if self.no_answer_reason is not None or self.qtype == "no_answer":
                raise ValueError("answerable item cannot have no_answer_reason or qtype no_answer")
            if (self.qtype == "multi_table") != (len(self.required) > 1):
                raise ValueError("qtype multi_table <=> more than one slot")
        elif self.required or self.no_answer_reason is None or self.qtype != "no_answer":
            raise ValueError("unanswerable item needs required=[], a no_answer_reason and qtype no_answer")
        return self

    @property
    def gold_keys(self) -> set[str]:
        return {key for slot in self.required for key in slot}

    @property
    def primary_keys(self) -> set[str]:
        """First key of each slot: the table the question is really about (alternatives follow it)."""
        return {slot[0] for slot in self.required}


class QueryItem(BaseModel):
    """L2: fields a query parser must pull out of the question. Only fields that compare deterministically."""

    model_config = ConfigDict(extra="forbid")

    qid: str = Field(min_length=1)  # same id as the L1 item
    split: Literal["dev", "test"]
    question: str = Field(min_length=1)
    period_start: str | None = None  # "YYYY" or "YYYYMM"; None when the question names no period
    period_end: str | None = None
    cycle: Literal["Y", "Q", "M", "F"] | None = None
    regions: list[str] = Field(default_factory=list)  # normalised labels, e.g. ["서울특별시"]
    answerable: bool = True
    # Added for the T2-28 slot ablation; names follow kosis_rag.query.ParsedQuery. None = not labelled yet (the
    # T2-14 draft has none of these), [] = labelled and the question names none. Labels follow the question's meaning,
    # not the parser's word lists, so words the parser does not know still get a label.
    years: list[int] | None = None  # single years written in the question; a range is in period_start/end only
    dimensions: list[str] | None = None  # breakdowns asked for, normalised: 성, 연령, 시도, ...
    measures: list[str] | None = None  # what is counted, one concept each: 출생, 혼인, ...


class CalcItem(BaseModel):
    """L3: a question with its table given (oracle) and the expected facts or refusal, from the Phase 1 golden set."""

    model_config = ConfigDict(extra="forbid")

    qid: str = Field(min_length=1)  # Phase 1 id ("S-VAL-03"), T2-31 "D-<key>-VAL" or T2-32 "V2-<key>" (= the L1 qid)
    kind: QuestionKind
    type: QuestionType | None = None
    question: str = Field(min_length=1)
    table_key: str  # tables.csv key, e.g. "101_DT_1B8000H"
    table_sha256: str  # Parquet hash the facts were checked against
    required_facts: list[Fact] = Field(default_factory=list)
    aux_facts: list[Fact] = Field(default_factory=list)
    allowed_reasons: list[RefusalReason] = Field(default_factory=list)
    default_applied: dict[str, str] = Field(default_factory=dict)
    clarify_options: list[str] = Field(default_factory=list)
    eda_traps: list[str] = Field(default_factory=list)
    split: Literal["dev", "test"] | None = None  # v2: the split of the L1 item with the same qid


class E2EItem(BaseModel):
    """L4: a question asked without a table; graded on the outcome, the table used and (when given) the L3 facts."""

    model_config = ConfigDict(extra="forbid")

    qid: str = Field(min_length=1)
    split: Literal["dev", "test"]
    question: str = Field(min_length=1)
    expected_outcome: Literal["answered", "refused", "clarify"]
    table_keys: list[str] = Field(default_factory=list)  # any of these counts as the right table (L1 slot)
    calc_qid: str | None = None  # L3 item whose facts grade the numbers


def load_jsonl(path: Path, model: type[BaseModel]) -> list:
    """Parse every line with the layer's model; all problems are raised together."""
    items, problems = [], []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                items.append(model.model_validate_json(line))
            except ValidationError as exc:
                problems.append(f"line {n}: {exc.errors()[0]['msg']}")
    if problems:
        raise GoldenError(f"{path}: " + "; ".join(problems))
    return items


def table_keys(path: Path = TABLES) -> set[str]:
    with path.open(encoding="utf-8") as f:
        return {json.loads(line)["info"]["key"] for line in f if line.strip()}


def load_items(path: Path = GOLDEN, keys: set[str] | None = None) -> list[RetrievalItem]:
    """Parse and check the L1 file; every problem is collected and raised together."""
    items = load_jsonl(path, RetrievalItem)
    problems = check_items(items, keys)
    if problems:
        raise GoldenError(f"{path}: " + "; ".join(problems))
    return items


def check_items(items: list[RetrievalItem], keys: set[str] | None = None) -> list[str]:
    """Duplicate ids, unknown table keys, and a primary table in both dev and test (leaks tuning into test).

    Only primary keys count: hub tables such as the yearly vital statistics are an alternative answer to dozens of
    questions, and keeping every item that lists them in one split left dev with 30 items and no ambiguous ones.
    """
    problems = [f"duplicate qid {q}" for q, c in Counter(i.qid for i in items).items() if c > 1]
    if keys is not None:
        problems += [f"{i.qid}: unknown key {k}" for i in items for k in sorted(i.gold_keys - keys)]
    split_of: dict[str, set[str]] = {}
    for item in items:
        for key in item.primary_keys:
            split_of.setdefault(key, set()).add(item.split)
    problems += [f"primary key {k} is in both dev and test" for k, s in sorted(split_of.items()) if len(s) > 1]
    return problems


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def distribution(items: list[RetrievalItem]) -> str:
    """Markdown counts only (no question text or keys), so the report can be committed."""
    rows = ["| qtype | dev | test | 합계 |", "| --- | --- | --- | --- |"]
    by = Counter((i.qtype, i.split) for i in items)
    for qtype in QTYPES:
        dev, test = by[(qtype, "dev")], by[(qtype, "test")]
        rows.append(f"| {qtype} | {dev} | {test} | {dev + test} |")
    splits = Counter(i.split for i in items)
    rows.append(f"| **합계** | {splits['dev']} | {splits['test']} | {len(items)} |")
    answerable = [i for i in items if i.answerable]
    lines = [
        *rows,
        "",
        f"- 답 없음 비율: {len(items) - len(answerable)}/{len(items)}"
        f" ({(len(items) - len(answerable)) / max(len(items), 1):.0%})",
        f"- 답 없음 사유: {dict(Counter(i.no_answer_reason for i in items if not i.answerable))}",
        f"- 출처: {dict(Counter(i.source for i in items))}",
        f"- 슬롯 수: {dict(sorted(Counter(len(i.required) for i in answerable).items()))}",
        f"- 정답으로 쓰인 고유 표: {len(set().union(*(i.gold_keys for i in items)))}",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the L1 retrieval golden set and print its distribution.")
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    parser.add_argument("--tables", type=Path, default=TABLES)
    parser.add_argument("--report", type=Path, help="also write the distribution to this markdown file")
    args = parser.parse_args(argv)
    try:
        items = load_items(args.golden, table_keys(args.tables))
    except GoldenError as exc:
        print(exc, file=sys.stderr)
        return 1
    text = f"골든셋 sha256 `{file_sha256(args.golden)[:12]}`\n\n" + distribution(items)
    print(text)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
