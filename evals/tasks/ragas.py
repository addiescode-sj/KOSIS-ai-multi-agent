"""Ragas layer (plans/T3-19.md): judge metrics over a saved e2e run, linked to each question's graph trace.

    python -m evals.run_eval --layer ragas --stage T3-19-ragas --from-run evals/reports/<e2e run>

The e2e run already holds what Ragas needs (question, StatAnswer with the computed facts, the ranked keys, the trace),
so nothing is searched or answered again; only the judge is called (RAGAS_JUDGE_MODEL, default gpt-4.1-mini: neither
HCX nor the pipeline's gpt-4o-mini). Each row gets the four Ragas scores, the two id-based cross-checks and, for a low
score, the LangGraph node it points at. The summary carries the gate metrics evals.quality records and compares.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean

from evals.evaluators import ragas_metrics as rm
from evals.schemas import ROOT, TABLES, CalcItem
from evals.tasks.common import calc_items, pct
from evals.tasks.l4_e2e import SERVICE_GRAPH

JUDGE_MODEL = "gpt-4.1-mini"
EMBED_MODEL = "text-embedding-3-small"
CACHE_DIR = ROOT / "data" / "cache" / "ragas"  # <repo>/data is gitignored
LABELS = {"faithfulness": "Faithfulness", "answer_relevancy": "Response Relevancy", "context_precision": "Context Precision",
          "context_recall": "Context Recall", "id_context_precision": "ID Precision@k (코드)", "id_context_recall": "ID Recall@k (코드)"}


def judge_models() -> tuple[str, str]:
    return os.environ.get("RAGAS_JUDGE_MODEL") or JUDGE_MODEL, os.environ.get("RAGAS_EMBED_MODEL") or EMBED_MODEL


def make_scorer(args) -> tuple[rm.Scorer, str]:
    """(score function, ragas version); tests replace this with a fake judge."""
    judge, embed = judge_models()
    scorer = rm.RagasScorer(judge, embed, None if args.no_judge_cache else CACHE_DIR)
    return scorer.score, scorer.version


def source_run(path: Path) -> tuple[dict, dict[str, dict]]:
    """(manifest, saved rows by qid) of the e2e run the layer scores."""
    m = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if m["layer"] != "e2e":
        raise SystemExit(f"--layer ragas needs an e2e run, {path} is {m['layer']}")
    saved = {}
    with (path / "results.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                saved[r["qid"]] = r
    return m, saved


def e2e_target(m: dict) -> str:
    return f"{m.get('agent') or SERVICE_GRAPH}+{','.join(m['ops'].get('models') or []) or 'summarize_stat'}"


def mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def run(args) -> tuple[list[dict], dict, str]:
    from evals.runners.stat import read_rows

    if not args.from_run:
        raise SystemExit("--layer ragas scores a saved e2e run: give --from-run <evals/reports/...-e2e...>")
    m, saved = source_run(args.from_run)
    items: list[CalcItem] = calc_items(args.calc_golden)
    if len(args.calc_golden) == 1 and (sha := hashlib.sha256(args.calc_golden[0].read_bytes()).hexdigest()) != m["golden"]["sha256"]:
        raise SystemExit(f"--calc-golden {args.calc_golden[0]} ({sha[:12]}) is not the golden of {args.from_run.name} "
                         f"({m['golden']['sha256'][:12]}): give the same file the e2e run used")
    args.split = m.get("split") or args.split
    by_qid = {i.qid: i for i in items}
    rows = []
    for item, r in zip(items, read_rows(args.from_run, items)):
        s = saved.get(item.qid, {})
        rows.append({**r, "question": item.question, "kind": item.kind, "gold_keys": s.get("gold_keys") or [item.table_key],
                     "verdict": s.get("verdict"), "outcome": s.get("outcome"), "right_table": s.get("right_table"),
                     "trace_id": s.get("trace_id"), "span_id": s.get("span_id"), "trace_spans": s.get("trace_spans") or []})
    k = args.k
    needed = {key for r in rows for key in [*r["keys"][:k], *r["gold_keys"], r.get("used_table") or ""] if key}
    texts = rm.table_texts(needed, TABLES)

    jobs, per_row = [], []
    for i, r in enumerate(rows):
        facts = by_qid[r["qid"]].required_facts if r["kind"] in ("answerable", "default_allowed") else []
        per_row.append({**rm.id_scores(r["keys"], set(r["gold_keys"]) if facts else set(), k),
                        **({"context_precision": 0.0, "context_recall": 0.0} if rm.no_contexts(r, facts) else {})})
        jobs += [(i, metric, kw) for metric, kw in rm.samples(r, facts, texts, k).items() if kw is not None]
    score, version = make_scorer(args)
    results = score(jobs)

    judged: dict[int, dict] = defaultdict(dict)
    errors: dict[int, dict] = defaultdict(dict)
    ms: Counter = Counter()
    judge_tokens: dict[int, dict[str, dict[str, int]]] = defaultdict(dict)  # T3-50: judge + embedding cost per row
    for (i, metric, _), (value, error, took, *usage) in zip(jobs, results):
        judged[i][metric] = value
        ms[i] += took
        for model, t in (usage[0] if usage else {}).items():
            acc = judge_tokens[i].setdefault(model, {"input": 0, "output": 0})
            acc["input"] += t["input"]
            acc["output"] += t["output"]
        if error:
            errors[i][metric] = error
    judge, embed = judge_models()
    out = []
    for i, r in enumerate(rows):
        scores = {**dict.fromkeys(rm.RAGAS_METRICS), **judged[i], **per_row[i]}
        low = {mt: v for mt, v in scores.items() if v is not None and v < rm.LOW}
        nodes = {mt: rm.node_for(mt, r, k) for mt in low}
        worst = min(low, key=low.get) if low else None
        out.append({
            "qid": r["qid"], "question": r["question"], "kind": r["kind"], "outcome": r["outcome"], "verdict": r["verdict"],
            **{mt: scores[mt] for mt in rm.METRICS}, "low_nodes": nodes, "judge_errors": errors[i],
            "used_table": r.get("used_table"), "gold_keys": r["gold_keys"], "keys": r["keys"][:k],
            "failure": {"stage": "ragas", "label": "Ragas 저점", "span": nodes[worst],
                        "reason": f"{worst} {low[worst]:.2f} → {nodes[worst]}"} if worst else None,
            "trace_id": r["trace_id"], "span_id": r["span_id"], "trace_spans": r["trace_spans"],
            "node_ms": {mt: rm.node_ms(r, n) for mt, n in nodes.items()},
            "latency_ms": ms[i], "llm_calls": sum(1 for j in jobs if j[0] == i),
            "input_tokens": sum(t["input"] for t in judge_tokens[i].values()),
            "output_tokens": sum(t["output"] for t in judge_tokens[i].values()),
            "tokens_by_model": judge_tokens[i], "model": judge, "failed": bool(errors[i]), "retried": False})

    metrics = {mt: {"mean": mean(v := [x[mt] for x in out if x[mt] is not None]), "n": len(v),
                    "errors": sum(1 for x in out if mt in x["judge_errors"])} for mt in rm.METRICS}
    agree = {}
    for llm, code in (("context_precision", "id_context_precision"), ("context_recall", "id_context_recall")):
        both = [(x[llm] >= rm.LOW) == (x[code] >= rm.LOW) for x in out if x[llm] is not None and x[code] is not None]
        agree[llm] = {"agree": sum(both), "n": len(both)}
    nodes = {mt: dict(Counter(x["low_nodes"][mt] for x in out if mt in x["low_nodes"]).most_common()) for mt in rm.METRICS}
    n = len(out)
    deterministic = {"e2e_pass_rate": sum(x["verdict"] == "PASS" for x in out) / n if n else None,
                     "e2e_right_table_rate": sum(bool(saved.get(x["qid"], {}).get("right_table")) for x in out) / n if n else None,
                     "answered": sum(x["outcome"] == "answered" for x in out)}
    key = {"golden_sha256": m["golden"]["sha256"], "split": m.get("split"), "tables_sha256": m["source_snapshot"]["tables.jsonl"],
           "alternates_sha256": (m.get("alternates") or {}).get("sha256"),  # T3-41: which tables count as right
           "e2e_target": e2e_target(m), "judge": judge, "embeddings": embed, "ragas": version, "context": rm.CONTEXT_FORMAT, "k": k}
    summary = {"n": n, "metrics": metrics, "agreement": agree, "low_nodes": nodes, "deterministic": deterministic,
               "gate": {**{mt: metrics[mt]["mean"] for mt in rm.METRICS}, **deterministic}, "key": key,
               "source_run": m["run_id"]}
    return out, summary, report(args, summary, out)


def report(args, s: dict, rows: list[dict]) -> str:
    fmt = lambda v: "—" if v is None else f"{v:.3f}"  # noqa: E731
    lines = [f"# Ragas — {args.stage}", "",
             f"- 채점 대상 e2e run `{s['source_run']}` ({s['n']}문항, 그래프·검색 재실행 없음) · 심판 {s['key']['judge']} · "
             f"임베딩 {s['key']['embeddings']} · ragas {s['key']['ragas']} · 컨텍스트 상위 {s['key']['k']}개 표 ({s['key']['context']})",
             "- 생성 지표는 답한 문항만, 검색 지표는 정답 표가 있는 문항만 채점한다 (거절·되묻기는 결정적 채점이 맡음). "
             "reference는 골든셋에서 코드가 만든 정답 표·기간·분류 문장이다.", "",
             "## 지표", "", "| 지표 | 평균 | 대상 | 심판 오류 |", "|---|---:|---:|---:|"]
    lines += [f"| {LABELS[mt]} | {fmt(v['mean'])} | {v['n']} | {v['errors']} |" for mt, v in s["metrics"].items()]
    d = s["deterministic"]
    lines += ["", f"- 결정적 (원 e2e): PASS {fmt(d['e2e_pass_rate'])} · 검색 1위 정답 표 {fmt(d['e2e_right_table_rate'])} · 답한 문항 {d['answered']}/{s['n']}",
              "- 심판 교차 확인 (LLM ≥ 0.5 ⇔ 코드 ID ≥ 0.5 일치): " + " · ".join(
                  f"{LABELS[k]} {pct(v['agree'], v['n'])}" for k, v in s["agreement"].items()), "",
              "## 저점(< 0.5) 문항의 담당 노드", "",
              "지표별로 점수가 낮은 문항이 trace의 어느 LangGraph 노드(그래프 노드/안쪽 span)를 가리키는지. Jaeger의 `eval.ragas` span이 각 문항 trace에 link된다.", "",
              "| 지표 | 노드 (저점 문항 수) |", "|---|---|"]
    lines += [f"| {LABELS[mt]} | {', '.join(f'`{n}` {c}' for n, c in nodes.items()) or '없음'} |" for mt, nodes in s["low_nodes"].items()]
    worst = sorted((r for r in rows if r["failure"]), key=lambda r: min(r[mt] for mt in r["low_nodes"]))[:10]
    if worst:
        lines += ["", "## 가장 낮은 문항 10", "", "| qid | 질문 | 판정 | 저점 지표 | 담당 노드 | trace |", "|---|---|---|---|---|---|"]
        lines += [f"| {r['qid']} | {r['question'].replace('|', '/')} | {r['verdict']} | " + ", ".join(f"{mt} {r[mt]:.2f}" for mt in r["low_nodes"])
                  + f" | {r['failure']['span']} | `{(r['trace_id'] or '')[:16]}` |" for r in worst]
    return "\n".join(lines) + "\n"


def span_attributes(row: dict) -> dict:
    """`eval.ragas` span attributes: each score and the node each low score points at (Jaeger tag search)."""
    return {**{f"kosis.ragas.{mt}": row[mt] for mt in rm.METRICS if row.get(mt) is not None},
            **{f"kosis.ragas.node.{mt}": node for mt, node in row["low_nodes"].items()}}


def target(args, op: dict) -> str:
    return f"ragas:{judge_models()[0]}"
