#!/usr/bin/env bash
# node-1에서 실행: 클러스터·서버 간·인터넷 연결을 한 번에 점검한다.
# 사용: NODE2_IP=<node-2 내부IP> GPU_IP=<GPU 내부IP> bash 40-network-check.sh
# 사전: GPU 서버에서 시험용 응답기를 띄워 둔다 →  python3 -m http.server 8000 & python3 -m http.server 8001 &
set -uo pipefail
: "${NODE2_IP:?node-2 내부 IP}" "${GPU_IP:?GPU 서버 내부 IP}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0
check() {  # check "설명" 명령...
  if eval "$2" >/dev/null 2>&1; then echo "  [통과] $1"; pass=$((pass+1)); else echo "  [실패] $1"; fail=$((fail+1)); fi
}

echo "== 1. 쿠버네티스 노드 =="
kubectl get nodes -o wide
check "노드 2대 모두 Ready" "[ \$(kubectl get nodes --no-headers | grep -c ' Ready') -eq 2 ]"

echo "== 2. 시험 컨테이너 배포 (노드마다 1개) =="
kubectl apply -f "$DIR/k8s-test/whoami.yaml" >/dev/null
kubectl -n nettest rollout status deploy/whoami --timeout=120s
kubectl -n nettest get pods -o wide
check "시험 컨테이너가 서로 다른 노드 2곳에 뜸" \
  "[ \$(kubectl -n nettest get pods -o jsonpath='{range .items[*]}{.spec.nodeName}{\"\\n\"}{end}' | sort -u | wc -l) -eq 2 ]"

echo "== 3. 노드를 넘는 컨테이너 간 통신 (8472/UDP) =="
for ip in $(kubectl -n nettest get pods -o jsonpath='{.items[*].status.podIP}'); do
  check "컨테이너 $ip 응답" "kubectl -n nettest run probe-\$RANDOM --rm -i --restart=Never --image=busybox:1.36 -- wget -qO- -T 5 http://$ip"
done

echo "== 4. 입구(Traefik 80번)가 두 노드 모두에서 응답 =="
check "node-1 내부IP:80" "curl -sf -m 5 http://$(hostname -I | awk '{print $1}')/"
check "node-2 내부IP:80" "curl -sf -m 5 http://$NODE2_IP/"

echo "== 5. 서비스 서버 → GPU 서버 =="
check "GPU:8000 (vLLM 자리)" "curl -s -m 5 -o /dev/null http://$GPU_IP:8000/"
check "GPU:8001 (임베딩·리랭커 자리)" "curl -s -m 5 -o /dev/null http://$GPU_IP:8001/"

echo "== 6. 인터넷으로 나가기 (NAT 없이) =="
check "OpenAI" "curl -s -m 5 -o /dev/null https://api.openai.com"
check "CLOVA Studio(HCX)" "curl -s -m 5 -o /dev/null https://clovastudio.stream.ntruss.com"
check "KOSIS OpenAPI" "curl -s -m 5 -o /dev/null https://kosis.kr"
check "GitHub 컨테이너 저장소" "curl -s -m 5 -o /dev/null https://ghcr.io"
check "Hugging Face" "curl -s -m 5 -o /dev/null https://huggingface.co"

echo "== 결과: 통과 $pass / 실패 $fail =="
echo "남은 확인 (내 PC에서): curl http://<node-1 공인주소>/  ,  curl http://<node-2 공인주소>/  → whoami 응답"
echo "GPU → node-1 방향은 GPU 서버에서: curl -s -m 5 http://<node-1 내부IP>/"
