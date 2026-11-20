"""T3-46 rules layer: today's rule-based classification vs the gold labels of the e2e golden set. No LLM, no search.

    python -m evals.run_eval --layer rules --stage <name>     # reads rule_pred stored by evals.builders.e2e_golden

rule_pred is computed when the golden file is built (the rules on the gold table, earlier turns assumed right), so
this layer only grades; rebuild the file after a rule change (python -m evals.builders.e2e_golden) to re-measure.
"""

from __future__ import annotations

from collections import Counter

from evals.evaluators import labels as lab
from evals.tasks.agent import load_scenarios, paths


def parser():
    from kosis_rag.parse.node import parse_query

    from evals.tasks.common import load_sigungu

    sigungu = load_sigungu()
    return lambda q: parse_query(q, sigungu=sigungu)


GATE_FIELDS = ("route", "outcome", "refusal_reasons", "judge_rule", "qtype", "default_applied")


def rows_of(scenarios, parse) -> list[dict]:
    rows = []
    for s in scenarios:
        for i, t in enumerate(s.turns):
            gold, pred = t.expect.labels, t.expect.rule_pred
            if gold is None:
                continue
            # skipped (table over the row cap) / timed out: the rules were not run, so nothing to grade (counted apart)
            not_run = bool(pred and (pred.judge_rule or "").startswith(("skipped", "error: timeout")))
            hits = {} if not_run else lab.grade(gold, pred, parse)
            # no gold table = the search gate must refuse (out of scope): phase 1 rules never see the turn offline
            gate = t.expect.table_key is None and not gold.sub_questions and gold.reading != "chart_only"
            if gate:
                hits = {f: ok for f, ok in hits.items() if f not in GATE_FIELDS}
            if len(gold.sub_questions or []) > 1:  # split turns: phase 1 runs per sub-question live, not offline
                hits = {f: ok for f, ok in hits.items() if f not in ("outcome", "qtype", "formula")}
            rows.append({"sid": s.sid, "turn": i, "kind": s.notes, "question": t.question, "search_gate": gate, "not_run": not_run,
                         "gold_labels": gold.model_dump(exclude_none=True),
                         "pred_labels": pred.model_dump(exclude_none=True) if pred else {},
                         "label_hits": hits, "latency_ms": 0.0})
    return rows


def run(args) -> tuple[list[dict], dict, str]:
    scenarios = [s for s in load_scenarios(args.scenarios) if args.split == "all" or s.split == args.split]
    rows = rows_of(scenarios, parser())
    missing = sum(not r["pred_labels"] for r in rows)
    summary = lab.summarize(rows)
    summary["by_kind"] = {k: {"n": n, "all_right": sum(bool(r["label_hits"]) and all(r["label_hits"].values()) for r in rows if r["kind"] == k)}
                          for k, n in Counter(r["kind"] for r in rows).items()}
    summary["n_turns"], summary["no_rule_pred"] = len(rows), missing
    summary["search_gate_turns"] = sum(r["search_gate"] for r in rows)
    summary["not_run_turns"] = sum(r["not_run"] for r in rows)
    wrong = [r for r in rows if r["label_hits"] and not all(r["label_hits"].values())]
    rows_note = (f"- 규칙을 돌리지 못한 턴 {summary['not_run_turns']}개(행 수 상한을 넘는 표 · 시간 초과)는 채점에서 뺐다"
                 if summary["not_run_turns"] else "")
    report = "\n".join([
        f"# Rules (규칙 분류 정확도, 오프라인) — {args.stage}", "",
        f"- 골든 `{' + '.join(str(p) for p in paths(args.scenarios))}` · 라벨 있는 턴 {len(rows)}"
        + (f" · rule_pred 없는 턴 {missing} (빌더를 --no-rules 없이 다시 실행)" if missing else ""),
        "- 예측 = 지금 규칙(context read_turn mode=rules · split_by_rules · 실전 1 judge·rule_plan)을 **정답 표로** 돌린 값,"
        " 앞 턴은 맞았다고 가정 → 검색이 맞았을 때의 규칙 분류 정확도",
        f"- 범위 밖 거절 턴 {summary['search_gate_turns']}개는 실전 2 검색 게이트 몫이라 outcome·거절 사유 등은 여기서 채점하지 않는다(live e2e에서 잰다). 멀티쿼리 분해 턴의 outcome·qtype·formula도 하위 질문마다 live에서만 잰다",
        "- formula 틀린 예의 `→ 규칙 미결정` = rule_plan이 계획을 못 세워 LLM fill에 넘김(규칙 커버리지 문제). % 값의 diff는 pp_diff로 본다", "",
        *([rows_note, ""] if rows_note else []),
        *lab.report_lines(summary), "",
        "| 문항 종류 | 턴 | 모든 라벨 맞음 |", "| --- | --- | --- |",
        *[f"| {k} | {v['n']} | {v['all_right']} |" for k, v in sorted(summary["by_kind"].items())], "",
        f"## 틀린 턴 ({len(wrong)}, 40개까지)", "",
        "| 시나리오 | 턴 | 질문 | 틀린 라벨 (정답 → 예측) |", "| --- | --- | --- | --- |",
        *[f"| {r['sid']} | {r['turn']} | {r['question'][:60]} | "
          + ", ".join(f"{f}: {r['gold_labels'].get(f)} → {r['pred_labels'].get(f)}" for f, ok in r["label_hits"].items() if not ok)
          + " |" for r in wrong[:40]],
    ]) + "\n"
    return rows, summary, report


def target(args, op: dict) -> str:
    return "rules-offline"
