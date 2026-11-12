"""Run one evaluation layer and save results.jsonl, manifest.json and report.md under evals/reports/<stamp>-<stage>/.

    python -m evals.run_eval --layer l1 --retriever bm25-catalog --stage T2-04-bm25 [--split test] [--k 10]
    python -m evals.run_eval --layer l1 --retriever oracle --stage selfcheck          # Hit@1 must be 100%
    python -m evals.run_eval --layer l3 --stage T1-27-regrade --from-run <run folder>  # re-grade, no model call
    python -m evals.run_eval --layer l3 --stage p2-l3 --interval 5                     # calls HCX per question
    python -m evals.run_eval --layer l3 --stage T2-31 --calc-golden evals/datasets/calculation_golden.jsonl evals/datasets/calculation_golden_diverse.jsonl
    python -m evals.run_eval --layer e2e --retriever bm25-record --stage p2-e2e --interval 5   # search top 1 -> L3
    python -m evals.run_eval --layer l0 --stage T2-28-l0                               # metadata fill rates, no search
    python -m evals.run_eval --layer l2 --stage T2-28-l2 --split dev                   # parse_query vs query_golden
    python -m evals.run_eval --layer ablation --stage T2-28-slots --split dev --slot-source pred [--retriever chroma]
    python -m evals.run_eval --layer l012 --stage T2-28-l012                           # L0 + L1 filter (gold, pred) + L2, dev
    python -m evals.run_eval --layer agent --stage T3-06-oracle --oracle --split all   # agent metrics self-check (1.000)
    python -m evals.run_eval --layer agent --stage T3-07-react --agent <module:function> --repeat 3   # live, pass^3

L1 runs every item (BM25 is fast) so the no-answer threshold tau is chosen on dev and applied to the split shown.
Every run writes results.jsonl, manifest.json, ops.json (latency, tokens, cost, tool failure rate), report.md and
report.html (charts, evals.report_html), and appends one aggregate line to evals/reports/ops_history.jsonl.
Run folders hold question text and table keys and stay out of git; ops_history holds aggregates only.
"""


from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

from kosis_agent.observability.tracing import SERVICE

from evals import ops, report_html, trace
from evals.schemas import AGENT_SCENARIOS, DATASETS, GOLDEN, QUERY_GOLDEN, ROOT, TABLES
from evals.tasks import ablation, agent, common, l0_metadata, l1_retrieval, l2_query, l3_calculation, l4_e2e, l012

# T3-29: one module per evaluation task, each with run(args) -> (rows, summary, report) and, where it has them,
# owners(rows, summary) for the "고칠 담당" section and target(args, op) for its ops_history line.
TASKS = {"l0": l0_metadata, "l1": l1_retrieval, "l2": l2_query, "ablation": ablation, "l012": l012, "l3": l3_calculation,
         "e2e": l4_e2e, "agent": agent}
REPORTS = ROOT / "evals" / "reports"
CALC_GOLDEN = DATASETS / "calculation_golden.jsonl"
POOL = DATASETS / "retrieval_pool.csv"
# T3-23: search settings as arguments (argument > shell > .env > default). Each sets the variable evals/config.py reads.
SEARCH_ARGS = {
    "chroma_host": ("CHROMA_HOST", "Chroma host (default localhost)"),
    "chroma_port": ("CHROMA_PORT", "Chroma port (default 8001)"),
    "collection": ("CHROMA_COLLECTION", "dense collection, e.g. kosis_a_bgeft_202611032104"),
    "embed_ckpt": ("EMBED_CKPT", "query embedder checkpoint .pt, or base (untrained bge-m3)"),
    "rerank_ckpt": ("RERANK_CKPT", "stage 1 re-ranker .pt, or base (untrained bge-reranker-v2-m3); presets without stage 1 ignore it"),
    "text_format": ("TEXT_FORMAT", "table text format the collection must hold (n2-L60)"),
    "rerank_mode": ("RERANK_MODE", "stage 1: blend | score"),
    "answer_k": ("ANSWER_K", "candidates handed to gate"),
    "hq_build": ("HQ_BUILD", "question index build id (T2-39)"),
    "hcx_thinking": ("HCX_THINKING", "stage 2 hcx: none | low"),
}


def apply_settings(args) -> Path | None:
    """Load .env (unless --no-env-file), then put the given search arguments into os.environ over it and the shell.
    Returns the .env file loaded, or None."""
    from kosis_agent.envfile import load_env_file

    loaded = None if args.no_env_file else load_env_file(args.env_file)
    for field, (name, _) in SEARCH_ARGS.items():
        if (value := getattr(args, field)) is not None:
            os.environ[name] = str(value)
    return loaded


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()



