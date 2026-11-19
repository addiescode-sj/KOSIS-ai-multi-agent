"""Build evals/datasets/calculation_alternates.jsonl (plans/T3-41.md): alternate gold tables of the calculation golden
set, found by cell values over every table in data/values (evals.evaluators.alternates). No model, about 20 s.

    python -m evals.builders.alternates [--calc-golden evals/datasets/calculation_golden.jsonl ...]

A rerun keeps a person's decision: a label set to accepted / rejected stays so. needs_review labels count only once a
person sets them to accepted. Lines with `self_check` record whether the rule finds the golden table itself.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from evals.evaluators import alternates
from evals.schemas import DATASETS
from evals.tasks.common import calc_items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--calc-golden", type=Path, nargs="+", default=[DATASETS / "calculation_golden.jsonl"])
    parser.add_argument("--out", type=Path, default=alternates.ALTERNATES)
    args = parser.parse_args(argv)
    kept = {}
    if args.out.exists():
        for line in args.out.read_text(encoding="utf-8").splitlines():
            if line.strip() and (d := json.loads(line)).get("status") in ("accepted", "rejected"):
                kept[(d["qid"], d["table_key"])] = d["status"]
    lines = alternates.scan(calc_items(args.calc_golden))
    for d in lines:
        if "status" in d:
            d["status"] = kept.get((d["qid"], d["table_key"]), d["status"])
    args.out.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in lines), encoding="utf-8")
    labels = [d for d in lines if "status" in d]
    checks = [d for d in lines if "self_check" in d]
    print(f"targets {len(checks)} · self-check {sum(d['self_check'] for d in checks)}/{len(checks)} · "
          f"questions with an alternate {len({d['qid'] for d in labels})} · labels {dict(Counter(d['status'] for d in labels))}")
    print(f"-> {args.out}")
    return 0 if all(d["self_check"] for d in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
