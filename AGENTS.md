# Dev Harness — Operating Rules

This repository is a general-purpose development harness. Every non-trivial
piece of work flows through: **Plan → Approve → Tasks → Execute → Verify → Report**.

## Workflow

1. `/plan <request>` — analyze user intent (including tacit knowledge), write `docs/plans/<id>/PLAN.md`.
2. **User approval gate** — do not proceed past PLAN.md without an explicit "approved" from the user.
3. `/tasks` — split the approved plan into micro tasks in `docs/plans/<id>/TASKS.md`.
4. `/execute` — run tasks through subagents (max 5 concurrent), checking off TASKS.md as you go.
5. `/wrapup` — write `docs/reports/<id>-REPORT.md` and update `docs/current-state.md`.

## Context hygiene (non-negotiable)

The conversation is NOT the source of truth. Files are.

- Never paste raw logs, long file dumps, or full subagent transcripts into the conversation.
- Long outputs go to files under `docs/`; the conversation keeps only a **Handoff Index**:
  Decision / Evidence / Implementation scope / Non-goals / Verification / Context Index.
- A Context Index entry is `path / contains / use_for` — enough for the next agent to find it.
- A new session must be able to continue from `docs/current-state.md` alone,
  without reading any previous conversation.

## Subagent roles

Defined in `.claude/agents/`. Tool allowlists in frontmatter are the hard boundary.

| Role | Responsibility | May write |
| --- | --- | --- |
| planner | intent analysis, plan authoring | `docs/plans/**` only |
| explorer | read-only investigation | nothing |
| implementer | code changes within task scope | files named in the current task |
| verifier | run checks, report pass/fail with evidence | nothing |
| scribe | reports and state docs | `docs/**` only |

- Max 5 subagents per execution wave.
- Read-heavy work goes to subagents; write-heavy work has a single owner per file.
- Parallel implementers only on disjoint file sets.

## Model routing

Route by how much *judgement* the role carries, not by how much text it moves.
Tier is the contract; the concrete model is per-harness.

| Role | Tier | Why |
| --- | --- | --- |
| planner | deep | tacit-knowledge inference, deciding what NOT to build |
| verifier | deep | final pass/fail judgement, spotting scope drift in a diff |
| implementer | balanced | scope is already fixed by the task |
| scribe | balanced | summarizing decided facts |
| explorer | fast | repetitive read-only recon, no decisions |

Tier → harness:

| Tier | Claude Code | Codex CLI |
| --- | --- | --- |
| deep | `model: opus` | `-c model_reasoning_effort=high` |
| balanced | `model: sonnet` | `-c model_reasoning_effort=medium` |
| fast | `model: haiku` | `-c model_reasoning_effort=low` |

Claude Code carries this in each `.claude/agents/*.md` frontmatter. Other
harnesses read this table and pass the equivalent flag when invoking the role.
A role escalates one tier only when it reports it was underpowered — never
preemptively.

## Isolation

Only **implementer** writes source code, so only implementer ever needs a
worktree. It is opt-in per spawn, not a default: an isolated implementer's
work is invisible to `scripts/verify.sh` in the main tree until merged, so a
worktree you forget to merge is silently lost work.

Spawn an implementer in a worktree when — and only when — either holds:

- two or more implementers run concurrently and their file scopes are not provably disjoint
- the plan calls for **approach comparison** (below)

Otherwise implementers write directly in the main tree.

```bash
git worktree add ../wt-<id>-<label> -b <id>/<label>   # create
git worktree remove ../wt-<id>-<label> --force        # discard
```

Claude Code: pass `isolation: "worktree"` on the Agent call.
Codex CLI: create the worktree yourself and run the role with `codex exec -C <worktree>`.
Either way the merge back is the **orchestrator's** job, never the implementer's.

## Approach comparison

Use this only when PLAN.md explicitly names competing strategies for the same
acceptance criteria — not to hedge on a plan that already decided.

1. One implementer per approach, each in its own worktree, each given the
   *same* task text and acceptance criteria.
2. One verifier per worktree, run against that worktree's tree. Verifiers do
   not see each other's results.
3. Pick the winner on verifier evidence, merge that branch only.
4. `git worktree remove --force` every loser and delete its branch. Record in
   the report which approaches lost and why — one line each.

If two approaches both PASS, the tie-break is diff size, then dependency count.

## Done criteria

Work is done only when ALL of the following hold:

1. Every task in TASKS.md is checked off or explicitly deferred with a reason.
2. `bash scripts/verify.sh` exits 0 (the Stop hook enforces this).
3. `docs/reports/<id>-REPORT.md` exists with verification evidence.
4. `docs/current-state.md` reflects the new state.

## General rules

- All code and comments in English.
- Never read or modify `.env`, `secrets/**`, `*.pem`, `*.key`.
- Do not fix a failing check by weakening the check or the test.
- When plan and reality diverge during execution, stop and update PLAN.md/TASKS.md first.
