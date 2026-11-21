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
    # Added for the T2-28 slot ablation; names follow kosis_rag.parse.schemas.ParsedQuery. None = not labelled yet (the
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


AGENT_SCENARIOS = DATASETS / "agent_scenarios.jsonl"  # T3-05 (KOS-41) fills it; T3-06 grades it
ToolName = Literal["search_tables", "summarize_stat", "make_chart", "combine_values"]  # combine_values: T3-28
# Graded argument fields (KOS-120): list fields match as sets and the rest exactly (evaluators.query.field_correct)
ARG_FIELDS = ("table_key", "period_start", "period_end", "regions", "dimensions", "chart_type", "op")


class ExpectedCall(BaseModel):
    """One tool call a turn should make; `args` holds only the graded fields (free text such as a query is left out)."""

    model_config = ConfigDict(extra="forbid")

    name: ToolName
    args: dict[str, str | list[str] | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _known_fields(self) -> ExpectedCall:
        if unknown := set(self.args) - set(ARG_FIELDS):
            raise ValueError(f"ungraded arg fields {sorted(unknown)}; graded: {ARG_FIELDS}")
        return self


FailureType = Literal["numbers", "formula", "rewrite", "classify"]  # T3-46 e2e golden: ① ② ③ ④
Route = Literal["rag", "rewrite", "clarify", "refuse"]
ReadingLabel = Literal["new", "chart_only", "reference", "carry_over", "defer"]


class TurnLabels(BaseModel):
    """T3-46: one turn's classification in the vocabulary the rules use today (plans/T3-46.md "정답 라벨").

    As gold (TurnExpect.labels) a field left None is not graded. The same model holds a prediction: rule_pred (the
    rules run offline on the gold table, evals.evaluators.labels.rule_labels) or a live turn's labels (graph state)."""

    model_config = ConfigDict(extra="forbid")
    failure_type: FailureType | None = None
    route: Route | None = None
    reading: ReadingLabel | None = None  # graph/context.py read_turn, derived (evaluators.labels.reading_of)
    resolved_question: str | None = None  # graded by its years and 시도 (phase 2 parse), not word for word
    sub_questions: list[str] | None = None  # graph/multiquery.py split; graded by count ([] or 1 = not split)
    op: Literal["diff", "sum", "ratio"] | None = None
    outcome: Literal["answered", "refused", "clarify"] | None = None  # phase 1 judge Verdict.outcome
    judge_rule: str | None = None  # Verdict.rule (causal, no_period, symbol, evaluative, not_aggregatable, ...)
    refusal_reasons: list[RefusalReason] | None = None  # gold: any of these; pred: the one given
    default_applied: bool | None = None  # phase 1 filled a condition the question left out (a period)
    qtype: QuestionType | None = None  # rule_plan Plan.type
    formula: list[str] | None = None  # CombineFn names the answer must compute (gold ⊆ pred)
    formula_forbidden: list[str] | None = None  # CombineFn names that would be wrong (pct_change on a % series)
    # pred only (rule_pred): plan steps compute drops when meaningless (optional, e.g. pct_change of a %), and whether
    # the rules left the plan to the model (route "unresolved": no formula decided by rules)
    formula_optional: list[str] | None = None
    plan_unresolved: bool | None = None
    # gold only (T3-47): a new-type item whose rule prediction disagrees waits for a person (needs_review); a person
    # sets accepted / rejected in the review CSV. Not graded; the quality gate warns on needs_review
    review: Literal["needs_review", "accepted", "rejected"] | None = None
    chart_request: Literal["bar", "line", "pie"] | None = None


class TurnExpect(BaseModel):
    """What one turn must do. A multi-query turn ("찾아서, 계산해서, 그려줘") lists several calls in order."""

    model_config = ConfigDict(extra="forbid")
    labels: TurnLabels | None = None  # T3-46 gold classification of the turn
    rule_pred: TurnLabels | None = None  # T3-46 what today's rules decide offline (gold table), stored at build

    tools: list[ExpectedCall] = Field(default_factory=list)  # in call order; [] = answer without a tool
    table_key: str | None = None  # the table the turn ends on (conversation state); None = not graded
    # T3-27: other tables that answer this turn too (the L1 gold slot of the same question). Ending on one of them is
    # not lost_state; its numbers pass when its facts carry the expected values (counted apart as via_alternate).
    alt_table_keys: list[str] = Field(default_factory=list)
    refuse: bool = False  # out of scope / no data: search_tables or summarize_stat must refuse (or no tool runs)
    calc_qid: str | None = None  # L3 item graded on this turn's summarize_stat answer (evaluators.calculation)
    # T3-28 multi-query: L3 items graded on this turn's summarize_stat answers in call order (one per sub-question),
    # and the value combine_values must return (computed by the builder from the golden facts)
    calc_qids: list[str] = Field(default_factory=list)
    combine_value: float | None = None
    # T3-38 partial refusal: per sub-question (search order), whether it must refuse. None = the turn-level `refuse`
    # rule. calc_qids then belong to the answering (False) sub-questions only, in order. Left out of dumps when None,
    # so files written before T3-38 serialize byte-identically.
    refuse_parts: list[bool] | None = Field(default=None, exclude_if=lambda v: v is None)
    # T3-37: the chart family make_chart must draw when the user named no chart type (its chart_type arg is None and
    # phase 1 picks the kind). Derived from the calculation item type (builders.multiturn.AUTO_CHART); None = not graded
    auto_chart: Literal["bar", "line", "pie"] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> TurnExpect:
        if self.refuse_parts is not None:
            n, answering = len(self.refuse_parts), self.refuse_parts.count(False)
            n_stat = sum(c.name == "summarize_stat" for c in self.tools)
            if self.refuse or n < 2:
                raise ValueError("refuse_parts is for a multi-query turn (two or more parts) with refuse=false")
            if sum(c.name == "search_tables" for c in self.tools) != n:
                raise ValueError("refuse_parts needs one search_tables call per sub-question")
            if n_stat not in (n, answering):
                raise ValueError("refuse_parts needs one summarize_stat per sub-question, or one per answering "
                                 "sub-question (the refusing ones stop at search)")
            if self.calc_qid or len(self.calc_qids) not in (0, answering):
                raise ValueError("with refuse_parts, calc_qids lists one item per answering sub-question")
        if self.refuse and any(c.name == "make_chart" for c in self.tools):
            raise ValueError("a refusing turn draws no chart")
        if self.auto_chart and not any(c.name == "make_chart" for c in self.tools):
            raise ValueError("auto_chart needs a make_chart call")
        if self.calc_qid and not any(c.name == "summarize_stat" for c in self.tools):
            raise ValueError("calc_qid needs a summarize_stat call")
        if self.calc_qid and self.calc_qids:
            raise ValueError("use calc_qid or calc_qids, not both")
        if len(self.calc_qids) > sum(c.name == "summarize_stat" for c in self.tools):
            raise ValueError("calc_qids needs one summarize_stat call each")
        if self.combine_value is not None and not any(c.name == "combine_values" for c in self.tools):
            raise ValueError("combine_value needs a combine_values call")
        return self


class ScenarioTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    expect: TurnExpect


class AgentScenario(BaseModel):
    """T3-05/T3-06: a conversation; it succeeds only when every turn passes. References such as "이거", "그 표"
    are graded through each turn's expected table_key and the expected call args."""

    model_config = ConfigDict(extra="forbid")

    sid: str = Field(min_length=1)
    split: Literal["dev", "test"] = "test"
    source: str = "scenario"  # scenario (과제 문서) | mentor | synthetic ...
    turns: list[ScenarioTurn] = Field(min_length=1)
    notes: str = ""


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
