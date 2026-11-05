"""Convert the Phase 1 golden set into the L3 calculation golden set (T2-03). Data only: no Phase 1 eval code.

    python -m evals.builders.phase1                                            # eval/golden of this repo
    python -m evals.builders.phase1 --src ../KOSIS-ai-nlp-summary/eval/golden  # or the Phase 1 repo's folder

The Phase 1 files are gitignored (not in vendor/phase1), so they are copied into this repo's eval/golden or read
from the sibling repo. A folder gives its stat_golden*.jsonl files, or golden.jsonl (the same rows merged) when it
has none; a file is read as is. Every source file's sha256 goes to calculation_golden.meta.json. A table whose
Parquet hash differs from the golden `table_sha256` is refused: its facts were checked against other data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from evals.schemas import DATASETS, CalcItem, GoldenError

ROOT = Path(__file__).resolve().parents[2]
OUT = DATASETS / "calculation_golden.jsonl"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def convert(row: dict) -> CalcItem:
    """Phase 1 row -> CalcItem. `table` is data/values/<key>.parquet; runner-only fields (scope, ...) are dropped."""
    fields = {name: row[name] for name in CalcItem.model_fields if name in row}
    return CalcItem(**fields, qid=row["id"], table_key=Path(row["table"]).stem)


def sources(src: Path) -> list[Path]:
    """The split files when present, else the merged golden.jsonl: never both, which would repeat every qid."""
    if src.is_file():
        return [src]
    return sorted(src.glob("stat_golden*.jsonl")) or [f for f in [src / "golden.jsonl"] if f.exists()]


def build(files: list[Path], values: Path) -> list[CalcItem]:
    items, problems, hashes = [], [], {}
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            item = convert(json.loads(line))
            parquet = values / f"{item.table_key}.parquet"
            if not parquet.exists():
                problems.append(f"{f.name}:{n}: {parquet} not found")
                continue
            hashes.setdefault(parquet, sha256(parquet))
            if hashes[parquet] != item.table_sha256:
                problems.append(f"{item.qid}: {parquet.name} sha256 {hashes[parquet][:12]} != golden {item.table_sha256[:12]}")
                continue
            items.append(item)
    ids = [i.qid for i in items]
    problems += [f"duplicate qid {q}" for q in sorted({q for q in ids if ids.count(q) > 1})]
    if problems:
        raise GoldenError("; ".join(problems))
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 1 golden set -> evals/datasets/calculation_golden.jsonl")
    parser.add_argument("--src", type=Path, default=ROOT / "eval" / "golden",
                        help="Phase 1 golden folder (stat_golden*.jsonl or golden.jsonl) or one file")
    parser.add_argument("--values", type=Path, default=ROOT / "data" / "values")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    files = sources(args.src)
    if not files:
        print(f"{args.src}: no stat_golden*.jsonl or golden.jsonl", file=sys.stderr)
        return 1
    try:
        items = build(files, args.values)
    except GoldenError as exc:
        print(exc, file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(i.model_dump_json(exclude_defaults=True) + "\n" for i in items), encoding="utf-8")
    meta = {"source": {str(f.resolve()): sha256(f) for f in files}, "items": len(items), "sha256": sha256(args.out)}
    args.out.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(items)} items from {len(files)} files -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
