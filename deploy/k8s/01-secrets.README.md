# 비밀값 만들기 (파일로 남기지 않고 명령으로 바로 생성)

> 11/27: 지금은 `scripts/51-make-secrets.sh`(kosis-db·kosis-env)와 `scripts/50-set-keys.sh`(kosis-keys)를 쓴다(`docs/infra/operations.md` 6절). 아래 "앱 설정" 절은 11/23 첫 설치 때 기록이라 모델 이름(Gemma) 등이 지금과 다르다. "운영자 페이지" 절은 지금도 그대로 쓴다.

node-1에서, 저장소 `.env.example`을 복사해 채운 `~/kosis.env`(chmod 600)로 만든다.
backend는 이 값을 환경변수로 받는다(compose의 env_file과 같은 역할).

```bash
kubectl create namespace kosis

# DB 계정 (postgres 컨테이너용)
kubectl -n kosis create secret generic kosis-db \
  --from-literal=POSTGRES_USER=kosis --from-literal=POSTGRES_DB=kosis \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 16)"

# 비밀번호 확인 (아래 DATABASE_URL에 넣을 값)
kubectl -n kosis get secret kosis-db -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d; echo

# 앱 설정
kubectl -n kosis create secret generic kosis-env --from-env-file="$HOME/kosis.env"
```

`~/kosis.env`에 넣을 것:

```
HCX_API_KEY=...
OPENAI_API_KEY=...
KOSIS_API_KEY=...
VECTOR_COLLECTION=kosis_a_bgeft_<버전>   # 옛 CHROMA_COLLECTION과 같은 값 (벡터는 PostgreSQL에 있음)
EMBED_CKPT=data/models/embedder/<파일>.pt
RERANK_CKPT=data/models/reranker/<파일>.pt
DATABASE_URL=postgresql+psycopg://kosis:<위 비밀번호>@postgres:5432/kosis

# GPU 서버 쓸 때 (Gemma)
STAGE2=gpt
STAGE2_MODEL=google/gemma-4-31B-it-qat-w4a16-ct
STAGE2_BASE_URL=http://<GPU 내부IP>:8000/v1
STAGE2_API_KEY=<VLLM_API_KEY>
Gemma_BASE_URL=http://<GPU 내부IP>:8000/v1
Gemma_API_KEY=<VLLM_API_KEY>
```

값을 바꿀 때:

```bash
kubectl -n kosis create secret generic kosis-env --from-env-file="$HOME/kosis.env" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n kosis rollout restart deploy/backend
```

## 운영자 페이지 (32-admin.yaml, T3-57)

처음 한 번. 비밀번호는 파일이나 대화에 남기지 않고 셸 변수로만 쓴다.

```bash
# 1) DB 계정 kosis_admin 만들기 + 권한 주기 (저장소 admin/sql/grants.sql)
ADMIN_PW="$(openssl rand -hex 16)"
kubectl -n kosis exec -i statefulset/postgres -- psql -U kosis -d kosis -v ON_ERROR_STOP=1 \
  -c "CREATE ROLE kosis_admin LOGIN PASSWORD '$ADMIN_PW'"
kubectl -n kosis exec -i statefulset/postgres -- psql -U kosis -d kosis -v ON_ERROR_STOP=1 < ~/kosis-ai/admin/sql/grants.sql

# 2) Secret (ADMIN_USER_IDS는 처음엔 비워 두고 3)에서 채움)
kubectl -n kosis create secret generic kosis-admin \
  --from-literal=ADMIN_DATABASE_URL="postgresql+psycopg://kosis_admin:${ADMIN_PW}@postgres:5432/kosis" \
  --from-literal=ADMIN_USER_IDS=""
unset ADMIN_PW
```

3) 운영자 ID 넣기: 팀원이 서비스에 카카오 로그인한 뒤 `/admin`을 한 번 연다(404가 보임). 운영자 앱 로그에 회원 ID가 남는다.

```bash
kubectl -n kosis logs deploy/kosis-admin | grep "not on ADMIN_USER_IDS"
kubectl -n kosis patch secret kosis-admin --type merge -p '{"stringData":{"ADMIN_USER_IDS":"<id1>,<id2>"}}'
kubectl -n kosis rollout restart deploy/kosis-admin
```

서비스 마이그레이션이 운영자 앱이 읽는 표를 새로 만들면 `grants.sql`을 다시 실행한다(같은 명령, 여러 번 실행해도 됨).
