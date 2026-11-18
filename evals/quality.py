"""Quality gate (plans/T3-19.md): every ragas layer run appends its gate metrics to quality_history.jsonl (aggregates
only, committed); tests/test_quality_gate.py compares the newest record with the committed baseline in CI.

    python -m evals.quality                          # newest record vs baseline, exit 1 on a drop (same as the CI test)
    python -m evals.quality --promote <run folder>   # make that ragas run the baseline (a reviewed change in the PR diff)

Only records whose comparison key (golden, split, tables.jsonl, e2e target, judge, embeddings, ragas version, context
format, k) equals the baseline's are compared, so an experiment on another architecture or model never trips the gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.schemas import ROOT

HISTORY = ROOT / "evals" / "reports" / "quality_history.jsonl"
BASELINE = ROOT / "evals" / "quality_baseline.json"
JUDGE_TOLERANCE = 0.03  # floor for judge metrics; the baseline may widen it per metric from measured judge noise
COUNT_TOLERANCE = 2  # questions: a rate may drop by 2/n, `answered` by 2


def record(run_id: str, stage: str, summary: dict, path: Path = HISTORY) -> dict:
    line = {"run_id": run_id, "stage": stage, "source_run": summary["source_run"], "n": summary["n"],
            "key": summary["key"], "metrics": summary["gate"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


def read(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def latest(history: list[dict], key: dict) -> dict | None:
    """Newest record with this key; run ids start with the time, so the largest is the newest whatever the line order."""
    same = [h for h in history if h["key"] == key]
    return max(same, key=lambda h: h["run_id"]) if same else None


def tolerance(metric: str, n: int, baseline: dict) -> float:
    if metric in baseline.get("tolerance", {}):
        return baseline["tolerance"][metric]
    if metric == "answered":
        return COUNT_TOLERANCE
    if metric.startswith("e2e_") or metric.startswith("id_"):
        return COUNT_TOLERANCE / n if n else 0.0
    return JUDGE_TOLERANCE


def compare(baseline: dict, record_: dict) -> list[dict]:
    """Per gated metric: baseline, newest, delta, tolerance, ok. A metric the newest record lacks (None) fails."""
    out = []
    for metric, base in baseline["metrics"].items():
        if base is None:
            continue
        new = record_["metrics"].get(metric)
        tol = tolerance(metric, record_["n"], baseline)
        out.append({"metric": metric, "baseline": base, "latest": new, "delta": None if new is None else new - base,
                    "tolerance": tol, "ok": new is not None and new >= base - tol - 1e-9})
    return out


def promote(run: Path, tolerances: dict[str, float] | None = None, path: Path = BASELINE) -> dict:
    m = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    if m["layer"] != "ragas":
        raise SystemExit(f"{run} is a {m['layer']} run, not ragas")
    s = m["summary"]
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    base = {"run_id": m["run_id"], "source_run": s["source_run"], "n": s["n"], "key": s["key"], "metrics": s["gate"],
            "tolerance": {**old.get("tolerance", {}), **(tolerances or {})}}
    path.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return base


def check(history: Path = HISTORY, baseline: Path = BASELINE) -> tuple[dict | None, list[dict]]:
    """(newest comparable record, comparison rows); (None, []) when there is no baseline or no comparable record."""
    if not baseline.exists():
        return None, []
    base = json.loads(baseline.read_text(encoding="utf-8"))
    rec = latest(read(history), base["key"])
    return rec, compare(base, rec) if rec else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Quality gate: newest ragas record vs the committed baseline.")
    parser.add_argument("--promote", type=Path, help="ragas run folder to make the baseline")
    parser.add_argument("--tolerance", nargs="*", default=[], help="metric=value, kept in the baseline (e.g. answer_relevancy=0.05)")
    args = parser.parse_args(argv)
    if args.promote:
        tol = {k: float(v) for k, v in (t.split("=", 1) for t in args.tolerance)}
        print(json.dumps(promote(args.promote, tol), ensure_ascii=False, indent=2))
        return 0
    rec, rows = check()
    if rec is None:
        print("quality gate: no baseline or no record with the baseline's key (nothing to compare)")
        return 0
    print(f"quality gate: {rec['run_id']} vs baseline")
    for r in rows:
        latest_ = "—" if r["latest"] is None else f"{r['latest']:.3f}"
        print(f"  {'ok  ' if r['ok'] else 'DROP'} {r['metric']:<22} {r['baseline']:.3f} -> {latest_} (tolerance {r['tolerance']:.3f})")
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
