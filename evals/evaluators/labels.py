"""T3-46: a turn's classification labels (evals.schemas.TurnLabels) — derived, predicted and graded. No LLM.

Three sources of the same labels:
- gold: written by the builder (evals/builders/e2e_golden.py) from what it made each turn to be;
- rule_pred: today's rules run offline on the gold table (rule_labels): context read_turn(mode="rules"),
  multiquery split_by_rules, phase 1 judge + step_plan_rules. "Rules accuracy when retrieval is right";
- live: a graph turn's state and phase 1 answer (state_labels), recorded by evals.runners.agent.run.

grade(gold, pred) compares only the fields gold sets; summarize(rows) gives accuracy per field and confusion pairs.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable

from evals.schemas import TurnLabels

# phase 1 derivation strings (kosis_chat/compute/plan_engine.py _combine) -> CombineFn. A diff of two percentages has
# unit %p either way (diff's _diff_unit or pp_diff), so a %p difference reads as pp_diff: the number is the same.
_ID = r"f\d+"
FORMULA_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"^\(\({_ID} / {_ID}\) \*\* \(1 / [\d.]+\) - 1\) \* 100$"), "cagr"),
    (re.compile(rf"^\({_ID} / {_ID} - 1\) \* 100$"), "pct_change"),
    (re.compile(rf"^{_ID} / {_ID} \* 100$"), "share"),  # share and index print the same expression
    (re.compile(rf"^{_ID} / {_ID} \* [\d.]+$"), "per"),
    (re.compile(rf"^{_ID} / {_ID}$"), "ratio"),
    (re.compile(rf"^{_ID} - {_ID}$"), "diff"),
]


def formula_of(derivation: str | None, unit: str | None) -> str | None:
    text = (derivation or "").strip()
    for pattern, name in FORMULA_PATTERNS:
        if pattern.match(text):
            return "pp_diff" if name == "diff" and unit == "%p" else name
    return None


def formulas(facts: Iterable) -> list[str]:
    """CombineFn names an answer computed, from its facts' derivations (dicts or Fact objects), in first-seen order."""
    out = []
    for f in facts:
        get = f.get if isinstance(f, dict) else (lambda k, f=f: getattr(f, k, None))
        name = formula_of(get("derivation"), get("unit"))
        if name and name not in out:
            out.append(name)
    return out


def reading_of(*, had_table: bool, use_previous_table: bool, chart_only: bool, resolved: str, question: str,
               by: str) -> str:
    """graph/context.py Reading -> label (plans/T3-46.md "정답 라벨"): rule 1 no table / rule 4 -> new, rule 2 chart_only,
    rule 3 reference (previous table, question as asked), rule 5 carry_over (previous table, question rewritten),
    rule 6 defer (a previous table, nobody decided: rules mode searches with the question as asked)."""
    if chart_only:
        return "chart_only"
    if use_previous_table:
        return "reference" if resolved == question else "carry_over"
    if had_table and by == "none":
        return "defer"
    return "new"


def route_of(labels: TurnLabels) -> str | None:
    """rag | rewrite | clarify | refuse from the other labels."""
    if labels.outcome == "clarify":
        return "clarify"
    if labels.outcome == "refused":
        return "refuse"
    if labels.reading in ("carry_over", "reference", "chart_only") or len(labels.sub_questions or []) > 1:
        return "rewrite"
    return "rag" if labels.outcome or labels.reading else None


def with_route(labels: TurnLabels) -> TurnLabels:
    return labels.model_copy(update={"route": route_of(labels)})


def state_labels(state: dict, question: str, had_table: bool) -> TurnLabels:
    """Labels of a live graph turn (AgentState after graph.invoke). had_table: an earlier turn of the conversation
    ended on a table (rule 1 and rule 6 both leave interpreted_by "none"). judge_rule is not in the state (None)."""
    stat = state.get("stat")
    answer = stat.answer if stat is not None else None
    qtype = next((s.get("attributes", {}).get("type") for s in (stat.spans if stat is not None else [])
                  if s.get("name") == "analyze.rules"), None)
    outcome = state.get("outcome")
    refusal = getattr(answer, "refusal", None)
    # T3-03: state refusal_reason is this turn's (a template turn has no search; state "search" is the previous turn's)
    reason = (getattr(refusal, "reason", None) or state.get("refusal_reason")
              or (getattr(state.get("search"), "refusal_reason", None) if "refusal_reason" not in state else None))
    facts = answer.result.facts if answer is not None and answer.result is not None else []
    labels = TurnLabels(
        reading=reading_of(had_table=had_table, use_previous_table=bool(state.get("reused_table")), chart_only=bool(state.get("chart_only")),
                           resolved=state.get("resolved_question") or question, question=question,
                           by=state.get("interpreted_by") or "none"),
        resolved_question=state.get("resolved_question") or question,
        sub_questions=list(state.get("sub_questions") or []), op=state.get("combine_op"),
        outcome=outcome if outcome in ("answered", "refused", "clarify") else None,
        refusal_reasons=[reason] if outcome == "refused" and reason else None,
        default_applied=bool(getattr(answer, "default_applied", None)) if answer is not None else None,
        qtype=qtype, formula=formulas(facts), chart_request=state.get("chart_type"))
    return with_route(labels)


