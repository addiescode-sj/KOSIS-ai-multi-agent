#!/usr/bin/env bash
# Project verification entry point. The Stop hook runs this before the agent
# may declare completion. Offline only: no network, no API keys, no HCX calls.
set -euo pipefail
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/..}"

# Harness self-check: typecheck the hook scripts (skipped until npm install)
if [ -d node_modules ]; then
  npx tsc --noEmit
fi
# Frontend typecheck (skipped until npm install in frontend/)
if [ -d frontend/node_modules ]; then
  (cd frontend && npx next typegen >/dev/null && npx tsc --noEmit && node --test lib/*.test.ts >/dev/null)
fi

# Phase 1 / phase 2 modules are imported from the submodules
for f in vendor/phase1/src/kosis_ai/schemas.py vendor/phase2/src/kosis_rag/schemas.py; do
  if [ ! -f "$f" ]; then
    echo "verify: $f missing; run: git submodule update --init" >&2
    exit 1
  fi
done

# Python: prefer the project venv, then python3, then python (Windows Git Bash).
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for candidate in .venv/Scripts/python.exe .venv/bin/python python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then
      PY="$candidate"
      break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "verify: no Python found (install Python 3.12 or set PYTHON=...)" >&2
  exit 1
fi
if ! "$PY" -c "import pytest, yaml, fastapi, langgraph" >/dev/null 2>&1; then
  echo "verify: missing packages; run: uv sync" >&2
  exit 1
fi

# Offline tests (fake / mock LLMs only).
"$PY" -m pytest

# Real data, golden sets and eval results must never be tracked by git.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  for path in data evals/datasets/x eval/golden/x eval/results/x .env; do
    if ! git check-ignore -q "$path"; then
      echo "verify: $path is not gitignored" >&2
      exit 1
    fi
  done
  tracked="$(git ls-files data evals/datasets eval/golden eval/results .env)"
  if [ -n "$tracked" ]; then
    echo "verify: real-data paths are tracked by git:" >&2
    echo "$tracked" >&2
    exit 1
  fi
fi

echo "verify: OK"
exit 0
