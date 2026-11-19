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
# Evaluation pipeline (T3-43, plans/T3-43.md):
#   bash scripts/dev.sh suite --stage <ID>                  # offline: fixed golden set, L0-L4 + agent, e2e -> Ragas,
#                                                            #   baseline comparison, regression gate (exit 2 on a drop)
#   bash scripts/dev.sh online --stage <ID>-online [--lookback 24h --sample 40]
#                                                            # online: service traces from Jaeger -> code checks on every
#                                                            #   turn, Ragas on a sample, drift (exit 3), failure cases
#   bash scripts/dev.sh pipeline --stage <ID> [suite args]  # both, offline first; exit = the first non-zero
#
# eval / suite / online: progress and the report path go to the terminal and to evals/reports/logs/<time>-<mode>.log.
# Settings come from .env (CHROMA_COLLECTION, EMBED_CKPT, RERANK_CKPT, HCX_API_KEY, OPENAI_API_KEY, STAGE2_* ...); shell values win.
# Containers keep running after Ctrl+C; stop them with: docker compose --profile obs stop chroma jaeger
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-api}"
[[ "$mode" == eval || "$mode" == suite || "$mode" == online || "$mode" == pipeline ]] && shift

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
# Every group a run needs, in one sync: `uv run --group X` removes the groups it is not given (T3-43: ragas was dropped)
groups=(--group embed --group ragas)

# run <module> <log name> <args...>: unbuffered, to the terminal and a log file; returns the module's exit code
run() {
  local module="$1" name="$2"
  shift 2
  mkdir -p evals/reports/logs
  local log
  log="evals/reports/logs/$(date +%Y%m%d-%H%M%S)-${name}.log"
  echo "log      $log"
  set +e
  PYTHONUNBUFFERED=1 uv run "${groups[@]}" python -m "$module" "$@" 2>&1 | tee "$log"
  local code="${PIPESTATUS[0]}"
  set -e
  return "$code"
}

case "$mode" in
  eval) run evals.run_eval eval "$@"; exit $? ;;
  suite) run evals.suite suite "$@"; exit $? ;;
  online) run evals.run_eval online --layer online "$@"; exit $? ;;
  pipeline)
    stage=""
    for ((i = 1; i <= $#; i++)); do [[ "${!i}" == --stage ]] && { j=$((i + 1)); stage="${!j}"; }; done
    [[ -n "$stage" ]] || { echo "pipeline needs --stage <ID>" >&2; exit 1; }
    offline=0; run evals.suite suite "$@" || offline=$?
    live=0; run evals.run_eval online --layer online --stage "${stage}-online" || live=$?
    echo "offline exit $offline (1 run failed, 2 regression gate) · online exit $live (3 quality drop)"
    [[ $offline -ne 0 ]] && exit "$offline"
    exit "$live" ;;
esac

port="${PORT:-8000}"
# --reload watches the whole repo by default (data/ symlinks, node_modules): keep it to the code folders
reload=()
[[ "${RELOAD:-0}" == 1 ]] && reload=(--reload --reload-dir backend --reload-dir src)

echo "API      http://localhost:${port}/docs"
exec uv run "${groups[@]}" uvicorn backend.app.main:app --port "$port" ${reload[@]+"${reload[@]}"}  # empty-array safe on macOS bash 3.2
