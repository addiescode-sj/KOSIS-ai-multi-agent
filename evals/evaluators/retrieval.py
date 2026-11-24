"""L1 retrieval evaluator: pure functions over ranked table keys. Knows nothing about LangChain.

Per item: the rank of the first gold key (any slot, any alternative), Hit@k, reciprocal rank, Complete@k (every
slot covered in the top k) and SlotRecall@k. No answer (2026-11-19): no threshold. An item is predicted unanswerable
when the search itself refused (`refused`: the graph's gate route, else the stage 2 LLM picked nothing or no table
came back); positives are the unanswerable items. The stage 2 LLM re-ranker makes the call the old tau made.
Overall accuracy (전체 정답률, `is_correct`): an answerable item is right when the search did not refuse and its top 1
table is a gold key (Hit@1); an unanswerable item is right when the search refused. A failed search is wrong.
tau (`predict_no_answer`, `best_tau`) is kept only for the slot ablation layer, which has no stage 2.
"""

from __future__ import annotations

from collections import defaultdict

from evals.schemas import RetrievalItem

KS = (1, 2, 3, 5, 10)  # 2: a multi-table item is correct only when every slot is in the top 2 (Complete@2)


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
        # Upper bound (T3-22): every unjudged key in the top 10 counted as gold. The real value lies between the two.
        upper = next((i for i, k in enumerate(top, 1) if k in item.gold_keys or k not in judged), None)
        for k in (1, 5, 10):
            row[f"hit@{k}_upper"] = int(upper is not None and upper <= k)
        row["rr_upper"] = 1 / upper if upper else 0.0
    return row


def filter_pass(item: RetrievalItem, passed: set[str]) -> bool:
    """Filter recall of one item: every table slot keeps at least one key among the tables that pass the filter.

    Measured on the whole registry (filters.filter_tables), not on the retriever's top k, so a gold table the
    filter kept but the ranking cut is not counted as a filter loss.
    """
    return all(passed & set(slot) for slot in item.required)


def predict_no_answer(top_score: float | None, tau: float) -> bool:
    """Ablation layer only (no stage 2 there). L1 uses `refused`."""
    return top_score is None or top_score < tau


def llm_no_answer(row: dict) -> bool:
    """The LLM re-ranker picked nothing. A failed call (stage2_error) is not a verdict: it keeps the stage 1 order."""
    return row.get("stage2_picked_n") == 0 and not row.get("stage2_error")


def refused(row: dict) -> bool:
    """Did the search itself refuse (find no table)? No tau. The graph's gate span decides (route == "refuse");
    a row without a gate span (a non-graph retriever) falls back to: the stage 2 LLM picked nothing, or no table came
    back. A plain retriever (BM25, hybrid) therefore never refuses."""
    gate = next((s for s in row.get("search_spans") or [] if s.get("name") == "gate"), None)
    if gate is not None:
        return (gate.get("attributes") or {}).get("route") == "refuse"
    return llm_no_answer(row) or not row.get("keys")


def is_correct(row: dict, gold: set[str]) -> bool:
    """One item of the overall accuracy. row carries answerable, keys, error and no_answer_pred (= refused(row)).
    Answerable: not refused and the top 1 table is a gold key. Unanswerable: refused. A failed search is wrong."""
    if row.get("error"):
        return False
    if row["answerable"]:
        return not row["no_answer_pred"] and bool(row["keys"]) and row["keys"][0] in gold
    return bool(row["no_answer_pred"])


OUTCOMES = {"hit": "정답 (1위가 정답 표)", "wrong_table": "다른 표 1위", "wrong_refusal": "답 있는데 거절",
            "refused": "답 없음 거절 (정답)", "answered_unanswerable": "답 없는데 표를 냄", "error": "실행 오류"}


def outcome(row: dict) -> str:
    """Key of OUTCOMES for a row that carries answerable, error, no_answer_pred and correct."""
    if row.get("error"):
        return "error"
    if row["answerable"]:
        return "hit" if row["correct"] else "wrong_refusal" if row["no_answer_pred"] else "wrong_table"
    return "refused" if row["correct"] else "answered_unanswerable"


def accuracy(rows: list[dict], by: str = "split") -> dict[str, dict]:
    """Overall accuracy ('all' and per group under `by`): every item counts, answerable or not.
    acc_ans = Hit@1 with refusals counted wrong; acc_una = share of unanswerable items refused (None: no such item)."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups["all"].append(row)
        groups[str(row.get(by))].append(row)
    out = {}
    for name, members in groups.items():
        ans = [r for r in members if r["answerable"]]
        una = [r for r in members if not r["answerable"]]
        out[name] = {"n": len(members), "correct": sum(r["correct"] for r in members),
                     "acc": sum(r["correct"] for r in members) / len(members),
                     "n_ans": len(ans), "acc_ans": sum(r["correct"] for r in ans) / len(ans) if ans else None,
                     "n_una": len(una), "acc_una": sum(r["correct"] for r in una) / len(una) if una else None}
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
    """Ablation layer only. Threshold with the best no-answer F1 over (top-1 score, gold no-answer) rows; ties keep
    the lowest tau."""
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
    filters relax). reasons = {qid: no_answer_reason}; rows carry `no_answer_pred` (L1: the search's own refusal)."""
    out: dict[str, dict] = {}
    for r in rows:
        if r["qid"] in reasons:
            m = out.setdefault(reasons[r["qid"]], {"n": 0, "refused": 0})
            m["n"] += 1
            m["refused"] += int(r["no_answer_pred"])
    return {k: {**m, "rate": m["refused"] / m["n"]} for k, m in sorted(out.items())}
