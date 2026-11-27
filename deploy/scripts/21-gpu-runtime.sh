#!/usr/bin/env bash
# GPU 서버 2단계(재부팅 후): 도커 + NVIDIA 컨테이너 도구. CUDA·아나콘다는 설치하지 않음(컨테이너 안에 포함).
set -euo pipefail
nvidia-smi
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
sudo docker run --rm --gpus all ubuntu nvidia-smi
echo "완료. 다시 접속하면 sudo 없이 docker 사용 가능"
