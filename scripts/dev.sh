#!/usr/bin/env bash
# Local backend with live traces (T3-30): Chroma + Jaeger in Docker, the API on this machine.
#
#   bash scripts/dev.sh            # API :8000, Swagger /docs, Jaeger UI :16686
#   RELOAD=1 bash scripts/dev.sh   # restart on code changes (backend/, src/ only)
#   PORT=8010 bash scripts/dev.sh
#
# Settings come from .env (CHROMA_COLLECTION, EMBED_CKPT, RERANK_CKPT, HCX_API_KEY ...); shell values win.
# Containers keep running after Ctrl+C; stop them with: docker compose --profile obs stop chroma jaeger
set -euo pipefail
cd "$(dirname "$0")/.."

# --wait: Chroma until healthy, Jaeger until running (it has no healthcheck)
docker compose --profile obs up -d --wait chroma jaeger

export OTEL_EXPORTER_OTLP_ENDPOINT="${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:4317}"
export PYTHONPATH=".:src:vendor/phase2/src:vendor/phase1:vendor/phase1/src"
port="${PORT:-8000}"

# --reload watches the whole repo by default (data/ symlinks, node_modules): keep it to the code folders
reload=()
[[ "${RELOAD:-0}" == 1 ]] && reload=(--reload --reload-dir backend --reload-dir src)

echo "API      http://localhost:${port}/docs"
echo "Jaeger   http://localhost:16686  (service: kosis-agent)"
# --group embed: the phase 2 query embedder / re-ranker (a plain `uv run` would sync it away)
exec uv run --group embed uvicorn backend.app.main:app --port "$port" ${reload[@]+"${reload[@]}"}  # empty-array safe on macOS bash 3.2
