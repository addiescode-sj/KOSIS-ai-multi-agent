#!/usr/bin/env bash
# 3대 공통: 패키지 갱신, 시간대, 기본 도구. 사용: bash 00-common.sh <호스트이름>
set -euo pipefail
HOST="${1:?호스트 이름을 넣으세요 (kosis-node-1 | kosis-node-2 | kosis-inference)}"
sudo hostnamectl set-hostname "$HOST"
sudo timedatectl set-timezone Asia/Seoul
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get -y upgrade
sudo apt-get install -y curl git jq htop pciutils
echo "== 확인 =="; hostname; free -h; df -h /; ip -4 -o addr show scope global | awk '{print "내부 IP:", $4}'
