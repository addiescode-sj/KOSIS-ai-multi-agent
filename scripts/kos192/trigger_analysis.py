"""KOS-192 P1 trigger analysis: does a low stage 1 signal find the questions the search gets wrong? No model call.
Dev split only.

    set PYTHONPATH=.;src;vendor\\phase2\\src;vendor\\phase1;vendor\\phase1\\src
    python scripts\\kos192\\trigger_analysis.py evals\\reports\\<P0 run folder> [--out <file>.json]

From one L1 run folder made after KOS-192 step 1 (rows with stage1_top_score: the highest stage 1 cross-encoder score
of the pool; stage1_margin: that score minus the second highest; top_score: the dense cosine of the stage 1 top table)
and the golden file. Answerable items are "hit1" (a gold table of the OR-group at rank 1 of the run's final ranking,
i.e. after stage 2 when the run had one: the evaluator's first_rank over the row's keys) or "miss"; unanswerable
items are "no_answer". For each signal (stage1_top_score, stage1_margin, top_score; nothing else):
- AUC of "a low score means miss" over miss vs hit1, and over miss + no_answer vs hit1 (rank-based, ties counted 1/2);
- score quantiles per group;
- for trigger rates 20 / 35 / 50 % of the split (plan §4.2): tau_rw (rule score < tau_rw), the share of misses it
  catches, the share of triggered items that were hit1 (wasted rewrites) and of no-answer items it triggers.
Rows without the score (stage 1 off, no candidate) are left out and counted. Aggregates only: no question, no key.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "vendor" / "phase1" / "src", ROOT / "vendor" / "phase1", ROOT / "vendor" / "phase2" / "src",
           ROOT / "src", ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("KOSIS_WORKSPACE", str(ROOT))

from kos192_lib import (GOLDEN, TRIGGER_RATES, auc_low, fmt, gold_rank, in_split, load_golden, load_run,  # noqa: E402
                        quantile, share, trigger_threshold, triggered, utf8_stdout, write_json)

SCORES = {"stage1_top_score": "1차 리랭커 1위 점수 (stage1_top_score)",
          "stage1_margin": "1차 리랭커 1·2위 점수 차 (stage1_margin)",
          "top_score": "dense 코사인 (1차 1위 표, top_score)"}
GROUPS = ("hit1", "miss", "no_answer")
QUANTILES = (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)
AUC_FLOOR = 0.65  # plan §6.2 P1: below it the score is not a usable trigger


def label_rows(rows: list[dict], golden: dict, split: str) -> list[dict]:
    """One record per run row of the split whose qid is in the golden file: qid, group (hit1 | miss | no_answer) and
    the two scores (None when the row has none). Pure."""
    out = []
    for r in rows:
        item = golden.get(r["qid"])
        if item is None or not in_split(item.split, split):
            continue
        if not item.answerable:
            group = "no_answer"
        else:
            group = "hit1" if gold_rank(item, r.get("keys") or []) == 1 else "miss"
        out.append({"qid": item.qid, "group": group, **{f: r.get(f) for f in SCORES}})
    return out


def analyze(labeled: list[dict], field: str, rates: tuple[float, ...] = TRIGGER_RATES) -> dict:
    """AUCs, quantiles per group and the trigger table of one score field over the labelled records. Pure.

    Out: {"field", "n" (records with the score), "missing" (without), "counts" per group, "auc_miss_vs_hit1",
        "auc_miss_or_no_answer_vs_hit1", "quantiles": group -> {q: value}, "triggers": [{"rate", "tau", "triggered",
        "trigger_rate", "misses", "miss_caught", "miss_caught_share", "hit1_triggered", "wasted_share",
        "no_answer_triggered", "no_answer_share"}]} (shares None when their denominator is 0).
    """
    have = [r for r in labeled if r.get(field) is not None]
    by = {g: [r[field] for r in have if r["group"] == g] for g in GROUPS}
    scores = [r[field] for r in have]
    triggers = []
    for rate in rates:
        tau = trigger_threshold(scores, rate)
        hit = [r for r in have if triggered(r[field], tau)]
        count = {g: sum(1 for r in hit if r["group"] == g) for g in GROUPS}
        triggers.append({
            "rate": rate, "tau": tau, "triggered": len(hit), "trigger_rate": len(hit) / len(have) if have else None,
            "misses": len(by["miss"]), "miss_caught": count["miss"],
            "miss_caught_share": count["miss"] / len(by["miss"]) if by["miss"] else None,
            "hit1_triggered": count["hit1"], "wasted_share": count["hit1"] / len(hit) if hit else None,
            "no_answer_triggered": count["no_answer"],
            "no_answer_share": count["no_answer"] / len(by["no_answer"]) if by["no_answer"] else None})
    return {"field": field, "n": len(have), "missing": len(labeled) - len(have),
            "counts": {g: len(v) for g, v in by.items()},
            "auc_miss_vs_hit1": auc_low(by["miss"], by["hit1"]),
            "auc_miss_or_no_answer_vs_hit1": auc_low(by["miss"] + by["no_answer"], by["hit1"]),
            "quantiles": {g: {str(q): quantile(v, q) for q in QUANTILES} for g, v in by.items()},
            "triggers": triggers}


def render(result: dict, run_id: str, split: str) -> str:
    """The markdown report (aggregates only)."""
    lines = [f"# KOS-192 P1 트리거 분석 — run `{run_id}`, split `{split}`", "",
             "- 답 있는 문항: hit1 = 정답 표(OR 그룹)가 최종 순위 1위, miss = 그 밖. no_answer = 답 없는 문항.",
             "- AUC: 점수가 낮을수록 miss(또는 miss+답 없음)라는 판정의 AUC (순위 기반, 동점 1/2). "
             f"{AUC_FLOOR} 미만이면 트리거로 부적합 (계획 §6.2 P1).",
             "- 트리거 규칙: 점수 < τ_rw. τ_rw는 이 split에서 해당 비율이 트리거되는 값 (동점이면 비율보다 적게)."]
    for field, a in result.items():
        c = a["counts"]
        lines += ["", f"## {SCORES[field]}", "",
                  f"- 점수 있는 문항 {a['n']} (hit1 {c['hit1']} · miss {c['miss']} · no_answer {c['no_answer']}), "
                  f"점수 없음 {a['missing']}",
                  f"- AUC miss vs hit1: **{fmt(a['auc_miss_vs_hit1'])}** · miss+답 없음 vs hit1: "
                  f"{fmt(a['auc_miss_or_no_answer_vs_hit1'])}"
                  + (f" · **{AUC_FLOOR} 미만**" if a["auc_miss_vs_hit1"] is not None and a["auc_miss_vs_hit1"] < AUC_FLOOR else ""),
                  "", "| 그룹 | n | " + " | ".join(f"q{int(q * 100)}" for q in QUANTILES) + " |",
                  "|" + " --- |" * (len(QUANTILES) + 2)]
        for g in GROUPS:
            lines.append(f"| {g} | {c[g]} | " + " | ".join(fmt(a["quantiles"][g][str(q)], 4) for q in QUANTILES) + " |")
        lines += ["", "| 목표 비율 | τ_rw | 트리거 | miss 잡음 | 트리거 중 hit1 (낭비) | 답 없음 트리거 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for t in a["triggers"]:
            lines.append(f"| {t['rate']:.0%} | {fmt(t['tau'], 4)} | {share(t['triggered'], a['n'])} | "
                         f"{share(t['miss_caught'], t['misses'])} | {share(t['hit1_triggered'], t['triggered'])} | "
                         f"{share(t['no_answer_triggered'], c['no_answer'])} |")
    return "\n".join(lines) + "\n"


def check_split(split: str) -> None:
    """tau_rw is chosen on dev, and test items are seen as aggregates of the final run only: SystemExit for any other
    split."""
    if split != "dev":
        raise SystemExit(f"trigger_analysis runs on the dev split only, not {split!r} (KOS-192 plan §6.1)")


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    parser = argparse.ArgumentParser(description="KOS-192 P1: stage 1 signals as the rewrite trigger (dev only).")
    parser.add_argument("run", type=Path, help="an evals.run_eval L1 run folder (the P0 baseline, --split all or dev)")
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="retrieval golden file (default: the KOS-192 set)")
    parser.add_argument("--split", default="dev", help="dev only (any other value is refused)")
    parser.add_argument("--out", type=Path, help="also write the result as JSON")
    args = parser.parse_args(argv)
    check_split(args.split)

    rows, manifest = load_run(args.run)
    labeled = label_rows(rows, load_golden(args.golden), args.split)
    if not labeled:
        raise SystemExit(f"no row of split {args.split!r} in {args.run}")
    result = {field: analyze(labeled, field) for field in SCORES}
    print(render(result, manifest.get("run_id", args.run.name), args.split), end="")
    if args.out:
        write_json(args.out, {"run_id": manifest.get("run_id"), "split": args.split, "scores": result})  # inf -> null
        print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
