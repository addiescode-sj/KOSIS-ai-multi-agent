#!/usr/bin/env bash
# node-1: 서버마다 다른 값(deploy/site.env)으로 k8s 덧씌우기(deploy/overlay)를 만든다.
# 공개 저장소의 k8s 설정은 주소가 빈 값(example.invalid)이고, 서버는 이 덧씌우기를 적용한다(60-deploy.sh).
# site.env 와 overlay/ 는 git에 올리지 않는다(.gitignore). 처음: cp site.env.example site.env 후 값 채우기
# 값은 줄 번호로 바꾼다(환경변수 순서를 지켜 불필요한 다시 시작을 막음). 줄 이름이 다르면(test) 적용이 실패한다.
set -euo pipefail
D="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=/dev/null
. "$D/site.env"
: "${SITE_HOST:?site.env 에 SITE_HOST}" "${GPU_IP:?site.env 에 GPU_IP}"
O="$D/overlay"
mkdir -p "$O"
E=/spec/template/spec/containers/0/env
cat > "$O/kustomization.yaml" <<K
# scripts/05-make-site.sh 가 site.env 로 만든 파일. 손으로 고치지 말고 site.env 를 고친 뒤 다시 만든다. git에 올리지 않음
resources: [../k8s]
patches:
  - target: { kind: Ingress, name: kosis }
    patch: |
      - { op: replace, path: /spec/rules/0/host, value: "$SITE_HOST" }
  - target: { kind: Middleware, name: to-https }
    patch: |
      - { op: replace, path: /spec/redirectRegex/replacement, value: "https://$SITE_HOST/\${1}" }
  - target: { kind: Deployment, name: backend }
    patch: |
      - { op: test,    path: $E/2/name,  value: EMBED_URL }
      - { op: replace, path: $E/2/value, value: "http://$GPU_IP:8002" }
      - { op: test,    path: $E/3/name,  value: RERANK_URL }
      - { op: replace, path: $E/3/value, value: "http://$GPU_IP:8003" }
  - target: { kind: Deployment, name: kosis-admin }
    patch: |
      - { op: test,    path: $E/0/name,  value: ADMIN_PUBLIC_ORIGIN }
      - { op: replace, path: $E/0/value, value: "https://$SITE_HOST" }
K
echo "만듦: $O/kustomization.yaml (사이트 $SITE_HOST)"