# ---------------------------------------------------------------- offline rule predictions
def phase1_rules(question: str, table_key: str, resources_of: Callable[[str], object]) -> dict:
    """Phase 1 judge + rule plan on one table, no model: outcome, judge_rule, refusal reason, default, qtype, formula."""
    from kosis_chat.analyze.node import step_plan_rules
    from kosis_chat.analyze.schemas import PlanRulesIn
    from kosis_chat.judge.rules import judge

    res = resources_of(table_key)
    verdict = judge(question, res.table, res.index)
    out = {"outcome": verdict.outcome, "judge_rule": verdict.rule,
           "refusal_reasons": [verdict.refusal.reason] if verdict.refusal else None,
           "default_applied": bool(verdict.default_applied)}
    if verdict.outcome != "answered":
        return out
    plan = step_plan_rules(PlanRulesIn(question=question, default_applied=dict(verdict.default_applied)), res)
    if plan.route == "unresolved":  # the rules left the plan to the model: no formula decided by rules
        return {**out, "qtype": plan.rule.type, "plan_unresolved": True}
    built = getattr(plan.built, "plan", plan.built)  # Built carries the v3 plan (PlanV3.steps)
    combine = [s for s in getattr(built, "steps", None) or [] if getattr(s, "op", "") == "combine"]
    return {**out, "qtype": plan.rule.type, "plan_unresolved": False,
            "formula": list(dict.fromkeys(s.fn for s in combine)),
            "formula_optional": list(dict.fromkeys(s.fn for s in combine if s.optional)) or None}


# Phase 1 loads a whole table into pandas: 101_DT_1B26002 (9.2M rows) with a few 3-4M-row tables in the cache pushed
# the offline build into swap until it was force-quit (2026-11-20). Bigger tables are skipped, not loaded.
# ponytail: row cap instead of a filtered read; raise it on a machine with more memory (--max-rows).
MAX_TABLE_ROWS = 2_000_000


class TableTooLarge(RuntimeError):
    """The table has more rows than the offline rules may load (recorded as a skip, not a rule error)."""


def table_rows(workspace) -> dict[str, int]:
    """tables.csv key -> rows (0 when unknown), read without opening any Parquet file."""
    import csv
    from pathlib import Path

    path = Path(workspace) / "data" / "tables.csv"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        return {r["key"]: int(r.get("rows") or 0) for r in csv.DictReader(f)}


def table_resources(workspace, max_rows: int = MAX_TABLE_ROWS, cache: int = 2) -> Callable[[str], object]:
    """table key -> phase 1 StatResources (cleaned table + index), the same loader phase 1 uses. A table over
    max_rows raises TableTooLarge before it is opened; at most `cache` tables stay in memory."""
    import os
    from functools import lru_cache

    os.environ.setdefault("KOSIS_WORKSPACE", str(workspace))
    from kosis_chat.api import load_table, table_path
    from kosis_chat.schemas.resources import load_resources

    rows = table_rows(workspace)

    @lru_cache(maxsize=cache)
    def load(key: str):
        if rows.get(key, 0) > max_rows:
            raise TableTooLarge(f"{key}: {rows[key]:,} rows > {max_rows:,}")
        return load_resources(load_table(table_path(key)))

    return load


def rule_labels(turns: list, parse: Callable, resources_of: Callable[[str], object]) -> list[TurnLabels]:
    """Today's rules on each turn of a scenario, assuming the earlier turns went right (the gold table and the gold
    independent question are the conversation state). Phase 1 runs on the gold table with the rules' own question."""
    from kosis_agent.graph.context import read_turn
    from kosis_agent.graph.multiquery import split_by_rules

    out, table, previous = [], None, None
    for turn in turns:
        e = turn.expect
        reading = read_turn(turn.question, active_table=table, active_question=previous, parse=parse, mode="rules")
        label = reading_of(had_table=table is not None, use_previous_table=reading.use_previous_table,
                           chart_only=reading.chart_only, resolved=reading.resolved_question, question=turn.question,
                           by=reading.by)
        split = None if reading.use_previous_table else split_by_rules(turn.question, parse)
        subs = split.sub_questions if split and len(split.sub_questions) > 1 else []
        pred = {"reading": label, "resolved_question": reading.resolved_question, "sub_questions": subs,
                "op": split.op if subs else None, "chart_request": reading.chart_type}
        gold_table = e.table_key
        if not reading.chart_only and not subs and gold_table:
            pred |= phase1_rules(reading.resolved_question, gold_table, resources_of)
        out.append(with_route(TurnLabels(**pred)))
        if gold_table:  # the conversation moves on as if this turn went right
            table = gold_table
            previous = (e.labels.resolved_question if e.labels and e.labels.resolved_question else turn.question)
    return out


