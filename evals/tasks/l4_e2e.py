"""L4 end to end (single turn): the service's agent graph answers each golden question, graded like L3.

T3-32 (option C): the graph is backend.app.main:build_agent (search settings from the shell, as the service; --agent
swaps it), one new conversation per question, live-traced like the service. The table is the one the graph handed to
phase 1 (search picked[0]); a search gate refusal is graded as a refusal. --retriever presets no longer apply here."""

from __future__ import annotations

import json
from collections import Counter

from evals.evaluators import alternates
from evals.schemas import TABLES, CalcItem, QueryItem, RetrievalItem, load_items, load_jsonl
from evals.trace import node_ranks
from evals.tasks.common import (MOCK_NOTE, answer_owners, calc_items, grade, pct, single_turn_rows, summarize_stat_target,
                                verdict_table)

SERVICE_GRAPH = "backend.app.main:build_agent"


def l1_index(items: list[RetrievalItem]) -> dict[str, RetrievalItem]:
    """Retrieval golden items by qid, by phase1_id and by question text, so a calculation item finds the L1 item
    of the same question whichever golden version it comes from (v2 shares qids; v1 links by phase1_id / text)."""
    out: dict[str, RetrievalItem] = {}
    for i in items:
        out.setdefault(i.question, i)
        if i.phase1_id:
            out.setdefault(i.phase1_id, i)
    for i in items:
        out[i.qid] = i
    return out


def l1_item(index: dict[str, RetrievalItem], item: CalcItem) -> RetrievalItem | None:
    return index.get(item.qid) or index.get(item.question)


def survey_names(path=TABLES) -> dict[str, str]:
    """Table key -> survey name (data/tables.jsonl)."""
    if not path.exists():
        return {}
    return {(i := json.loads(line)["info"])["key"]: i["survey_name"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def by_survey(rows: list[dict], names: dict[str, str]) -> dict[str, list[int]]:
    """T3-46: [PASS, n] per survey of the golden table, most questions first."""
    out: dict[str, list[int]] = {}
    for r in rows:
        c = out.setdefault(names.get(r["table_key"], "?"), [0, 0])
        c[0] += r["verdict"] == "PASS"
        c[1] += 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1][1]))


def scenario_run(args) -> tuple[list[dict], dict, str]:
    """T3-46 (option C): e2e over the e2e golden scenarios (evals/builders/e2e_golden.py) — the agent layer's runner
    and grader (tools, state, numbers, answers) on the service graph, plus each turn's labels from the graph state
    (evals.runners.agent records them) against the gold labels. --oracle: predictions = gold (self-check 1.000)."""
    from evals.evaluators import labels as lab
    from evals.tasks import agent as agent_task
    from evals.tasks.rules import parser

    args.agent = args.agent or (None if args.oracle or args.from_run else SERVICE_GRAPH)
    rows, summary, report = agent_task.run(args)
    gold = {(s.sid, i): t.expect.labels for s in agent_task.load_scenarios(args.scenarios) for i, t in enumerate(s.turns)}
    parse = parser()
    for r in rows:
        want = gold.get((r["sid"], r["turn"]))
        got = want if args.oracle else (lab.TurnLabels.model_validate(r["labels"]) if r.get("labels") else None)
        r["gold_labels"] = want.model_dump(exclude_none=True) if want else {}
        r["pred_labels"] = got.model_dump(exclude_none=True) if got else {}
        r["label_hits"] = lab.grade(want, got, parse)
    summary["labels"] = lab.summarize(rows)
    report = report.replace("# Agent (multi-turn · multi-query)", "# L4 end to end (e2e 골든 시나리오, T3-46)", 1)
    report += "\n".join(["", "## 분류 라벨 (그래프 상태 vs 정답 라벨)", "",
                         "- 그래프 상태에서 읽은 라벨: reading·resolved_question(연도·시도)·sub_questions·op·outcome·"
                         "refusal_reasons·default_applied·qtype(실전 1 analyze.rules span)·formula(답 fact 계산식)·"
                         "chart_request. judge_rule은 상태에 없어 `--layer rules`(오프라인)에서만 잰다.", "",
                         *lab.report_lines(summary["labels"]), ""])
    return rows, summary, report


