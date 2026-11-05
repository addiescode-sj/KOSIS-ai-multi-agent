# KOSIS 통계 AI 에이전트 (종합 프로젝트)

국가데이터처(KOSIS) 통계 데이터를 활용한 AI 서비스 — 자연어 질문 하나로 **통계표 탐색 → 수치 조회·계산 → 요약 → 차트**까지 처리하는 근거 수치 고정형 챗봇.

- 실전 1 수치 요약 모듈: [`vendor/phase1`](https://github.com/addiescode-sj/KOSIS-ai-nlp-summary) · 실전 2 통계표 검색 RAG: [`vendor/phase2`](https://github.com/addiescode-sj/KOSIS-ai-RAG)
- 로드맵: [Linear 종합 프로젝트](https://linear.app/kosis-ai-08/project/종합-프로젝트-045c4bbf9eb0/overview) · 태스크: [TASK.md](TASK.md) · 작업 지침: [CLAUDE.md](CLAUDE.md)

## 빠른 시작

```bash
git clone --recurse-submodules https://github.com/addiescode-sj/KOSIS-ai-multi-agent.git  # 또는 git submodule update --init
uv sync && npm install && (cd frontend && npm install)
cp .env.example .env   # HCX_API_KEY 등 입력
bash scripts/verify.sh # 오프라인 검증
docker compose up --build   # backend :8000, frontend :3000, chroma :8001, postgres :5432, redis :6379
```

전체 README는 T3-12에서 작성한다.
