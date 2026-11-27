#!/usr/bin/env bash
# node-1: HCX·카카오 키 입력(화면에 안 보임). 빈 칸으로 Enter = 기존 값 유지. 사용(내 PC): ssh -t kosis-node-1 bash deploy/scripts/50-set-keys.sh
# OpenAI 는 안 씀(11/25 결정). 카카오 돌아올 주소는 비밀이 아니라 여기 고정 — 카카오 콘솔에 같은 주소를 등록해야 한다.
set -euo pipefail
. "$(dirname "$0")/../site.env"   # SITE_HOST (site.env.example)
OUT=""; NAMES=""
add() { OUT+="${OUT:+,}\"$1\":\"$(printf '%s' "$2" | base64 -w0)\""; NAMES+=" $1"; }
for k in HCX_API_KEY KAKAO_CLIENT_ID KAKAO_CLIENT_SECRET; do
  read -rsp "$k: " v; echo
  v="$(printf '%s' "$v" | tr -d '[:space:]')"
  [ -z "$v" ] || add "$k" "$v"
done
[ -n "$OUT" ] || { echo "입력한 키가 없어 바꾸지 않음"; exit 1; }
add KAKAO_REDIRECT_URI "https://${SITE_HOST:?site.env}/auth/kakao/callback"
kubectl -n kosis get secret kosis-keys >/dev/null 2>&1 || kubectl -n kosis create secret generic kosis-keys >/dev/null
printf '{"data":{%s}}' "$OUT" | kubectl -n kosis patch secret kosis-keys --patch-file /dev/stdin
echo "저장함:$NAMES"
kubectl -n kosis rollout restart deploy/backend && kubectl -n kosis rollout status deploy/backend --timeout=180s