def calc_ref(paths: list[Path]) -> dict:
    """One golden ref for report_html: one file as is, several as joined paths and a hash over their hashes."""
    shas = [sha256(p) for p in paths]
    return {"path": " + ".join(os.path.relpath(p.resolve(), ROOT) for p in paths),
            "sha256": shas[0] if len(shas) == 1 else hashlib.sha256("".join(shas).encode()).hexdigest(),
            **({"files": dict(zip((p.name for p in paths), shas))} if len(paths) > 1 else {})}


def owner_section(layer: str, rows: list[dict], summary: dict) -> str:
    """'Who fixes it' for one run: the task's owners() lines (L0 / L2 data side, L1 search, L3 / L4 summarize_stat)."""
    task = TASKS[layer]
    return common.owner_section(task.owners(rows, summary)) if hasattr(task, "owners") else ""


def main(argv: list[str] | None = None) -> Path:
    """Run one layer; returns the run folder (evals.suite reads it)."""
    parser = argparse.ArgumentParser(description="Run one evaluation layer (L1 retrieval, L3 calculation, e2e).")
    parser.add_argument("--layer", choices=("l0", "l1", "l2", "ablation", "l012", "l3", "e2e", "agent"), required=True)  # e2e: first part of L4
    parser.add_argument("--stage", required=True, help="what this run measures, e.g. T2-04-bm25")
    parser.add_argument("--split", choices=("dev", "test", "all"), help="default: dev for l012 (the slot baselines), test otherwise")
    parser.add_argument("--retriever", default="bm25-catalog", help="l1, e2e: oracle (l1 only) | bm25-catalog | bm25-record | chroma")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--tau", type=float, help="l1: fixed no-answer threshold (default: best F1 on dev)")
    parser.add_argument("--from-run", type=Path, help="l1, l3, agent: grade a past run folder instead of searching / calling the model (l1 reuses its retriever, k and tau)")
    parser.add_argument("--slot-source", choices=("gold", "pred"), default="pred", help="ablation: query_golden labels or parse_query")
    parser.add_argument("--slots", default="year,cycle,region,dimension,measure", help="ablation: slots to switch")
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="l0, l1, ablation, l012, e2e (§6.3 table slots by qid): retrieval golden (v2: retrieval_golden_v2.jsonl)")
    parser.add_argument("--query-golden", type=Path, default=QUERY_GOLDEN, help="l2, ablation, l012, e2e (§6.3 연도·지역 by qid): query golden (v2: query_golden_v2.jsonl)")
    parser.add_argument("--pool", type=Path, default=POOL, help="l1: judged pool for unjudged@k (v2: retrieval_pool_v2.csv)")
    parser.add_argument("--calc-golden", type=Path, nargs="+", default=[CALC_GOLDEN],
                        help="l3, e2e: golden files, e.g. calculation_golden.jsonl calculation_golden_diverse.jsonl")
    parser.add_argument("--interval", type=float, default=0.0, help="l3, e2e: seconds between model calls")
    parser.add_argument("--llm", choices=("hcx", "mock"), default="hcx",
                        help="l3, e2e: hcx (HCX_API_KEY, costs) | mock (no call: checks the path, scores are not scores; not in ops_history)")
    parser.add_argument("--scenarios", type=Path, nargs="+", default=[AGENT_SCENARIOS],
                        help="agent: scenario files (T3-05), several run as one (sids must not repeat), e.g. multiturn + multiquery")
    parser.add_argument("--agent", help="agent, l3, e2e: module:function returning a compiled graph, run live "
                        "(l3 default: evals.runners.multiturn_dev:build with the oracle search; e2e default: backend.app.main:build_agent)")
    parser.add_argument("--oracle", action="store_true", help="agent: grade the perfect run (self-check, all 1.000)")
    parser.add_argument("--repeat", type=int, default=1, help="agent: runs per scenario for pass^k")
    settings = parser.add_argument_group("settings (argument > shell > .env > default)")
    settings.add_argument("--env-file", type=Path, default=None, help="default: <repo>/.env (skipped when KOSIS_DOTENV=off)")
    settings.add_argument("--no-env-file", action="store_true", help="do not read any .env")
    for field, (name, what) in SEARCH_ARGS.items():
        settings.add_argument("--" + field.replace("_", "-"), type=int if field in ("chroma_port", "answer_k") else str,
                              help=f"{name}: {what}")
    args = parser.parse_args(argv)
    apply_settings(args)
    args.split = args.split or ("dev" if args.layer == "l012" else "test")

    args.telemetry = None  # set by a task that ran live (l1 / l3 / e2e: evals.runners.agent.tracing)
    rows, summary, report = TASKS[args.layer].run(args)
    # T3-32: spans went out live (one service kosis-agent, environment eval) when the OTLP endpoint is set
    live = args.telemetry is not None or (args.layer == "agent" and bool(args.agent))
    exported = live and bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    if args.layer in ("l3", "e2e"):  # only these rows carry verdict/spans for trace.blame
        for r in rows:
            r["failure"] = trace.blame(r)
        summary["failures"] = dict(Counter(r["failure"]["stage"] for r in rows if r["failure"]))
        report += common.failure_section(rows, exported)
    elif "search_failures" in summary:  # L1 over the LangGraph search
        report += common.search_failure_section(rows, exported)
    if args.telemetry is not None:  # the verdict is known only now: link it to each live trace
        from evals.runners.agent import link_questions

        link_questions(args.telemetry[0], rows, args.layer)
    if owners := owner_section(args.layer, rows, summary):  # right under the title: the first thing a teammate reads
        head, _, rest = report.partition("\n\n")
        report = f"{head}\n\n{owners}{rest}"
    if args.layer == "l012":  # per layer: L0 and filter-only rows carry latency 0.0 and would bury the L2 timings
        op = {name: ops.summarize([r for r in rows if r["layer"] == name]) for name in summary}
        report += "".join(common.ops_section(o, f"운영 지표 — {name}") for name, o in op.items())
    else:
        op = ops.summarize(rows)
        report += common.ops_section(op)
    live_llm = args.layer in ("l3", "e2e") and not args.from_run  # runs that call summarize_stat now
    run_id = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{args.stage}"
    out = REPORTS / run_id
    out.mkdir(parents=True)
    with (out / "results.jsonl").open("w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows)
    ref = lambda path: {"path": os.path.relpath(path.resolve(), ROOT), "sha256": sha256(path)}  # noqa: E731
    golden = {"l2": args.query_golden}.get(args.layer, args.golden)
    slotted = args.layer in ("ablation", "l012")
    manifest = {
        "run_id": run_id, "layer": args.layer, "stage": args.stage, "split": args.split,
        "golden": {"l0·l1": ref(args.golden), "l2": ref(args.query_golden)} if args.layer == "l012"
        else calc_ref(args.calc_golden) if args.layer in ("l3", "e2e") else calc_ref(args.scenarios) if args.layer == "agent"
        else ref(golden),
        "source_snapshot": {"tables.jsonl": sha256(TABLES)},
        "git": git("rev-parse", "HEAD"), "git_dirty": bool(git("status", "--porcelain")),
        "vendor_phase1": git("-C", "vendor/phase1", "rev-parse", "HEAD"),
        # phase 2 too: its search code and stage 2 prompt (e.g. v3 -> v4 at 3f76487) move L1 / L2 / e2e numbers
        "vendor_phase2": git("-C", "vendor/phase2", "rev-parse", "HEAD"),
        "retriever": args.retriever if args.layer in ("l1", "ablation", "l012") else None,
        "top_k": args.k if args.layer in ("l1", "ablation", "l012") else None,
        "slot_source": "gold,pred" if args.layer == "l012" else args.slot_source if slotted else None,
        "slots": args.slots if slotted else None,
        "from_run": str(args.from_run) if args.from_run else None,
        "summary": summary,
        "ops": op,
        "agent": (args.agent or ("oracle" if args.oracle else "from-run")) if args.layer == "agent"
        else args.agent if args.layer in ("l3", "e2e") else None,
        "llm": args.llm if live_llm else None,
        "jaeger": {"ui": trace.JAEGER_UI, "service": SERVICE, "environment": "eval", "layer": args.layer} if exported else None,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (out / "ops.json").write_text(json.dumps(op, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.md").write_text(report, encoding="utf-8")
    if args.llm == "mock" and live_llm:
        pass  # a mock run checks the path; its numbers stay out of the committed history
    elif args.layer == "l1" and args.from_run:
        pass  # a regrade copies the past run's latencies: they are already in the history once
    elif args.layer == "l012":
        for name, o in op.items():
            ops.append_history(run_id, name, args.stage, {"l0": "registry", "l2": "parse_query"}.get(name, args.retriever), o)
    else:
        ops.append_history(run_id, args.layer, args.stage, TASKS[args.layer].target(args, op), op)
    report_html.write(out)  # every layer (T3-20); l0 / l2 / ablation show report.md as HTML, dedicated charts when they are presented
    print(report)
    print(f"-> {out}")
    return out


if __name__ == "__main__":
    main()
