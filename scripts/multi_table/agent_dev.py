"""T3-51: run phase 1's multi-table dev set (27 questions, T1-30) through the agent graph and grade every stage.

    python -m scripts.multi_table.agent_dev --stage T3-51-v4            # STAGE2_PROMPT unset or v4 in .env: baseline
    python -m scripts.multi_table.agent_dev --stage T3-51-v4m           # STAGE2_PROMPT=v4m in .env
    python -m scripts.multi_table.agent_dev --stage T3-51-v4m --only M01,M02 --interval 5

The graph is the e2e one (default scripts.answerability.e2e_condyears:build: the service graph with cond_scope=years),
so search, stage 2, phase 1 and their settings come from .env as in an e2e run. One fresh thread per question.

Per question (vendor/phase1/eval/dev/multi_table_dev.jsonl, table_keys = the tables the answer needs):
- complete@2  every gold table is in stage 2's picked top 2 (the ceiling: the agent can only pass picked tables)
- together    stage 2's together verdict (rerank2 span stage2_together) is the gold set
- multi_call  the graph asked phase 1 with those tables at once (attempts[0].key == "A+B")
- pass        phase 1's own grader (eval.multi_dev.grade) on the turn's final answer: cells and final value,
              or the refusal for refuse rows
Results: evals/reports/<time>-<stage>-multi/results.jsonl and summary.json (gitignored, like run_eval folders).
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from kosis_agent.envfile import load_env_file

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "vendor" / "phase1" / "eval" / "dev" / "multi_table_dev.jsonl"


def build(spec: str):
    module, _, func = spec.partition(":")
    return getattr(importlib.import_module(module), func)()


def rate(values: list[bool]) -> str:
    return f"{sum(values)}/{len(values)}" if values else "0/0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="multi-table dev set through the agent (T3-51)")
    parser.add_argument("--stage", required=True, help="run name, e.g. T3-51-v4m")
    parser.add_argument("--agent", default="scripts.answerability.e2e_condyears:build", help="module:function -> graph")
    parser.add_argument("--dev", type=Path, default=DEV)
    parser.add_argument("--only", default="", help="comma-separated qids")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between questions (HCX 429)")
    args = parser.parse_args(argv)

    load_env_file()
    from eval.multi_dev import grade, load  # phase 1's grader (vendor/phase1 on PYTHONPATH)
    from kosis_agent.retrieval.together import together_keys

    rows = load(args.dev, {q.strip() for q in args.only.split(",") if q.strip()} or None)
    graph = build(args.agent)
    out_dir = ROOT / "evals" / "reports" / f"{datetime.now():%Y%m%d-%H%M%S}-{args.stage}-multi"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for n, row in enumerate(rows):
        if n and args.interval:
            time.sleep(args.interval)
        gold = list(row["table_keys"])
        started = time.perf_counter()
        record = {"qid": row["qid"], "relation": row["relation"], "kind": row["kind"], "gold": gold}
        try:
            state = graph.invoke({"messages": [("user", row["question"])]},
                                 config={"configurable": {"thread_id": f"{args.stage}#{row['qid']}"}})
            search = state.get("search")
            picked = [c.key for c in search.picked] if search is not None else []
            together = together_keys(search)
            attempts = state.get("attempts") or []
            stat = state.get("stat")
            answer = stat.answer if stat is not None else None
            ok, why = grade(row, answer) if answer is not None else (False, f"no phase 1 answer ({state.get('outcome')})")
            record.update(
                route=search.route if search is not None else None, picked=picked, together=together,
                complete2=set(gold) <= set(picked[:2]), together_ok=set(together) == set(gold) and len(gold) >= 2,
                multi_call=bool(attempts) and attempts[0]["key"] == "+".join(together) and bool(together),
                attempts=[a["key"] for a in attempts], outcome=state.get("outcome"), pass_=ok, why=why,
                text=answer.text if answer is not None else None,
                facts=[f.value for f in answer.result.facts] if answer is not None and answer.result else [],
                sources=[s.tbl_id for s in answer.sources] if answer is not None else [])
        except Exception as exc:  # noqa: BLE001 - one failed question is a failed row, not a stopped run
            record.update(pass_=False, why=f"error: {type(exc).__name__}: {exc}"[:500])
        record["seconds"] = round(time.perf_counter() - started, 2)
        results.append(record)
        print(f"{record['qid']:4} {'PASS' if record.get('pass_') else 'FAIL'}  together={record.get('together')}  "
              f"{record['why']}", file=sys.stderr)

    answer_rows = [r for r in results if r["kind"] == "answer"]
    by_relation: dict[str, Counter] = {}
    for r in results:
        by_relation.setdefault(r["relation"], Counter())[bool(r.get("pass_"))] += 1
    summary = {
        "stage": args.stage, "agent": args.agent, "dev": str(args.dev), "questions": len(results),
        "pass": rate([bool(r.get("pass_")) for r in results]),
        "answer_pass": rate([bool(r.get("pass_")) for r in answer_rows]),
        "complete@2 (answer rows)": rate([bool(r.get("complete2")) for r in answer_rows]),
        "together = gold (answer rows)": rate([bool(r.get("together_ok")) for r in answer_rows]),
        "multi call (answer rows)": rate([bool(r.get("multi_call")) for r in answer_rows]),
        "errors": sum(str(r.get("why", "")).startswith("error") for r in results),
        "by_relation": {k: f"{c[True]}/{c[True] + c[False]}" for k, c in by_relation.items()},
    }
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"-> {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
