"""L2: parse_query (rules, no LLM) against the query golden labels."""

from __future__ import annotations

import os
import time

from evals.schemas import ROOT, QueryItem, load_jsonl
from evals.tasks.common import add, load_sigungu


def run(args) -> tuple[list[dict], dict, str]:
    """L2: parse_query on every labelled question against query_golden.jsonl, with the 시군구 dictionary."""
    from kosis_rag.parse.node import parse_query

    from evals.evaluators import query

    items: list[QueryItem] = load_jsonl(args.query_golden, QueryItem)
    sigungu = load_sigungu()
    rows = []
    for item in items:
        if args.split != "all" and item.split != args.split:
            continue
        started = time.perf_counter()
        pred = parse_query(item.question, sigungu=sigungu).model_dump()
        rows.append({"qid": item.qid, "split": item.split, **query.score_query(item, pred), "pred": pred,
                     "latency_ms": (time.perf_counter() - started) * 1000})
    agg = query.aggregate(rows)
    fmt = lambda v: "—" if v is None else f"{v:.3f}"
    cols = (*query.FIELDS, "all_fields")
    lines = ["| split | n | " + " | ".join(cols) + " |", "|" + " --- |" * (len(cols) + 2)]
    for name, m in agg.items():
        lines.append(f"| {name} | {m['n']} | " + " | ".join(f"{fmt(m[c])} ({m['n_' + c]})" for c in cols) + " |")
    report = "\n".join([
        f"# L2 query understanding — {args.stage}", "",
        f"- `parse_query` (규칙, LLM 0) vs `{os.path.relpath(args.query_golden.resolve(), ROOT)}` · 괄호는 라벨이 있는 문항 수, — 는 라벨 없음", "",
        *lines, "",
        "- 목록 필드 P/R (§5, 미시 평균; P가 낮으면 지어낸 필터, R이 낮으면 놓친 조건): " + " · ".join(
            f"{f} P {fmt(agg['all'][f + '_p'])} R {fmt(agg['all'][f + '_r'])}" for f in query.PR_FIELDS),
        "- 빈 슬롯도 채점: 정답이 빈 필드에 값을 내면 오답. measures는 정답 개념이 예측 검색어 안에 있으면 정답.",
    ]) + "\n"
    return rows, {"metrics": agg}, report


def owners(rows: list[dict], summary: dict) -> dict[str, list[str]]:
    from evals.evaluators.query import FIELDS

    found: dict[str, list[str]] = {}
    for f in FIELDS:
        add(found, "data", f"parse_query {f} 오답", [r["qid"] for r in rows if r.get(f) == 0])
    return found


def target(args, op: dict) -> str:
    return "parse_query"
