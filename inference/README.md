# 1차 검색 모델 서버 (임베딩·리랭커 이미지)

1차 검색 모델(임베딩 `E2s-e2`, 리랭커 `RR-E2s-e2`, 11/25 기준)을 HTTP로 서빙하는 이미지다. 바꾸려면 `docker-compose.inference.yml`의 `EMBED_CKPT`·`RERANK_CKPT`만 고친다.
backend 이미지에서 torch를 빼고 이 서버를 부르게 하는 배선은 "서버 배포용 구조 수정" 작업이 맡는다.

- 이미지 하나, 컨테이너마다 모델 하나: `INFER_MODEL=embed` 또는 `rerank`.
- 로딩은 `vendor/phase2`의 `kosis_rag.adapters.models`(`load_embedder`·`load_cross_encoder`)를 그대로 import한다. 그래서 지금 프로세스 안 검색과 같은 가중치·같은 토큰 길이·GPU면 fp16 규칙으로 계산한다.
- 미세조정 가중치(`.pt` + `.json`)는 이미지에 넣지 않고 `data/models`를 `/models`에 읽기 전용으로 붙인다.
- 공개 기반 모델(BAAI/bge-m3, BAAI/bge-reranker-v2-m3)은 빌드 때 받아 이미지에 넣는다(`PRELOAD=1`, 기본). 서버에서 인터넷 없이 뜬다.

## 파일

| 파일 | 내용 |
| --- | --- |
| `inference/server.py` | FastAPI 서버 (`/health`, `/embed`, `/rerank`) |
| `inference/client.py` | `RemoteEmbedder`·`RemoteScorer`: `models.Embedder`·`CrossEncoderScorer`와 같은 모양, httpx만 import(torch 없음) |
| `inference/Dockerfile` | 이미지 (빌드 위치 = 레포 루트) |
| `inference/requirements.txt` | 서버 의존성 (uv.lock 버전과 같음) |
| `inference/check_parity.py` | 서버 값과 로컬 로딩 값이 같은지 확인 |
| `docker-compose.inference.yml` | `embedder`(8001)·`reranker`(8002) 두 컨테이너 |
| `tests/inference/test_inference_server.py` | 가짜 모델로 서버·클라이언트 계약 테스트 (torch 불필요) |

## API 계약

| 요청 | 본문 | 응답 |
| --- | --- | --- |
| `GET /health` | | `{status, kind, model, ckpt, doc_format, device}` |
| `POST /embed` | `{texts: [str], kind?: "query" \| "document"}` | `{vectors, model, ckpt}` |
| `POST /rerank` | `{question, passages: [str]}` | `{scores, model, ckpt}` |

- `ckpt`는 `adapters.models.ckpt_id` 값(`E2s-e2.pt@<sha256 앞 12자리>`)이다. 컬렉션에 기록된 값과 같아야 검색이 열린다.
- `doc_format`은 `.pt` 옆 `.json`의 값(없으면 `n2-L60`)이다.
- `kind: query`(기본)는 512토큰으로 한 문장씩, `document`는 1024토큰으로 묶어서 계산한다. 벡터는 L2 정규화된다.
- `scores`는 `CrossEncoderScorer.score`와 같은 값(sigmoid)이다. 512토큰.
- 질문 정규화(`norm_query`)는 서버가 하지 않는다. 지금처럼 호출하는 쪽이 한다.
- 한 요청에 최대 256개. 넘으면 422. 이 서버가 맡지 않은 모델을 부르면 404.
- 인증은 없다. 포트 8001·8002를 모든 인터페이스에 열므로, 방화벽에서 같은 대역(backend 머신)만 허용한다.
- 클라이언트는 연결 실패·타임아웃·오류 응답을 URL이 들어간 `InferenceError`로 낸다. 서버 IP가 바뀌면 이 오류가 난다.

## 실행 (PowerShell, 레포 루트)

GPU로 두 서버 띄우기 (NVIDIA Container Toolkit 필요, Docker Desktop은 WSL2 GPU 지원):

```powershell
docker compose -f docker-compose.inference.yml up -d --build
docker compose -f docker-compose.inference.yml ps
curl.exe http://127.0.0.1:8001/health
curl.exe http://127.0.0.1:8002/health
```

첫 빌드는 torch(CUDA)와 기반 모델 두 개를 받아서 오래 걸린다(이미지 약 10GB 안팎). 모델 적재는 컨테이너마다 30초~1분쯤 걸리고, 그동안 `ps`는 `starting`이다.

