"""L1: overall accuracy, retrieval ranking (Hit@k, MRR, slot recall) and the search's own refusals (no tau, 2026-11-19),
or a regrade of a past run."""

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
            r["no_answer_reason"] = by_qid[r["qid"]].no_answer_reason
    else:
        from evals.runners.agent import tracing

        args.telemetry = tracing("l1")  # T3-32: live search spans; run_eval links the misses after grading
        rows = runner.run(runner.build(args.retriever, items, args.k), items, args.k, args.telemetry)
    judged = judged_keys(args.pool)
    shown = [r for r in rows if args.split == "all" or r["split"] == args.split]
    for r in shown:
        r["no_answer_pred"] = retrieval.refused(r)  # what the search did (gate), no tau
        r["correct"] = int(retrieval.is_correct(r, by_qid[r["qid"]].gold_keys))
        r["outcome"] = retrieval.outcome(r)
        if r["answerable"]:
            r.update(retrieval.score_item(by_qid[r["qid"]], r["keys"], judged.get(r["qid"])))
    scored = [r for r in shown if r["answerable"]]
    agg = retrieval.aggregate(scored)
    no_answer = retrieval.prf([(r["no_answer_pred"], not r["answerable"]) for r in shown])
    reasons = {i.qid: i.no_answer_reason for i in items if not i.answerable}
    summary = {"accuracy": retrieval.accuracy(shown), "accuracy_by_qtype": retrieval.accuracy(shown, "qtype"),
               "outcomes": dict(Counter(r["outcome"] for r in shown)),
               "metrics": agg, "no_answer": no_answer, "errors": sum(1 for r in shown if r["error"]),
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
    a = s["metrics"]["all"]
    bounds = ([f"- 판정 구간 (하한 = 판정 없는 표를 오답, 상한 = 정답으로 볼 때. 실제 값은 이 사이): "
               + " · ".join(f"{name} {a[c]:.3f} ~ {a[c + '_upper']:.3f}"
                            for name, c in (("Hit@1", "hit@1"), ("Hit@5", "hit@5"), ("Hit@10", "hit@10"), ("MRR", "rr"))), ""]
              if "rr_upper" in a else [])
    return "\n".join([
        f"# L1 retrieval — {args.stage}", "",
        f"- retriever `{args.retriever}`, k={args.k}, split `{args.split}`"
        + (f" · 과거 실행 재채점 `{args.from_run}` (검색 다시 안 함)" if args.from_run else ""), "",
        *accuracy_lines(s), "",
        "## 순위 지표 (답 있는 문항 기준, 거절과 무관하게 순위만 봄)", "",
        *lines, "", *bounds,
        f"- 답 없음 (실제 거절 기준, 타우 없음): P {na['precision']:.3f} · R {na['recall']:.3f} · F1 {na['f1']:.3f} "
        f"(tp {na['tp']}, fp {na['fp']}, fn {na['fn']})",
        "- 거절 = 검색 그래프 gate의 route가 refuse. gate 기록이 없는 검색기는 2차 LLM이 0개를 골랐거나(호출 오류 제외) 결과가 0개일 때.",
        "- 사유별 답 없음 판정 (§4.3, no_period는 필터 완화 회귀 감시): " + (", ".join(
            f"{k} {v['refused']}/{v['n']} ({v['rate']:.0%})" for k, v in s.get("refusal_by_reason", {}).items()) or "답 없음 문항 없음"),
        "- slot_recall@k: 정답 슬롯(표 자리) 중 상위 k에서 채운 비율. 단일 표 문항은 hit@k와 같고 복합 문항에서만 다르다.",
        "- unjudged@10: 상위 10개 중 사람이 판정하지 않은 표 비율. 높으면 정답 라벨 추가 판정이 필요하다 (plans/T2-02.md 동결 규칙).",
    ]) + "\n"


def _f(x) -> str:
    return "-" if x is None else f"{x:.3f}"


def accuracy_lines(s: dict) -> list[str]:
    """report.md: the overall accuracy by split and by qtype, then the outcome counts."""
    head = ["| {} | 문항 | 정답 | 전체 정답률 | 답 있는 문항 (Hit@1, 거절 반영) | 답 없는 문항 (거절) |", "| --- | --- | --- | --- | --- | --- |"]
    out = ["## 전체 정답률", "",
           "- 답 있는 문항: 거절하지 않고 1위 표가 정답 표면 정답 · 답 없는 문항: 표를 찾지 않으면(거절) 정답 · 실행 오류는 오답", ""]
    for by, key in (("split", "accuracy"), ("qtype", "accuracy_by_qtype")):
        out += [head[0].format(by), head[1]]
        for name, a in s.get(key, {}).items():
            if by == "qtype" and name == "all":
                continue
            out.append(f"| {name} | {a['n']} | {a['correct']} | {a['acc']:.3f} | {_f(a['acc_ans'])} ({a['n_ans']}) | "
                       f"{_f(a['acc_una'])} ({a['n_una']}) |")
        out.append("")
    out.append("- 결과 유형: " + " · ".join(f"{retrieval.OUTCOMES[k]} {v}" for k, v in sorted(s.get("outcomes", {}).items())))
    return out


def owners(rows: list[dict], summary: dict) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    add(found, "ai_search", "답 있는데 상위 5에 정답 표 없음", [r["qid"] for r in rows if r["answerable"] and not r.get("hit@5")])
    add(found, "ai_search", "답 있는데 1위가 다른 표", [r["qid"] for r in rows if r.get("outcome") == "wrong_table"])
    add(found, "ai_search", "답 없음 판정 틀림", [r["qid"] for r in rows if r["no_answer_pred"] == r["answerable"]])
    return found


def target(args, op: dict) -> str:
    return args.retriever
