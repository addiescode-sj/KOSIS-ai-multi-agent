# 배포 요청: 임베딩·리랭커 추론 서버 분리 (브랜치 `min-multiagent-service`)

backend가 검색 모델(임베딩·1차 리랭커)을 프로세스 안에서 torch로 돌리지 않고, GPU 서버의 추론 서버를 HTTP로 부르도록 바꿈.
코드는 기본값이 예전 동작 그대로라, 아래 설정을 넣어야 새 방식으로 동작함.

## 1. 구성

```
CPU 서버 (k3s)                          GPU 서버 (private IP 고정)
  backend ── EMBED_URL ───────────────▶  embedder  :8001  (E2s-e2)
          ── RERANK_URL ──────────────▶  reranker  :8002  (RR-E2s-e2)
          ── STAGE2_BASE_URL (기존) ──▶  vLLM Qwen  :<포트>/v1
  frontend, PostgreSQL(pgvector) 그대로
```

## 2. GPU 서버: 추론 서버 띄우기

준비물
- NVIDIA Container Toolkit (컨테이너에서 GPU 사용)
- 레포의 `data/models/` 아래 가중치 4개 (git에 없음, 별도 전달)
  - `data/models/embedder/E2s-e2.pt`, `E2s-e2.json`
  - `data/models/reranker/RR-E2s-e2.pt`, `RR-E2s-e2.json`

실행 (레포 루트)

```bash
git submodule update --init vendor/phase2   # 이미지가 vendor/phase2의 모델 로딩 코드를 복사함
docker compose -f docker-compose.inference.yml up -d --build
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8002/health
```

- 두 응답 모두 `"status":"ok"`, `"device":"cuda"`, `"doc_format":"n2-L60"`이어야 함.
- `ckpt` 값은 4절 컬렉션 확인에 씀. 임베딩 서버는 `E2s-e2.pt@1a794c032797`이어야 함. 컬렉션과 맞는지는 `inference/check_collection.py`로 확인할 수 있음(`inference/README.md`).
- 첫 빌드는 CUDA torch와 기반 모델을 받아 오래 걸림(이미지 약 10GB). 컨테이너마다 모델 적재에 30초~1분.
- 두 컨테이너가 GPU 1장을 Qwen(vLLM)과 나눠 씀. 둘이 합쳐 약 3~5GB이므로 vLLM `--gpu-memory-utilization`을 낮춰 5GB쯤 남겨야 함.
- GPU가 컨테이너에 안 보이면 CPU로 내려가지 않고 시작에서 실패함(`INFER_DEVICE: cuda`).
- 가중치를 바꿀 때는 `docker-compose.inference.yml`의 `EMBED_CKPT`·`RERANK_CKPT`를 고침(.env 값은 이 compose에 안 들어감).

## 3. backend 이미지 빌드

```bash
docker build -f backend/Dockerfile --target backend --build-arg WITH_EMBED=0 -t <레지스트리>/kosis-agent-backend:<태그> .
```

- `WITH_EMBED=0`: torch·sentence-transformers를 빼고 빌드함(이미지가 크게 줄어듦).
- 인자를 빼면 기본값 1로 예전처럼 torch가 들어감. 동작은 하지만 이미지가 큼.
- frontend 이미지는 바뀐 것 없음.

## 4. backend 환경변수 (k3s Deployment의 ConfigMap/Secret)

추가

| 변수 | 값 | 설명 |
|---|---|---|
| `EMBED_URL` | `http://<GPU private IP>:8001` | 질의 임베딩 서버 |
| `RERANK_URL` | `http://<GPU private IP>:8002` | 1차 리랭커 서버 |
| `AGENT_GRAPH` | `multi` | 멀티에이전트 그래프로 답함(빼면 기존 그래프). 테스트용 임시 스위치 |
| `INTERPRETER_MODEL` | `stage2` | 이어 묻기 해석·질문 나누기도 Qwen(STAGE2 서버)으로. 빼면 gpt-4o-mini(OPENAI_API_KEY 필요) |
| `KOSIS_DISABLE_DEV_ROUTES` | `1` | **필수.** 개발용 주소(`/dev/*`, 옛 `/tools/*`·`/threads/*`·`POST /chat`)를 404로 막음. 없으면 thread_id만으로 남의 대화 상태를 볼 수 있음 |

