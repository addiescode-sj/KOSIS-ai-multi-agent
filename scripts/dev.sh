#!/usr/bin/env bash
# Local backend with live traces (T3-30): Chroma + Jaeger in Docker, the API on this machine.
# The same setup runs an evaluation (T3-33), so a run never starts with Docker, Chroma or Jaeger down.
#
#   bash scripts/dev.sh            # API :8000, Swagger /docs, Jaeger UI :16686
#   RELOAD=1 bash scripts/dev.sh   # restart on code changes (backend/, src/ only)
#   PORT=8010 bash scripts/dev.sh
#   bash scripts/dev.sh eval --layer l1 --retriever graph:staged-gpt --stage T3-33-l1   # any run_eval arguments
#   bash scripts/dev.sh suite --stage T3-33 --only l1-graph e2e                          # any evals.suite arguments
#
# eval / suite: progress and the report path go to the terminal and to evals/reports/logs/<time>-<mode>.log (unbuffered).
# Settings come from .env (CHROMA_COLLECTION, EMBED_CKPT, RERANK_CKPT, HCX_API_KEY, STAGE2_* ...); shell values win.
# Containers keep running after Ctrl+C; stop them with: docker compose --profile obs stop chroma jaeger
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-api}"
[[ "$mode" == eval || "$mode" == suite ]] && shift

# Docker Desktop first (macOS): `docker compose` fails with "Cannot connect to the Docker daemon" otherwise
if ! docker info >/dev/null 2>&1; then
  [[ "$(uname)" == Darwin ]] && open -a Docker
  echo "waiting for the Docker daemon ..."
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 2; done
  docker info >/dev/null 2>&1 || { echo "Docker daemon not running" >&2; exit 1; }
fi
# --wait: Chroma until healthy, Jaeger until running (it has no healthcheck)
docker compose --profile obs up -d --wait chroma jaeger

export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4317}"
export PYTHONPATH=".:src:vendor/phase2/src:vendor/phase1:vendor/phase1/src"
echo "Jaeger   http://localhost:16686  (service: kosis-agent; Tags deployment.environment.name=service | eval)"

if [[ "$mode" == eval || "$mode" == suite ]]; then
  module=evals.run_eval
  [[ "$mode" == suite ]] && module=evals.suite
  mkdir -p evals/reports/logs
  log="evals/reports/logs/$(date +%Y%m%d-%H%M%S)-${mode}.log"
  echo "log      $log"
  # --group embed: the phase 2 query embedder / re-ranker (a plain `uv run` would sync it away)
  PYTHONUNBUFFERED=1 uv run --group embed python -m "$module" "$@" 2>&1 | tee "$log"
  exit "${PIPESTATUS[0]}"
fi

port="${PORT:-8000}"
# --reload watches the whole repo by default (data/ symlinks, node_modules): keep it to the code folders
reload=()
[[ "${RELOAD:-0}" == 1 ]] && reload=(--reload --reload-dir backend --reload-dir src)

echo "API      http://localhost:${port}/docs"
exec uv run --group embed uvicorn backend.app.main:app --port "$port" ${reload[@]+"${reload[@]}"}  # empty-array safe on macOS bash 3.2
