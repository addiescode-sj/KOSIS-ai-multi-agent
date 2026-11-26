"""Bring eval runs made elsewhere (Colab, another worktree) into this repo's evals/reports, as if they ran here.

    python scripts/pull_reports.py                      # default: Google Drive for desktop, My Drive/kosis/reports
    python scripts/pull_reports.py <folder or .zip>     # a folder of run folders, or the zip the Colab notebook downloads

Copies every run folder (has manifest.json) and compare folder (compare-*, has compare.md) that evals/reports does
not have yet, and appends the source's ops_history lines of those runs that the local ops_history.jsonl lacks (plans/T3-60.md).
Folders already here are never overwritten. Run folders stay out of git; ops_history.jsonl is committed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "evals" / "reports"
DRIVE_GLOB = "Library/CloudStorage/GoogleDrive-*/My Drive/kosis/reports"


def default_source() -> Path | None:
    found = sorted(Path.home().glob(DRIVE_GLOB))
    return found[0] if found else None


def run_folders(source: Path) -> list[Path]:
    """Run folders and compare folders anywhere under source (a zip keeps them under evals/reports/)."""
    found = {p.parent for p in source.rglob("manifest.json")} | {p.parent for p in source.rglob("compare.md")}
    return sorted(found)


def history_lines(source: Path) -> list[dict]:
    lines = []
    for path in sorted(source.rglob("ops_history*.jsonl")):
        lines += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines


def pull(source: Path, reports: Path | None = None) -> dict:
    """Copy what is new from source into reports (default evals/reports). Returns {copied, skipped, history}."""
    reports = reports or REPORTS
    reports.mkdir(parents=True, exist_ok=True)
    copied, skipped = [], []
    folders = run_folders(source)
    for folder in folders:
        target = reports / folder.name
        if target.exists():
            skipped.append(folder.name)
            continue
        shutil.copytree(folder, target)
        copied.append(folder.name)
    history = reports / "ops_history.jsonl"
    known = {json.loads(line)["run_id"] for line in history.read_text(encoding="utf-8").splitlines() if line.strip()} \
        if history.exists() else set()
    new = []
    pulled = {f.name for f in folders}  # only the lines of runs this source holds, not its whole history
    for line in history_lines(source):
        if line.get("run_id") in pulled and line["run_id"] not in known:
            known.add(line["run_id"])
            new.append(line)
    if new:
        with history.open("a", encoding="utf-8") as f:
            f.writelines(json.dumps(line, ensure_ascii=False) + "\n" for line in new)
    return {"copied": copied, "skipped": skipped, "history": [line["run_id"] for line in new]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", nargs="?", type=Path, help="folder or .zip (default: Google Drive My Drive/kosis/reports)")
    args = parser.parse_args(argv)
    source = args.source or default_source()
    if source is None or not source.exists():
        print("no source: pass the folder or the zip (Google Drive for desktop not found)", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        if source.suffix == ".zip":
            with zipfile.ZipFile(source) as z:
                z.extractall(tmp)
            source = Path(tmp)
        out = pull(source)
    print(f"copied {len(out['copied'])}: {', '.join(out['copied']) or '-'}")
    print(f"already here {len(out['skipped'])}, ops_history lines added {len(out['history'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
