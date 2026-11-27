# 인프라 운영 절차 (2026-11-27 기준)

명령은 따로 적지 않으면 **node-1**에서 실행한다. 이 문서의 `<…>`는 실제 값으로 바꾼다. 실제 값은 저장소에 없고, 담당자끼리 따로 전달한다.

## 0. 처음 받을 것 (저장소 밖)

| 무엇 | 용도 |
|---|---|
| 서버 접속 키(`.pem`) | 3대 모두 같은 키. `~/.ssh/`에 두고 `chmod 600` |
| ssh 설정 | 아래 예시 + 실제 주소 |
| 학원 클라우드 콘솔 권한 | 서버 켜기·끄기. 권한이 없으면 가진 사람에게 부탁해야 한다 |
| node-1 `~/deploy/site.env` 내용 | 서버에 이미 있다. 새로 설치할 때만 필요 |

ssh 설정 예시(`~/.ssh/config`). 공인 주소는 node-1만 있다. node-2와 GPU는 node-1을 거쳐(ProxyJump) 내부 IP로 들어간다.

```
Host kosis-node-1
  HostName <node-1 공인 IP>
  User ubuntu
  IdentityFile ~/.ssh/<키 파일>.pem
Host kosis-node-2
  HostName <node-2 내부 IP>
  User ubuntu
  IdentityFile ~/.ssh/<키 파일>.pem
  ProxyJump kosis-node-1
Host kosis-inference
  HostName <GPU 내부 IP>
  User ubuntu
  IdentityFile ~/.ssh/<키 파일>.pem
  ProxyJump kosis-node-1
```

## 1. 켜고 끄기

**켜는 순서: node-1 → node-2 → GPU.** 학원 콘솔에서 켠다.
- node-1이 꺼져 있으면 다른 두 대에 ssh로 들어갈 수 없다(ProxyJump).
- k3s 관리 기능도 node-1에 있다.
- k3s·DB·앱은 저절로 올라온다. 1~2분 걸린다.
- GPU 컨테이너도 저절로 켜진다(`restart: unless-stopped`). vLLM이 준비되기까지 **약 4분** 걸린다.
- GPU가 준비되기 전 질문에는 503이 나간다. GPU가 뜨면 다음 질문부터 저절로 된다.

**끄기**
- 순서는 상관없다.
- 쓰지 않을 때는 3대 모두 끈다. GPU는 시간 예산이 있다.
- 학원 콘솔에는 **자동 중지(기본 2시간)**가 있다. 시연·QA 중이면 시간을 늘리거나 다시 켠다.

**꺼져 있다 켜지면**
- DB 백업(04:00)·정리(04:30)·운영자 페이지 정리(03:30)를 놓쳤어도 켜진 뒤 20시간 안에 한 번 실행된다.

## 2. 점검 (켠 뒤 5분)

```bash
kubectl get nodes                                  # 둘 다 Ready
kubectl -n kosis get pods -o wide | grep -v Completed   # backend·frontend 노드마다 1개, postgres·kosis-admin Running
kubectl -n kosis get cronjob                       # db-backup, db-cleanup, kosis-admin-cleanup
kubectl top nodes                                  # 메모리: 평소 노드당 2GB 안팎 / 8GB
df -h /                                            # node-1 30% 안팎, node-2 16% 안팎
. ~/deploy/site.env
curl -s -o /dev/null -w '%{http_code}\n' "https://$SITE_HOST/"         # 200
curl -s "http://$GPU_IP:8002/health"; echo         # {"status":"ok","kind":"embed",...,"ckpt":"A1-e3.pt@1a8018f6aff2"}
curl -s "http://$GPU_IP:8003/health"; echo         # {"status":"ok","kind":"rerank",...,"ckpt":"RR-A1-e3.pt@c344f8856143"}
kubectl -n kosis logs deploy/backend --since=10m | grep -ciE "error|traceback"   # 0
```

GPU 서버 자체 확인:

```bash
ssh kosis-inference 'nvidia-smi --query-gpu=memory.used,memory.total --format=csv; sudo docker ps --format "{{.Names}} {{.Status}}"; df -h /'
```

