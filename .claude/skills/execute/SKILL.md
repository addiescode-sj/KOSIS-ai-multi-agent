---
name: execute
description: Execute TASKS.md through subagents (max 5 concurrent) with per-phase verification. Use after tasks are confirmed, or when the user invokes /execute.
---

# /execute — Orchestrated execution

You are the orchestrator. Subagents do the work; you route tasks, keep
TASKS.md current, and keep the conversation clean.

## Orchestration rules

- **Max 5 subagents in flight at any time.**
- Read-heavy unknowns → **explorer**. Code changes → **implementer**.
  Phase gates → **verifier**.
- One implementer owns one task; run implementers in parallel ONLY when their
  file scopes are disjoint. When in doubt, serialize — or isolate.
- Worktree isolation and approach comparison: follow AGENTS.md
  ("Isolation", "Approach comparison"). Merging a worktree back is yours, not
  the implementer's; an unmerged worktree at phase end is a FAIL, not a pass.
- Roles run at the tier in the AGENTS.md model routing table. Do not override
  per-spawn unless a role reports it was underpowered.
- Every subagent prompt is a self-contained context pack:
  task text, PLAN.md and TASKS.md paths, file scope, non-goals,
  verification command. Never say "as discussed above".

## Loop (per phase)

1. Dispatch the phase's tasks respecting `[P]` markers and `after:` dependencies.
2. As each result returns, check the box in TASKS.md and append a one-line
   result note under the task (`> done: <files>, <verify result>`).
3. If an implementer reports the plan is wrong: STOP the phase, update
   PLAN.md/TASKS.md (with user approval if scope changes), then resume.
4. At phase end, spawn **verifier** for the phase's acceptance criteria.
   FAIL → create fix tasks in TASKS.md and loop; PASS → next phase.
5. Long outputs (logs, diffs, investigation detail) go to
   `docs/reports/raw/<id>-*.md`, never into the conversation.

## After the last phase

Run the wrapup flow (see the `wrapup` skill), then report to the user with a
Handoff Index only:

```markdown
## Handoff Index
- Decision: <what was built, one sentence>
- Evidence: <changed files / report file>
- Verification: <command + result>
- Non-goals honored: <yes / exceptions>
- Context Index: <path / contains / use_for, one per file>
```
