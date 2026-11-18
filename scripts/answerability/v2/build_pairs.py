"""Answerability data v2 (04-dataset-plan.md §4.4): turn checked Claude questions (+ P2 positives) into labelled pairs.

    python build_pairs.py --tables data/tables.jsonl --gen GEN.jsonl... --pool mixft-v2/v2-strict/pool.jsonl --out DIR
writes DIR/pairs.jsonl (decided by code), DIR/judge_pairs.jsonl (need a blind Claude judgement before use) and
DIR/pairs_stats.json. One pair = {"pid", "qid", "q", "table", "label", "kind", "group", "src", "why"}.
Kinds:
  pos       base / ctrl question + its own table (1)              ctrl  ctrl question + own table (1)
  no_item   no_item question + own table (0, code: own slot "no", not hidden)
  neg_near  base question + a neighbour holding the asked years (top-5 BM25): code "no" -> 0; "yes" -> pos_alt (1);
            "unknown" or superset-suspect -> judge
  np_near   base question with the year moved to one the own table lacks + a neighbour holding that year: as neg_near
  np_table  the same moved question + its own table (0, the year is surely missing)
  month     base question with a month added on an annual table + own table (0)       (code variant, §3.5)
  oos       oos question + its top-2 BM25 tables (0)              easy  base question + a random other-topic table (0)
  pos_p2    P2 question (pool type P2, doc table) + its table and its `ok` tables (1), quota per q-type (§4.3)
KOS-192 dev / test gold tables are NOT removed here (the cloud does not see them): finalize on the PC drops every pair
whose own or paired table is excluded.
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from candidates import BM25  # noqa: E402
from cards import card, years_of  # noqa: E402
from spec_check import check, table_axes  # noqa: E402

YEAR = re.compile(r"(?<!\d)(19[2-9]\d|20[0-2]\d)(?=\s*년|\s|$|[^\d])")
P2_QUOTA = {"q0": 850, "q1": 850, "q2": 1300, "q3": 300, "q4": 200}


def name_words(rec: dict) -> set[str]:
    return set(re.findall(r"[가-힣A-Za-z0-9]+", rec["info"]["table_name"]))


def superset_suspect(own: dict, nb: dict) -> bool:
    """§3.4: the neighbour may hold the own table's data plus more (same indicator, one more breakdown)."""
    if name_words(own) <= name_words(nb):
        return True
    oi, ni = set(own.get("items") or []), set(nb.get("items") or [])
    if oi and len(oi & ni) >= 0.8 * len(oi) and set(table_axes(own)) <= set(table_axes(nb)):
        return True
    return False


def move_year(q: str, y: int, new: int) -> str | None:
    hits = [m for m in YEAR.finditer(q) if int(m.group(1)) == y]
    if len(hits) != 1:
        return None
    m = hits[0]
    return q[:m.start(1)] + str(new) + q[m.end(1):]


