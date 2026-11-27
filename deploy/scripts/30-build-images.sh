#!/usr/bin/env bash
# node-1: GHCR 이미지를 쓸 수 없을 때(CI 실패 등)의 대비. 저장소 코드로 이미지 3개(backend = torch 없는 판)를 빌드해 node-1 k3s에 넣는다.
# 사용: bash 30-build-images.sh [태그]    코드 위치: KOSIS_SRC (기본 ~/kosis-ai, submodule 포함)
# 이 이미지를 쓰려면 k8s/kustomization.yaml images 의 newName 을 지우고 newTag 를 이 태그로, 각 yaml 의 imagePullPolicy 를 Never 로 바꾼다.
# node-2 에도 넣어야 한다: 이 PC에서  ssh kosis-node-1 cat /tmp/kosis-images.tar | ssh kosis-node-2 'cat > /tmp/k.tar && sudo k3s ctr images import /tmp/k.tar'
set -euo pipefail
T="${1:-$(date +%m%d%H%M)}"
cd "${KOSIS_SRC:-$HOME/kosis-ai}"
sudo docker build -q -f backend/Dockerfile --target migrate -t "kosis/migrate:$T" .
sudo docker build -q -f backend/Dockerfile --target backend --build-arg WITH_EMBED=0 -t "kosis/backend:$T" .
sudo docker build -q -t "kosis/frontend:$T" frontend
sudo docker save "kosis/backend:$T" "kosis/migrate:$T" "kosis/frontend:$T" -o /tmp/kosis-images.tar
sudo chmod 644 /tmp/kosis-images.tar
sudo k3s ctr images import /tmp/kosis-images.tar
echo "BUILD_DONE tag=$T commit=$(git rev-parse --short HEAD)"
