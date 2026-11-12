"""L3: summarize_stat with the golden table given (oracle table), graded against the table's Parquet cells."""

from __future__ import annotations

from evals.schemas import CalcItem
from evals.tasks.common import MOCK_NOTE, answer_owners, calc_items, grade, summarize_stat_target, verdict_table


def run(args) -> tuple[list[dict], dict, str]:
    from evals.runners import stat as runner

    items: list[CalcItem] = calc_items(args.calc_golden)
    answers = runner.read_run(args.from_run, items) if args.from_run else runner.run(items, args.interval, args.llm)
    rows = grade(items, answers)
    verdicts, errors, lines = verdict_table(rows)
    summary = {"verdicts": verdicts, "errors": dict(errors)}
    report = "\n".join([
        f"# L3 calculation — {args.stage}", "",
        f"- 답: {'과거 실행 재채점 ' + str(args.from_run) if args.from_run else 'summarize_stat 호출'} · 정답 표 고정(oracle)"
        + (MOCK_NOTE if args.llm == "mock" and not args.from_run else ""), "",
        *lines, "",
        "- 오류 유형: " + (", ".join(f"{k} {v}" for k, v in errors.most_common()) or "없음"),
    ]) + "\n"
    return rows, summary, report


def owners(rows: list[dict], summary: dict) -> dict[str, list[str]]:
    return answer_owners(rows, e2e=False)


def target(args, op: dict) -> str:
    return summarize_stat_target(op)
