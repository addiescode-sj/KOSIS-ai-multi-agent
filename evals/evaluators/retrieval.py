"""L1 retrieval evaluator: pure functions over ranked table keys. Knows nothing about LangChain.

Per item: the rank of the first gold key (any slot, any alternative), Hit@k, reciprocal rank, Complete@k (every
slot covered in the top k) and SlotRecall@k. No answer: an item is predicted unanswerable when the top-1 score is
below tau (or nothing came back); positives are the unanswerable items. tau is chosen on dev and applied to test,
because BM25 scores above 0 whenever one bigram overlaps, so "no candidates" alone never refuses.
"""

from __future__ import annotations

from collections import defaultdict

from evals.schemas import RetrievalItem

KS = (1, 3, 5, 10)


def first_rank(item: RetrievalItem, keys: list[str]) -> int | None:
    gold = item.gold_keys
    return next((i for i, k in enumerate(keys, 1) if k in gold), None)


def slots_covered(item: RetrievalItem, keys: list[str], k: int) -> int:
    top = set(keys[:k])
    return sum(1 for slot in item.required if top & set(slot))


def slot_ranks(item: RetrievalItem, keys: list[str]) -> list[int | None]:
    """First rank at which each slot is covered (None: not in the list). The slot-level score of a question."""
    return [next((i for i, k in enumerate(keys, 1) if k in set(slot)), None) for slot in item.required]


def score_item(item: RetrievalItem, keys: list[str], judged: set[str] | None = None) -> dict:
    """Metrics of one answerable item for its ranked keys. judged: keys a person looked at for this item."""
    rank = first_rank(item, keys)
    row = {"rank": rank, "rr": 1 / rank if rank else 0.0, "slot_ranks": slot_ranks(item, keys)}
    for k in KS:
        row[f"hit@{k}"] = int(rank is not None and rank <= k)
        row[f"complete@{k}"] = int(slots_covered(item, keys, k) == len(item.required))
        row[f"slot_recall@{k}"] = slots_covered(item, keys, k) / len(item.required)
    if judged is not None:
        top = keys[:10]
        row["unjudged@10"] = sum(1 for key in top if key not in judged and key not in item.gold_keys) / max(len(top), 1)
    return row


def filter_pass(item: RetrievalItem, passed: set[str]) -> bool:
    """Filter recall of one item: every table slot keeps at least one key among the tables that pass the filter.

    Measured on the whole registry (filters.filter_tables), not on the retriever's top k, so a gold table the
    filter kept but the ranking cut is not counted as a filter loss.
    """
    return all(passed & set(slot) for slot in item.required)


def predict_no_answer(top_score: float | None, tau: float) -> bool:
    return top_score is None or top_score < tau


def llm_no_answer(row: dict) -> bool:
    """The LLM re-ranker picked nothing. A failed call (stage2_error) is not a verdict: it keeps the stage 1 order."""
    return row.get("stage2_picked_n") == 0 and not row.get("stage2_error")


def no_answer_rules(rows: list[dict]) -> dict:
    """No-answer P/R/F1 under three rules on rows that carry `no_answer_pred` (tau) and stage 2 fields (T2-09).

    tau: top-1 score below tau · llm: the LLM picked nothing · either: one of the two. Also per gold reason, the
    recall of each rule (out_of_scope / no_period / no_item), how many answerable items each rule refused, and how
    often the LLM's reason matched the gold reason on the unanswerable items it refused.
    """
    preds = {"tau": lambda r: r["no_answer_pred"], "llm": llm_no_answer,
             "either": lambda r: r["no_answer_pred"] or llm_no_answer(r)}
    out: dict = {}
    for name, pred in preds.items():
        m = prf([(pred(r), not r["answerable"]) for r in rows])
        reasons = sorted({r["no_answer_reason"] for r in rows if not r["answerable"]})
        m["recall_by_reason"] = {g: [sum(1 for r in rows if r["no_answer_reason"] == g and pred(r)),
                                     sum(1 for r in rows if r["no_answer_reason"] == g)] for g in reasons}
        out[name] = m
    refused = [r for r in rows if not r["answerable"] and llm_no_answer(r)]
    out["llm_reason_match"] = [sum(1 for r in refused if r.get("stage2_reason") == r["no_answer_reason"]), len(refused)]
    out["llm_errors"] = sum(1 for r in rows if r.get("stage2_error"))
    return out


def prf(pairs: list[tuple[bool, bool]]) -> dict:
    """(predicted no-answer, gold no-answer) pairs -> precision, recall, F1 of the no-answer class."""
    tp = sum(p and g for p, g in pairs)
    fp = sum(p and not g for p, g in pairs)
    fn = sum(g and not p for p, g in pairs)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def best_tau(rows: list[tuple[float | None, bool]]) -> float:
    """Threshold with the best no-answer F1 over (top-1 score, gold no-answer) rows; ties keep the lowest tau."""
    candidates = sorted({s for s, _ in rows if s is not None}) + [float("inf")]
    best, best_f1 = 0.0, -1.0
    for tau in [0.0, *candidates]:
        f1 = prf([(predict_no_answer(s, tau), g) for s, g in rows])["f1"]
        if f1 > best_f1:
            best, best_f1 = tau, f1
    return best


def aggregate(rows: list[dict], by: str = "qtype") -> dict[str, dict]:
    """Mean of every numeric metric, overall ('all') and per group; rows carry their group under `by`."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups["all"].append(row)
        groups[row[by]].append(row)
    out = {}
    for name, members in groups.items():
        keys = [k for k, v in members[0].items() if isinstance(v, (int, float)) and k != "rank" and not isinstance(v, bool)]
        out[name] = {"n": len(members), **{k: sum(m[k] for m in members if k in m) / len(members) for k in keys}}
    return out


def refusal_by_reason(rows: list[dict], reasons: dict[str, str]) -> dict[str, dict]:
    """§4.3: share of unanswerable questions predicted no-answer, per gold reason (no_period must not regress when
    filters relax). reasons = {qid: no_answer_reason}; rows carry `no_answer_pred`."""
    out: dict[str, dict] = {}
    for r in rows:
        if r["qid"] in reasons:
            m = out.setdefault(reasons[r["qid"]], {"n": 0, "refused": 0})
            m["n"] += 1
            m["refused"] += int(r["no_answer_pred"])
    return {k: {**m, "rate": m["refused"] / m["n"]} for k, m in sorted(out.items())}
