"""L1: retrieval ranking (Hit@k, MRR, slot recall) and no-answer threshold, or a regrade of a past run."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from evals import trace
from evals.evaluators import gate, retrieval
from evals.schemas import load_items
from evals.tasks.common import add


def judged_keys(path: Path) -> dict[str, set[str]]:
    """Keys a person looked at per question: every row of the labelled pool, gold or not."""
    judged: dict[str, set[str]] = defaultdict(set)
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                judged[row["qid"]].add(row["key"])
    return judged


def recut_jev(row: dict, threshold: float, k: int) -> None:
    """Re-pick a jev stage 2 row at `threshold` from its saved probabilities (stage2_scores, the top stage2_k in
    stage 1 order): picked tables first (highest first, at most MAX_PICK), then the other scored tables in stage 1
    order, then the rest as saved; cut to k. A row without scores (no stage 2, or a call error) is left as it was
    (T3-23 Jev)."""
    from kosis_rag.rerank.systemone import cut

    scores = row.get("stage2_scores")
    if not scores or row.get("stage2_error"):
        return
    picked = cut(scores, threshold)
    order = picked + [key for key in scores if key not in picked] + [key for key in row["keys"] if key not in scores]
    row["keys"], row["stage2_picked_n"] = order[:k], len(picked)


def checkpoint(rows: list[dict], args) -> None:
    """Save the live search rows before grading, so a grading crash never costs the search again.

    In: the runner's rows (every split) and the run arguments.
    Out: none.
    Side effects: writes <evals/reports>/_partial/<stage>/results.jsonl and manifest.json (summary.tau null, which
    `--from-run` reads as "pick tau on dev") and prints one line; any failure prints a one-line warning instead.
    """
    try:
        from evals.run_eval import REPORTS  # the folder run_eval writes run folders to (gitignored)

        out = REPORTS / "_partial" / args.stage
        out.mkdir(parents=True, exist_ok=True)
        with (out / "results.jsonl").open("w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows)
        manifest = {"retriever": args.retriever, "top_k": args.k, "golden": str(args.golden), "stage": args.stage,
                    "summary": {"tau": None}, "partial": True}
        (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"checkpoint: {len(rows)} search rows saved to {out} (regrade with --from-run)")
    except Exception as e:  # noqa: BLE001 - the checkpoint must never break the run
        print(f"warning: search checkpoint not written ({type(e).__name__}: {e})")


def run(args) -> tuple[list[dict], dict, str]:
    from evals.runners import retrieval as runner  # LangChain loads only for L1

    items = load_items(args.golden)
    by_qid = {i.qid: i for i in items}
    if args.from_run:  # T3-22: regrade the saved rankings under the current labels; search is not run again
        past = json.loads((args.from_run / "manifest.json").read_text(encoding="utf-8"))
        args.retriever, args.k = past["retriever"], past["top_k"]
        rows = [json.loads(line) for line in (args.from_run / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        for r in rows:  # answerable / reason follow the current golden (labels may have changed)
            r["answerable"] = by_qid[r["qid"]].answerable
        if getattr(args, "jev_recut", None) is not None:  # T3-23 Jev: re-pick stage 2 at another threshold, no model call
            for r in rows:
                recut_jev(r, args.jev_recut, args.k)
        # the saved run keeps only its split, so tau cannot be picked on dev again: reuse the run's tau.
        # A search checkpoint (_partial, tau null) keeps every split: pick tau on its dev rows as a live run does.
        tau = args.tau if args.tau is not None else past["summary"]["tau"]
        if tau is None:
            dev = [(r["top_score"], not r["answerable"]) for r in rows if r["split"] == "dev"]
            if not dev:
                raise SystemExit(f"{args.from_run}: no saved tau and no dev rows to pick one on; pass --tau")
            tau = retrieval.best_tau(dev)
    else:
        from evals.runners.agent import tracing

        args.telemetry = tracing("l1")  # T3-32: live search spans; run_eval links the misses after grading
        rows = runner.run(runner.build(args.retriever, items, args.k), items, args.k, args.telemetry)
        checkpoint(rows, args)
        tau = args.tau if args.tau is not None else retrieval.best_tau(
            [(r["top_score"], not r["answerable"]) for r in rows if r["split"] == "dev"])
    judged = judged_keys(args.pool)
    shown = [r for r in rows if args.split == "all" or r["split"] == args.split]
    for r in shown:
        r["no_answer_pred"] = retrieval.predict_no_answer(r["top_score"], tau)
        if r["answerable"]:
            r.update(retrieval.score_item(by_qid[r["qid"]], r["keys"], judged.get(r["qid"])))
    scored = [r for r in shown if r["answerable"]]
    agg = retrieval.aggregate(scored)
    no_answer = retrieval.prf([(r["no_answer_pred"], not r["answerable"]) for r in shown])
    reasons = {i.qid: i.no_answer_reason for i in items if not i.answerable}
    summary = {"tau": tau, "metrics": agg, "no_answer": no_answer, "errors": sum(1 for r in shown if r["error"]),
               "refusal_by_reason": retrieval.refusal_by_reason(shown, reasons)}
    if any(r["search_spans"] for r in shown):  # a graph search (T2-36): which node each miss is lost at
        for r in shown:
            r["failure"] = trace.blame_search(r, by_qid[r["qid"]].gold_keys, args.k)
        summary["search_failures"] = dict(Counter(r["failure"]["span"] or "-" for r in shown if r["failure"]))
    if gate.has_gate(shown):  # KOS-193: what the graph really refused (gate), next to the evaluator's tau rule
        summary["gate"] = gate.summarize([gate.row_of(r) for r in shown])
    return shown, summary, report(args, summary)


def report(args, s: dict) -> str:
    cols = ["hit@1", "hit@5", "hit@10", "rr", "slot_recall@5", "slot_recall@10", "complete@2", "complete@3", "complete@10", "unjudged@10"]
    head = "| qtype | n | " + " | ".join("MRR" if c == "rr" else c for c in cols) + " |"
    lines = [head, "|" + " --- |" * (len(cols) + 2)]
    order = ["all"] + sorted(k for k in s["metrics"] if k != "all")
    for name in order:
        m = s["metrics"][name]
        lines.append(f"| {name} | {m['n']} | " + " | ".join(f"{m.get(c, 0):.3f}" for c in cols) + " |")
    na = s["no_answer"]
    rules = []
    if "no_answer_rules" in s:  # T2-09: the LLM re-ranker's verdict next to tau
        nr = s["no_answer_rules"]
        rules = ["", "| 답 없음 규칙 | P | R | F1 | tp / fp / fn | 사유별 recall |", "| --- | --- | --- | --- | --- | --- |"]
        rules += [f"| {name} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} | {m['tp']} / {m['fp']} / {m['fn']} | "
                  + ", ".join(f"{g} {a}/{b}" for g, (a, b) in m["recall_by_reason"].items()) + " |"
                  for name, m in nr.items() if name in ("tau", "llm", "either")]
        rules += ["", f"- llm: 2차 LLM이 고른 표 0개(호출 오류 제외) · either: llm 또는 tau · LLM 사유 일치 "
                      f"{nr['llm_reason_match'][0]}/{nr['llm_reason_match'][1]} · LLM 호출 오류 {nr['llm_errors']}건"]
    a = s["metrics"]["all"]
    bounds = ([f"- 판정 구간 (하한 = 판정 없는 표를 오답, 상한 = 정답으로 볼 때. 실제 값은 이 사이): "
               + " · ".join(f"{name} {a[c]:.3f} ~ {a[c + '_upper']:.3f}"
                            for name, c in (("Hit@1", "hit@1"), ("Hit@5", "hit@5"), ("Hit@10", "hit@10"), ("MRR", "rr"))), ""]
              if "rr_upper" in a else [])
    return "\n".join([
        f"# L1 retrieval — {args.stage}", "",
        f"- retriever `{args.retriever}`, k={args.k}, split `{args.split}`, 답 있는 문항 기준"
        + (f" · 과거 실행 재채점 `{args.from_run}` (검색 다시 안 함, tau는 그 run 값)" if args.from_run else ""), "",
        *lines, "", *bounds,
        f"- 답 없음 (tau={s['tau']:.4g}, {'재채점한 run의 값' if args.from_run else 'dev에서 선택'}): P {na['precision']:.3f} · R {na['recall']:.3f} · F1 {na['f1']:.3f} "
        f"(tp {na['tp']}, fp {na['fp']}, fn {na['fn']})",
        *([gate.summary_line(s["gate"])] if "gate" in s else []),
        "- 사유별 답 없음 판정 (§4.3, no_period는 필터 완화 회귀 감시): " + (", ".join(
            f"{k} {v['refused']}/{v['n']} ({v['rate']:.0%})" for k, v in s.get("refusal_by_reason", {}).items()) or "답 없음 문항 없음"),
        "- slot_recall@k: 정답 슬롯(표 자리) 중 상위 k에서 채운 비율. 단일 표 문항은 hit@k와 같고 복합 문항에서만 다르다.",
        "- unjudged@10: 상위 10개 중 사람이 판정하지 않은 표 비율. 높으면 정답 라벨 추가 판정이 필요하다 (plans/T2-02.md 동결 규칙).",
    ]) + "\n"


def extra_files(args, rows: list[dict], summary: dict) -> dict[str, str]:
    """Files written next to report.md: gate_report.md (the graph's real refusals) when the search had a gate."""
    if "gate" not in summary:
        return {}
    na = summary["no_answer"]
    info = [f"- retriever `{args.retriever}`, k={args.k}, split `{args.split}`"
            + (f" · 과거 실행 재채점 `{args.from_run}`" if args.from_run else "")]
    tau_line = (f"tau={summary['tau']:.4g}: P {na['precision']:.3f} · R {na['recall']:.3f} · F1 {na['f1']:.3f} "
                f"(tp {na['tp']}, fp {na['fp']}, fn {na['fn']})")
    return {"gate_report.md": gate.render_md(args.stage, info, summary["gate"], tau_line)}


def owners(rows: list[dict], summary: dict) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    add(found, "ai_search", "답 있는데 상위 5에 정답 표 없음", [r["qid"] for r in rows if r["answerable"] and not r.get("hit@5")])
    add(found, "ai_search", "답 없음 판정 틀림", [r["qid"] for r in rows if r["no_answer_pred"] == r["answerable"]])
    return found


def target(args, op: dict) -> str:
    return args.retriever