CPU만 있는 머신: `docker-compose.inference.yml`의 `deploy:` 블록과 `INFER_DEVICE: cuda` 줄을 지우고 CPU torch로 빌드한다.

```powershell
docker build -f inference/Dockerfile --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu -t kosis-inference:latest .
docker compose -f docker-compose.inference.yml up -d
```

backend는 다른 머신(같은 대역)에서 `http://<이 머신 IP>:8001`, `http://<이 머신 IP>:8002`로 부른다. 주소는 backend `.env`의 `EMBED_URL`·`RERANK_URL`로 준다. 같은 대역에서는 매번 같은 주소로 붙는다.

한 머신에서 앱 스택과 같이 띄울 때만(같은 compose 네트워크라 `http://embedder:8001`, `http://reranker:8002`도 됨):

```powershell
docker compose -f docker-compose.yml -f docker-compose.inference.yml up -d --build
```

가중치를 바꾸려면 `docker-compose.inference.yml`의 `EMBED_CKPT`·`RERANK_CKPT`를 고친다(컨테이너 경로 `/models/...`). 이 파일은 `.env`를 읽지 않는다.

## GPU 서버 한 대에 Qwen과 같이 올리기

서버 구성(11/25): CPU 서버에는 backend·frontend·postgres, GPU 서버에는 Qwen(vLLM)·임베더·리랭커. 이 compose는 GPU 서버에서만 띄운다.

- 컨테이너 두 개가 같은 GPU를 나눠 쓴다(`count: 1`은 GPU를 독점하지 않는다). GPU가 여러 장이면 `count: 1` 대신 `device_ids: ["0"]`처럼 장 번호를 정한다.
- GPU 메모리는 대략 임베더 1.5~2GB, 리랭커 1.5~2.5GB다(fp16 가중치 각 약 1.1GB + CUDA 기본 사용량 + 한 번에 16쌍·512토큰 계산). 실제 값은 띄운 뒤 `nvidia-smi`로 확인한다.
- vLLM은 기본으로 GPU 메모리의 90%를 미리 잡는다(`--gpu-memory-utilization 0.9`). 그대로면 임베더·리랭커가 메모리 부족(CUDA out of memory)으로 죽거나, 둘이 먼저 떠 있으면 vLLM이 시작을 거부한다. vLLM에 이 비율을 낮춰 5GB쯤 남긴다. 예: 48GB면 0.85, 80GB면 0.9 그대로 두어도 8GB가 남는다.
- 띄우는 순서는 임베더·리랭커 먼저, 그다음 vLLM을 권한다. vLLM은 시작할 때 남은 메모리를 확인하므로 순서가 바뀌면 위 비율 계산이 어긋날 수 있다.
- CPU 서버의 backend는 `.env`의 `EMBED_URL=http://<GPU 서버 IP>:8001`, `RERANK_URL=http://<GPU 서버 IP>:8002`로 부른다. Qwen 주소(`STAGE2_BASE_URL`)와 같은 IP다. GPU 서버 방화벽에서 8001·8002를 CPU 서버 IP에만 연다.

## 확인

계약 테스트(가짜 모델, torch 불필요):

```powershell
uv run pytest tests/inference
```

서버 값 = 로컬 값 확인(conda env `kosis`, GPU torch 있는 환경에서):

```powershell
conda activate kosis
python inference/check_parity.py --embed-url http://127.0.0.1:8001 --rerank-url http://127.0.0.1:8002
```

`same weights True`, 최소 cosine 0.995 넘음, 점수 차 0.02 미만이면 `PASS`. 양쪽 다 GPU fp16이면 cosine 1.0000에 가깝다. 한쪽만 CPU(fp32)면 cosine 0.999대, 점수 차 1e-3 정도가 정상이다.

## 참고

- 서버는 요청을 하나씩 처리한다(모델마다 잠금 하나, uvicorn worker 1). 동시 사용자가 많아지면 컨테이너를 늘리고 앞에 로드밸런서를 둔다.
- 서버가 다른 가중치로 다시 뜨면 클라이언트는 응답의 `ckpt`가 달라진 것을 보고 오류를 낸다(조용히 다른 벡터로 검색하지 않게).
- 문서 임베딩(`kind: document`)은 색인 빌드용으로 열어 두었다. 지금 색인 빌드(`kosis_build.py vectors`)는 로컬 로딩 그대로다.
