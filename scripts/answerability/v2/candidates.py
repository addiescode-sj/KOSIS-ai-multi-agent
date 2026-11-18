"""Answerability data v2: candidate tables for question-level checks (04-dataset-plan.md §5.1, 05 §5).

For an eval "no answer" question (C1 no_item, C2 no_period) every table of the registry must fail, not just the one it
was written from. A blind judge looks at the top-k lexical candidates: BM25 over char 2-grams of the card text, with the
year filter the service applies (only tables holding the asked years; month / quarter asks keep annual tables too).
    python candidates.py --tables data/tables.jsonl --gen gen_*.jsonl --kinds no_item no_period --k 10 --out cand.jsonl
writes one line per question: {"id", "q", "own", "cands": [{"table", "score", "code"}]} where code is spec_check's
verdict for the pair (own=False) as a hint for the judge input builder.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cards import card, years_of  # noqa: E402
from spec_check import check  # noqa: E402


def grams(s: str) -> list[str]:
    s = "".join(s.split())
    return [s[i:i + 2] for i in range(len(s) - 1)]


class BM25:
    def __init__(self, docs: dict[str, str], k1: float = 1.2, b: float = 0.75):
        self.tf = {k: Counter(grams(v)) for k, v in docs.items()}
        self.len = {k: sum(c.values()) for k, c in self.tf.items()}
        self.avg = sum(self.len.values()) / max(1, len(self.len))
        df = Counter(g for c in self.tf.values() for g in c)
        n = len(docs)
        self.idf = {g: math.log(1 + (n - d + 0.5) / (d + 0.5)) for g, d in df.items()}
        self.k1, self.b = k1, b

    def score(self, q: str, key: str) -> float:
        tf, ln = self.tf[key], self.len[key]
        s = 0.0
        for g in set(grams(q)):
            f = tf.get(g, 0)
            if f:
                s += self.idf[g] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * ln / self.avg))
        return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", type=Path, required=True)
    ap.add_argument("--gen", type=Path, nargs="+", required=True)
    ap.add_argument("--kinds", nargs="+", default=["no_item", "no_period"])
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    recs = {}
    for line in a.tables.open(encoding="utf-8"):
        r = json.loads(line)
        recs[r["info"]["key"]] = r
    bm = BM25({k: card(r) for k, r in recs.items()})
    years = {k: set(years_of(r.get("periods") or [])) for k, r in recs.items()}
    out = []
    for p in a.gen:
        for line in p.open(encoding="utf-8"):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") not in a.kinds:
                continue
            want = {int(y) for y in ((row.get("spec") or {}).get("time") or {}).get("years") or []}
            pool = [k for k in recs if k != row.get("table") and want <= years[k]]
            ranked = sorted(pool, key=lambda k: -bm.score(row["q"], k))[: a.k]
            cands = [{"table": k, "score": round(bm.score(row["q"], k), 2),
                      "code": check(row.get("spec") or {}, recs[k], own=False)["verdict"]} for k in ranked]
            out.append({"id": row["id"], "q": row["q"], "own": row.get("table"), "cands": cands})
    with a.out.open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(len(out), "questions;", Counter(c["code"] for r in out for c in r["cands"]))


if __name__ == "__main__":
    main()
