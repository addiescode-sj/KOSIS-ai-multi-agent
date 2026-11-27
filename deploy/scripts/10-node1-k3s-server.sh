#!/usr/bin/env bash
# node-1: k3s server(관리+작업) 설치. 사용: bash 10-node1-k3s-server.sh
set -euo pipefail
PRIV_IP="$(hostname -I | awk '{print $1}')"
curl -sfL https://get.k3s.io | sh -s - server --node-ip "$PRIV_IP" --write-kubeconfig-mode 644
mkdir -p ~/.kube && cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
sudo kubectl label node "$(hostname)" kosis/role=data --overwrite   # DB(PostgreSQL + pgvector)를 이 노드에 고정
echo "== node-2 합류에 쓸 값 =="
echo "K3S_URL=https://${PRIV_IP}:6443"
echo "K3S_TOKEN=$(sudo cat /var/lib/rancher/k3s/server/node-token)"
kubectl get nodes -o wide
