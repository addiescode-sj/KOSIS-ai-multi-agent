#!/usr/bin/env bash
# node-1: 저장소 커밋 하나를 GHCR 이미지(backend·migrate·frontend)로 배포한다. 손 배포와 자동 배포(61)가 같은 스크립트를 쓴다.
#   bash ~/deploy/scripts/60-deploy.sh                 main 최신 커밋 배포
#   bash ~/deploy/scripts/60-deploy.sh --sha <커밋>    그 커밋 배포 (짧은 번호 가능, 되돌리기에도 사용)
#   bash ~/deploy/scripts/60-deploy.sh --dry-run       확인만 하고 바꾸지 않음
#   --auto                                             자동 실행용: "멈춤"·실패 기록을 따르고, 할 일이 없으면 조용히 끝남
# 순서: 이미지 3개 확인 → 서버 상태 → DB 백업(node-2) → DB 변경 → 앱 교체 → 확인. DB 변경이 실패하면 앱은 그대로,
#       앱 교체·확인이 실패하면 앱만 이전 판으로 돌린다(DB는 자동으로 되돌리지 않음).
# 기록(~/deploy/state): deploy.log, current(마지막으로 성공한 커밋), failed(실패한 커밋), paused(있으면 자동 배포 안 함)
set -euo pipefail
OWNER=addiescode-sj
REPO=$OWNER/KOSIS-ai-multi-agent
IMG=ghcr.io/$OWNER/kosis-agent
K8S=${KOSIS_K8S_DIR:-$HOME/deploy/k8s}
STATE=$HOME/deploy/state
. "$(dirname "$0")/../site.env"   # SITE_HOST (site.env.example)
SITE="https://${SITE_HOST:?site.env}"
APPLY="${KOSIS_APPLY_DIR:-$(dirname "$K8S")/overlay}"   # 주소를 채운 덧씌우기(05-make-site.sh). 태그는 $K8S/kustomization.yaml 에서 바꾼다
mkdir -p "$STATE"
[ -f "$APPLY/kustomization.yaml" ] || { echo "덧씌우기 없음: bash $(dirname "$0")/05-make-site.sh"; exit 1; }

AUTO=0; DRY=0; SHA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --auto) AUTO=1 ;;
    --dry-run) DRY=1 ;;
    --sha) SHA="${2:?--sha 뒤에 커밋}"; shift ;;
    *) echo "모르는 옵션: $1"; exit 2 ;;
  esac
  shift
done

log() { echo "$(date '+%F %T') $*" | tee -a "$STATE/deploy.log"; }
later() { [ $AUTO = 1 ] || echo "$1"; [ $AUTO = 1 ] && exit 0; exit 1; }   # 지금은 할 수 없음(자동 실행은 다음 차례에)
k() { kubectl -n kosis "$@"; }

exec 9>"$STATE/lock"
flock -n 9 || later "다른 배포가 실행 중"
[ $AUTO = 1 ] && [ -e "$STATE/paused" ] && exit 0

