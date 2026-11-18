"""Online evaluation (plans/T3-43.md): real user turns, no golden set.

    python -m evals.run_eval --layer online --stage <ID>-online [--lookback 24h --limit 500 --sample 40]   # Jaeger
    python -m evals.run_eval --layer online --stage <ID>-online --from-run evals/reports/<agent run>         # replay

1. trace 수집: service turns from Jaeger (evals.runners.online), or a past agent run in the same record shape.
2. 표본 추출 평가: every turn gets the code checks of the agent layer (evaluators.agent.answer: final-answer numbers
   grounded in the conversation, cited source) and its outcome; a fixed-seed sample of answered turns gets the Ragas
   judge (Faithfulness over the turn's computed facts, Response Relevancy against the standalone question).
3. 품질 저하 감지: the run's aggregates go to evals/datasets/online_history.jsonl (local, not the committed
   quality_history: traffic changes every day) and are compared with the median of the previous records of the same
   key once there are MIN_HISTORY of them; tolerance = max(judge noise, 2 standard errors of this sample).
4. 실패 케이스 수집: errors, ungrounded numbers, missing sources and low faithfulness go to
   evals/datasets/online_failures.jsonl (local, deduplicated by trace, kept RETENTION_DAYS); refusals go there as
   "refusal_review" by reason, apart from failures (a right refusal is no failure). Candidates for the golden set.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import statistics
from collections import Counter, defaultdict

from evals.evaluators import ragas_metrics as rm
from evals.evaluators.agent import final_calls
from evals.evaluators.agent.answer import Grounds, grounding, missing_sources, unsupported_final_numbers
from evals.evaluators.agent.numbers import stat_answers
from evals.schemas import DATASETS
from evals.tasks import ragas as offline

HISTORY = DATASETS / "online_history.jsonl"
FAILURES = DATASETS / "online_failures.jsonl"
MIN_HISTORY = 3  # earlier records of the same key needed before a drop is judged
JUDGE_NOISE = 0.03  # T3-19: the judge's own run-to-run noise on a fixed input
RETENTION_DAYS = 30
JUDGED = ("faithfulness", "answer_relevancy")
HIGHER = {"faithfulness", "answer_relevancy", "answered_rate"}  # the rest are rates where lower is better


def code_checks(turns: list[dict]) -> list[dict]:
    """Per turn, in conversation order: ungrounded final-answer numbers and missing sources (agent layer rules)."""
    out = []
    grounds: dict[str, Grounds] = defaultdict(Grounds)
    for t in turns:
        calls = final_calls(t.get("calls") or [])
        g = grounds[t["conversation"]]
        g.add(grounding(t["question"], calls))
        answer = t.get("answer") or ""
        out.append({"unsupported_numbers": unsupported_final_numbers(answer, g),
                    "missing_sources": [] if t.get("error") else missing_sources(answer, stat_answers(calls))})
    return out


def judge_samples(t: dict) -> dict[str, dict] | None:
    """Ragas inputs of one answered turn: the facts summarize_stat computed ground the answer; the standalone question
    (resolved, or the search query the context node wrote) is what Relevancy compares with."""
    answers = [a for a in stat_answers(final_calls(t.get("calls") or [])) if a is not None and a.outcome == "answered"]
    if not answers or not (t.get("answer") or "").strip():
        return None
    search = next((c for c in t.get("calls") or [] if c["name"] == "search_tables"), None)
    question = t.get("resolved_question") or ((search or {}).get("args") or {}).get("question") or t["question"]
    grounds = [line for a in answers for line in [*rm.fact_lines(a), rm.source_line(a)] if line]
    return {"faithfulness": {"user_input": question, "response": t["answer"], "retrieved_contexts": ["\n".join(grounds)]},
            "answer_relevancy": {"user_input": question, "response": rm.SOURCE_LINE.sub("", t["answer"]).strip() or t["answer"]}}


def rate(rows: list[dict], pred) -> float | None:
    return sum(map(pred, rows)) / len(rows) if rows else None


def stderr(values: list[float]) -> float:
    return statistics.stdev(values) / len(values) ** 0.5 if len(values) > 1 else 0.0


def drift(record: dict, history: list[dict]) -> dict:
    """Each metric against the median of the earlier records of the same key; 'insufficient' below MIN_HISTORY."""
    same = [h for h in history if h["key"] == record["key"]]
    if len(same) < MIN_HISTORY:
        return {"status": "insufficient", "history": len(same), "metrics": {}}
    out = {}
    for metric, value in record["metrics"].items():
        past = [h["metrics"][metric] for h in same if h["metrics"].get(metric) is not None]
        if value is None or not past:
            continue
        base = statistics.median(past)
        n = record["n_judged"] if metric in JUDGED else record["n"]
        tol = (max(JUDGE_NOISE, 2 * record["stderr"].get(metric, 0.0)) if metric in JUDGED
               else max(2 / n, 2 * (base * (1 - base) / n) ** 0.5) if n else 0.0)
        drop = base - value if metric in HIGHER else value - base
        out[metric] = {"median": base, "latest": value, "tolerance": tol, "degraded": drop > tol + 1e-9}
    return {"status": "degraded" if any(m["degraded"] for m in out.values()) else "ok", "history": len(same), "metrics": out}


def failure_cases(rows: list[dict], now: dt.datetime) -> list[dict]:
    out = []
    for r in rows:
        kinds = (["error"] * (r["outcome"] in ("error", "failed")) + ["unsupported_number"] * bool(r["unsupported_numbers"])
                 + ["missing_source"] * bool(r["missing_sources"])
                 + ["low_faithfulness"] * (r.get("faithfulness") is not None and r["faithfulness"] < rm.LOW))
        if r["outcome"] == "refused":
            kinds.append(f"refusal_review:{r.get('no_answer_reason') or 'unknown'}")
        if kinds:
            out.append({"id": r["qid"], "trace_id": r["trace_id"], "collected": now.isoformat(timespec="seconds"), "kinds": kinds,
                        "question": r["question"], "answer": r["answer"], "table_key": r.get("table_key")})
    return out


def collect(cases: list[dict], now: dt.datetime, path=None) -> int:
    """Append new cases (one per trace) and drop those older than RETENTION_DAYS; returns how many were new."""
    path = path or FAILURES  # read at call time (a default argument would freeze it: tests point it elsewhere)
    keep = []
    if path.exists():
        cutoff = now - dt.timedelta(days=RETENTION_DAYS)
        keep = [d for d in map(json.loads, path.read_text(encoding="utf-8").splitlines())
                if dt.datetime.fromisoformat(d["collected"]) >= cutoff]
    seen = {d["id"] for d in keep}
    new = [c for c in cases if c["id"] not in seen]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in keep + new), encoding="utf-8")
    return len(new)


def run(args) -> tuple[list[dict], dict, str]:
    from evals.runners import online

    from evals.trace import JAEGER_UI

    if args.from_run:
        turns, unreadable = online.from_run(args.from_run)
        source = f"run {args.from_run.name}"
    else:
        turns, unreadable = online.from_jaeger(JAEGER_UI, args.lookback, args.limit)
        source = f"Jaeger {JAEGER_UI} (최근 {args.lookback}, 최대 {args.limit} trace)"
    checks = code_checks(turns)
    rows = [{"qid": t.get("trace_id") or f"{t['conversation']}#{t['turn']}",  # runs before T3-30 have no trace id "conversation": t["conversation"], "turn": t["turn"],
             "question": t["question"], "answer": t.get("answer") or "", "outcome": t["outcome"] or "unknown",
             "no_answer_reason": t.get("no_answer_reason"), "table_key": t.get("table_key"),
             **c, **dict.fromkeys(JUDGED), "trace_id": t.get("trace_id"), "span_id": t.get("span_id"),
             "latency_ms": t.get("latency_ms", 0.0), "llm_calls": t.get("llm_calls", 0), "input_tokens": t.get("input_tokens", 0),
             "output_tokens": t.get("output_tokens", 0), "model": t.get("model"), "tokens_by_model": t.get("tokens_by_model") or {},
             "failed": bool(t.get("error")), "retried": bool(t.get("retried")), "verdict": None}
            for t, c in zip(turns, checks)]
    judgeable = [(i, s) for i, t in enumerate(turns) if (s := judge_samples(t))]
    sample = sorted(random.Random(args.seed).sample(judgeable, min(args.sample, len(judgeable))))
    jobs = [(i, m, kw) for i, s in sample for m, kw in s.items()]
    errors = Counter()
    if jobs:
        score, version = offline.make_scorer(args)
        for (i, metric, _), (value, error, _) in zip(jobs, score(jobs)):
            rows[i][metric] = value
            errors[metric] += bool(error)
    else:
        version = None
    judge, embed = offline.judge_models()
    n = len(rows)
    judged = {m: [r[m] for r in rows if r[m] is not None] for m in JUDGED}
    metrics = {**{m: statistics.fmean(v) if v else None for m, v in judged.items()},
               "answered_rate": rate(rows, lambda r: r["outcome"] == "answered"),
               "error_rate": rate(rows, lambda r: r["outcome"] in ("error", "failed")),
               "unsupported_number_rate": rate(rows, lambda r: bool(r["unsupported_numbers"])),
               "missing_source_rate": rate(rows, lambda r: bool(r["missing_sources"]))}
    now = dt.datetime.now()
    record = {"run_id": None, "stage": args.stage, "source": "replay" if args.from_run else "jaeger", "n": n,
              "n_judged": len(sample), "key": {"judge": judge, "embeddings": embed, "ragas": version, "source": "replay" if args.from_run else "jaeger"},
              "metrics": metrics, "stderr": {m: stderr(v) for m, v in judged.items()}}
    history = [json.loads(line) for line in HISTORY.read_text(encoding="utf-8").splitlines() if line.strip()] if HISTORY.exists() else []
    trend = drift(record, history)
    cases = failure_cases(rows, now)
    new_cases = collect(cases, now) if not args.no_record else 0
    by_id = {c["id"]: c for c in cases}
    for r in rows:
        c = by_id.get(r["qid"])
        r["failure"] = {"stage": "online", "label": "온라인 실패 케이스", "span": None, "reason": ", ".join(c["kinds"])} if c else None
    outcomes = Counter(r["outcome"] for r in rows)
    refusals = Counter(r.get("no_answer_reason") or "unknown" for r in rows if r["outcome"] == "refused")
    summary = {"n": n, "unreadable_traces": unreadable, "n_judged": len(sample), "judge_errors": dict(errors), "metrics": metrics,
               "stderr": record["stderr"], "outcomes": dict(outcomes), "refusals": dict(refusals), "drift": trend,
               "failure_cases": dict(Counter(k.split(":")[0] for c in cases for k in c["kinds"])), "new_failure_cases": new_cases,
               "degraded": trend["status"] == "degraded", "online_record": record}
    return rows, summary, report(args, source, summary)


def record_history(run_id: str, summary: dict) -> None:
    """Called by run_eval once the run id is known (never for --no-record)."""
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**summary["online_record"], "run_id": run_id}, ensure_ascii=False) + "\n")


def report(args, source: str, s: dict) -> str:
    fmt = lambda v: "—" if v is None else f"{v:.3f}"  # noqa: E731
    d = s["drift"]
    lines = [f"# 온라인 평가 — {args.stage}", "",
             f"- 수집: {source} · 턴 {s['n']} (질문 없는 trace {s['unreadable_traces']}개 제외) · 심판 표본 {s['n_judged']}턴 (시드 {args.seed})",
             "- 정답이 없는 실제 요청이라 숫자는 코드가 대화 근거(질문·계산 사실·합산)와 대조하고, 심판은 표본 답 문장만 채점한다.", "",
             "## 지표", "", "| 지표 | 이번 | 표준오차 | 직전 기록 중앙값 | 허용폭 | 저하 |", "|---|---:|---:|---:|---:|---|"]
    for k, v in s["metrics"].items():
        m = d["metrics"].get(k, {})
        lines.append(f"| {k} | {fmt(v)} | {fmt(s['stderr'].get(k)) if k in JUDGED else ''} | {fmt(m.get('median'))} | "
                     f"{fmt(m.get('tolerance'))} | {'**저하**' if m.get('degraded') else ('—' if not m else '')} |")
    lines += ["", f"- 품질 저하 판정: **{d['status']}** (같은 키 이전 기록 {d['history']}개, {MIN_HISTORY}개 이상부터 판정)",
              "- 턴 결과: " + ", ".join(f"{k} {v}" for k, v in sorted(s["outcomes"].items())),
              "- 거절 사유 (실패가 아니라 검토 대상): " + (", ".join(f"{k} {v}" for k, v in s["refusals"].items()) or "없음"), "",
              "## 실패 케이스 수집", "",
              f"- 유형별: " + (", ".join(f"{k} {v}" for k, v in s["failure_cases"].items()) or "없음")
              + f" · 새로 쌓인 케이스 {s['new_failure_cases']} (`evals/datasets/online_failures.jsonl`, 로컬 전용, {RETENTION_DAYS}일 보존)",
              "- 골든셋 후보: 사람이 검토해 정답 표·사실을 달면 수치 골든에 추가한다."]
    return "\n".join(lines) + "\n"


def span_attributes(row: dict) -> dict:
    """`eval.online` span attributes (Jaeger tag search on the service turn): judge scores and failure kinds."""
    return {**{f"kosis.online.{m}": row[m] for m in JUDGED if row.get(m) is not None},
            "kosis.online.unsupported_numbers": len(row["unsupported_numbers"]), "kosis.online.missing_sources": len(row["missing_sources"])}


def target(args, op: dict) -> str:
    return "online"
