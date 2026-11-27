#!/usr/bin/env bash
# node-1: DB 계정과 앱 설정 Secret(kosis-env). GPU vLLM 키 한 줄(VLLM_API_KEY=)을 표준 입력으로 받는다. 값을 화면에 찍지 않는다.
# 사용(이 PC): ssh kosis-inference "grep ^VLLM_API_KEY= ~/deploy/gpu/.env" | ssh kosis-node-1 bash deploy/scripts/51-make-secrets.sh
set -euo pipefail
. "$(dirname "$0")/../site.env"   # GPU_IP (site.env.example)
GPU="${GPU_IP:?site.env}"
IN="$(cat)"
VK="$(sed -n 's/^VLLM_API_KEY=//p' <<<"$IN")"
[ -n "$VK" ] || { echo "GPU 키(VLLM_API_KEY)를 받지 못함"; exit 1; }
kubectl create namespace kosis --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n kosis get secret kosis-db >/dev/null 2>&1 || kubectl -n kosis create secret generic kosis-db \
  --from-literal=POSTGRES_USER=kosis --from-literal=POSTGRES_DB=kosis --from-file=POSTGRES_PASSWORD=<(openssl rand -hex 16 | tr -d '\n')
PW="$(kubectl -n kosis get secret kosis-db -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)"
kubectl -n kosis create secret generic kosis-env --dry-run=client -o yaml --from-env-file=<(cat <<E
DATABASE_URL=postgresql+psycopg://kosis:${PW}@postgres:5432/kosis
VECTOR_COLLECTION=kosis_mixft_A1_e3
EMBED_CKPT=data/models/embedder/A1-e3.pt
RERANK_CKPT=data/models/reranker/RR-A1-e3.pt
STAGE2=gpt
STAGE2_MODEL=RedHatAI/Qwen3.8-27B-INT4
STAGE2_BASE_URL=http://${GPU}:8000/v1
STAGE2_API_KEY=${VK}
E
) | kubectl apply -f - 