def run(args) -> tuple[list[dict], dict, str]:
    """L4 end to end: the agent graph picks the table and answers, then the same grader as L3. --from-run regrades
    a past e2e run's saved answers and rankings (no model, no search). Without --calc-golden: scenario_run (T3-46)."""
    if getattr(args, "e2e_scenarios", False):
        return scenario_run(args)
    from evals.runners import e2e
    from evals.runners.agent import load_graph
    from evals.runners.stat import read_rows

    items: list[CalcItem] = calc_items(args.calc_golden)
    # §6.3: the L1 slots judge the table and the L2 labels the 연도·지역, linked by qid (golden v2 shares qids),
    # by phase1_id or by the question text (golden v1: the phase 1 questions the retrieval golden took over, T3-39)
    l1 = l1_index(load_items(args.golden)) if args.golden.exists() else {}
    l2 = {q.qid: q for q in load_jsonl(args.query_golden, QueryItem)} if args.query_golden.exists() else {}
    raw = (read_rows(args.from_run, items) if args.from_run
           else single_turn_rows(args, "e2e", items, lambda: load_graph(SERVICE_GRAPH)))
    alts = alternates.load()  # T3-41: tables holding every golden cell value count as right too
    rows = [{**g, **e2e.locate(item, a, g["verdict"], l1_item(l1, item), alts.get(item.qid, set())),
             **e2e.template_slots(a["answer"], l2.get(item.qid)),
             "error": a["error"], "keys": a["keys"], "no_period_item": "no_period" in item.allowed_reasons}
            for item, a, g in zip(items, raw, grade(items, raw))]
    verdicts, errors, lines = verdict_table(rows)
    right = [r for r in rows if r["right_table"]]
    split = {"right": dict(Counter(r["verdict"] for r in right)),
             "wrong": dict(Counter(r["verdict"] for r in rows if not r["right_table"]))}
    causes = dict(Counter(r["cause"] for r in rows))
    rate = lambda key: (lambda v: (sum(v), len(v)))([r[key] for r in rows if r[key] is not None])  # noqa: E731
    no_period = [r for r in rows if r["no_period_item"]]
    template = {"year": rate("year_ok"), "region": rate("region_ok"), "l1_linked": sum(r["l1_linked"] for r in rows),
                "alt_linked": sum(r["alt_linked"] for r in rows), "linked": sum(r["l1_linked"] or r["alt_linked"] for r in rows),
                "graded_alternate": sum(r["graded_table"] != r["table_key"] for r in rows),
                "right_alternate": sum(r["right_alternate"] for r in rows),
                "no_period": (sum(r["outcome"] == "refused" for r in no_period), len(no_period)),
                "no_period_reason": (sum(r["outcome"] == "refused" and "wrong_reason" not in r["errors"] for r in no_period), len(no_period))}
    # T3-45: an error tag on a row answered from another table mostly says "other table" (its cells against the golden)
    errors_by_top1 = {side: dict(Counter(e for r in rows if r["right_table"] is (side == "right") for e in r["errors"]))
                      for side in ("right", "wrong")}
    k = max((len(r["keys"]) for r in rows), default=5)
    nodes = node_ranks(rows, k)
    restored = sum(bool(a.get("search_restored")) for a in raw)
    surveys = by_survey(rows, survey_names())
    summary = {"by_survey": surveys, "verdicts": verdicts, "errors": dict(errors), "top1_right": len(right), "by_top1": split, "causes": causes,
               "template": template, "errors_by_top1": errors_by_top1, "search_nodes": nodes, "search_k": k,
               "search_restored": restored}
    n = len(rows) or 1
    report = "\n".join([
        f"# L4 end to end — {args.stage}", "",
        (f"- 과거 실행 재채점 `{args.from_run}` (검색·모델 호출 없음)" if args.from_run else
         f"- 에이전트 그래프 한 턴 `{args.agent or SERVICE_GRAPH}` (검색 설정 = 서비스와 같은 환경변수)")
        + " · 그래프가 실전 1에 넘긴 표로 채점 · L3와 같은 채점기(정답 표 기준)"
        + (MOCK_NOTE if args.llm == "mock" and not args.from_run else ""), "",
        *lines, "",
        f"- 검색 1위가 정답 표: {len(right)}/{len(rows)} ({len(right) / n:.1%})",
        f"  - 맞힌 표에서 PASS {split['right'].get('PASS', 0)}/{len(right)} · 틀린 표에서 PASS {split['wrong'].get('PASS', 0)}/{len(rows) - len(right)}",
        "- 실패 원인: " + ", ".join(f"{k} {causes.get(k, 0)}" for k in ("pass", "retrieval", "answer"))
        + " (retrieval = 1위가 다른 표, answer = 표는 맞았는데 답이 틀림)",
        f"- 정답 표 판정 (§6.3): 대체 표를 아는 문항 {template['linked']}/{len(rows)} (L1 정답 슬롯 {template['l1_linked']}, "
        f"값 일치 대체 표 라벨 {template['alt_linked']}, T3-41), 그중 대체 표로 맞힘 {template['right_alternate']}"
        " (모르는 문항은 L3 대표 표만 정답)",
        f"- 대체 표 셀로 숫자 채점 (T3-41): {template['graded_alternate']}문항 — 답이 쓴 표가 골든 값을 모두 같은 기간·지역으로 가진 표",
        f"- 템플릿 {{연도}} 일치 {pct(*template['year'])} · {{지역}} 일치 {pct(*template['region'])} (L2 정답 라벨과 같은 qid, 답한 문항만)",
        f"- no_period 문항 (§4.3): 거절 {pct(*template['no_period'])} · 사유까지 맞음 {pct(*template['no_period_reason'])}",
        "- 오류 유형: " + (", ".join(f"{k} {v}" for k, v in errors.most_common()) or "없음"),
        *(f"  - {'맞힌' if side == 'right' else '틀린'} 표에서: "
          + (", ".join(f"{e} {c}" for e, c in sorted(tags.items(), key=lambda kv: -kv[1])) or "없음")
          for side, tags in errors_by_top1.items()),
        "- 검색 노드별 정답 표 순위 (답할 문항, T3-45): " + (" → ".join(
            f"{name} 1위 {c['top1']}/{c['n']} · top{k} {c['topk']} · 후보 밖 {c['missing']}" for name, c in nodes.items()) or "노드 span 없음"),
        "- 조사별 PASS (T3-46, 정답 표의 조사): " + " · ".join(f"{name} {pct(*c)}" for name, c in surveys.items()),
        *([f"- 실행 중 예외 뒤 검색 후보 복원 (T3-45): {restored}문항 — 검색 노드 span에서 keys·used_table"] if restored else []),
    ]) + "\n"
    return rows, summary, report


def owners(rows: list[dict], summary: dict) -> dict[str, list[str]]:
    if "labels" in summary:  # scenario rows have no L3 verdict to blame
        return {}
    return answer_owners(rows, e2e=True)


def target(args, op: dict) -> str:
    if getattr(args, "e2e_scenarios", False):
        return f"{args.agent or ('oracle' if args.oracle else 'from-run')}+e2e_golden"
    return f"{args.agent or SERVICE_GRAPH}+{summarize_stat_target(op)}"
