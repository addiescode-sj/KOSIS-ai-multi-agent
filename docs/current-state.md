# Current State

> A new session starts here. Keep this short and current — it is the handoff
> packet between sessions.

## T3-00 repo initial setup (2026-11-04)

- Submodules: `vendor/phase1` = KOSIS-ai-nlp-summary @ `e248a45` (same commit phase 2 pins), `vendor/phase2` = KOSIS-ai-RAG @ `484bd06`.
- Harness copied from phase 2 unchanged and committed for the team: `.claude/` (agents, hooks `plan_gate.py`·`linear_sync.py`, skills, `settings.json`), `AGENTS.md`, `scripts/*.ts`, `.mcp.json` (Linear), PR template, `docs/templates/`. `CLAUDE.md` rewritten for phase 3.
- Stack skeleton: `src/kosis_agent/graph.py` (LangGraph, one LLM node), `backend/app/main.py` (`/health`, `/chat/stream` SSE), phase 2 frontend wired to `/chat/stream`, compose (backend · frontend · chroma · postgres · redis · jaeger[obs]), GitHub Actions CI (`verify.sh` + image builds). K8s·AWS deferred to M6.
- `evals/` copied from phase 2 (`vendor/phase2@484bd06`, with `tests/test_evals_*.py` and `ops_history.jsonl`) and continued here; paths now resolve to this repo. Local golden sets copied into `evals/datasets/` (gitignored).
- Open issues:
  - `kosis_rag` reads `vendor/phase2/data` and `vendor/phase2/.env` (fixed `parents[2]`): needs a phase 2 PR before T3-01.
  - Postgres·Redis run in compose but no code uses them yet (KOS-86, M3).
  - `.env.example` must be added by hand (agent write to `.env*` is denied by permission settings).

### Context Index

- `plans/T3-00.md` / contains: options, review, decision (B) / use_for: scope review.
- `TASK.md` / contains: Phase 3 task list T3-00~16 / use_for: picking the next task.
