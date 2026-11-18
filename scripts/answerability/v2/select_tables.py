"""Answerability data v2: pick the tables Claude writes questions for (04-dataset-plan.md §4.3), and dump them in batches.

Stratified by survey (census 75%, 인구동향 8%, 외국인주민 6%, 출입국 4%, 인구이동 3%, other 4%), spread over menu
families, tables with 2+ classes first, monthly / quarterly tables always in. The pool is the trainer pool (mixft tables,
held 420 already out); KOS-192 dev / test gold tables are dropped later on the PC build, not here.
    python select_tables.py --tables data/tables.jsonl --pool pool_tables.json --n 60 --batch 20 --out OUT
writes OUT/tables.json (picked keys + stratum) and OUT/batch_XX.json (generation input: compact records).
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

SHARES = (("인구총조사", 0.75), ("인구동향조사", 0.08), ("지방자치단체외국인주민현황", 0.06),
          ("출입국자및체류외국인통계", 0.04), ("국내인구이동통계", 0.03), ("기타", 0.04))
LIST_CAP = 120  # labels shown per class in the generation input


def stratum(rec: dict) -> str:
    s = rec["info"]["survey_name"]
    return s if s in dict(SHARES) else "기타"


def family(rec: dict) -> str:
    parts = rec["info"]["menu_path"].split(" > ")
    return " > ".join(parts[2:6])


def monthly(rec: dict) -> bool:
    return bool({"M", "Q"} & set(rec.get("prd_se") or []))


def compact(rec: dict) -> dict:
    """The generation input: everything the writer needs, long label lists capped (region names: 시도 + a sample)."""
    classes = []
    for c in rec.get("classes") or []:
        labs = c.get("labels") or []
        out = {"name": c["name"], "n_labels": len(labs)}
        if c.get("levels"):
            out["levels"] = c["levels"]
        out["labels"] = labs[:LIST_CAP]
        if len(labs) > LIST_CAP:
            out["labels_note"] = f"앞 {LIST_CAP}개만 보임. 명세에는 보이는 값만 쓸 것"
        classes.append(out)
    return {"table": rec["info"]["key"], "name": rec["info"]["table_name"], "survey": rec["info"]["survey_name"],
            "menu": family(rec), "periods": rec.get("periods") or [], "prd_se": rec.get("prd_se") or [],
            "unit": rec.get("unit"), "items": rec.get("items") or [], "classes": classes}


def pick(recs: dict[str, dict], pool: list[str], n: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    by = defaultdict(list)
    for k in pool:
        by[stratum(recs[k])].append(k)
    want = {s: max(1, round(n * share)) for s, share in SHARES}
    want["인구총조사"] += n - sum(want.values())
    picked: list[str] = []
    for s, _ in SHARES:
        keys = sorted(by[s])
        rng.shuffle(keys)
        must = [k for k in keys if monthly(recs[k])][: max(1, want[s] // 3)]
        rest = sorted((k for k in keys if k not in must), key=lambda k: len(recs[k].get("classes") or []) < 2)
        got, fams = list(must), defaultdict(int)
        for k in got:
            fams[family(recs[k])] += 1
        for cap in (1, 2, 3, 99):  # spread over menu families first
            for k in rest:
                if len(got) >= want[s]:
                    break
                if k not in got and fams[family(recs[k])] < cap:
                    got.append(k)
                    fams[family(recs[k])] += 1
        picked += got[: want[s]]
    return picked


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", type=Path, required=True)
    ap.add_argument("--pool", type=Path, required=True)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--batch", type=int, default=20)
    ap.add_argument("--seed", type=int, default=1119)
    ap.add_argument("--exclude", type=Path, help="json list of keys already used (pilot) to skip")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    recs = {}
    for line in a.tables.open(encoding="utf-8"):
        r = json.loads(line)
        recs[r["info"]["key"]] = r
    pool = [k for k in json.loads(a.pool.read_text(encoding="utf-8")) if k in recs]
    if a.exclude:
        skip = set(json.loads(a.exclude.read_text(encoding="utf-8")))
        pool = [k for k in pool if k not in skip]
    keys = pick(recs, pool, a.n, a.seed)
    a.out.mkdir(parents=True, exist_ok=True)
    meta = [{"table": k, "stratum": stratum(recs[k]), "family": family(recs[k]), "monthly": monthly(recs[k]),
             "n_classes": len(recs[k].get("classes") or [])} for k in keys]
    (a.out / "tables.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    for i in range(0, len(keys), a.batch):
        part = [compact(recs[k]) for k in keys[i:i + a.batch]]
        (a.out / f"batch_{i // a.batch + 1:02d}.json").write_text(json.dumps(part, ensure_ascii=False, indent=1),
                                                                  encoding="utf-8")
    print(len(keys), "tables;", {s: sum(m["stratum"] == s for m in meta) for s, _ in SHARES},
          "monthly", sum(m["monthly"] for m in meta))


if __name__ == "__main__":
    main()
