---
name: plan
description: Turn a user request into an approved-ready PLAN.md via the planner subagent. Use when the user asks to plan a feature, fix, or refactor, or invokes /plan.
---

# /plan — Intent analysis and plan authoring

Goal: fix the plan in a file BEFORE any implementation, so agents execute
instead of guessing. The conversation keeps only a handoff index.

## Steps

1. Derive a work id: `<YYYYMMDD>-<short-slug>` from today's date and the request.
2. If the request is ambiguous on points you cannot resolve from the codebase,
   ask the user 1-3 sharp questions FIRST (batch them, don't dribble).
   Questions about preference/scope go to the user; questions about facts in
   the code go to the code.
3. Spawn the **planner** subagent with:
   - the raw user request, verbatim
   - the work id and target path `docs/plans/<id>/PLAN.md`
   - instruction to follow `docs/templates/PLAN.template.md`
   - any answers gathered in step 2
4. Relay the planner's handoff index to the user. Do NOT paste PLAN.md
   contents into the conversation — link the file.
5. If the handoff contains `needs_clarification` items, ask the user now and
   have the planner revise PLAN.md with the answers.
6. End the turn asking for approval:
   "PLAN.md를 검토하고 승인해주세요. 승인하시면 /tasks로 진행합니다."

## Rules

- Never start implementation from this skill.
- The plan must make every acceptance criterion observable/testable
  (a command, a test, or a manual QA step — not "works well").
- Unresolved decisions stay as `[NEEDS CLARIFICATION]` in PLAN.md; they must
  all be resolved before /tasks.
