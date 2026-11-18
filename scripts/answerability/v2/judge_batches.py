"""Blind judge batches for training pairs (04 plan §5.2, §4.4).

Mixes two things into one blind set so the judge cannot tell them apart:
  1. a capped sample of judge_pairs.jsonl (pairs the code could not decide), and
  2. an audit sample of code-decided pairs.jsonl (100 per 0-label kind, 60 per 1-label kind).
Pairs are grouped by question text, so each judge item is one question with a few table cards.

  python judge_batches.py --tables tables.jsonl --pairs DIR --out DIR/judge --per 120
  python judge_batches.py --suspect --tables tables.jsonl --pairs DIR --out DIR/judge   # doubtful -> fb_in_NN
  python judge_batches.py --merge --pairs DIR --out DIR/judge     # jp_out_NN (+ fb_out_NN overrides)

merge writes DIR/judged.jsonl (resolved judge pairs, kind pos_alt / neg_near / np_near) and DIR/audit_report.json.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cards import card  # noqa: E402

# how many undecided pairs to judge, by (kind, code verdict); None = all
CAP = {("near?", "yes"): None, ("np_near?", "yes"): None, ("near?", "no"): 300,
       ("np_near?", "no"): None, ("near?", "unknown"): 700, ("np_near?", "unknown"): 250}
AUDIT = {"neg_near": 100, "np_table": 100, "np_near": 100, "easy": 100, "no_item": 100, "month": 100,
         "oos": 100, "pos": 60, "ctrl": 60, "pos_alt": 60, "pos_p2": 60}


def jl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.open(encoding="utf-8") if x.strip()]


def code_of(why: str) -> str:
    return why.split("code=")[-1].split()[0]


def make(a) -> None:
    rng = random.Random(a.seed)
    recs = {}
    for line in a.tables.open(encoding="utf-8"):
        r = json.loads(line)
        recs[r["info"]["key"]] = r
    undecided = jl(a.pairs / "judge_pairs.jsonl")
    by = defaultdict(list)
    for d in undecided:
        by[(d["kind"], code_of(d["why"]))].append(d)
    pick = []
    for k, rows in by.items():
        cap = CAP.get(k, 0)
        rng.shuffle(rows)
        pick += [dict(d, src_set="undecided") for d in (rows if cap is None else rows[:cap])]
    decided = jl(a.pairs / "pairs.jsonl")
    byk = defaultdict(list)
    for i, d in enumerate(decided):
        byk[d["kind"]].append(dict(d, pid=f"a{i:06d}", src_set="audit"))
    for k, n in AUDIT.items():
        rows = byk.get(k, [])
        rng.shuffle(rows)
        pick += rows[:n]
    groups = defaultdict(list)
    for d in pick:
        groups[d["q"]].append(d)
    items = list(groups.items())
    rng.shuffle(items)
    a.out.mkdir(parents=True, exist_ok=True)
    keymap, nb = {}, 0
    for b in range(0, len(items), a.per):
        nb += 1
        with (a.out / f"jp_in_{nb:02d}.jsonl").open("w", encoding="utf-8") as f:
            for j, (q, ds) in enumerate(items[b:b + a.per]):
                qid = f"b{nb:02d}q{j:03d}"
                seen, cards = {}, []
                for d in ds:
                    if d["table"] not in seen:
                        seen[d["table"]] = len(cards) + 1
                        cards.append({"n": seen[d["table"]], "card": card(recs[d["table"]])})
                    keymap.setdefault(qid, []).append([seen[d["table"]], d["pid"]])
                f.write(json.dumps({"qid": qid, "q": q, "cards": cards}, ensure_ascii=False) + "\n")
    (a.out / "keymap.json").write_text(json.dumps(keymap, ensure_ascii=False), encoding="utf-8")
    (a.out / "picked.jsonl").write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in pick),
                                        encoding="utf-8")
    print(json.dumps({"batches": nb, "questions": len(items), "pairs": len(pick),
                      "by_set": Counter(d["src_set"] for d in pick)}, ensure_ascii=False))


def merge(a) -> None:
    keymap = json.loads((a.out / "keymap.json").read_text(encoding="utf-8"))
    pick = {d["pid"]: d for d in jl(a.out / "picked.jsonl")}
    verdict, real = {}, {}
    fkey = a.out / "fb_keymap.json"
    keymap.update(json.loads(fkey.read_text(encoding="utf-8")) if fkey.exists() else {})
    first = {}
    for pat in ("jp_out_*.jsonl", "fb_out_*.jsonl"):  # fb (Fable re-judge) comes last and overrides
        for p in sorted(a.out.glob(pat)):
            for o in jl(p):
                yes, amb = set(o.get("yes", [])), set(o.get("ambiguous", []))
                for n, pid in keymap.get(o["qid"], []):
                    v = "yes" if n in yes else "ambiguous" if n in amb else "no"
                    first.setdefault(pid, v)
                    verdict[pid] = v
                    real[pid] = o.get("realistic", True)
    missing = [pid for pid in pick if pid not in verdict]
    out, audit = [], defaultdict(Counter)
    for pid, d in pick.items():
        v = verdict.get(pid)
        if v is None:
            continue
        if d["src_set"] == "audit":
            want = "yes" if d["label"] == 1 else "no"
            hid = d.get("hidden_pos") and v == "ambiguous"
            audit[d["kind"]]["n"] += 1
            audit[d["kind"]]["disagree"] += int(v != want and not hid)
            audit[d["kind"]]["ambiguous"] += int(v == "ambiguous")
            audit[d["kind"]]["unrealistic"] += int(not real[pid])
            continue
        if v == "ambiguous":
            continue
        kind = "pos_alt" if v == "yes" else ("neg_near" if d["kind"] == "near?" else "np_near")
        out.append({k: x for k, x in d.items() if k != "src_set"} | {"label": int(v == "yes"), "kind": kind,
                                                                    "why": d["why"] + " judge=" + v})
    with (a.pairs / "judged.jsonl").open("w", encoding="utf-8") as f:
        for d in out:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    flipped = Counter(pick[p]["kind"] for p in verdict if first[p] != verdict[p])
    rep = {"missing": len(missing), "fable_changed": dict(flipped), "judged": Counter(d["kind"] for d in out),
           "audit": {k: dict(c) for k, c in sorted(audit.items())},
           "audit_fail": [k for k, c in audit.items() if c["disagree"] > 3]}  # §5.2: <= 3 per 100 (0) / 60 (1)
    (a.pairs / "audit_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def suspect(a) -> None:
    """Pairs whose Sonnet verdict is doubtful -> fb_in_NN.jsonl for a Fable re-judge.
    doubtful = ambiguous, audit pair that disagrees with its code label, or undecided pair whose
    verdict contradicts the code verdict (code yes -> judge no, code no -> judge yes)."""
    recs = {}
    for line in a.tables.open(encoding="utf-8"):
        r = json.loads(line)
        recs[r["info"]["key"]] = r
    keymap = json.loads((a.out / "keymap.json").read_text(encoding="utf-8"))
    pick = {d["pid"]: d for d in jl(a.out / "picked.jsonl")}
    sus, why = [], Counter()
    for p in sorted(a.out.glob("jp_out_*.jsonl")):
        for o in jl(p):
            yes, amb = set(o.get("yes", [])), set(o.get("ambiguous", []))
            for n, pid in keymap.get(o["qid"], []):
                d = pick[pid]
                v = "yes" if n in yes else "ambiguous" if n in amb else "no"
                if v == "ambiguous":
                    r = "ambiguous"
                elif d["src_set"] == "audit":
                    r = "audit_disagree" if v != ("yes" if d["label"] == 1 else "no") else None
                else:
                    c = code_of(d["why"])
                    r = "code_conflict" if (c == "yes" and v == "no") or (c == "no" and v == "yes") else None
                if r:
                    why[r] += 1
                    sus.append(d)
    groups = defaultdict(list)
    for d in sus:
        groups[d["q"]].append(d)
    keymap2, nb = {}, 0
    items = list(groups.items())
    for b in range(0, len(items), a.per):
        nb += 1
        with (a.out / f"fb_in_{nb:02d}.jsonl").open("w", encoding="utf-8") as f:
            for j, (q, ds) in enumerate(items[b:b + a.per]):
                qid = f"f{nb:02d}q{j:03d}"
                seen, cards = {}, []
                for d in ds:
                    if d["table"] not in seen:
                        seen[d["table"]] = len(cards) + 1
                        cards.append({"n": seen[d["table"]], "card": card(recs[d["table"]])})
                    keymap2.setdefault(qid, []).append([seen[d["table"]], d["pid"]])
                f.write(json.dumps({"qid": qid, "q": q, "cards": cards}, ensure_ascii=False) + "\n")
    (a.out / "fb_keymap.json").write_text(json.dumps(keymap2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"pairs": len(sus), "questions": len(items), "batches": nb, "why": why}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", type=Path)
    ap.add_argument("--pairs", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--per", type=int, default=120)
    ap.add_argument("--seed", type=int, default=1124)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--suspect", action="store_true", help="write fb_in_NN.jsonl for a Fable re-judge")
    a = ap.parse_args()
    merge(a) if a.merge else suspect(a) if a.suspect else make(a)