# 1. 배포할 커밋
[ -n "$SHA" ] || SHA=$(git ls-remote "https://github.com/$REPO" refs/heads/main | cut -f1)
[ ${#SHA} -eq 40 ] || SHA=$(curl -fsS "https://api.github.com/repos/$REPO/commits/$SHA" | jq -r .sha)
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || later "커밋을 찾지 못함"
# 지금 배포된 판: 실제로 도는 backend 이미지로 판단(손으로 바꿔도 맞음). GHCR 판이면 커밋, 아니면 태그 그대로
RUN_IMG=$(k get deploy backend -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo none)
CUR=${RUN_IMG##*:}; CUR=${CUR#sha-}
if [ "$SHA" = "$CUR" ]; then [ $AUTO = 1 ] || echo "이미 배포됨: ${SHA:0:7}"; exit 0; fi
[ $AUTO = 1 ] && grep -qx "$SHA" "$STATE/failed" 2>/dev/null && exit 0

# 2. 이미지 3개 (CI가 검사를 통과한 커밋만 저장한다. 공개 이미지라 키 없이 확인)
have() {
  local t
  t=$(curl -fsS "https://ghcr.io/token?scope=repository:$OWNER/kosis-agent-$1:pull" | jq -r .token) &&
    curl -fsS -o /dev/null -H "Authorization: Bearer $t" \
      -H 'Accept: application/vnd.oci.image.manifest.v1+json,application/vnd.oci.image.index.v1+json' \
      "https://ghcr.io/v2/$OWNER/kosis-agent-$1/manifests/sha-$SHA" 2>/dev/null
}
for n in backend migrate frontend; do have $n || later "이미지가 아직 없음(CI 진행 중이거나 실패): $n sha-${SHA:0:7}"; done

# 3. 서버 상태
ready=$(kubectl get nodes --no-headers | awk '$2 == "Ready"' | wc -l)
[ "$ready" -ge 2 ] || later "노드 Ready ${ready}/2"
k rollout status sts/postgres --timeout=60s >/dev/null || later "DB가 준비되지 않음"
free=$(df --output=avail -BG / | tail -1 | tr -dc 0-9)
[ "$free" -ge 5 ] || later "node-1 디스크 여유 ${free}G"
if [ $DRY = 1 ]; then echo "확인만: ${CUR:0:7} → ${SHA:0:7} 배포 가능 (이미지 3개, 노드 ${ready}개, 디스크 ${free}G)"; exit 0; fi

log "시작: ${CUR:0:7} → ${SHA:0:7}"
trap 'log "중단됨 (줄 $LINENO, ${SHA:0:7})"' ERR
fail() { trap - ERR; log "실패: $1 (${SHA:0:7})"; echo "$SHA" >> "$STATE/failed"; exit 1; }
BAK="$STATE/kustomization.before-${SHA:0:7}.yaml"
cp "$K8S/kustomization.yaml" "$BAK"

# 4. DB 백업 (33-db-jobs.yaml 의 db-backup 과 같은 방식, node-2 /srv/kosis/backup)
J="db-backup-predeploy-$(date +%m%d%H%M%S)"
k create job --from=cronjob/db-backup "$J" >/dev/null
k wait --for=condition=complete "job/$J" --timeout=300s >/dev/null || fail "DB 백업 ($J)"

# 5. DB 변경 (새 이미지의 alembic). 실패하면 앱을 바꾸지 않는다
k delete job migrate --ignore-not-found --cascade=foreground >/dev/null
sed "s#image: kosis/migrate#image: $IMG-migrate:sha-$SHA#" "$K8S/11-migrate-job.yaml" | k apply -f - >/dev/null
k wait --for=condition=complete job/migrate --timeout=600s >/dev/null || fail "DB 변경 (앱은 그대로)"

# 6. 앱 교체
undo() {
  trap - ERR
  log "앱을 이전 판으로 돌림: $1"
  cp "$BAK" "$K8S/kustomization.yaml"
  kubectl apply -k "$APPLY" >/dev/null || true
  k rollout status deploy/backend --timeout=420s >/dev/null || true
  k rollout status deploy/frontend --timeout=300s >/dev/null || true
  fail "$1 (앱은 이전 판, DB 변경은 남음)"
}
sed -i -E "/kosis-agent-(backend|migrate|frontend)/s/newTag: \"[^\"]*\"/newTag: \"sha-$SHA\"/" "$K8S/kustomization.yaml"
kubectl apply -k "$APPLY" >/dev/null || undo "설정 적용"
k rollout status deploy/backend --timeout=420s >/dev/null || undo "backend 교체"
k rollout status deploy/frontend --timeout=300s >/dev/null || undo "frontend 교체"

# 7. 확인 (GPU 서버가 꺼져 있어도 통과한다. 답변은 따로 확인)
sleep 5
k exec deploy/frontend -- wget -qO- -T 10 http://backend:8000/health >/dev/null || undo "backend /health"
code=$(curl -s -o /dev/null -w '%{http_code}' -m 20 "$SITE/" || true)
[ "$code" = 200 ] || undo "첫 화면 응답 $code"

trap - ERR
echo "$SHA" > "$STATE/current"
log "완료: ${SHA:0:7} (이전 ${CUR:0:7}, 백업 $J)"
