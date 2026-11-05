---
name: wrapup
description: Externalize results into a report and update current-state.md so the next session needs no conversation history. Use at the end of /execute or when the user invokes /wrapup.
---

# /wrapup — Externalize results

Goal: after this skill runs, a brand-new session can continue the project by
reading `docs/current-state.md` alone.

## Steps

1. Spawn the **scribe** subagent with:
   - the work id and paths to PLAN.md / TASKS.md
   - the final verification result (verdict + commands)
   - the list of changed files
2. Scribe writes `docs/reports/<id>-REPORT.md`
   (template: `docs/templates/REPORT.template.md`) and updates
   `docs/current-state.md`.
3. Confirm `bash scripts/verify.sh` exits 0. If not, this work is NOT done —
   report the failure instead of wrapping up.
4. Reply to the user with the report path and a 3-5 line summary
   (what shipped, how it was verified, what remains).

## Rules

- The report records decisions, evidence paths, and verification results —
  never conversation transcripts.
- Deferred tasks must appear in both REPORT.md and current-state.md with a reason.
