#!/usr/bin/env bash
# node-1: 자동 배포 예약(3분마다 60-deploy.sh --auto)을 등록한다. 등록만 하고 켜지 않는다.
#   켜기:   sudo systemctl enable --now kosis-deploy.timer
#   끄기:   sudo systemctl disable --now kosis-deploy.timer   (진행 중인 배포는 끝까지 함)
#   상태:   systemctl status kosis-deploy.timer · tail ~/deploy/state/deploy.log
set -euo pipefail
sudo tee /etc/systemd/system/kosis-deploy.service >/dev/null <<EOF
[Unit]
Description=KOSIS 자동 배포 (main 최신 커밋, 60-deploy.sh --auto)
After=network-online.target k3s.service
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
ExecStart=/usr/bin/bash $HOME/deploy/scripts/60-deploy.sh --auto
TimeoutStartSec=30min
EOF
sudo tee /etc/systemd/system/kosis-deploy.timer >/dev/null <<'EOF'
[Unit]
Description=KOSIS 자동 배포 확인 (3분마다)

[Timer]
OnBootSec=5min
OnUnitInactiveSec=3min
Unit=kosis-deploy.service

[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
echo "등록함(꺼져 있음): $(systemctl is-enabled kosis-deploy.timer 2>/dev/null || true) / $(systemctl is-active kosis-deploy.timer 2>/dev/null || true)"