확인

| 변수 | 값 |
|---|---|
| `STAGE2_BASE_URL` | `http://<GPU private IP>:<vLLM 포트>/v1` (Qwen, 기존 변수) |
| `VECTOR_COLLECTION` | E2s-e2로 만든 컬렉션 이름 |

- `VECTOR_COLLECTION` 컬렉션의 메타 `embed_ckpt`가 임베딩 서버 `/health`의 `ckpt`와 같아야 검색이 열림. 다르면 다른 가중치로 조용히 검색하지 않고 오류를 냄.
- `EMBED_CKPT`·`RERANK_CKPT`는 URL을 넣으면 backend에서 쓰지 않음. 남아 있어도 무시됨.
- backend의 `data/` 마운트(tables.jsonl, values 등)는 예전 그대로 필요함. `data/models`는 backend에 필요 없음.

## 5. 네트워크·보안

- 추론 서버에는 인증이 없음. GPU 서버 포트 8001·8002(그리고 vLLM 포트)는 클러스터 노드 private IP에서만 받도록 해야 함.
- k3s 파드에서 GPU 서버 private IP로 바로 나갈 수 있으면 추가 설정 필요 없음.

## 6. 배포 뒤 확인

1. backend 파드에서 `curl http://<GPU private IP>:8001/health` 응답 확인.
2. 서비스에서 질문 하나("2024년 출생아 수")를 보냄. 검색 준비는 첫 질문 때 하므로 첫 답은 몇 초 더 걸림.
3. 실패하면 첫 질문이 503 `agent not available: ...`로 답하고, 메시지에 원인이 나옴.
   - `... the embed server http://... serves '...'`: 컬렉션과 임베딩 가중치가 다름 → `VECTOR_COLLECTION` 또는 GPU 서버 가중치 확인
   - `InferenceError: ... http://...:8001 ...`: 연결 실패 → IP·포트·방화벽 확인
   - `no sentence-transformers in this image: set EMBED_URL and RERANK_URL ...`: WITH_EMBED=0 이미지에 URL이 안 들어감 → 4절 확인

## 7. 운영 주의

- **가중치 교체 뒤 backend 재시작.** backend는 처음 연결할 때 추론 서버의 `ckpt`를 기억하고, 응답의 `ckpt`가 바뀌면 거절함. GPU 서버의 가중치를 바꾸면 backend 파드를 재시작해야 하고, 새 가중치에 맞는 `VECTOR_COLLECTION`도 함께 바꿔야 함.
- **추론 서버 여러 개를 한 주소 뒤에 둘 때** 모두 같은 가중치여야 함(다르면 요청마다 실패할 수 있음).
- **`/health`는 추론 서버 상태를 보지 않음.** GPU 서버가 꺼져 있어도 backend `/health`는 ok임. 이 경우 첫 질문이 최대 60초(연결 시간 제한) 기다린 뒤 503으로 실패함. GPU 서버 상태는 `:8001/health`·`:8002/health`로 따로 감시해야 함.

## 8. 되돌리기

`EMBED_URL`·`RERANK_URL`을 지우고 `WITH_EMBED=1`(기본)로 빌드한 이미지를 쓰면 예전처럼 backend 안에서 모델을 로드함(이때는 `EMBED_CKPT`·`RERANK_CKPT`와 `data/models`가 backend에 필요).

## 관련 파일

- `backend/app/search_open.py`: 원격/로컬 선택과 가중치 검사
- `inference/README.md`: 추론 서버 API 계약·실행
- `docker-compose.inference.yml`: GPU 서버 compose
