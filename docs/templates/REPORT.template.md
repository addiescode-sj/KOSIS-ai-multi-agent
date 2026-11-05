# Report: <title>

- id: <id>
- date: <YYYY-MM-DD>
- plan: docs/plans/<id>/PLAN.md

## Summary

<What shipped, 3-5 lines. State plainly if the work is incomplete.>

## Changes

- <file>: <one-line description>

## Verification

- verdict: PASS | PASS_WITH_NOTES | FAIL | INCOMPLETE
  - PASS: every AC met with evidence
  - PASS_WITH_NOTES: every AC met, with recorded caveats
  - FAIL: at least one check run and failed
  - INCOMPLETE: at least one AC not run (e.g. HCX evaluation not executed, provisional score with pending reviews)
- offline checks: `bash scripts/verify.sh` -> <exit code, summary>
- online checks (HCX / API): <command, run id, manifest path> -> <result> | not run: <reason>
- acceptance: one line per AC — `AC-001: met (<evidence>)` | `not met (<gap>)` | `not run (<reason>)`

## Not run / failed

- <check or AC, status (not run / failed / provisional), reason, what is needed to finish — or "none">

## Deviations from plan

- <what changed vs PLAN.md and why, or "none">

## Deferred / Remaining risks

- <item, reason, suggested follow-up, or "none">

## Context Index

- <path> — contains: <...> — use_for: <...>
