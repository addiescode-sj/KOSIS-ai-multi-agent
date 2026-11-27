# deploy — 서버 배포 설정

서버 3대(CPU k3s 2대 + GPU 1대)의 설정이다. 무엇이 어디에 있는지는 [`docs/infra/README.md`](../docs/infra/README.md), 운영 방법은 [`docs/infra/operations.md`](../docs/infra/operations.md)를 본다.

**공개 저장소라 주소·IP·키를 넣지 않는다.**
- k8s 설정의 주소 자리에는 빈 값(`kosis.example.invalid`, `gpu.example.invalid`)이 들어 있다.
- 서버마다 다른 값은 `site.env`(git 밖) 한 곳에 둔다.
  - `scripts/05-make-site.sh`가 그 값으로 덧씌우기 `overlay/`(git 밖)를 만든다.
  - 서버는 그 덧씌우기를 적용한다.
- 키는 k8s Secret에만 있다(`scripts/50`·`51`, `k8s/01-secrets.README.md`).

## 폴더

| 경로 | 내용 |
|---|---|
| `site.env.example` | 서버별 값 틀(사이트 주소, GPU 내부 IP). `site.env`로 복사해 채운다 |
| `k8s/kustomization.yaml` | 앱 이미지 태그(GHCR `sha-<커밋>`)와 묶음. `60-deploy.sh`가 태그를 바꾼다 |
| `k8s/00-namespace.yaml` | `kosis` 네임스페이스 |
| `k8s/01-secrets.README.md` | Secret 만드는 법(운영자 페이지 포함) |
| `k8s/10-postgres.yaml` | PostgreSQL + pgvector, node-1 고정, 볼륨 10Gi |
| `k8s/11-migrate-job.yaml` | DB 변경(alembic). `60-deploy.sh`가 배포마다 앱보다 먼저 돌린다 |
| `k8s/12-vectors-job.yaml` | 벡터 묶음 넣기(가중치 바꿀 때만) |
| `k8s/30-backend.yaml`·`31-frontend.yaml` | 앱, 2개씩 노드마다 1개 |
| `k8s/32-admin.yaml` | 운영자 앱: 데이터 창구 `/admin/api`, 이전 화면 `/admin/classic`, 정리 작업 03:30. 화면 `/admin`은 프론트(T3-67) |
| `k8s/33-db-jobs.yaml` | DB 백업 04:00(node-2), 정리 04:30 |
| `k8s/39-traefik-https.yaml` | HTTPS 인증서(Let's Encrypt). kube-system이라 따로 적용 |
| `k8s/40-ingress.yaml` | 입구: HTTPS만, `/admin/api`·`/admin/classic`은 운영자 앱(나머지 `/admin`은 프론트), http는 https로 보냄 |
| `scripts/00`~`21` | 서버 처음 설치(아래 순서) |
| `scripts/05-make-site.sh` | `site.env` → `overlay/` |
| `scripts/30-build-images.sh` | GHCR을 못 쓸 때 node-1에서 직접 빌드(대비용) |
| `scripts/40-network-check.sh` | 서버 간·외부 연결 점검 |
| `scripts/50-set-keys.sh`·`51-make-secrets.sh` | 키·앱 설정 Secret |
| `scripts/60-deploy.sh`·`61-install-timer.sh`·`63-rollback.sh` | 배포, 자동 배포 예약(꺼져 있음), 되돌리기 |
| `gpu/docker-compose.gpu.yml` | GPU 서버 vLLM(8000) |
| `gpu/docker-compose.inference.yml` | GPU 서버 임베딩(8002)·1차 리랭커(8003) |

서버 위치
- node-1: 이 폴더 내용이 `~/deploy/`에 있다.
- GPU 서버: `gpu/`가 `~/deploy/gpu/`에 있다.
- 지금은 손으로 복사한다. 저장소를 바꿨으면 서버 사본도 같게 맞춘다.

## 처음 설치 순서 (서버를 새로 만들 때)

1. **3대 공통:** `bash scripts/00-common.sh <kosis-node-1 | kosis-node-2 | kosis-inference>`
2. **k3s**
   - node-1: `bash scripts/10-node1-k3s-server.sh`. 마지막에 합류용 주소·토큰이 나온다. 토큰은 비밀이다.
   - node-2: `K3S_URL=… K3S_TOKEN=… bash scripts/11-node2-k3s-agent.sh`
   - 두 노드: `bash scripts/12-apparmor.sh`
3. **GPU 서버**
   - `bash scripts/20-gpu-driver.sh` → 재부팅 → `bash scripts/21-gpu-runtime.sh`
   - 모델을 `/srv/hf-cache`에 받는다. 미세조정 가중치는 `/srv/models/{embedder,reranker}`에 scp한다(GitHub 금지).
   - `~/deploy/gpu/.env`에 `VLLM_API_KEY`를 넣는다(chmod 600).
   - `inference/` 이미지를 빌드하고 compose 2개를 `up -d`.
4. **연결 점검:** `NODE2_IP=… GPU_IP=… bash scripts/40-network-check.sh`
5. **node-1에 설정 놓기**
   - 이 폴더를 `~/deploy`로 복사한다.
   - `cp site.env.example site.env`로 만들어 값을 채운다(chmod 600).
   - `bash scripts/05-make-site.sh`
6. **데이터**
   - 제공 데이터를 두 노드 `/srv/kosis/data`에 같은 사본으로 둔다(팀원에게 받음, 공개 금지).
   - node-2: `sudo install -d -o 999 -g 999 -m 700 /srv/kosis/backup`
7. **키**
   - `51-make-secrets.sh`(vLLM 키를 표준 입력으로)
   - `50-set-keys.sh`(HCX·카카오)
   - 운영자 페이지 Secret(`k8s/01-secrets.README.md`)
8. **HTTPS:** `kubectl apply -f k8s/39-traefik-https.yaml`
9. **앱:** `bash scripts/60-deploy.sh`(DB 변경 + 앱). 그다음 벡터 `kubectl -n kosis apply -f k8s/12-vectors-job.yaml`.
10. **확인:** `docs/infra/operations.md` 2절 점검
