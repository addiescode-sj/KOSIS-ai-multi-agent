#!/usr/bin/env bash
# 두 노드: k3s 앱 컨테이너용 AppArmor 규칙(kosis-app). containerd 기본 규칙과 같은 차단 + 신호 허용.
# 이유: Ubuntu 26.04에서 기본 규칙이 컨테이너 안 프로세스 사이 신호를 막아(kill EACCES) postgres 시작, 정상 종료가 안 됨 (11/25)
set -euo pipefail
sudo tee /etc/apparmor.d/kosis-app >/dev/null <<'P'
#include <tunables/global>
profile kosis-app flags=(attach_disconnected,mediate_deleted) {
  #include <abstractions/base>
  network,
  capability,
  file,
  umount,
  signal,
  ptrace (trace,read) peer=kosis-app,
  ptrace (trace,read) peer=kosis-app//&unconfined,
  deny @{PROC}/* w,
  deny @{PROC}/{[^1-9],[^1-9][^0-9],[^1-9s][^0-9y][^0-9s],[^1-9][^0-9][^0-9][^0-9]*}/** w,
  deny @{PROC}/sys/[^k]** w,
  deny @{PROC}/sys/kernel/{?,??,[^s][^h][^m]**} w,
  deny @{PROC}/sysrq-trigger rwklx,
  deny @{PROC}/mem rwklx,
  deny @{PROC}/kmem rwklx,
  deny @{PROC}/kcore rwklx,
  deny mount,
  deny /sys/[^f]*/** wklx,
  deny /sys/f[^s]*/** wklx,
  deny /sys/fs/[^c]*/** wklx,
  deny /sys/fs/c[^g]*/** wklx,
  deny /sys/fs/cg[^r]*/** wklx,
  deny /sys/firmware/** rwklx,
  deny /sys/kernel/security/** rwklx,
}
P
sudo apparmor_parser -r -W /etc/apparmor.d/kosis-app
sudo grep '^kosis-app ' /sys/kernel/security/apparmor/profiles
