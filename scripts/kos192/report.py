"""KOS-192 P0 report: aggregates of one L1 run over the KOS-192 test set by split, style (S0..S6), qtype and
rewrite_status (when the rows carry it). No question text and no table key is printed or written.

    set PYTHONPATH=.;src;vendor\\phase2\\src;vendor\\phase1;vendor\\phase1\\src
    python scripts\\kos192\\report.py evals\\reports\\<run folder> [--split all|dev|test] [--out <file>.json]
    python scripts\\kos192\\report.py evals\\reports\\<run folder> --split dev --items

Input: an evals.run_eval L1 run folder (results.jsonl: one row per question of the split the run showed, with keys =
the ranked top k, top_score, stage2_* fields, latency_ms; manifest.json), the golden file (--golden, default the
KOS-192 set) and its meta file (--meta: style per qid). Graded with the evaluator's own functions on the current
labels: score_item (Hit@1, Hit@5, reciprocal rank over the OR-groups of `required`) on answerable items, and the
no-answer P/R/F1 (prf) of the graph's rule: no candidate, or stage 2 picked nothing without an error (llm_no_answer).
Latency p50/p95 as evals.ops. --items (dev only, plan §6.1: test content is never shown) adds one line per dev
question: qid, style, rank of the gold table, top key.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "vendor" / "phase1" / "src", ROOT / "vendor" / "phase1", ROOT / "vendor" / "phase2" / "src",
           ROOT / "src", ROOT, Path(__file__).resolve().parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("KOSIS_WORKSPACE", str(ROOT))

from kos192_lib import (GOLDEN, META, fmt, in_split, load_golden, load_meta, load_run, route_no_answer,  # noqa: E402
                        utf8_stdout, write_json)

DIMENSIONS = ("all", "split", "style", "qtype", "rewrite_status")
BASE_STYLE = "S0"  # official terms: C4 compares every other style's Hit@1 with it


def check_items_allowed(split: str, items: bool) -> None:
    """--items prints qids, gold ranks and top keys: allowed only with --split dev. SystemExit otherwise."""
    if items and split != "dev":
        raise SystemExit(f"--items is allowed only with --split dev (not {split!r}): test items are seen as aggregates "
                         "only (KOS-192 plan §6.1)")


def grade_rows(rows: list[dict], golden: dict, meta: dict[str, dict], split: str) -> tuple[list[dict], int]:
    """One graded record per run row of the split: (records, rows whose qid is not in the golden file).

    A record holds qid, split, qtype, style ("-" without meta), rewrite_status ("-" when the row has none),
    answerable (current golden), no_answer_pred (route_no_answer), latency_ms, error and, for answerable items,
    rank, hit@1, hit@5, rr (evals.evaluators.retrieval.score_item over the row's keys). Pure: no file is read.
    """
    from evals.evaluators.retrieval import score_item

    graded, unknown = [], 0
    for r in rows:
        item = golden.get(r["qid"])
        if item is None:
            unknown += 1
            continue
        if not in_split(item.split, split):
            continue
        g = {"qid": item.qid, "split": item.split, "qtype": item.qtype,
             "style": (meta.get(item.qid) or {}).get("style") or "-",
             "rewrite_status": r.get("rewrite_status") or "-", "answerable": item.answerable,
             "no_answer_pred": route_no_answer(r), "latency_ms": r.get("latency_ms"), "error": bool(r.get("error"))}
        if item.answerable:
            s = score_item(item, r.get("keys") or [])
            g.update({"rank": s["rank"], "hit@1": s["hit@1"], "hit@5": s["hit@5"], "rr": s["rr"]})
        graded.append(g)
    return graded, unknown


def group_metrics(members: list[dict]) -> dict:
    """n, answerable n, Hit@1 / Hit@5 / MRR over the answerable members (None when there are none), the no-answer
    P/R/F1 of the graph rule over all members (evals.evaluators.retrieval.prf; precision, recall and f1 None when the
    group has no no-answer item, tp / fp / fn kept), errors, latency p50 / p95 (ms)."""
    from evals.evaluators.retrieval import prf
    from evals.ops import percentile

    ans = [m for m in members if m["answerable"]]
    avg = lambda k: sum(m[k] for m in ans) / len(ans) if ans else None  # noqa: E731
    latency = [m["latency_ms"] for m in members if m["latency_ms"] is not None]
    no_answer = prf([(m["no_answer_pred"], not m["answerable"]) for m in members])
    if len(members) == len(ans):  # nothing to find: P/R/F1 would read 0, not "not applicable"
        no_answer.update(precision=None, recall=None, f1=None)
    return {"n": len(members), "n_answerable": len(ans), "n_no_answer": len(members) - len(ans),
            "hit@1": avg("hit@1"), "hit@5": avg("hit@5"), "mrr": avg("rr"),
            "no_answer": no_answer,
            "errors": sum(1 for m in members if m["error"]),
            "latency_p50_ms": percentile(latency, 50) if latency else None,
            "latency_p95_ms": percentile(latency, 95) if latency else None}


def aggregate(graded: list[dict], dimensions: tuple[str, ...] = DIMENSIONS) -> dict[str, dict[str, dict]]:
    """dimension -> group -> group_metrics. "all" has the one group "all"; rewrite_status is left out when no record
    carries one (every value "-"). Groups in sorted order."""
    out: dict[str, dict[str, dict]] = {}
    for dim in dimensions:
        if dim == "rewrite_status" and all(g["rewrite_status"] == "-" for g in graded):
            continue
        groups: dict[str, list[dict]] = defaultdict(list)
        for g in graded:
            groups["all" if dim == "all" else str(g[dim])].append(g)
        out[dim] = {name: group_metrics(groups[name]) for name in sorted(groups)}
    return out


def render(summary: dict) -> str:
    """The markdown report of a summary (aggregates only)."""
    head = ("| {dim} | n | 답 있음 | Hit@1 | Hit@5 | MRR | 답 없음 | 답 없음 P | R | F1 | 오류 | p50 ms | p95 ms |{extra}")
    lines = [f"# KOS-192 L1 집계 — run `{summary['run_id']}`", "",
             f"- retriever `{summary['retriever']}`, split `{summary['split']}`, 행 {summary['n_graded']}"
             f" (골든에 없는 qid {summary['not_in_golden']})",
             "- Hit·MRR은 답 있는 문항 기준(정답 OR 그룹, evaluator score_item). 답 없음 = 그래프 규칙"
             "(후보 0개 또는 2차가 오류 없이 0개 고름)의 P/R/F1."]
    for dim, groups in summary["groups"].items():
        base = groups.get(BASE_STYLE, {}).get("hit@1") if dim == "style" else None
        extra = " ΔHit@1 vs S0 |" if base is not None else ""
        lines += ["", f"## {dim}", "", head.format(dim=dim, extra=extra),
                  "|" + " --- |" * (13 + (1 if extra else 0))]
        for name, m in groups.items():
            na = m["no_answer"]
            row = (f"| {name} | {m['n']} | {m['n_answerable']} | {fmt(m['hit@1'])} | {fmt(m['hit@5'])} | {fmt(m['mrr'])} | "
                   f"{m['n_no_answer']} | {fmt(na['precision'])} | {fmt(na['recall'])} | {fmt(na['f1'])} | {m['errors']} | "
                   f"{fmt(m['latency_p50_ms'], 0)} | {fmt(m['latency_p95_ms'], 0)} |")
            if extra:
                row += f" {fmt(m['hit@1'] - base, 3) if m['hit@1'] is not None else '-'} |"
            lines.append(row)
    return "\n".join(lines) + "\n"


def item_lines(rows: list[dict], graded: list[dict]) -> str:
    """--items (dev only): qid, style, gold rank ("-" not in the top k, "n/a" for a no-answer item), top key."""
    top = {r["qid"]: (r.get("keys") or [None])[0] for r in rows}
    lines = ["", "## 문항 (dev)", "", "| qid | style | gold rank | top key |", "| --- | --- | --- | --- |"]
    for g in sorted(graded, key=lambda g: g["qid"]):
        if g["split"] != "dev":  # check_items_allowed already refused other splits; never print a test line
            continue
        rank = (g["rank"] or "-") if g["answerable"] else "n/a"
        lines.append(f"| {g['qid']} | {g['style']} | {rank} | {top.get(g['qid']) or '-'} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    utf8_stdout()
    parser = argparse.ArgumentParser(description="KOS-192 P0: aggregates of an L1 run by split / style / qtype.")
    parser.add_argument("run", type=Path, help="an evals.run_eval L1 run folder (results.jsonl, manifest.json)")
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="retrieval golden file (default: the KOS-192 set)")
    parser.add_argument("--meta", type=Path, default=META, help="meta file: style per qid")
    parser.add_argument("--split", choices=("all", "dev", "test"), default="all")
    parser.add_argument("--out", type=Path, help="also write the aggregates as JSON")
    parser.add_argument("--items", action="store_true", help="dev only: one line per question (qid, style, gold rank, top key)")
    args = parser.parse_args(argv)
    check_items_allowed(args.split, args.items)

    rows, manifest = load_run(args.run)
    meta = load_meta(args.meta)
    if not meta:
        print(f"note: no meta file at {args.meta}: style is '-' for every item", file=sys.stderr)
    graded, unknown = grade_rows(rows, load_golden(args.golden), meta, args.split)
    summary = {"run_id": manifest.get("run_id", args.run.name), "retriever": manifest.get("retriever"),
               "split": args.split, "n_graded": len(graded), "not_in_golden": unknown, "groups": aggregate(graded)}
    print(render(summary), end="")
    if args.items:
        print(item_lines([r for r in rows if r["qid"] in {g["qid"] for g in graded}], graded), end="")
    if args.out:
        write_json(args.out, summary)  # no Infinity / NaN: null
        print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