- 정상이면 GPU 메모리는 약 37 / 45GiB다.
- 컨테이너 3개(vllm, embedder, reranker)가 떠 있어야 한다.
- 디스크는 80% 안팎이고, 여유가 15GB 아래로 내려가면 정리한다(9절).

## 3. 배포 (앱)

**자동 배포 흐름**
- main에 merge되면 GitHub Actions가 이미지 3개를 GHCR에 저장한다(`sha-<커밋 전체>`).
- 서버는 `deploy/scripts/60-deploy.sh`로 그 커밋을 배포한다.
- 순서는 다음과 같다. 실패하면 앱을 이전 판으로 돌린다. DB 변경은 자동으로 되돌리지 않는다.
  1. 이미지 3개가 있는지 확인
  2. 서버 상태 확인
  3. DB 백업(node-2)
  4. DB 변경(alembic)
  5. 앱 교체
  6. 확인(`/health`, 첫 화면 200)

```bash
bash ~/deploy/scripts/60-deploy.sh --dry-run        # 확인만 (main 최신, 이미지·노드·디스크)
bash ~/deploy/scripts/60-deploy.sh                  # main 최신 커밋 배포
bash ~/deploy/scripts/60-deploy.sh --sha <커밋>     # 그 커밋 배포
tail ~/deploy/state/deploy.log                      # 기록. 마지막 성공 커밋은 ~/deploy/state/current
```

**자동 실행(3분마다 `--auto`)**
- 등록만 되어 있고 **꺼져 있다**(`bash ~/deploy/scripts/61-install-timer.sh`).
- 켜기: `sudo systemctl enable --now kosis-deploy.timer`
- 끄기: `sudo systemctl disable --now kosis-deploy.timer`

**배포 뒤 확인**
- 질문 몇 개를 화면에서 직접 해 본다. 예: "2024년 출생아 수", 이어 묻기 "대구는?", 인사, 범위 밖 질문.
- `60-deploy.sh`는 GPU가 꺼져 있어도 통과한다. 그래서 답변은 따로 확인해야 한다.

**k8s 설정을 바꿀 때**
- `deploy/k8s/*.yaml`을 고친다.
- 서버 `~/deploy`에 같은 파일을 넣고 `60-deploy.sh`로 배포한다(`kubectl apply -k ~/deploy/overlay`).
- 손으로 `apply -k` 하면 DB 변경을 건너뛴다. 적용 전에 `kubectl diff -k ~/deploy/overlay`로 무엇이 바뀌는지 본다.

**GHCR을 못 쓸 때(CI 실패 등)**
- `30-build-images.sh`로 node-1에서 빌드한다. 파일 맨 위 주석대로 태그와 `imagePullPolicy`를 바꾼다.

**운영자 페이지(`32-admin.yaml`)**
- 이미지가 GHCR에 없어 node-1에서 `docker build -f admin/Dockerfile -t kosis/admin:<태그> .`로 만든다. 앱은 node-2에서 돌므로 node-2에도 넣는다(이 PC에서):
  `ssh kosis-node-1 "sudo docker save kosis/admin:<태그>" | ssh kosis-node-2 "sudo k3s ctr images import -"`
- 태그는 그 파일에 적는다.
- 처음 설치는 `deploy/k8s/01-secrets.README.md`의 "운영자 페이지"를 따른다.

## 4. 되돌리기

```bash
bash ~/deploy/scripts/63-rollback.sh <커밋>    # 그 커밋으로 앱을 되돌리고 자동 배포를 멈춤
bash ~/deploy/scripts/63-rollback.sh --resume  # 고친 커밋이 main에 들어온 뒤 멈춤 풀기
```

- DB는 되돌리지 않는다.
- 이전 앱이 새 DB 구조에서 돌지 않으면 5절의 복원을 사람이 결정한다.
- 배포 직전 백업 이름은 `deploy.log`에 있다(`db-backup-predeploy-…`).

## 5. DB 백업·복원

**예약** (`deploy/k8s/33-db-jobs.yaml`)
- `db-backup`: 매일 04:00. `pg_dump`를 node-2 `/srv/kosis/backup/kosis-YYYYMMDD-HHMM.dump`로 받는다. 7일 지난 파일은 지운다.
- `db-cleanup`: 매일 04:30. 26시간 안의 백업이 있을 때만 오래된 대화를 지운다.
  - 지우는 것: 1시간 넘게 안 쓴 비회원·미저장 대화 등.
  - 규칙은 `docs/service/api.md` 4장에 있다.