# ---------------------------------------------------------------- grading
CATEGORICAL = ("failure_type", "route", "reading", "op", "outcome", "judge_rule", "qtype", "chart_request")


def same_slots(gold: str, pred: str | None, parse: Callable | None) -> bool:
    """Independent questions match when they ask the same years and 시도 (the words may differ)."""
    if pred is None:
        return False
    if parse is None:
        return gold == pred
    g, p = parse(gold), parse(pred)
    return (sorted(g.regions), g.period_start, g.period_end) == (sorted(p.regions), p.period_start, p.period_end)


def grade(gold: TurnLabels | None, pred: TurnLabels | None, parse: Callable | None = None) -> dict[str, bool]:
    """{field: right} for every field gold sets (failure_type is a grouping, not graded)."""
    if gold is None:
        return {}
    pred = pred or TurnLabels()
    out: dict[str, bool] = {}
    for field in TurnLabels.model_fields:
        want = getattr(gold, field)
        if want is None or field in ("failure_type", "formula_forbidden", "formula_optional", "plan_unresolved"):
            continue
        got = getattr(pred, field)
        if field == "resolved_question":
            out[field] = same_slots(want, got, parse)
        elif field == "sub_questions":
            out[field] = max(len(want), 1) == max(len(got or []), 1)
        elif field == "refusal_reasons":
            out[field] = bool(got) and got[0] in want
        elif field == "formula":
            have = set(got or [])
            if "pp_diff" in want and "diff" in have:  # a diff of two percentages is the %p difference (same number)
                have.add("pp_diff")
            sure = have - set(pred.formula_optional or [])  # optional steps are dropped when meaningless (a % growth)
            out[field] = set(want) <= have and not (set(gold.formula_forbidden or []) & sure)
        else:
            out[field] = want == got
    return out


def summarize(rows: list[dict], key: str = "label_hits") -> dict:
    """Per field {acc, n} plus confusion pairs (gold -> pred, most common first) for the categorical fields, and the
    same accuracy per failure type. rows carry gold labels in row["gold_labels"], predictions in row["pred_labels"]."""
    hits: dict[str, list[bool]] = defaultdict(list)
    by_type: dict[str, dict[str, list[bool]]] = defaultdict(lambda: defaultdict(list))
    confusion: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        gold, pred = r.get("gold_labels") or {}, r.get("pred_labels") or {}
        for field, ok in (r.get(key) or {}).items():
            hits[field].append(ok)
            by_type[gold.get("failure_type") or "?"][field].append(ok)
            if not ok and field in CATEGORICAL:
                confusion[field][f"{gold.get(field)} → {pred.get(field)}"] += 1
            elif not ok and field == "formula":  # "→ 규칙 미결정" = rule_plan unresolved (the model's fill decides)
                got = "규칙 미결정" if pred.get("plan_unresolved") else pred.get("formula")
                confusion[field][f"{gold.get(field)} → {got}"] += 1
    acc = lambda v: {"acc": sum(v) / len(v) if v else None, "n": len(v)}  # noqa: E731
    return {"fields": {f: acc(v) for f, v in hits.items()},
            "by_type": {t: {f: acc(v) for f, v in fs.items()} for t, fs in sorted(by_type.items())},
            "confusion": {f: c.most_common(5) for f, c in confusion.items()}}


def report_lines(s: dict) -> list[str]:
    fmt = lambda v: "—" if v["acc"] is None else f"{v['acc']:.3f} ({v['n']})"  # noqa: E731
    fields = list(s["fields"])
    lines = ["| 라벨 | 정확도 (n) | 틀린 예 (정답 → 예측) |", "| --- | --- | --- |"]
    for f in fields:
        lines.append(f"| {f} | {fmt(s['fields'][f])} | {', '.join(f'{k} ×{n}' for k, n in s['confusion'].get(f, [])) or '—'} |")
    lines += ["", "| 실패 유형 | " + " | ".join(fields) + " |", "|" + " --- |" * (len(fields) + 1)]
    for t, fs in s["by_type"].items():
        lines.append(f"| {t} | " + " | ".join(fmt(fs[f]) if f in fs else "—" for f in fields) + " |")
    return lines