def pick_np_year(own_years: set[int], counts: Counter, rng: random.Random) -> int | None:
    lo, hi = min(own_years), max(own_years)
    ok = [y for y in range(1925, 2026) if y not in own_years and counts[y] >= 30]
    gap = [y for y in ok if lo < y < hi]
    near = [y for y in ok if not lo < y < hi and abs(y - lo) <= 10 or abs(y - hi) <= 10]
    for pool in (gap if rng.random() < 0.5 else [], near, ok):
        if pool:
            return rng.choice(pool)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", type=Path, required=True)
    ap.add_argument("--gen", type=Path, nargs="+", required=True, help="checked.jsonl files (rows with keep)")
    ap.add_argument("--pool", type=Path, help="mixft pool.jsonl for P2 positives")
    ap.add_argument("--held", type=Path, help="json list of eval-held table keys (never paired)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=1123)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    recs = {}
    for line in a.tables.open(encoding="utf-8"):
        r = json.loads(line)
        recs[r["info"]["key"]] = r
    held = set(json.loads(a.held.read_text(encoding="utf-8"))) if a.held else set()
    usable = [k for k in recs if k not in held]
    years = {k: set(years_of(recs[k].get("periods") or [])) for k in recs}
    ycount = Counter(y for k in usable for y in years[k])
    bm = BM25({k: card(recs[k]) for k in usable})
    topic = {k: recs[k].get("topic") or recs[k]["info"]["survey_name"] for k in recs}

    rows = []
    for p in a.gen:
        rows += [json.loads(x) for x in p.open(encoding="utf-8") if x.strip()]
    rows = [r for r in rows if r.get("keep")]
    pairs, judge = [], []

    def add(q, t, label, kind, row, why="", to_judge=False):
        d = {"qid": row["id"], "q": q, "table": t, "label": label, "kind": kind, "group": row.get("group"),
             "src": row.get("src", "claude"), "why": why}
        (judge if to_judge else pairs).append(d)

    def neighbours(q, want, own, k):
        pool = [t for t in usable if t != own and want <= years[t]]
        return sorted(pool, key=lambda t: -bm.score(q, t))[:k]

    for r in rows:
        own, spec, kind = r.get("table"), r.get("spec") or {}, r["kind"]
        if kind == "oos":
            for t in neighbours(r["q"], set(), own, 2):
                add(r["q"], t, 0, "oos", r)
            continue
        if own not in recs or own in held:
            continue
        if kind in ("base", "ctrl"):
            add(r["q"], own, 1, "pos" if kind == "base" else "ctrl", r)
        elif kind == "no_item":
            add(r["q"], own, 0, "no_item", r, (r.get("mutation") or {}).get("type", ""))
            continue
        if kind != "base":
            continue
        want = {int(y) for y in (spec.get("time") or {}).get("years") or []}
        n_cond = len(spec.get("dims") or []) + bool((spec.get("region") or {}).get("names")) + bool(want)
        # neg_near / pos_alt
        for t in neighbours(r["q"], want, own, 5 if want else 3):
            res = check(spec, recs[t], own=False)
            sus = superset_suspect(recs[own], recs[t])
            if not want or sus or res["verdict"] == "unknown" or n_cond < 2:
                add(r["q"], t, None, "near?", r, f"code={res['verdict']} sus={sus}", to_judge=True)
            elif res["verdict"] == "no" and not res["hidden"]:
                add(r["q"], t, 0, "neg_near", r, json.dumps(res["slots"], ensure_ascii=False))
            elif res["verdict"] == "yes":
                add(r["q"], t, 1, "pos_alt", r, "code yes")
        if len(want) == 1:
            y = next(iter(want))
            ny = pick_np_year(years[own], ycount, rng)
            q2 = move_year(r["q"], y, ny) if ny else None
            if q2:
                spec2 = {**spec, "time": {**(spec.get("time") or {}), "years": [ny]}}
                add(q2, own, 0, "np_table", r, f"{y}->{ny}")
                for t in neighbours(q2, {ny}, own, 2):
                    res = check(spec2, recs[t], own=False)
                    if res["verdict"] == "no" and not res["hidden"] and not superset_suspect(recs[own], recs[t]):
                        add(q2, t, 0, "np_near", r, f"{y}->{ny}")
                    else:
                        add(q2, t, None, "np_near?", r, f"{y}->{ny} code={res['verdict']}", to_judge=True)
            prd = set(recs[own].get("prd_se") or [])
            if not prd & {"M", "Q"} and rng.random() < 0.25:
                m = rng.choice([3, 6, 9, 12])
                q3 = re.sub(rf"(?<!\d){y}(?!\d)(?:년도|년)?", f"{y}년 {m}월", r["q"], count=1)
                if q3 != r["q"]:
                    add(q3, own, 0, "month", r, f"{m}월")
        # easy
        others = [t for t in usable if topic[t] != topic[own]] or usable
        ys = {int(x) for x in YEAR.findall(r["q"])}
        others = [t for t in others if ys <= years[t]] or others  # same years, so the year alone is no cue
        add(r["q"], rng.choice(others), 0, "easy", r)

    if a.pool:
        p2 = defaultdict(list)
        for line in a.pool.open(encoding="utf-8"):
            x = json.loads(line)
            if x.get("type") == "P2" and x.get("doc") == "table" and x["table"] in recs and x["table"] not in held:
                qt = x["id"].split("#")[-1]
                p2[qt].append(x)
        for qt, xs in p2.items():
            rng.shuffle(xs)
            for x in xs[: P2_QUOTA.get(qt, 0)]:
                ok = x.get("ok")
                ok = ast.literal_eval(ok) if isinstance(ok, str) else (ok or [])
                for t in dict.fromkeys([x["table"], *ok]):
                    if t in recs and t not in held:
                        pairs.append({"qid": x["id"], "q": x["q"], "table": t, "label": 1, "kind": "pos_p2",
                                      "group": x["id"], "src": "P2", "why": qt})
    for i, d in enumerate(pairs):
        d["pid"] = f"p{i:06d}"
    for i, d in enumerate(judge):
        d["pid"] = f"j{i:06d}"
    a.out.mkdir(parents=True, exist_ok=True)
    for name, data in (("pairs.jsonl", pairs), ("judge_pairs.jsonl", judge)):
        with (a.out / name).open("w", encoding="utf-8") as f:
            for d in data:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
    st = {"pairs": len(pairs), "judge": len(judge), "by_kind": Counter(d["kind"] for d in pairs),
          "by_label": Counter(str(d["label"]) for d in pairs), "judge_by_kind": Counter(d["kind"] for d in judge)}
    (a.out / "pairs_stats.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(st, ensure_ascii=False))


if __name__ == "__main__":
    main()
