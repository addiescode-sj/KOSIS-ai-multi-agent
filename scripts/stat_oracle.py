"""Phase 1 reproduction through the phase 3 agent graph: golden questions with their own table key -> a graded run.

    set PYTHONPATH=.;src;vendor\\phase2\\src;vendor\\phase1;vendor\\phase1\\src
    python scripts\\stat_oracle.py --stage t3-oracle-mock --provider mock
    python scripts\\stat_oracle.py --stage t3-oracle-hcx --interval 5          (HCX_API_KEY in the shell)
    cd vendor\\phase1 && set KOSIS_WORKSPACE=<this repo> && python -m eval.grade_run --run <run folder>

Each golden question goes through kosis_agent.graph.main_graph.build_graph with an oracle search in place of phase 2: the
search returns `found` with the golden table's tables.csv key (Path(item.table).stem), so stat_agent calls
phase 1 summarize_stat_run exactly as it does after a real search (01-reproduce-baselines.md W4). Here a difference
from the phase 1 baseline run is the tool wiring (key, LLMConfig, result), not the search.

The run folder (manifest.json, golden.jsonl, results.jsonl) is the one phase 1 eval.grade_run and eval.parity read.
results rows carry stat_answer, chart (ChartSpec with its Vega-Lite) and decision_metadata.spans. The public API
does not return decision_metadata.route or judge, and run_eval's golden snapshot, answer format, judge mode, form and
golden scope do not apply here, so `eval.parity --mode semantic` against a run_eval run takes
`--ignore route --ignore judge --allow-condition golden_snapshot_sha256 --allow-condition answer_format
--allow-condition judge_mode --allow-condition form --allow-condition scope_mode` (scope only feeds the fallback chatbot).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "vendor" / "phase1" / "src", ROOT / "vendor" / "phase1", ROOT / "vendor" / "phase2" / "src",
           ROOT / "src", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("KOSIS_WORKSPACE", str(ROOT))


class OracleSearch:
    """Stands in for kosis_rag.api.SearchModule: `found` with one given table key, no search."""

    def __init__(self) -> None:
        self.key: str | None = None

    def stream(self, inp):
        from kosis_rag.schemas.io import SearchOutput, TableCandidate

        org_id, _, tbl_id = self.key.partition("_")
        table = TableCandidate(key=self.key, org_id=org_id, tbl_id=tbl_id, table_name="", survey_name="",
                               organization="", period="", url="")
        yield "output", SearchOutput(question=inp.question, route="found", picked=[table], candidates=[table])


def _copy_golden(src: Path, dst: Path, keep: set[str] | None) -> None:
    """The golden lines the run asked, as one .jsonl (a byte copy when it is one file and every question ran)."""
    files = sorted(src.glob("*.jsonl")) if src.is_dir() else [src]
    if keep is None and len(files) == 1:
        shutil.copyfile(files[0], dst)
        return
    lines = [line for f in files for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    lines = [line for line in lines if keep is None or json.loads(line)["id"] in keep]
    dst.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def run(golden: Path, stage: str, llm, interval: float = 0.0, limit: int | None = None,
        out_root: Path | None = None, summarize=None) -> Path:
    """Answer every golden question once through the agent graph; returns the run folder.

    llm = phase 1 LLMConfig; summarize = phase 1 summarize_stat_run (a fake in tests).
    """
    from eval.golden import load_items
    from kosis_chat import api
    from kosis_chat.chart.vega import with_vega
    from kosis_chat.config import resolve_path, settings_from_llm
    from kosis_chat.pipeline import VERSION

    from kosis_agent.graph.main_graph import build_graph

    if interval < 0:
        raise ValueError("interval cannot be negative")
    version, items, golden_sha = load_items(golden)  # schema, table files and their sha256 are checked here
    items = items[:limit] if limit else items
    for item in items:  # the key must reach the very file the golden answer was written against
        if api.table_path(Path(item.table).stem).resolve() != item.path.resolve():
            raise LookupError(f"{item.id}: tables.csv key {Path(item.table).stem} is not {item.table}")

    search = OracleSearch()
    kwargs = {"summarize": summarize} if summarize else {}
    graph = build_graph(search, llm, retry_interval=interval, **kwargs)
    settings = settings_from_llm(llm)  # the manifest's model record (no key), the names phase 1 graders read

    started = dt.datetime.now().astimezone()
    run_id = f"{started:%Y%m%d-%H%M%S-%f}-{stage}"
    run_dir = (out_root or resolve_path("eval/results")) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _copy_golden(golden, run_dir / "golden.jsonl", {i.id for i in items} if limit else None)
    manifest = {
        "run_id": run_id,
        "stage": stage,
        "started_at": started.isoformat(timespec="seconds"),
        "git_commit": "unknown",
        "entry": "kosis_agent.graph.main_graph.build_graph (oracle search) -> kosis_chat.api.summarize_stat_run",
        "pipeline": "stat",
        "golden_version": version,
        "golden_sha256": golden_sha,
        "data_sha256": {i.table: i.table_sha256 for i in items},
        "prompt_version": VERSION,
        "interval_seconds": interval,
        "serialization": settings.serialization,  # "long": llm= only (phase2-llmconfig-migration.md 3)
        "scope_mode": "auto",
        "model": settings.model_params(),
        "runs": 1,
        "questions": len(items),
        "question_ids": sorted({str(i.id) for i in items}),
        "pretest": llm.provider == "mock",
        "status": "running",
    }

    def write_manifest() -> None:
        (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_manifest()
    t_run, waited, failed = time.perf_counter(), 0.0, 0
    try:
        with (run_dir / "results.jsonl").open("w", encoding="utf-8") as out:
            for n, item in enumerate(items, start=1):
                if interval and n > 1:
                    time.sleep(interval)  # stay under per-minute rate limits (HCX 429)
                    waited += interval
                search.key = key = Path(item.table).stem
                t0 = time.perf_counter()
                try:
                    stat = graph.invoke({"messages": [("user", item.question)]})["stat"]
                    error = None
                except KeyboardInterrupt:
                    raise
                except Exception as exc:  # one question's failure is that question's run_failure; the rest still run
                    stat, error = None, f"{type(exc).__name__}: {exc}"
                latency = time.perf_counter() - t0
                failed += stat is None
                row = {
                    "question_id": item.id,
                    "kind": item.kind,
                    "run_index": 0,
                    "question": item.question,
                    "table_key": key,
                    "call_status": "ok" if stat else "error",
                    "call_error": error,
                    "latency_seconds": round(latency, 3),
                    "decision_metadata": {"spans": stat.spans} if stat else {},
                    "stat_answer": stat.answer.model_dump(mode="json") if stat else None,
                    "chart": with_vega(stat.chart).model_dump(mode="json") if stat and stat.chart else None,
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                outcome = stat.answer.outcome if stat else error[:120]
                print(f"[{n}/{len(items)}] {item.id} {latency:.1f}s {outcome}", flush=True)
        manifest["status"] = "completed"
    except BaseException as exc:
        manifest["status"] = "stopped"
        manifest["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        manifest["finished_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        manifest["timing"] = {"total_s": round(time.perf_counter() - t_run, 2), "interval_wait_s": round(waited, 2)}
        manifest["failed_calls"] = failed
        write_manifest()
    return run_dir


def main(argv: list[str] | None = None) -> int:
    from kosis_chat.config import resolve_path

    from backend.app.config import stat_llm

    parser = argparse.ArgumentParser(prog="python scripts/stat_oracle.py", description=__doc__.splitlines()[0])
    parser.add_argument("--stage", required=True, help="label for the run folder, e.g. t3-oracle-mock")
    parser.add_argument("--golden", default="eval/golden/golden.jsonl",
                        help="golden .jsonl file or a directory of them, under KOSIS_WORKSPACE")
    parser.add_argument("--provider", choices=("hcx", "mock"), default=None,
                        help="phase 1 model provider (default: STAT_PROVIDER, else hcx)")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between questions (default 1)")
    parser.add_argument("--limit", type=int, default=None, help="answer only the first N questions")
    args = parser.parse_args(argv)
    if args.provider:
        os.environ["STAT_PROVIDER"] = args.provider
    llm = stat_llm()  # key from HCX_API_KEY in the shell; never printed
    run_dir = run(resolve_path(args.golden), args.stage, llm, interval=args.interval, limit=args.limit)
    print(f"run folder: {run_dir}")
    return 0


if __name__ == "__main__":
    from kosis_agent.envfile import load_env_file

    load_env_file()  # <repo>/.env, shell values win
    sys.exit(main())
