"""L4 end to end (single turn): the service's agent graph answers each golden question, graded like L3.

T3-32 (option C): the graph is backend.app.main:build_agent (search settings from the shell, as the service; --agent
swaps it), one new conversation per question, live-traced like the service. The table is the one the graph handed to
phase 1 (search picked[0]); a search gate refusal is graded as a refusal. --retriever presets no longer apply here."""

from __future__ import annotations

from collections import Counter

from evals.schemas import CalcItem, QueryItem, load_items, load_jsonl
from evals.tasks.common import (MOCK_NOTE, answer_owners, calc_items, grade, pct, single_turn_rows, summarize_stat_target,
                                verdict_table)

SERVICE_GRAPH = "backend.app.main:build_agent"


def run(args) -> tuple[list[dict], dict, str]:
    """L4 end to end: the agent graph picks the table and answers, then the same grader as L3."""
    from evals.runners import e2e
    from evals.runners.agent import load_graph

    items: list[CalcItem] = calc_items(args.calc_golden)
    # §6.3: the L1 slots judge the table and the L2 labels the 연도·지역, linked by qid (golden v2 shares qids)
    l1 = {i.qid: i for i in load_items(args.golden)} if args.golden.exists() else {}
    l2 = {q.qid: q for q in load_jsonl(args.query_golden, QueryItem)} if args.query_golden.exists() else {}
    raw = single_turn_rows(args, "e2e", items, lambda: load_graph(SERVICE_GRAPH))
    rows = [{**g, **e2e.locate(item, a, g["verdict"], l1.get(item.qid)), **e2e.template_slots(a["answer"], l2.get(item.qid)),
             "error": a["error"], "no_period_item": "no_period" in item.allowed_reasons}
            for item, a, g in zip(items, raw, grade(items, raw))]
    verdicts, errors, lines = verdict_table(rows)
    right = [r for r in rows if r["right_table"]]
    split = {"right": dict(Counter(r["verdict"] for r in right)),
             "wrong": dict(Counter(r["verdict"] for r in rows if not r["right_table"]))}
    causes = dict(Counter(r["cause"] for r in rows))
    rate = lambda key: (lambda v: (sum(v), len(v)))([r[key] for r in rows if r[key] is not None])  # noqa: E731
    no_period = [r for r in rows if r["no_period_item"]]
    template = {"year": rate("year_ok"), "region": rate("region_ok"), "l1_linked": sum(r["l1_linked"] for r in rows),
                "right_alternate": sum(r["right_alternate"] for r in rows),
                "no_period": (sum(r["outcome"] == "refused" for r in no_period), len(no_period)),
                "no_period_reason": (sum(r["outcome"] == "refused" and "wrong_reason" not in r["errors"] for r in no_period), len(no_period))}
    summary = {"verdicts": verdicts, "errors": dict(errors), "top1_right": len(right), "by_top1": split, "causes": causes,
               "template": template}
    n = len(rows) or 1
    report = "\n".join([
        f"# L4 end to end — {args.stage}", "",
        f"- 에이전트 그래프 한 턴 `{args.agent or SERVICE_GRAPH}` (검색 설정 = 서비스와 같은 환경변수) · 그래프가 실전 1에 넘긴 표로 채점 · L3와 같은 채점기(정답 표 기준)"
        + (MOCK_NOTE if args.llm == "mock" else ""), "",
        *lines, "",
        f"- 검색 1위가 정답 표: {len(right)}/{len(rows)} ({len(right) / n:.1%})",
        f"  - 맞힌 표에서 PASS {split['right'].get('PASS', 0)}/{len(right)} · 틀린 표에서 PASS {split['wrong'].get('PASS', 0)}/{len(rows) - len(right)}",
        "- 실패 원인: " + ", ".join(f"{k} {causes.get(k, 0)}" for k in ("pass", "retrieval", "answer"))
        + " (retrieval = 1위가 다른 표, answer = 표는 맞았는데 답이 틀림)",
        f"- 정답 표 판정 (§6.3): L1 정답 슬롯과 연결된 문항 {template['l1_linked']}/{len(rows)}, 그중 대체 표로 맞힘 {template['right_alternate']}"
        " (연결 안 된 문항은 L3 대표 표만 정답)",
        f"- 템플릿 {{연도}} 일치 {pct(*template['year'])} · {{지역}} 일치 {pct(*template['region'])} (L2 정답 라벨과 같은 qid, 답한 문항만)",
        f"- no_period 문항 (§4.3): 거절 {pct(*template['no_period'])} · 사유까지 맞음 {pct(*template['no_period_reason'])}",
        "- 오류 유형: " + (", ".join(f"{k} {v}" for k, v in errors.most_common()) or "없음"),
    ]) + "\n"
    return rows, summary, report


def owners(rows: list[dict], summary: dict) -> dict[str, list[str]]:
    return answer_owners(rows, e2e=True)


def target(args, op: dict) -> str:
    return f"{args.agent or SERVICE_GRAPH}+{summarize_stat_target(op)}"
