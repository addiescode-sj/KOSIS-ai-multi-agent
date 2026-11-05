"""Slot ablation (T2-28, docs/research/langchain_eval_design_kosis.md §4.4): which question slot moves retrieval.

Combinations: none, only:<s> and all-<s> per slot, all (12 for the 5 slots, 8 for 3). Each runs on one slot source:
gold (QueryItem labels; filter loss is then metadata only, L0) or pred (parse_query; metadata + parser, L0 + L2).
The filter is built the way ConditionRetriever builds it, ParsedQuery.to_filter("hard") with the switched-off fields
emptied, so a run with every slot on and pred slots is the T2-14 search (tests/test_evals_ablation.py checks it).

Filter recall and candidate counts come from filters.filter_tables over the whole registry, no search needed.
With a retriever (Chroma, `with_filter`), the same filter feeds the search and the ranked keys are kept, at most
MAX_RESULTS, so every combination is cut to the same length. BM25 is not used here: ConditionRetriever filters
its top 20 after the search, which is not the same as filtering first.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Iterable

from kosis_rag.filters import TableFilter, filter_tables
from kosis_rag.query import MAX_RESULTS, ParsedQuery
from kosis_rag.schemas import TableRecord

from evals.evaluators import retrieval
from evals.schemas import QueryItem, RetrievalItem

SLOT_FIELDS = {
    "year": ("years", "period_start", "period_end"),
    "cycle": ("cycle",),
    "region": ("regions",),
    "dimension": ("dimensions",),
    "measure": ("measures",),
}
SLOTS = tuple(SLOT_FIELDS)
# A gold measure concept (출생) is what ParsedQuery.measures holds too (T2-29, C7); to_filter turns a concept into
# the search words (출생, 출산, 생아) or a rank-only hint, so a gold run and a pred run that read the same concept
# build the same filter.
HELP_MIN = 2  # net Hit@5 gain on dev that counts as help; 2 questions out of 29, see §4.4 for why it is read with the flips


def combos(slots: Iterable[str] = SLOTS) -> dict[str, frozenset[str]]:
    slots = tuple(slots)
    out = {"none": frozenset(), "all": frozenset(slots)}
    for s in slots:
        out[f"only:{s}"] = frozenset({s})
        out[f"all-{s}"] = frozenset(slots) - {s}
    return out


def masked(parsed: ParsedQuery, on: frozenset[str]) -> ParsedQuery:
    """The same reading with every field of the switched-off slots back to empty."""
    off = [f for s in SLOTS if s not in on for f in SLOT_FIELDS[s]]
    return parsed.model_copy(update={f: ParsedQuery.model_fields[f].get_default(call_default_factory=True) for f in off})


def filter_for(parsed: ParsedQuery, on: frozenset[str]) -> TableFilter:
    return masked(parsed, on).to_filter("hard")


def gold_parsed(item: QueryItem) -> ParsedQuery:
    """A QueryItem as the parser's output; an unlabelled slot (None) is empty."""
    years = item.years
    if years is None:
        # ponytail: the T2-14 draft labels periods only. One year (start == end) is that year; anything else is a
        # range and to_filter turns it into period_from/period_to, as it does for the parser. "2005년과 2025년"
        # (two years, not a range) cannot be told from "2005~2025" this way: label `years` to grade that case.
        start, end = (p[:4] if p else None for p in (item.period_start, item.period_end))
        years = [int(start)] if start and start == end else []
    return ParsedQuery(years=years, period_start=item.period_start, period_end=item.period_end, cycle=item.cycle,
                       regions=item.regions, dimensions=item.dimensions or [], measures=item.measures or [])


def has_slot(parsed: ParsedQuery, slot: str) -> bool:
    return any(getattr(parsed, f) for f in SLOT_FIELDS[slot])


def run(items: list[RetrievalItem], slots: dict[str, ParsedQuery], records: dict[str, TableRecord],
        on_by_combo: dict[str, frozenset[str]], retriever=None, k: int = MAX_RESULTS) -> list[dict]:
    """One row per (combination, item); items without a slot reading are skipped."""
    cache: dict[str, tuple[set[str], dict[str, str]]] = {}
    everything = set(records)
    rows = []
    for combo, on in on_by_combo.items():
        for item in items:
            if item.qid not in slots:
                continue
            f = filter_for(slots[item.qid], on)
            sig = f.model_dump_json()
            if sig not in cache:
                cache[sig] = (everything, {}) if f.is_empty() else filter_tables(records, f)
            passed, removed = cache[sig]
            row = {
                "combo": combo, "qid": item.qid, "split": item.split, "qtype": item.qtype,
                "answerable": item.answerable, "no_answer_reason": item.no_answer_reason,
                "n_candidates": len(passed), "filter": f.model_dump(exclude_defaults=True),
                "gold_removed": {key: removed[key] for key in sorted(item.gold_keys) if key in removed},
                "slots": sorted(s for s in on if has_slot(slots[item.qid], s)),
                "latency_ms": 0.0,
            }
            if item.answerable:
                row["filter_pass"] = int(retrieval.filter_pass(item, passed))
            if retriever is not None:
                started = time.perf_counter()
                docs = retriever.with_filter(f, records).invoke(item.question)[: min(k, MAX_RESULTS)] if passed else []
                row["keys"] = [d.metadata["key"] for d in docs]
                row["top_score"] = docs[0].metadata.get("score") if docs else None
                row["latency_ms"] = (time.perf_counter() - started) * 1000
                if item.answerable:
                    row.update(retrieval.score_item(item, row["keys"]))
            rows.append(row)
    return rows


