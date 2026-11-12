"""L3: the agent graph with the golden table given (oracle search), graded against the table's Parquet cells.

T3-32 (option C): each golden question is one turn of the agent graph (evals.runners.multiturn_dev:build with the
items' own question -> table map as its search; --agent swaps the graph), live-traced like the service. Its phase 1
call is the summarize_stat tool the service calls. --from-run re-grades a past run's answers with no model call."""

from __future__ import annotations

from evals.schemas import CalcItem
from evals.tasks.common import (MOCK_NOTE, answer_owners, calc_items, grade, single_turn_rows, summarize_stat_target,
                                verdict_table)


def run(args) -> tuple[list[dict], dict, str]:
    from evals.runners import multiturn_dev
    from evals.runners import stat as runner

    items: list[CalcItem] = calc_items(args.calc_golden)
    oracle = {i.question: i.table_key for i in items}
    answers = (runner.read_run(args.from_run, items) if args.from_run
               else single_turn_rows(args, "l3", items, lambda: multiturn_dev.build(oracle)))
    rows = grade(items, answers)
    verdicts, errors, lines = verdict_table(rows)
    summary = {"verdicts": verdicts, "errors": dict(errors)}
    report = "\n".join([
        f"# L3 calculation — {args.stage}", "",
        f"- 답: {'과거 실행 재채점 ' + str(args.from_run) if args.from_run else '에이전트 그래프 한 턴 (`' + (args.agent or 'evals.runners.multiturn_dev:build') + '`)'} · 정답 표 고정(oracle 검색)"
        + (MOCK_NOTE if args.llm == "mock" and not args.from_run else ""), "",
        *lines, "",
        "- 오류 유형: " + (", ".join(f"{k} {v}" for k, v in errors.most_common()) or "없음"),
    ]) + "\n"
    return rows, summary, report


def owners(rows: list[dict], summary: dict) -> dict[str, list[str]]:
    return answer_owners(rows, e2e=False)


def target(args, op: dict) -> str:
    return summarize_stat_target(op)
