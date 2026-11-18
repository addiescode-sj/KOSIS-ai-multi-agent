"""Answerability data v2: code checks on Claude-written questions (05-label-guide.md §6), then blind-judge input.

    python check_gen.py --tables data/tables.jsonl --gen DIR/gen_*.jsonl --out DIR
writes DIR/checked.jsonl (every row + check result + keep flag), DIR/judge_in.jsonl (kept rows as question + card only,
shuffled, no kind / label), DIR/check_report.json (pass rates, no_item per table, style / time / region mix, overlap).
Rules: base / ctrl must be "yes" on their own table; no_item must be "no" on its own table, not hidden, and on the slot
its type says (A/B/F dims, C region, D measure, E time); oos only needs a measure text. Duplicates (char 2-gram
Jaccard >= 0.7, same table and kind, other group) drop the later row.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cards import MAX_ITEMS, MAX_LABELS, card  # noqa: E402
from spec_check import check  # noqa: E402

TYPE_SLOT = {"P-GAP": {"time"}, "P-BEFORE": {"time"}, "P-AFTER": {"time"}, "P-MONTH": {"time"}, "A": {"dims"}, "B": {"dims"}, "F": {"dims", "measure"}, "C": {"region"}, "D": {"measure"}, "E": {"time"}}
METRO_FROM = (("인천직할", 1981), ("인천광역", 1981), ("대구직할", 1981), ("대구광역", 1981), ("광주직할", 1986),
              ("광주광역", 1986), ("대전직할", 1989), ("대전광역", 1989), ("울산광역", 1997), ("세종", 2012))
FIELDS = ("id", "table", "group", "kind", "style", "q", "spec", "mutation", "note")


def grams(s: str) -> set[str]:
    s = "".join(s.split())
    return {s[i:i + 2] for i in range(len(s) - 1)}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def overlap(q: str, rec: dict) -> float:
    """Share of the question's char 2-grams found in the table name or items (copying the table's words)."""
    qg = grams(q)
    tg = grams(rec["info"]["table_name"] + " ".join(rec.get("items") or []))
    return len(qg & tg) / len(qg) if qg else 0.0


def hidden_value(spec: dict, rec: dict) -> str:
    """A base / ctrl value the card does not show (past the label / item caps): the model could not see it is there."""
    cls = {c.get("name"): c for c in rec.get("classes") or []}
    for d in spec.get("dims") or []:
        c = cls.get(d.get("class"))
        if c is None or c.get("levels"):
            continue
        labs = c.get("labels") or []
        for lab in d.get("labels") or []:
            if lab in labs and labs.index(lab) >= MAX_LABELS:
                return f"label {lab} hidden on card"
    items = rec.get("items") or []
    for it in (spec.get("measure") or {}).get("items") or []:
        if it in items and items.index(it) >= MAX_ITEMS:
            return f"item {it} hidden on card"
    return ""


def judge_row(row: dict, rec: dict) -> tuple[bool, str, dict | None]:
    kind, spec = row.get("kind"), row.get("spec") or {}
    if kind == "oos":
        return bool((spec.get("measure") or {}).get("text")), "oos", None
    if rec is None:
        return False, "unknown table", None
    res = check(spec, rec, own=True)
    years = (spec.get("time") or {}).get("years") or []
    for n in (spec.get("region") or {}).get("names") or []:
        for head, since in METRO_FROM:
            if n.startswith(head) and any(int(y) < since for y in years) and kind in ("base", "ctrl"):
                return False, f"{n} did not exist before {since}", res
    if kind in ("base", "ctrl"):
        if res["verdict"] == "yes":
            res["hidden_pos"] = hidden_value(spec, rec)  # kept: true at serving too; the judge may say ambiguous
        return res["verdict"] == "yes", f"want yes, got {res['verdict']}", res
    if kind in ("no_item", "no_period"):
        if res["verdict"] != "no":
            return False, f"want no, got {res['verdict']}", res
        if res["hidden"]:
            return False, "no rests on values the card hides", res
        t = ((row.get("mutation") or {}).get("type") or "").upper()
        bad = {k for k, v in res["slots"].items() if v.startswith("no")}
        if t in TYPE_SLOT and not (bad & TYPE_SLOT[t]):
            return False, f"type {t} but failing slot {sorted(bad)}", res
        return True, "", res
    return False, f"bad kind {kind}", res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", type=Path, required=True)
    ap.add_argument("--gen", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    recs = {}
    for line in a.tables.open(encoding="utf-8"):
        r = json.loads(line)
        recs[r["info"]["key"]] = r
    rows, bad_lines = [], 0
    for p in a.gen:
        for line in p.open(encoding="utf-8"):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                bad_lines += 1
                continue
            if any(f not in r for f in FIELDS):
                bad_lines += 1
                continue
            rows.append(r)
    seen = defaultdict(list)
    out, judge = [], []
    for r in rows:
        rec = recs.get(r.get("table") or "")
        ok, why, res = judge_row(r, rec)
        g = grams(r["q"])
        key = (r.get("table"), r.get("kind"))  # minimal pairs (same group, base -> no_item -> ctrl) are meant to overlap
        if ok and any(grp != r.get("group") and jaccard(g, h) >= 0.7 for grp, h in seen[key]):
            ok, why = False, "duplicate"
        if ok:
            seen[key].append((r.get("group"), g))
        ov = overlap(r["q"], rec) if rec else None
        out.append({**r, "check": res, "keep": ok, "why": why, "overlap": ov})
        if ok and rec is not None:
            judge.append({"jid": r["id"], "q": r["q"], "card": card(rec)})
    random.Random(a.seed).shuffle(judge)
    a.out.mkdir(parents=True, exist_ok=True)
    with (a.out / "checked.jsonl").open("w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with (a.out / "judge_in.jsonl").open("w", encoding="utf-8") as f:
        for r in judge:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    by_kind = defaultdict(Counter)
    for r in out:
        by_kind[r["kind"]][r["keep"]] += 1
    tables = {r["table"] for r in out if r.get("table") and r["kind"] != "oos"}
    ni = Counter(r["table"] for r in out if r["kind"] == "no_item" and r["keep"])
    new = [r for r in out if r["kind"] in ("base", "ctrl", "no_item")]
    rep = {
        "rows": len(out), "bad_lines": bad_lines,
        "pass_rate": round(sum(r["keep"] for r in out) / max(1, len(out)), 3),
        "by_kind": {k: {"keep": c[True], "drop": c[False]} for k, c in by_kind.items()},
        "drop_reasons": Counter(r["why"].split(",")[0] for r in out if not r["keep"]).most_common(),
        "tables_with_2_no_item": f"{sum(ni[t] >= 2 for t in tables)}/{len(tables)}",
        "no_item_types": Counter(((r.get("mutation") or {}).get("type") or "?") for r in out
                                 if r["kind"] == "no_item" and r["keep"]),
        "style": Counter(r.get("style") for r in out if r["kind"] == "base"),
        "time": Counter(("month" if (r["spec"].get("time") or {}).get("months") or (r["spec"].get("time") or {}).get(
            "quarters") else {0: "none", 1: "one_year"}.get(len((r["spec"].get("time") or {}).get("years") or []),
                                                               "range")) for r in new),
        "region": Counter(("names" if (r["spec"].get("region") or {}).get("names") else
                           ("level" if (r["spec"].get("region") or {}).get("level") not in (None, "전국") else "none"))
                          for r in new),
        "overlap_le_0.5": round(sum(1 for r in new if r["overlap"] is not None and r["overlap"] <= 0.5)
                                / max(1, len(new)), 3),
        "judge_rows": len(judge),
    }
    (a.out / "check_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
