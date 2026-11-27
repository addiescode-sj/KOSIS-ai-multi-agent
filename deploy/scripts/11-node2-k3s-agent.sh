#!/usr/bin/env bash
# node-2: k3s agent(작업)로 합류. 사용: K3S_URL=... K3S_TOKEN=... bash 11-node2-k3s-agent.sh
set -euo pipefail
: "${K3S_URL:?node-1 스크립트가 출력한 K3S_URL}" "${K3S_TOKEN:?node-1 스크립트가 출력한 K3S_TOKEN}"
PRIV_IP="$(hostname -I | awk '{print $1}')"
curl -sfL https://get.k3s.io | K3S_URL="$K3S_URL" K3S_TOKEN="$K3S_TOKEN" sh -s - agent --node-ip "$PRIV_IP"
echo "node-1에서 확인: kubectl get nodes  (둘 다 Ready)"