```bash
kubectl -n kosis create job --from=cronjob/db-backup db-backup-manual-$(date +%m%d%H%M)   # 지금 백업
ssh kosis-node-2 'sudo ls -l /srv/kosis/backup'                                          # 파일 보기 (폴더 권한 700, 999번 사용자)
```

**복원 시험** (운영 DB는 건드리지 않음. 11/26에 성공)

```bash
F=kosis-<날짜-시각>.dump
ssh kosis-node-2 "sudo cat /srv/kosis/backup/$F" | kubectl -n kosis exec -i postgres-0 -- sh -c 'cat > /tmp/r.dump'
kubectl -n kosis exec postgres-0 -- sh -c 'createdb -U "$POSTGRES_USER" restore_test && pg_restore -U "$POSTGRES_USER" -d restore_test /tmp/r.dump'
kubectl -n kosis exec postgres-0 -- psql -U kosis -d restore_test -c "select count(*) from users"   # 운영 DB와 비교
kubectl -n kosis exec postgres-0 -- sh -c 'dropdb -U "$POSTGRES_USER" restore_test; rm /tmp/r.dump'
```

**실제 복원** (사람이 결정)
1. 앱을 멈춘다: `kubectl -n kosis scale deploy/backend deploy/kosis-admin --replicas=0`
2. 위처럼 파일을 옮기고 운영 DB에 덮어쓴다: `pg_restore -U kosis -d kosis --clean --if-exists /tmp/r.dump`
3. 백업에는 DB 계정이 없다. 새 DB라면 운영자 계정 `kosis_admin`과 `admin/sql/grants.sql`을 다시 만든다(01-secrets.README).
4. 앱을 다시 켠다: `kubectl -n kosis scale deploy/backend --replicas=2; kubectl -n kosis scale deploy/kosis-admin --replicas=1`

## 6. 키·비밀값

- 값은 대화·문서·파일에 남기지 않는다. 화면에 찍히는 명령도 쓰지 않는다.
- **이름만 보는 명령:** `kubectl -n kosis get secret kosis-env -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'`
  - `-o yaml`이나 `custom-columns`는 **값이 찍힌다.** 쓰지 않는다.

| Secret | 들어 있는 것 | 넣는 방법 |
|---|---|---|
| `kosis-db` | DB 계정·비밀번호 | `51-make-secrets.sh`가 처음 한 번 무작위로 만듦 |
| `kosis-env` | DB 주소, 벡터 묶음, 가중치 이름, Qwen 주소·키 | `51-make-secrets.sh` (아래) |
| `kosis-keys` | HCX 키, 카카오 ID·시크릿·돌아올 주소 | `ssh -t kosis-node-1 bash deploy/scripts/50-set-keys.sh` (화면에 안 보임, 빈 칸 Enter = 그대로) |
| `kosis-admin` | 운영자 DB 주소, 운영자 회원 ID | `01-secrets.README.md` "운영자 페이지" |

`kosis-env` 다시 만들기(vLLM 키를 GPU 서버에서 바로 넘김, 이 PC에서):

```bash
ssh kosis-inference "grep ^VLLM_API_KEY= ~/deploy/gpu/.env" | ssh kosis-node-1 bash deploy/scripts/51-make-secrets.sh
ssh kosis-node-1 kubectl -n kosis rollout restart deploy/backend
```

카카오 콘솔의 "Redirect URI"는 `https://<사이트 주소>/auth/kakao/callback`과 같아야 한다.

## 7. 임베딩·리랭커 가중치와 벡터 교체

임베딩을 바꾸면 벡터 묶음도 **같은 가중치로 다시 만들어** 함께 바꾼다.
- backend는 임베딩 서버 `/health`의 `ckpt`와 벡터 묶음의 `embed_ckpt`가 같아야 검색한다.

