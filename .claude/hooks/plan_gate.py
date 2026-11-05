#!/usr/bin/env python3
"""Plan-gate hooks for the KOSIS project.

  status  (SessionStart): print the active task and the workflow reminder.
  gate    (PreToolUse on Write/Edit): block edits outside the planning area
          until plans/<TASK-ID>.md is verified and approved.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
ALWAYS_ALLOWED = ("plans/", ".claude/", "TASK.md", "CLAUDE.md", ".gitignore", ".mcp.json", "README.md")
REQUIRED_SECTIONS = ("## Option A", "## Option B", "## Option C", "## Verify", "## Decision")
TASK_ID = r"T\d+-\d+"


def in_progress_tasks():
    path = ROOT / "TASK.md"
    if not path.exists():
        return []
    return re.findall(rf"^- \[~\] \*\*({TASK_ID})\*\*", path.read_text(), re.M)


def branch_task():
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True
        ).stdout
    except OSError:
        return None
    m = re.match(TASK_ID, branch.strip())
    return m.group(0) if m else None


def plan_problems(task):
    path = ROOT / "plans" / f"{task}.md"
    if not path.exists():
        return [f"plans/{task}.md does not exist"]
    text = path.read_text()
    problems = [f"missing section '{s}'" for s in REQUIRED_SECTIONS if s not in text]
    verify = text.split("## Verify", 1)[-1].split("\n## ", 1)[0]
    if "- [ ]" in verify:
        problems.append("Verify checklist has unchecked items")
    if not re.search(r"^status:\s*approved\b", text, re.M):
        problems.append("status is not 'approved' (user must pick an option first)")
    return problems


def gate(payload):
    tool_input = payload.get("tool_input", {})
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not raw:
        return 0
    try:
        rel = Path(raw).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return 0  # outside the project (scratchpad, memory, ...)
    if rel.startswith(ALWAYS_ALLOWED):
        return 0

    # Branch name decides the task when it carries an ID; otherwise any [~] task may unlock.
    # ponytail: team members on master share one gate; branch-per-task keeps them separate.
    tasks = in_progress_tasks()
    bt = branch_task()
    candidates = [bt] if bt else tasks
    if not candidates:
        reason = "No task is in progress. Run /plan-gate to pick a TASK.md item and plan it first."
    else:
        report = {t: plan_problems(t) for t in candidates}
        if any(not p for p in report.values()):
            return 0
        details = "; ".join(f"{t}: {', '.join(p)}" for t, p in report.items())
        reason = f"Plan gate blocked edit to {rel}. {details}. Follow the plan-gate skill."
    print(reason, file=sys.stderr)
    return 2


def status():
    tasks = in_progress_tasks()
    bt = branch_task()
    lines = ["[plan-gate] Every TASK.md item goes Plan -> 3 options -> Verify -> user approval -> Implement -> Measure (skill: plan-gate)."]
    for t in ([bt] if bt else tasks):
        problems = plan_problems(t)
        lines.append(f"[plan-gate] Active {t}: " + ("approved, implement and measure." if not problems else "; ".join(problems)))
    if not tasks and not bt:
        lines.append("[plan-gate] No task in progress.")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "gate"
    if mode == "status":
        sys.exit(status())
    sys.exit(gate(json.load(sys.stdin)))
