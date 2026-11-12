"""L4 end to end, first part (plans/T2-03.md): the retriever's top 1 table -> summarize_stat -> the L3 grader.

Same questions and grader as L3 (calculation_golden.jsonl, evaluators/calculation.py), but the table comes from
search instead of the golden set (L3 is the oracle run). The difference between the two runs is what retrieval
costs. Grading is against the golden table's facts and cells, so an answer read from another table fails unless
its numbers are the same. Any BaseRetriever works (runners/retrieval.build), as long as metadata["key"] holds the
table key.
"""

from __future__ import annotations



from kosis_ai.schemas import StatAnswer
from kosis_rag.shared.vocab import SIDO, SIDO_ORDER

from evals.schemas import CalcItem, QueryItem, RetrievalItem

OFFICIAL = {name: name for name in SIDO_ORDER} | {alias: name for name, aliases in SIDO.items() for alias in aliases}


def locate(item: CalcItem, row: dict, verdict: str, l1: RetrievalItem | None = None) -> dict:
    """Where the golden table ranked and why a row did not pass: retrieval (another table at top 1) or answer.

    §6.3 {지표명}: with the L1 item of the same qid, any key of its answer slots is the right table (an alternate
    table answers the question too); without it, only the L3 table counts."""
    keys = row["keys"]
    gold = l1.gold_keys if l1 and l1.answerable else {item.table_key}
    right = row["used_table"] in gold
    ranks = [n for n, k in enumerate(keys, 1) if k in gold]
    return {"used_table": row["used_table"], "right_table": right, "right_alternate": right and row["used_table"] != item.table_key,
            "l1_linked": l1 is not None, "gold_rank": ranks[0] if ranks else None,
            "top_keys": keys[:5], "gold_keys": sorted(gold), "cause": "pass" if verdict == "PASS" else "answer" if right else "retrieval"}


def template_slots(answer: StatAnswer | None, gold: QueryItem | None) -> dict:
    """§6.3 {연도}·{지역}: the periods and 시도 the answer was computed for against the L2 labels of the same qid.
    None when there is nothing to compare (no L2 item, no answer, or the label names no period / region)."""
    if gold is None or answer is None or answer.outcome != "answered" or answer.query is None:
        return {"year_ok": None, "region_ok": None}
    year_ok = None
    if gold.period_start or gold.period_end:
        width = len(gold.period_start or gold.period_end)
        got = sorted({p[:width] for p in answer.query.periods})
        year_ok = bool(got) and got[0] == (gold.period_start or got[0]) and got[-1] == (gold.period_end or got[-1])
    named = {OFFICIAL[v] for v in answer.query.filters.values() if v in OFFICIAL}
    return {"year_ok": year_ok, "region_ok": named == set(gold.regions)}
