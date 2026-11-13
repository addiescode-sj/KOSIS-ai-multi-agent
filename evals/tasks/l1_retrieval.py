"""L1: retrieval ranking (Hit@k, MRR, slot recall) and no-answer threshold, or a regrade of a past run."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from evals import trace
from evals.evaluators import retrieval
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
        # the saved run keeps only its split, so tau cannot be picked on dev again: reuse the run's tau
        tau = args.tau if args.tau is not None else past["summary"]["tau"]
    else:
        from evals.runners.agent import tracing

        args.telemetry = tracing("l1")  # T3-32: live search spans; run_eval links the misses after grading
        rows = runner.run(runner.build(args.retriever, items, args.k), items, args.k, args.telemetry)
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
        "- 사유별 답 없음 판정 (§4.3, no_period는 필터 완화 회귀 감시): " + (", ".join(
            f"{k} {v['refused']}/{v['n']} ({v['rate']:.0%})" for k, v in s.get("refusal_by_reason", {}).items()) or "답 없음 문항 없음"),
        "- slot_recall@k: 정답 슬롯(표 자리) 중 상위 k에서 채운 비율. 단일 표 문항은 hit@k와 같고 복합 문항에서만 다르다.",
        "- unjudged@10: 상위 10개 중 사람이 판정하지 않은 표 비율. 높으면 정답 라벨 추가 판정이 필요하다 (plans/T2-02.md 동결 규칙).",
    ]) + "\n"


def owners(rows: list[dict], summary: dict) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    add(found, "ai_search", "답 있는데 상위 5에 정답 표 없음", [r["qid"] for r in rows if r["answerable"] and not r.get("hit@5")])
    add(found, "ai_search", "답 없음 판정 틀림", [r["qid"] for r in rows if r["no_answer_pred"] == r["answerable"]])
    return found


def target(args, op: dict) -> str:
    return args.retriever
