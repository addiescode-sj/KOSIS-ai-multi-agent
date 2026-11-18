"""04 plan §5.3 shortcut checks on training pairs (code only, no model).

  python shortcut_check.py --tables tables.jsonl --pairs pairs.jsonl [judged.jsonl] --out report.json

1. year-only rule: share of all negatives caught by "card has no asked year" (np_table only should rely on it): <= 10%
2. BM25 AUC: pos vs no_item <= .65, pos vs neg_near <= .80
3. token bias: top-50 tokens skewed to label 0 must appear in label-1 pairs at >= 30% of their label-0 rate
4. no_item questions with a paired ctrl in the same group: >= 70%
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from candidates import BM25  # noqa: E402
from cards import card, years_of  # noqa: E402

YEAR = re.compile(r"(?<!\d)(19[4-9]\d|20[0-4]\d)(?!\d)")
TOK = re.compile(r"[가-힣A-Za-z]+")


def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    rank, i, sum_r = 0, 0, 0.0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        r = (i + 1 + j) / 2
        sum_r += r * sum(1 for k in range(i, j) if allv[k][1] == 1)
        i = j
    return (sum_r - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", type=Path, required=True)
    ap.add_argument("--pairs", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    recs = {}
    for line in a.tables.open(encoding="utf-8"):
        r = json.loads(line)
        recs[r["info"]["key"]] = r
    pairs = [json.loads(x) for p in a.pairs for x in p.open(encoding="utf-8") if x.strip()]
    pairs = [d for d in pairs if d["table"] in recs]
    rep = {}

    neg = [d for d in pairs if d["label"] == 0]
    caught = Counter()
    for d in neg:
        ys = {int(y) for y in YEAR.findall(d["q"])}
        if ys and not ys & set(years_of(recs[d["table"]].get("periods") or [])):
            caught[d["kind"]] += 1
    non_np = sum(v for k, v in caught.items() if k != "np_table")
    rep["1_year_only"] = {"by_kind": dict(caught), "share_all_neg": round(sum(caught.values()) / len(neg), 3),
                          "share_excl_np_table": round(non_np / len(neg), 3), "pass": non_np / len(neg) <= .10}

    tabs = sorted({d["table"] for d in pairs})
    bm = BM25({k: card(recs[k]) for k in tabs})
    score = defaultdict(list)
    for d in pairs:
        if d["kind"] in ("pos", "no_item", "neg_near"):
            score[d["kind"]].append(bm.score(d["q"], d["table"]))
    a1, a2 = auc(score["pos"], score["no_item"]), auc(score["pos"], score["neg_near"])
    rep["2_bm25_auc"] = {"pos_vs_no_item": a1 and round(a1, 3), "pos_vs_neg_near": a2 and round(a2, 3),
                         "pass": (a1 or 0) <= .65 and (a2 or 0) <= .80}

    tf = {0: Counter(), 1: Counter()}
    n = Counter()
    for d in pairs:
        n[d["label"]] += 1
        tf[d["label"]].update(set(TOK.findall(d["q"])))
    rate = {lab: {t: c / n[lab] for t, c in tf[lab].items()} for lab in (0, 1)}
    skew = sorted((t for t in tf[0] if tf[0][t] >= 30),
                  key=lambda t: rate[0][t] / (rate[1].get(t, 0) + 1e-4), reverse=True)[:50]
    bad = [(t, round(rate[0][t], 4), round(rate[1].get(t, 0), 4)) for t in skew
           if rate[1].get(t, 0) < .3 * rate[0][t]]
    rep["3_token_bias"] = {"top50": skew, "under_30pct": bad, "pass": not bad}

    grp = defaultdict(set)
    for d in pairs:
        grp[d["group"]].add(d["kind"])
    ni = [g for g, k in grp.items() if "no_item" in k]
    paired = sum(1 for g in ni if "ctrl" in grp[g])
    rep["4_ctrl_pairing"] = {"no_item_groups": len(ni), "with_ctrl": paired,
                             "share": round(paired / max(1, len(ni)), 3), "pass": paired >= .7 * len(ni)}
    a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: {x: y for x, y in v.items() if x not in ("top50", "under_30pct", "by_kind")}
                      for k, v in rep.items()}, ensure_ascii=False, indent=1))
    print("token bias fails:", bad[:15])


if __name__ == "__main__":
    main()