def refused(row: dict, tau: float) -> bool:
    """No table passed the filter, or the top score is below tau (the only way left when no slot is on).

    tau is chosen once on the `none` combination and kept for all, so the rates differ by the filter alone.
    """
    searched = "top_score" in row  # a filter-only run has no score: only the filter can refuse
    return row["n_candidates"] == 0 or (searched and retrieval.predict_no_answer(row["top_score"], tau))


def verdict(only: dict, minus: dict) -> str:
    """§4.4: help needs the gain both alone (only:s vs none) and on top of the others (all vs all-s)."""
    if any(g["hit5_net"] < 0 or g["recall_delta"] < 0 for g in (only, minus)):
        return "해로움"
    if all(g["hit5_net"] >= HELP_MIN and g["hit1_net"] >= 0 for g in (only, minus)):
        return "도움"
    return "불분명"


def compare(rows: list[dict], a: str, b: str) -> dict:
    """Paired change from combination b to a on the answerable items both hold."""
    ra = {r["qid"]: r for r in rows if r["combo"] == a and r["answerable"]}
    rb = {r["qid"]: r for r in rows if r["combo"] == b and r["answerable"]}
    qids = sorted(ra.keys() & rb.keys())
    out = {"recall_delta": sum(ra[q]["filter_pass"] - rb[q]["filter_pass"] for q in qids) / max(len(qids), 1)}
    if qids and "hit@5" in ra[qids[0]]:
        better = [q for q in qids if ra[q]["hit@5"] > rb[q]["hit@5"]]
        worse = [q for q in qids if ra[q]["hit@5"] < rb[q]["hit@5"]]
        out.update(hit5_net=len(better) - len(worse), better=better, worse=worse,
                   hit1_net=sum(ra[q]["hit@1"] - rb[q]["hit@1"] for q in qids),
                   mrr_delta=sum(ra[q]["rr"] - rb[q]["rr"] for q in qids) / len(qids))
    else:
        out.update(hit5_net=0, hit1_net=0)  # filter only: a verdict can only be harm (recall) or unclear
    return out


def summarize(rows: list[dict], on_by_combo: dict[str, frozenset[str]], slots: dict[str, ParsedQuery], tau: float) -> dict:
    per_combo = {}
    for combo in on_by_combo:
        members = [r for r in rows if r["combo"] == combo]
        scored = [r for r in members if r["answerable"]]
        no_period = [r for r in members if r["no_answer_reason"] == "no_period"]
        no_answer = [r for r in members if not r["answerable"]]
        zero = lambda rs: sum(r["n_candidates"] == 0 for r in rs) / len(rs) if rs else None  # noqa: E731
        m = {
            "n": len(scored),
            "filter_recall": sum(r["filter_pass"] for r in scored) / max(len(scored), 1),
            "candidates_median": statistics.median(r["n_candidates"] for r in scored) if scored else None,
            # hard filter left 0 tables: where a step-wise relaxation (T2-29) would have to start; answerable
            # zeros are a subset of filter_pass == 0, no-answer zeros are the filter half of refused()
            "zero_answerable": zero(scored),
            "zero_no_answer": zero(no_answer),
            "n_no_answer": len(no_answer),
            "no_period_refusal": (sum(refused(r, tau) for r in no_period) / len(no_period)) if no_period else None,
            "n_no_period": len(no_period),
        }
        if scored and "rr" in scored[0]:
            m.update(mrr=sum(r["rr"] for r in scored) / len(scored),
                     **{f"hit@{n}": sum(r[f"hit@{n}"] for r in scored) for n in (1, 5, 10)})
        per_combo[combo] = m
    slot_names = [c.split(":", 1)[1] for c in on_by_combo if c.startswith("only:")]
    per_slot = {}
    for s in slot_names:
        only, minus = compare(rows, f"only:{s}", "none"), compare(rows, "all", f"all-{s}")
        with_s = {q for q, p in slots.items() if has_slot(p, s)}
        cond = compare([r for r in rows if r["qid"] in with_s], f"only:{s}", "none")
        per_slot[s] = {"only": only, "minus": minus, "verdict": verdict(only, minus),
                       "n_with_slot": len({r["qid"] for r in rows if r["answerable"] and r["qid"] in with_s}),
                       "conditional_mrr_delta": cond.get("mrr_delta")}
    return {"combos": per_combo, "slots": per_slot, "tau": tau}
