#!/usr/bin/env bash
# GPU 서버 1단계: NVIDIA 드라이버 580 (Ubuntu 26.04용). 끝나면 재부팅.
# 학원 튜토리얼(24.04, CUDA 저장소 + dkms로 직접 빌드)과 다르게, Ubuntu가 AWS 커널에 맞춰 미리 빌드한
# 모듈(linux-modules-nvidia-580-server-aws)을 쓴다 → 커널 7.0에서 빌드 실패 위험 없음. (2026-11-23 패키지 확인)
set -euo pipefail
lspci | grep -i nvidia || { echo "GPU 장치가 안 보입니다 → 강사에게 문의"; exit 1; }
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
sudo apt-get update
sudo -E apt-get -y full-upgrade            # 커널을 미리 빌드된 드라이버 모듈과 같은 버전으로 맞춤
sudo -E apt-get install -y linux-modules-nvidia-580-server-aws nvidia-headless-580-server nvidia-utils-580-server
echo "재부팅합니다. 3~5분 뒤 다시 접속해서 nvidia-smi 확인"; sudo reboot
