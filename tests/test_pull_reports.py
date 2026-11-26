"""plans/T3-60.md: runs made on Colab land in evals/reports as if they ran here (scripts/pull_reports.py)."""

import importlib.util
import json
import zipfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location("pull_reports", Path(__file__).resolve().parents[1] / "scripts" / "pull_reports.py")
pull_reports = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pull_reports)


def run(folder: Path, run_id: str) -> None:
    (folder / run_id).mkdir(parents=True)
    (folder / run_id / "manifest.json").write_text(json.dumps({"run_id": run_id}))
    (folder / run_id / "report.md").write_text("# report")


def test_new_runs_and_history_lines_are_added_once_and_local_folders_are_kept(tmp_path):
    src, reports = tmp_path / "drive", tmp_path / "evals" / "reports"
    run(src, "r1"); run(src, "r2")
    (src / "compare-r1-vs-r2").mkdir(); (src / "compare-r1-vs-r2" / "compare.md").write_text("# cmp")
    (src / "ops_history.jsonl").write_text("".join(json.dumps({"run_id": r}) + "\n" for r in ("r0", "r1", "r2", "x")))
    run(reports, "r1"); (reports / "r1" / "report.md").write_text("local")
    (reports / "ops_history.jsonl").write_text(json.dumps({"run_id": "r0"}) + "\n")

    out = pull_reports.pull(src, reports)
    assert out["copied"] == ["compare-r1-vs-r2", "r2"] and out["skipped"] == ["r1"]
    assert out["history"] == ["r1", "r2"]  # r0 is already here; x has no run folder in the source
    assert (reports / "r1" / "report.md").read_text() == "local"  # never overwritten
    assert pull_reports.pull(src, reports) == {"copied": [], "skipped": ["compare-r1-vs-r2", "r1", "r2"], "history": []}


def test_the_notebook_zip_is_read_as_a_source(tmp_path):
    src = tmp_path / "content"
    run(src / "evals" / "reports", "r9")
    (src / "evals" / "reports" / "ops_history.colab.jsonl").write_text(json.dumps({"run_id": "r9"}) + "\n")
    zpath = tmp_path / "x.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        for p in src.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(src))
    reports = tmp_path / "repo" / "evals" / "reports"
    pull_reports.REPORTS = reports
    try:
        assert pull_reports.main([str(zpath)]) == 0
    finally:
        pull_reports.REPORTS = Path(pull_reports.ROOT) / "evals" / "reports"
    assert (reports / "r9" / "report.md").exists() and "r9" in (reports / "ops_history.jsonl").read_text()
