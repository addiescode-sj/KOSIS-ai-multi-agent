#!/usr/bin/env bash
# node-1: 앱을 지정한 커밋으로 되돌리고 자동 배포를 멈춘다(멈추지 않으면 예약 실행이 다시 main 최신으로 올림).
#   bash ~/deploy/scripts/63-rollback.sh <커밋>    되돌리기 + 자동 배포 멈춤
#   bash ~/deploy/scripts/63-rollback.sh --resume  멈춤 풀기(문제를 고친 커밋이 main에 들어온 뒤)
# DB는 되돌리지 않는다. 이전 앱이 새 DB 구조에서 안 돌면 백업 복원을 사람이 결정한다(33-db-jobs.yaml 주석).
set -euo pipefail
STATE=$HOME/deploy/state
mkdir -p "$STATE"
if [ "${1:-}" = "--resume" ]; then rm -f "$STATE/paused"; echo "자동 배포 멈춤 풀림"; exit 0; fi
SHA="${1:?되돌릴 커밋 (배포 기록: tail ~/deploy/state/deploy.log)}"
echo "$(date '+%F %T') $SHA 로 되돌림 요청" > "$STATE/paused"
bash "$(dirname "$0")/60-deploy.sh" --sha "$SHA"
echo "자동 배포는 멈춘 상태. 풀기: bash $0 --resume"
