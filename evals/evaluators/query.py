"""L2 query understanding: grade what the parser read (kosis_rag.query.ParsedQuery) against a QueryItem. No LLM.

Per field (docs/research/langchain_eval_design_kosis.md §5): scalars match exactly, lists match as sets, and an empty
gold field is graded too, so a slot the question never named counts as wrong (an invented filter drops right tables).
measures: the gold holds one concept per counted thing and the parser expands it into search words
(출생 -> 출생, 출산, 생아), so a gold concept is read when it is one of the predicted words.
regions, dimensions and years also get micro P/R (PR_FIELDS). years, dimensions and measures with a None gold were not labelled yet and are left out, of the field and of the
all-fields match; on the period and cycle fields None is a label ("the question names no period").
"""

from __future__ import annotations

from collections import defaultdict

from evals.schemas import QueryItem

FIELDS = ("period_start", "period_end", "cycle", "regions", "years", "dimensions", "measures")
LISTS = {"regions", "years", "dimensions", "measures"}
OPTIONAL = {"years", "dimensions", "measures"}  # None = not labelled; on the period fields None means "no period"
# §5: list fields also get micro precision/recall, so "read too little" and "invented a filter" show apart. measures is
# left out: the parser expands one concept into several search words and is graded by inclusion, so precision means nothing
PR_FIELDS = ("regions", "dimensions", "years")


def field_correct(name: str, gold, pred) -> bool:
    pred = pred if pred is not None or name not in LISTS else []
    if name == "measures":
        return bool(gold) == bool(pred) and set(gold) <= set(pred)
    if name in LISTS:
        return set(gold) == set(pred)
    return gold == pred


def score_query(item: QueryItem, pred: dict) -> dict:
    """{field: 0/1} for every labelled field, plus all_fields, and `pr` {field: tp/fp/fn} for the list fields;
    pred is ParsedQuery.model_dump() or the same keys."""
    row = {f: int(field_correct(f, getattr(item, f), pred.get(f)))
           for f in FIELDS if f not in OPTIONAL or getattr(item, f) is not None}
    row["all_fields"] = int(all(row.values()))
    counts = {}
    for f in PR_FIELDS:
        gold = getattr(item, f)
        if gold is not None:
            g, p = set(gold), set(pred.get(f) or [])
            counts[f] = {"tp": len(g & p), "fp": len(p - g), "fn": len(g - p)}
    return {**row, "pr": counts}


def aggregate(rows: list[dict], by: str = "split") -> dict[str, dict]:
    """Accuracy per field over the items that label it, overall ('all') and per group under `by`."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups["all"].append(row)
        groups[row[by]].append(row)
    out = {}
    for name, members in groups.items():
        out[name] = {"n": len(members)}
        for f in (*FIELDS, "all_fields"):
            graded = [m[f] for m in members if f in m]
            out[name][f] = sum(graded) / len(graded) if graded else None
            out[name][f"n_{f}"] = len(graded)
        for f in PR_FIELDS:
            tp, fp, fn = (sum(m.get("pr", {}).get(f, {}).get(c, 0) for m in members) for c in ("tp", "fp", "fn"))
            out[name][f"{f}_p"] = tp / (tp + fp) if tp + fp else None
            out[name][f"{f}_r"] = tp / (tp + fn) if tp + fn else None
    return out
