---
name: tasks
description: Split an approved PLAN.md into micro tasks in TASKS.md. Use right after the user approves a plan, or when they invoke /tasks.
---

# /tasks — Micro task breakdown

Precondition: the user has explicitly approved `docs/plans/<id>/PLAN.md` and
it contains no unresolved `[NEEDS CLARIFICATION]` markers. If either fails,
go back to /plan.

## Steps

1. Read the approved PLAN.md.
2. Write `docs/plans/<id>/TASKS.md` following `docs/templates/TASKS.template.md`:
   - IDs `T-001`, `T-002`, ... grouped into phases
     (typical: Phase 1 tests/contracts, Phase 2 implementation, Phase 3 integration & verification).
   - Each task names ONE primary file and ONE observable result.
     "Implement auth" is not a task; "Add locked_until column migration in db/migrations/" is.
   - Mark independent tasks `[P]` (parallelizable); list dependencies as `after: T-xxx`.
   - Each task carries its acceptance link (`AC-xxx`) and, where possible,
     a verification command.
3. Cross-check: every FR/AC in PLAN.md maps to at least one task, and every
   task traces back to the plan. Fix the documents, not the mapping.
4. Reply with only: TASKS.md path, task count per phase, parallelizable count.
   Then ask: "TASKS.md 확인 후 /execute로 실행을 시작할까요?"

## Rules

- A task an agent cannot finish and verify in one focused pass is too big — split it.
- Do not add tasks that have no corresponding requirement in PLAN.md.