**순서** (11/26 A1-e3 교체 때 한 대로)
1. **가중치 올리기:** `.pt`와 `.json`을 GPU `/srv/models/embedder/`, `/srv/models/reranker/`에 scp한다. 원본과 해시(sha256)가 같은지 확인한다.
2. **벡터 만들기:** GPU 서버에서 임시 컨테이너를 쓴다(이미지 `kosis/inference`, 백엔드와 같은 커밋의 `vendor/phase2/src`).
   - 명령: `python -m kosis_rag_build.cli vectors --model bge-m3-ft --ckpt <새 임베딩.pt> --text-format n2-L60 --export <이름>.npz`
   - 2,364건에 몇 초 걸린다.
   - 만든 방법이 맞는지는, 예전 가중치로 같은 방법을 써서 DB에 있던 것과 코사인이 1.0인지로 확인한다.
3. **벡터 넣기:** npz를 node-1·node-2 `/srv/kosis/data/vectors/`에 복사한다.
   - GPU → 이 PC → node 순서로 파이프로 옮긴다. 서버끼리는 키가 없다.
   - `deploy/k8s/12-vectors-job.yaml`의 파일 이름을 바꾼다. `kubectl -n kosis delete job vectors`로 지난 작업을 지운 뒤 그 파일을 적용한다.
   - 이전 묶음은 DB에 남아 되돌리기에 쓴다.
4. **GPU 설정 바꾸기:** `~/deploy/gpu/docker-compose.inference.yml`의 `EMBED_CKPT`·`RERANK_CKPT`를 바꾸고 `sudo docker compose -f docker-compose.inference.yml up -d`.
5. **backend 설정 바꾸기:** `kosis-env`의 이름 3개(`VECTOR_COLLECTION`, `EMBED_CKPT`, `RERANK_CKPT`)를 바꾼다.
   - `51-make-secrets.sh`를 고쳐 다시 만들거나, 그 3개만 `kubectl patch`한다.
   - 그 뒤 `rollout restart deploy/backend`.
6. **확인:** 2절의 `/health` ckpt, 질문 몇 개.

## 8. GPU 서버

```bash
ssh kosis-inference
cd ~/deploy/gpu
sudo docker compose -f docker-compose.gpu.yml up -d          # vLLM (8000). 시작 약 4분
sudo docker compose -f docker-compose.inference.yml up -d    # 임베딩 8002 · 리랭커 8003
sudo docker compose -f docker-compose.gpu.yml logs --tail 50 vllm
```

- vLLM 키는 같은 폴더 `.env`(chmod 600)에 있다. backend는 `kosis-env`의 `STAGE2_API_KEY`로 같은 값을 쓴다.
- `--max-num-seqs=32`를 빼면 시작에 실패한다. 기본값 256은 Qwen3.8 선형 어텐션 상태 공간을 넘는다.

## 9. 정리(디스크)

- **node-1:** 지난 이미지와 빌드 캐시를 지운다.
  - `sudo docker images`, `sudo docker builder prune -f`
  - k3s 쪽은 `sudo k3s crictl images`, `sudo k3s crictl rmi <이미지>`
  - 지금 판과 바로 이전 판은 남긴다(되돌리기).
- **node-2:** `sudo k3s crictl images`. 백업 폴더는 7일 보관이라 따로 정리하지 않는다.
- **GPU:** 디스크가 가장 빠듯하다(96GB 중 80% 안팎). 안 쓰는 컨테이너·이미지(`sudo docker ps -a`, `sudo docker images`)와 예전 가중치를 정리한다.
  - QA가 끝나기 전에는 예전 가중치(`E2s-e2`)를 지우지 않는다.

## 10. 노드를 다시 붙일 때

- node-2를 새로 만들었거나 클러스터에서 빠졌을 때 쓴다.
- node-1에서 `sudo cat /var/lib/rancher/k3s/server/node-token`으로 토큰을 얻는다.
  - 토큰은 비밀이다. 문서에 남기지 않는다.
- node-2에서 실행한다: `K3S_URL=https://<node-1 내부 IP>:6443 K3S_TOKEN=<토큰> bash 11-node2-k3s-agent.sh`
- 그 뒤 `12-apparmor.sh`를 실행하고, `/srv/kosis/data` 사본과 `/srv/kosis/backup` 폴더를 만든다(`deploy/README.md`).
