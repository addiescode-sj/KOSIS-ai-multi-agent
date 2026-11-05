"""Pooled labeling for the L1 retrieval golden set (T2-02): export candidate pools to CSV, import the labels back.

    python -m evals.builders.pool export --drafts evals/datasets/retrieval_drafts.jsonl --out evals/datasets/retrieval_pool.csv
    (label pool.csv: slot number 1-3 in `slot` for each correct table; add rows by hand for tables not in the pool)
    python -m evals.builders.pool import --drafts evals/datasets/retrieval_drafts.jsonl --labels evals/datasets/retrieval_pool.csv --assign-splits

A draft is a RetrievalItem line without `required` (unanswerable drafts carry answerable=false and their reason).
The pool is the union of BM25 top-10 over the catalog document and over TableRecord.search_text, so a table either
retriever finds is judged instead of silently counted wrong. Later retrievers add keys only (label_version).
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
from evals.schemas import GOLDEN, TABLES, GoldenError, RetrievalItem, check_items, table_keys

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pooled labeling for the search golden set.")
    parser.add_argument("mode", choices=("export", "import"))
    parser.add_argument("--drafts", type=Path, required=True)
    parser.add_argument("--labels", type=Path, help="labelled pool CSV (import)")
    parser.add_argument("--out", type=Path, help="pool CSV (export) or golden jsonl (import, default evals/datasets/retrieval_golden.jsonl)")
    parser.add_argument("--tables", type=Path, default=TABLES)
    parser.add_argument("--assign-splits", action="store_true", help="import: set dev/test by shared gold keys")
    args = parser.parse_args(argv)
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
