"""Pooled labeling for the L1 retrieval golden set (T2-02): export candidate pools to CSV, import the labels back.

    python -m evals.builders.pool export --drafts evals/datasets/retrieval_drafts.jsonl --out evals/datasets/retrieval_pool.csv
    (label pool.csv: slot number 1-3 in `slot` for each correct table; add rows by hand for tables not in the pool)
    python -m evals.builders.pool import --drafts evals/datasets/retrieval_drafts.jsonl --labels evals/datasets/retrieval_pool.csv --assign-splits

A draft is a RetrievalItem line without `required` (unanswerable drafts carry answerable=false and their reason).
The pool is the union of BM25 top-10 over the catalog document and over TableRecord.search_text, so a table either
retriever finds is judged instead of silently counted wrong. Later retrievers add keys only (label_version).

Widening the pool for a later retriever (T3-22, the T2-02 freeze rule: a person judges, keys are only added):

    python -m evals.builders.pool sheet --runs evals/reports/<l1 run> [<l1 run> ...] --top 1 --out <sheet.csv>
    (a person fills `slot`: the slot number the table can stand in for, or x when it cannot; blank = not judged yet)
    python -m evals.builders.pool adjudicate --sheet <sheet.csv>
    python -m evals.run_eval --layer l1 --from-run evals/reports/<l1 run> --stage <ID>-regrade
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from evals.runners.retrieval import bm25_retrievers, load_records
from evals.schemas import GOLDEN, TABLES, GoldenError, RetrievalItem, check_items, load_items, table_keys

POOL = GOLDEN.with_name("retrieval_pool.csv")
COLUMNS = ("qid", "question", "qtype", "rank", "found_by", "key", "table_name", "period", "slot")
POOL_K = 10


def pool_rows(draft: dict, retrievers: dict) -> list[dict]:
    """One row per distinct table, best rank first; found_by lists every retriever that returned it."""
    found: dict[str, dict] = {}
    for name, retriever in retrievers.items():
        for rank, doc in enumerate(retriever.invoke(draft["question"]), 1):
            row = found.setdefault(doc.metadata["key"], {
                "qid": draft["qid"], "question": draft["question"], "qtype": draft["qtype"], "rank": rank,
                "found_by": [], "key": doc.metadata["key"], "table_name": doc.metadata["table_name"],
                "period": doc.metadata["period"], "slot": "",
            })
            row["rank"] = min(row["rank"], rank)
            row["found_by"].append(name)
    rows = sorted(found.values(), key=lambda r: r["rank"])
    return [{**r, "found_by": "+".join(r["found_by"])} for r in rows]


def apply_labels(drafts: list[dict], rows: list[dict]) -> list[RetrievalItem]:
    """Slots come from the `slot` column (1-3); rows without a slot are judged not relevant."""
    slots: dict[str, dict[int, list[str]]] = {}
    for row in rows:
        if row.get("slot", "").strip():
            keys = slots.setdefault(row["qid"], {}).setdefault(int(row["slot"]), [])
            if row["key"] not in keys:
                keys.append(row["key"])
    items, problems = [], []
    for draft in drafts:
        by_slot = slots.get(draft["qid"], {})
        try:
            items.append(RetrievalItem.model_validate({**draft, "required": [by_slot[s] for s in sorted(by_slot)]}))
        except ValueError as exc:
            problems.append(f"{draft['qid']}: {exc}")
    if problems:
        raise GoldenError("; ".join(problems))
    return items


def assign_splits(items: list[RetrievalItem], dev_share: float = 0.36) -> None:
    """Items that share a primary key form one group and land in the same split (see golden.check_items).

    Groups go to dev in a fixed hash order while their qtype is under dev_share, so every qtype appears in both.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            x = parent[x]
        return x

    for item in items:
        for key in item.primary_keys:
            parent[find(key)] = find(item.qid)
    groups: dict[str, list[RetrievalItem]] = {}
    for item in items:
        groups.setdefault(find(item.qid), []).append(item)
    total = Counter(i.qtype for i in items)
    dev: Counter = Counter()
    for group in sorted(groups.values(), key=lambda g: hashlib.sha256(g[0].qid.encode()).hexdigest()):
        qtype = group[0].qtype  # ponytail: a mixed-type group counts toward its first item's type
        split = "dev" if dev[qtype] + len(group) <= round(total[qtype] * dev_share) else "test"
        dev[qtype] += len(group) if split == "dev" else 0
        for item in group:
            item.split = split


