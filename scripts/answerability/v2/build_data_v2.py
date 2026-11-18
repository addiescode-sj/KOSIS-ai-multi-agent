"""Answerability v2: build the Colab training folder on the PC from the judged pair lists (04 plan, decided 11-17).

Inputs (cloud-built, copied to scripts/answerability/v2/data/):
    pairs.jsonl    code-decided pairs (build_pairs.py)
    judged.jsonl   judge-decided pairs (judge_batches.py --merge): pos_alt / neg_near / np_near
    --tables       data/tables.jsonl (cards, menu families)
    --exclude      KOS-192 dev + test files: only their table keys are read (build_data.excluded_keys); no question
                   text is read or printed. A pair is dropped when its table or the table its question was written
                   for (qid prefix) is one of them, and those tables get no card.
Kinds dropped from pairs.jsonl by default (audit failed, 11-17): pos_alt (code), pos_p2, month.
Validation split: whole menu families (menu_path parent) of the question tables, until --val-tables question tables are
out; every pair of those questions goes to val, and train pairs whose table is in a val family are dropped too.
Outputs (--out-root/<build>): pairs.jsonl (fields q, table, label, kind, group, split), cards.json, stats.json, <build>.zip

    python scripts\\answerability\\v2\\build_data_v2.py --selftest
    python scripts\\answerability\\v2\\build_data_v2.py --build ans-v2
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from build_data import excluded_keys, sha8  # noqa: E402
from cards import CARD_FORMAT, card, load_records  # noqa: E402

DROP = ("pos_alt", "pos_p2", "month")
KEEP_FIELDS = ("q", "table", "label", "kind", "group")


def read_jsonl(p: Path) -> list[dict]:
    with p.open(encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def own_table(d: dict) -> str:
    """Table the question was written for; out-of-scope questions (qid oos-*) have none -> the paired table."""
    return d["table"] if d["qid"].startswith("oos-") else d["qid"].rsplit("-", 1)[0]


def family(rec: dict) -> str:
    mp = rec["info"].get("menu_path") or rec["info"]["key"]
    return mp.rsplit(" > ", 1)[0]


def assemble(code: list[dict], judged: list[dict], recs: dict, held: set[str], drop: tuple[str, ...],
             val_tables: int, seed: int) -> tuple[list[dict], Counter]:
    tally = Counter()
    rows = []
    for src, data in (("code", code), ("judge", judged)):
        for d in data:
            if src == "code" and d["kind"] in drop:
                tally[f"drop_kind:{d['kind']}"] += 1
                continue
            if d["table"] in held or own_table(d) in held:
                tally["drop_kos192"] += 1
                continue
            if d["table"] not in recs or own_table(d) not in recs:
                tally["drop_no_record"] += 1
                continue
            rows.append({k: d[k] for k in KEEP_FIELDS} | {"own": own_table(d), "src": src})
    fam: dict[str, set[str]] = {}
    for d in rows:
        fam.setdefault(family(recs[d["own"]]), set()).add(d["own"])
    order = sorted(fam)
    random.Random(seed).shuffle(order)
    val_fams, n = set(), 0
    for f in order:
        if n >= val_tables:
            break
        val_fams.add(f)
        n += len(fam[f])
    out = []
    for d in rows:
        if family(recs[d["own"]]) in val_fams:
            d["split"] = "val"
        elif family(recs[d["table"]]) in val_fams:
            tally["drop_train_pair_on_val_family"] += 1
            continue
        else:
            d["split"] = "train"
        out.append(d)
    tally["val_families"] = len(val_fams)
    tally["val_question_tables"] = n
    return out, tally


def build(a) -> dict:
    recs = load_records(Path(a.tables))
    held = excluded_keys([Path(p) for p in a.exclude])
    code, judged = read_jsonl(Path(a.pairs)), read_jsonl(Path(a.judged))
    rows, tally = assemble(code, judged, recs, held, tuple(a.drop), a.val_tables, a.seed)
    out = Path(a.out_root) / a.build
    out.mkdir(parents=True, exist_ok=True)
    with (out / "pairs.jsonl").open("w", encoding="utf-8") as f:
        for d in rows:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    used = sorted({d["table"] for d in rows})
    (out / "cards.json").write_text(json.dumps({k: card(recs[k]) for k in used}, ensure_ascii=False), encoding="utf-8")
    stats = {
        "card_format": CARD_FORMAT, "seed": a.seed, "pairs_sha8": sha8(Path(a.pairs)), "judged_sha8": sha8(Path(a.judged)),
        "tables_sha8": sha8(Path(a.tables)), "excluded_tables": len(held), "dropped_kinds": list(a.drop),
        "filter": dict(tally), "pairs": len(rows), "cards": len(used),
        "by_split": {s: {"pairs": sum(d["split"] == s for d in rows),
                         "groups": len({d["group"] for d in rows if d["split"] == s}),
                         "label1": sum(d["split"] == s and d["label"] == 1 for d in rows),
                         "kinds": dict(Counter(d["kind"] for d in rows if d["split"] == s))} for s in ("train", "val")},
    }
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    stats["zip"] = shutil.make_archive(str(out), "zip", root_dir=out.parent, base_dir=out.name)
    return stats


def selftest() -> None:
    def rec(k, mp):
        return {"info": {"key": k, "menu_path": mp, "table_name": k}}
    recs = {"A": rec("A", "x > f1 > A"), "B": rec("B", "x > f1 > B"), "C": rec("C", "x > f2 > C"),
            "D": rec("D", "x > f3 > D"), "G": rec("G", "x > f4 > G")}
    code = [
        {"qid": "A-B1", "q": "q1", "table": "A", "label": 1, "kind": "pos", "group": "A-g1"},
        {"qid": "A-N1", "q": "q2", "table": "A", "label": 0, "kind": "no_item", "group": "A-g1"},
        {"qid": "A-B1", "q": "q1", "table": "C", "label": 1, "kind": "pos_alt", "group": "A-g1"},
        {"qid": "A-B1", "q": "q1 9월", "table": "A", "label": 0, "kind": "month", "group": "A-g1"},
        {"qid": "C-B1", "q": "q3", "table": "C", "label": 1, "kind": "pos", "group": "C-g1"},
        {"qid": "C-B1", "q": "q3", "table": "B", "label": 0, "kind": "neg_near", "group": "C-g1"},
        {"qid": "G-B1", "q": "q4", "table": "G", "label": 1, "kind": "pos", "group": "G-g1"},
        {"qid": "C-B2", "q": "q5", "table": "G", "label": 0, "kind": "easy", "group": "C-g2"},
        {"qid": "oos-01-1", "q": "q7", "table": "B", "label": 0, "kind": "oos", "group": "oos-01-1"},
    ]
    judged = [{"qid": "D-B1", "q": "q6", "table": "C", "label": 1, "kind": "pos_alt", "group": "D-g1"}]
    rows, t = assemble(code, judged, recs, {"G"}, DROP, 1, 0)
    kinds = Counter(d["kind"] for d in rows)
    assert kinds["month"] == 0 and t["drop_kind:pos_alt"] == 1 and t["drop_kind:month"] == 1, t
    assert t["drop_kos192"] == 2 and all("G" not in (d["table"], d["own"]) for d in rows), t
    assert kinds["pos_alt"] == 1 and kinds["oos"] == 1 and not t["drop_no_record"]  # judged pos_alt, oos kept
    val = {d["own"] for d in rows if d["split"] == "val"}
    vf = {recs[k]["info"]["menu_path"].rsplit(" > ", 1)[0] for k in val}
    assert val and all(recs[d["table"]]["info"]["menu_path"].rsplit(" > ", 1)[0] not in vf
                       for d in rows if d["split"] == "train"), rows
    print("selftest ok")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--pairs", default=str(HERE / "data" / "pairs.jsonl"))
    p.add_argument("--judged", default=str(HERE / "data" / "judged.jsonl"))
    p.add_argument("--tables", default="data/tables.jsonl")
    p.add_argument("--exclude", nargs="*", default=["eval/golden/kos192/retrieval_test_kos192.jsonl"],
                   help="KOS-192 files (dev + test); table keys only")
    p.add_argument("--drop", nargs="*", default=list(DROP), help="kinds dropped from the code-decided pairs")
    p.add_argument("--val-tables", type=int, default=80, help="question tables held out (whole menu families)")
    p.add_argument("--build", default="ans-v2")
    p.add_argument("--out-root", default="data/lab/answerability")
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    if a.selftest:
        selftest()
        return
    print(json.dumps(build(a), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
