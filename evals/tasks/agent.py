"""Agent layer: multi-turn / multi-query scenarios graded from each turn's tool calls (evals.evaluators.agent)."""

from __future__ import annotations

import json
import os
from collections import defaultdict

from evals.evaluators import calculation
from evals.schemas import ROOT, AgentScenario, load_jsonl
from evals.tasks.common import calc_items

TASK_NAMES = {"multiturn": "멀티턴 (MT-*)", "multiquery": "멀티쿼리 (MQ-*)", "other": "기타"}


def paths(scenarios) -> list:
    """--scenarios as a list (tests and evals.suite may still pass one Path)."""
    return list(scenarios) if isinstance(scenarios, (list, tuple)) else [scenarios]


def load_scenarios(scenarios) -> list[AgentScenario]:
    """Scenarios of every file in order (T3-29: multi-turn and multi-query files in one run); sids must not repeat."""
    out = [s for p in paths(scenarios) for s in load_jsonl(p, AgentScenario)]
    seen: set[str] = set()
    if dupes := sorted({s.sid for s in out if s.sid in seen or seen.add(s.sid)}):
        raise SystemExit(f"duplicate sids across {[str(p) for p in paths(scenarios)]}: {dupes[:5]}")
    return out


def task_table(by_task: dict) -> list[str]:
    """Success and answer quality per evaluation task (multi-turn / multi-query), from aggregate()["by_task"]."""
    fmt = lambda v: "—" if v is None else f"{v:.3f}"  # noqa: E731
    lines = ["| 평가 태스크 | 대화 | 대화 성공률 | 턴 통과율 | 툴 선택 | 상태(표 키) | 최종 수치 (n) | 합산 (n) | 근거 없는 숫자 턴 |",
             "|" + " --- |" * 9]
    for name, m in by_task.items():
        lines.append(f"| {TASK_NAMES.get(name, name)} | {m['n_conversations']} | {fmt(m['conversation_success'])} | {fmt(m['turn_pass'])} "
                     f"| {fmt(m['tool_selection'])} | {fmt(m['state_accuracy'])} | {fmt(m['numeric_accuracy'])} ({m['n_numeric']}) "
                     f"| {fmt(m['combine_accuracy'])} ({m['n_combine']}) | {m['unsupported_final_number_turns']} |")
    return [*lines, ""]


