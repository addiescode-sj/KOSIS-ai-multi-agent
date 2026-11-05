#!/usr/bin/env python3
"""One-way sync: TASK.md micro tasks -> Linear issues.

TASK.md is the source of truth (the plan gate reads it). Each task line becomes
one Linear issue titled "<ID> <title>"; matching is by that ID prefix, so the
sync is idempotent and nothing is written back into TASK.md.

  [ ] -> first "unstarted" state   [~] -> first "started" state   [x] -> first "completed" state

Usage:
  python3 .claude/hooks/linear_sync.py [--dry-run]   # manual
  python3 .claude/hooks/linear_sync.py hook           # PostToolUse: runs only when TASK.md was edited
  python3 .claude/hooks/linear_sync.py --selftest

Env (shell or .env): LINEAR_API_KEY, LINEAR_TEAM_KEY (e.g. "KOS"). Missing key -> silent no-op.
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
API = "https://api.linear.app/graphql"
TASK_LINE = re.compile(r"^- \[([ ~x])\] \*\*(T\d+-\d+)\*\*\s*(.*)$")
STATE_TYPE = {" ": "unstarted", "~": "started", "x": "completed"}


def parse_tasks(text):
    tasks, phase = [], ""
    for line in text.splitlines():
        if line.startswith("## "):
            phase = line[3:].strip()
        m = TASK_LINE.match(line)
        if not m:
            continue
        mark, tid, rest = m.groups()
        title, _, criteria = rest.partition(" — ")
        desc = f"**Phase:** {phase}\n\n{criteria.strip() or title}\n\n_Synced from TASK.md — edit there, not here._"
        tasks.append({"id": tid, "title": f"{tid} {title.strip()}", "desc": desc, "type": STATE_TYPE[mark]})
    return tasks


def plan_ops(tasks, issues, states):
    """Pure diff: return (creates, updates) for the given TASK.md tasks and existing Linear issues."""
    by_tid = {}
    for issue in issues:
        m = re.match(r"T\d+-\d+\b", issue["title"])
        if m:
            by_tid.setdefault(m.group(0), issue)
    first_state = {}
    for s in sorted(states, key=lambda s: s["position"]):
        first_state.setdefault(s["type"], s["id"])

    creates, updates = [], []
    for t in tasks:
        issue = by_tid.get(t["id"])
        if issue is None:
            creates.append({"title": t["title"], "description": t["desc"], "stateId": first_state[t["type"]]})
            continue
        patch = {}
        if issue["title"] != t["title"]:
            patch["title"] = t["title"]
        if (issue.get("description") or "") != t["desc"]:
            patch["description"] = t["desc"]
        # Compare by state type so a manual move to e.g. "In Review" (also "started") is kept.
        if issue["state"]["type"] != t["type"]:
            patch["stateId"] = first_state[t["type"]]
        if patch:
            updates.append((issue["id"], issue["identifier"], patch))
    return creates, updates


def gql(key, query, variables):
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Authorization": key},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body["data"]


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip().startswith("LINEAR_"):
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def sync(dry_run=False):
    load_env()
    key, team_key = os.environ.get("LINEAR_API_KEY"), os.environ.get("LINEAR_TEAM_KEY")
    if not key or not team_key:
        return "linear-sync: LINEAR_API_KEY / LINEAR_TEAM_KEY not set, skipped"

    # ponytail: first 250 issues only; paginate if the team ever exceeds that.
    data = gql(key, """
        query($key: String!) { teams(filter: {key: {eq: $key}}) { nodes {
          id
          states { nodes { id type position } }
          issues(first: 250, filter: {title: {startsWith: "T"}}) {
            nodes { id identifier title description state { type } } }
        } } }""", {"key": team_key})
    teams = data["teams"]["nodes"]
    if not teams:
        raise RuntimeError(f"Linear team '{team_key}' not found")
    team = teams[0]

    tasks = parse_tasks((ROOT / "TASK.md").read_text())
    creates, updates = plan_ops(tasks, team["issues"]["nodes"], team["states"]["nodes"])
    if dry_run:
        return f"linear-sync (dry run): {len(creates)} to create, {len(updates)} to update: " + \
            ", ".join([c["title"].split()[0] for c in creates] + [u[1] for u in updates])

    for c in creates:
        gql(key, "mutation($i: IssueCreateInput!) { issueCreate(input: $i) { success } }",
            {"i": {**c, "teamId": team["id"]}})
    for issue_id, _, patch in updates:
        gql(key, "mutation($id: String!, $i: IssueUpdateInput!) { issueUpdate(id: $id, input: $i) { success } }",
            {"id": issue_id, "i": patch})
    return f"linear-sync: created {len(creates)}, updated {len(updates)}"


def hook():
    payload = json.load(sys.stdin)
    path = (payload.get("tool_input") or {}).get("file_path", "")
    if Path(path).name != "TASK.md":
        return 0
    try:
        print(sync())
    except Exception as e:  # never block the edit because Linear is down
        print(f"linear-sync failed: {e}", file=sys.stderr)
    return 0


def selftest():
    text = "## Phase 1 — x\n- [~] **T1-05** Fetch — criteria A\n- [x] **T1-06** Slots — B\n- [ ] **T1-07** New — C\n"
    tasks = parse_tasks(text)
    assert [t["type"] for t in tasks] == ["started", "completed", "unstarted"]
    states = [{"id": "s-todo", "type": "unstarted", "position": 1}, {"id": "s-prog", "type": "started", "position": 2},
              {"id": "s-rev", "type": "started", "position": 3}, {"id": "s-done", "type": "completed", "position": 4}]
    issues = [
        {"id": "a", "identifier": "KOS-1", "title": tasks[0]["title"], "description": tasks[0]["desc"], "state": {"type": "started"}},
        {"id": "b", "identifier": "KOS-2", "title": "T1-06 old", "description": tasks[1]["desc"], "state": {"type": "started"}},
    ]
    creates, updates = plan_ops(tasks, issues, states)
    assert [c["title"] for c in creates] == ["T1-07 New"] and creates[0]["stateId"] == "s-todo"
    assert updates == [("b", "KOS-2", {"title": "T1-06 Slots", "stateId": "s-done"})]
    real = parse_tasks((ROOT / "TASK.md").read_text())
    assert len({t["id"] for t in real}) == len(real), "duplicate task IDs in TASK.md"
    print(f"selftest ok ({len(real)} tasks in TASK.md)")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "hook":
        sys.exit(hook())
    if arg == "--selftest":
        sys.exit(selftest())
    print(sync(dry_run=arg == "--dry-run"))