SHEET = ("qid", "question", "qtype", "found_by", "key", "table_name", "period", "detail", "gold", "slot", "note")


def catalog(path: Path) -> dict[str, dict]:
    """key -> what a person needs to judge a table: name, period, survey, a few items and class names."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            info = r["info"]
            out[info["key"]] = {
                "table_name": info.get("table_name", ""), "period": r.get("contain_period", ""),
                "detail": f"{info.get('survey_name', '')} | 주기 {','.join(r.get('cycle') or [])} | 항목 "
                          + ", ".join((r.get("items") or [])[:6]) + " | 분류 "
                          + ", ".join(c["name"] for c in r.get("classes") or []),
            }
    return out


def sheet_rows(runs: list[tuple[str, list[dict]]], items: dict[str, RetrievalItem], pooled: dict[str, set[str]],
               top: int, tables: dict[str, dict]) -> list[dict]:
    """Unjudged (qid, key) pairs in each run's top `top` of answerable items, one row per pair over all runs.

    runs: (retriever name, saved L1 rows). A key already in the pool or already gold is skipped, so the sheet holds
    only what nobody judged. found_by lists every retriever@rank that returned it.
    """
    found: dict[tuple[str, str], list[str]] = {}
    for name, rows in runs:
        for r in rows:
            item = items[r["qid"]]
            if not item.answerable:
                continue
            for rank, key in enumerate(r["keys"][:top], 1):
                if key not in pooled.get(item.qid, set()) and key not in item.gold_keys:
                    found.setdefault((item.qid, key), []).append(f"{name}@{rank}")
    out = []
    for (qid, key), by in sorted(found.items()):
        item, t = items[qid], tables.get(key, {})
        gold = " / ".join(f"슬롯{n}: " + ", ".join(f"{k} {tables.get(k, {}).get('table_name', '')}" for k in slot)
                          for n, slot in enumerate(item.required, 1))
        out.append({"qid": qid, "question": item.question, "qtype": item.qtype, "found_by": "+".join(by), "key": key,
                    "table_name": t.get("table_name", ""), "period": t.get("period", ""), "detail": t.get("detail", ""),
                    "gold": gold, "slot": "", "note": ""})
    return out


def adjudicate(sheet: list[dict], items: list[RetrievalItem], pooled: dict[str, set[str]]) -> tuple[list[dict], list[str], list[str]]:
    """Apply a person's verdicts. Returns (rows to append to the pool, qids whose slots gained a key, warnings).

    slot = n: the key joins slot n as an alternative (added only; split, question and answerability never change)
    and the item's label_version goes up by one. slot = x: judged not relevant (pool row with an empty slot).
    Blank slot: not judged yet, skipped. A key on an unanswerable item is reported, never applied (freeze rule).
    """
    by_qid = {i.qid: i for i in items}
    added, changed, warnings, problems = [], [], [], []
    for row in sheet:
        verdict = row.get("slot", "").strip().lower()
        if not verdict or row["key"] in pooled.get(row["qid"], set()):
            continue
        item = by_qid[row["qid"]]
        slot = ""
        if verdict != "x" and not item.answerable:
            warnings.append(f"{item.qid}: 답 없음 문항인데 {row['key']}가 답이 된다고 판정됨 (라벨은 바꾸지 않음)")
            continue
        if verdict != "x":
            if not verdict.isdigit() or not 1 <= int(verdict) <= len(item.required):
                problems.append(f"{row['qid']} {row['key']}: slot {verdict!r} is not x or 1-{len(item.required) or 0}")
                continue
            slot = verdict
            if row["key"] not in item.required[int(slot) - 1]:
                item.required[int(slot) - 1].append(row["key"])
                if item.qid not in changed:
                    changed.append(item.qid)
        ranks = [int(f.rsplit("@", 1)[1]) for f in row["found_by"].split("+") if "@" in f]
        added.append({"qid": item.qid, "question": item.question, "qtype": item.qtype, "rank": min(ranks, default=""),
                      "found_by": row["found_by"], "key": row["key"], "table_name": row.get("table_name", ""),
                      "period": row.get("period", ""), "slot": slot})
        pooled.setdefault(item.qid, set()).add(row["key"])
    if problems:
        raise GoldenError("; ".join(problems))
    for qid in changed:
        by_qid[qid].label_version += 1
    return added, changed, warnings


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pooled labeling for the search golden set.")
    parser.add_argument("mode", choices=("export", "import", "sheet", "adjudicate"))
    parser.add_argument("--drafts", type=Path, help="export, import: draft items")
    parser.add_argument("--labels", type=Path, help="labelled pool CSV (import)")
    parser.add_argument("--out", type=Path, help="pool CSV (export), golden jsonl (import, default evals/datasets/retrieval_golden.jsonl) or sheet CSV (sheet)")
    parser.add_argument("--tables", type=Path, default=TABLES)
    parser.add_argument("--assign-splits", action="store_true", help="import: set dev/test by shared gold keys")
    parser.add_argument("--runs", type=Path, nargs="+", help="sheet: saved L1 run folders")
    parser.add_argument("--top", type=int, default=10, help="sheet: ranks per run to collect")
    parser.add_argument("--sheet", type=Path, help="adjudicate: the judged sheet")
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="sheet, adjudicate: retrieval golden")
    parser.add_argument("--pool", type=Path, default=POOL, help="sheet, adjudicate: the judged pool")
    args = parser.parse_args(argv)

    if args.mode in ("sheet", "adjudicate"):
        items = load_items(args.golden)
        pool_rows_ = read_csv(args.pool)
        pooled: dict[str, set[str]] = {}
        for row in pool_rows_:
            pooled.setdefault(row["qid"], set()).add(row["key"])
        if args.mode == "sheet":
            runs = [(json.loads((d / "manifest.json").read_text(encoding="utf-8"))["retriever"],
                     [json.loads(x) for x in (d / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()])
                    for d in args.runs]
            rows = sheet_rows(runs, {i.qid: i for i in items}, pooled, args.top, catalog(args.tables))
            out = args.out or args.golden.with_name("retrieval_pool_sheet.csv")
            with out.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=SHEET)
                writer.writeheader()
                writer.writerows(rows)
            print(f"{len(rows)} unjudged pairs ({len({r['qid'] for r in rows})} items) -> {out}")
            return 0
        try:
            added, changed, warnings = adjudicate(read_csv(args.sheet), items, pooled)
            problems = check_items(items, table_keys(args.tables))
            if problems:
                raise GoldenError("; ".join(problems))
        except GoldenError as exc:
            print(exc, file=sys.stderr)
            return 1
        with args.pool.open("a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=list(pool_rows_[0])).writerows(
                [{**{c: "" for c in pool_rows_[0]}, **{k: v for k, v in r.items() if k in pool_rows_[0]}} for r in added])
        args.golden.write_text("".join(i.model_dump_json(exclude_defaults=True) + "\n" for i in items), encoding="utf-8")
        print(f"pool +{len(added)} rows ({sum(1 for r in added if r['slot'])} alternatives) · "
              f"label_version up: {', '.join(changed) or 'none'}")
        for w in warnings:
            print("warning:", w, file=sys.stderr)
        return 0

    drafts = [json.loads(line) for line in args.drafts.read_text(encoding="utf-8").splitlines() if line.strip()]

    if args.mode == "export":
        retrievers = bm25_retrievers(load_records(args.tables), POOL_K)
        out = args.out or args.drafts.with_name("retrieval_pool.csv")
        with out.open("w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: Excel opens Korean correctly
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()
            for draft in drafts:
                if draft.get("answerable", True):
                    writer.writerows(pool_rows(draft, retrievers))
                else:  # still judged: a pool hit here means the "no answer" label may be wrong
                    writer.writerows(pool_rows(draft, retrievers)[:3])
        print(f"{len(drafts)} drafts -> {out}")
        return 0

    with args.labels.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    try:
        items = apply_labels(drafts, rows)
        if args.assign_splits:
            assign_splits(items)
        problems = check_items(items, table_keys(args.tables))
        if problems:
            raise GoldenError("; ".join(problems))
    except GoldenError as exc:
        print(exc, file=sys.stderr)
        return 1
    out = args.out or GOLDEN
    out.write_text("".join(item.model_dump_json(exclude_defaults=True) + "\n" for item in items), encoding="utf-8")
    print(f"{len(items)} items -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