def run(args) -> tuple[list[dict], dict, str]:
    """Agent layer (T3-06, KOS-120): multi-turn / multi-query scenarios graded from each turn's tool calls (no LLM).

    --oracle grades the run a perfect agent would leave (self-check: every rate 1.000), --from-run regrades a past
    run's transcripts, --agent module:function runs a compiled graph live (spans -> records, runners.agent).
    """
    from evals.evaluators import agent as grader
    from evals.runners import agent as runner

    scenarios = [s for s in load_scenarios(args.scenarios) if args.split == "all" or s.split == args.split]
    needed = {q for s in scenarios for t in s.turns for q in [t.expect.calc_qid, *t.expect.calc_qids] if q}
    calc = {i.qid: i for i in calc_items(args.calc_golden)} if needed else {}
    if missing := needed - set(calc):
        raise SystemExit(f"calc_qid not in {[str(p) for p in args.calc_golden]}: {sorted(missing)[:5]}")
    cache: dict[str, dict] = {}
    cells_of = lambda key: cache.setdefault(key, calculation.read_cells(ROOT / "data" / "values" / f"{key}.parquet"))  # noqa: E731
    telemetry = metric_reader = None
    if args.oracle:
        records = {(s.sid, 0): grader.oracle_records(s, calc) for s in scenarios}
        source = "oracle (정답 호출을 그대로 낸 기록, 자기검증: 모든 비율 1.000이어야 함)"
    elif args.from_run:
        records = defaultdict(list)
        for line in (args.from_run / "results.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                records[(r["sid"], r.get("run", 0))].append(r)
        source = f"과거 실행 재채점 {args.from_run}"
    elif args.agent:
        telemetry = runner.tracing()
        meters, metric_reader = runner.metering()
        records = runner.run(runner.load_graph(args.agent), scenarios, args.repeat, telemetry, meters)
        source = f"`{args.agent}` 실행 ×{args.repeat} (턴 기록 = 실행 중 OTel span)"
    else:
        raise SystemExit("--layer agent needs --oracle, --from-run <run folder> or --agent module:function")
    by_sid = {s.sid: s for s in scenarios}
    rows = [{**row, "run": run_no}
            for (sid, run_no), recs in sorted(records.items()) if sid in by_sid
            for row in grader.grade_conversation(by_sid[sid], sorted(recs, key=lambda r: r.get("turn", 0)), calc, cells_of)]
    for r in rows:
        r.setdefault("latency_ms", 0.0)
    if telemetry:
        runner.link_verdicts(telemetry[0], rows)
    s = grader.aggregate(rows)
    tokens_note = []
    if metric_reader is not None:  # T3-31: the token metric must equal the span totals the ops read
        m = runner.metric_tokens(metric_reader)
        span_in = sum(r.get("input_tokens", 0) for recs in records.values() for r in recs)
        span_out = sum(r.get("output_tokens", 0) for recs in records.values() for r in recs)
        tokens_note = [f"- 토큰 대조 (span 합 / 메트릭 gen_ai.client.token.usage 합): 입력 {span_in} / {m['input']}, "
                       f"출력 {span_out} / {m['output']} → {'일치' if (span_in, span_out) == (m['input'], m['output']) else '불일치'}", ""]
    fmt = lambda v: "—" if v is None else f"{v:.3f}"  # noqa: E731
    count = lambda d: ", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "0"  # noqa: E731
    failed = [r for r in rows if not r["passed"]]
    report = "\n".join([
        f"# Agent (multi-turn · multi-query) — {args.stage}", "",
        f"- 기록: {source} · 시나리오 `{' + '.join(os.path.relpath(p.resolve(), ROOT) for p in paths(args.scenarios))}` split `{args.split}` · "
        f"시나리오 {s['n_scenarios']}, 대화 {s['n_conversations']}, 턴 {s['n_turns']}", "",
        *tokens_note,
        "| 지표 | 값 |", "| --- | --- |",
        f"| 대화 성공률 (모든 턴 통과) | {fmt(s['conversation_success'])} |",
        f"| pass^{s['repeats']} (반복 실행 모두 성공한 시나리오) | {fmt(s['pass^k'])} |",
        f"| 턴 통과율 | {fmt(s['turn_pass'])} |",
        f"| 툴 선택 정확도 (이름·순서 정확 일치) | {fmt(s['tool_selection'])} |",
        f"| 툴 순서 일치 (불필요 호출 허용) | {fmt(s['tool_order'])} |",
        f"| 빠진 호출 | {count(s['missing_calls'])} |",
        f"| 불필요 호출 | {count(s['extra_calls'])} |",
        f"| 파라미터 정확도 (필드 단위) | {fmt(s['param_accuracy'])} |",
        f"| 상태(표 키) 정확도 | {fmt(s['state_accuracy'])} ({s['n_state']}) |",
        f"| 최종 수치 정확도 (L3 PASS) | {fmt(s['numeric_accuracy'])} ({s['n_numeric']}) |",
        f"| 근거 없는 숫자가 있는 턴 | {s['unsupported_final_number_turns']} |", "",
        *task_table(s["by_task"]),
        "| 인자 필드 | 정확도 | n |", "| --- | --- | --- |",
        *[f"| {f} | {fmt(v['acc'])} | {v['n']} |" for f, v in s["param_by_field"].items()], "",
        "- 오류: " + (", ".join(f"{k} {v}" for k, v in sorted(s["errors"].items(), key=lambda kv: -kv[1])) or "없음"),
        "- 처음 실패한 턴 (0부터): " + (count(s["first_failed_turn"]) if s["first_failed_turn"] else "없음"), "",
        f"## 실패한 턴 ({len(failed)}, 50개까지)", "",
        *(["| 시나리오#실행 | 턴 | 오류 | 부른 툴 | 끝난 표 |", "| --- | --- | --- | --- | --- |",
           *[f"| {r['sid']}#{r['run']} | {r['turn']} | {', '.join(r['errors'])} | {' → '.join(c['name'] for c in r.get('calls') or []) or '-'} "
             f"| {r['state_key'] or '-'} |" for r in failed[:50]]] if failed else ["없음"]),
        "", "오류 뜻: " + " · ".join(f"`{k}` {v}" for k, v in grader.ERRORS.items()),
    ]) + "\n"
    return rows, s, report


def target(args, op: dict) -> str:
    return args.agent or ("oracle" if args.oracle else "from-run")
