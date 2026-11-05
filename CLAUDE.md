# KOSIS 통계 AI 서비스 — 종합 프로젝트 (통계 AI 에이전트) 작업 지침

멋쟁이사자처럼 AI NLP 부트캠프 6기 팀 프로젝트. 국가데이터처(KOSIS) 통계표를 대상으로
**실전 1(수치 요약 모듈) → 실전 2(통계표 검색 챗봇) → 종합(툴 호출 에이전트 + 시각화)**을 만든다.
이 레포는 **종합**이다. 실전 1([KOSIS-ai-nlp-summary](https://github.com/addiescode-sj/KOSIS-ai-nlp-summary))은 `vendor/phase1`,
실전 2([KOSIS-ai-RAG](https://github.com/addiescode-sj/KOSIS-ai-RAG))는 `vendor/phase2` submodule로 들어 있고, 다시 짜지 않고 import해서 쓴다.
로드맵·마일스톤·담당은 Linear [종합 프로젝트](https://linear.app/kosis-ai-08/project/종합-프로젝트-045c4bbf9eb0/overview),
태스크는 [TASK.md](TASK.md), 세션 인계는 [docs/current-state.md](docs/current-state.md).

## 0. 작업 절차 (필수 — hook으로 강제됨)

모든 TASK는 `plan-gate` skill(`/plan-gate`)로 진행한다. 산출물부터 만들지 않는다.

1. **Pick** — TASK.md에서 태스크를 `[~]`로 바꾸고 브랜치 `T3-01-slug` 생성
2. **Plan** — `plans/<TASK-ID>.md`에 수행 아이디어와 옵션 A/B/C 작성
   (옵션마다 예상 성능 향상 수치 + 실질 반영 이점 + 비용·리스크)
3. **Verify** — Verify 체크리스트를 모두 통과시키고, 독립 리뷰(서브에이전트) 결과 반영
4. **Decide** — 사용자에게 옵션 선택을 받아 `status: approved` 기록
5. **Implement** — 선택한 옵션만 구현
6. **Measure** — 같은 평가셋으로 전→후 수치를 plan의 `## Result`와 TASK.md에 기록, `[x]` 처리

`.claude/hooks/plan_gate.py`가 승인된 plan 없이 `plans/`, `TASK.md`, `CLAUDE.md`, `.claude/` 밖의
파일을 수정하면 차단한다. 우회하지 말 것 (Bash로 파일 쓰기 포함).
TASK.md를 고치면 `.claude/hooks/linear_sync.py`가 `T3-xx` 제목으로 Linear 이슈의 제목·설명·상태를 덮어쓴다
(`.env`에 `LINEAR_API_KEY`·`LINEAR_TEAM_KEY`가 있을 때만). 설명을 Linear에서 길게 쓴 이슈는 TASK.md 문구가 이긴다.

## 1. 평가 기준 (모든 판단의 기준)

데이터 정확성 > 예외 처리 > 질의 이해 > 기능 완성도 > 응답 품질 > 기술 설계 > 결과 검증.
과제에 합격 점수는 없다. **"무엇을 바꿔서 몇 → 몇"**을 남기는 것이 결과물이다.
종합 핵심 지표: **툴·파라미터 정확도, 최종 수치 정확도, 멀티턴 성공률, 요청당 비용·지연**.
어떤 도구나 모델이든 골든셋 수치로 전→후를 보인 뒤 채택한다. 도입 자체는 성과가 아니다.

## 2. 설계 원칙

- **숫자는 코드(pandas)가 계산하고 LLM은 문장만 쓴다.** LLM이 만든 숫자는 버그다.
- 모든 답에 출처: `KOSIS / 조사명 / 통계표명 (+링크)`. 표 ID는 툴 호출 전 과정에서 보존한다.
- 자료가 없으면 지어내지 말고 없다고 말한다 (범위 밖·기간/주기 불일치 포함).
- 노드·툴 입출력은 Pydantic 계약을 유지한다: 공용 `kosis_ai.schemas`(실전 1), 검색 `kosis_rag.schemas`(실전 2). 깨는 변경은 plan에 명시하고 승인받는다.
- 툴 (T3-01): `search_tables`(실전 2) · `summarize_stat`(실전 1) · `make_chart`(T3-02, Vega-Lite spec).
- 오케스트레이션: LangGraph StateGraph(대화 상태 유지). 실전 2 노드 워크플로우(parse → retrieve → rerank1 → rerank2 → gate → refuse | guide)는
  고치지 않고 상위 그래프의 search_agent로 감싼다. 성능이 낮은 단계만 에이전트로 바꾸고, 바꿀 때마다 같은 골든셋으로 "몇 → 몇"을 남긴다. ReAct 단일 / Plan-and-Execute / 멀티 에이전트를 같은 평가셋으로 비교하고 ADR로 결정한다 (T3-10).
- 필요한 것만 만든다. 구현 하나·인터페이스 하나면 추상화하지 않는다.

## 3. 디렉터리

```
backend/app/       FastAPI (main.py). 에이전트 API(SSE)를 노출만 한다 — 로직은 src/
src/kosis_agent/   멀티에이전트 그래프(graph.py): supervisor → search_agent(실전 2 검색 그래프 그대로) · stat_agent(T3-01) · chart_agent(T3-02)
evals/             계층별 평가 모듈 (실전 2에서 복사해 이어감, T3-00): datasets/(gitignored) · evaluators/ · runners/ · reports/. 에이전트 지표는 T3-06에서 여기에 추가
frontend/          Next.js 16 (App Router) + TypeScript + Tailwind + shadcn/ui + Vercel AI SDK. 실전 2 UI에서 시작 (T3-11)
vendor/phase1/     실전 1 submodule — kosis_ai(src/), kosis_chat/, eval/, configs/model.yaml. 직접 수정 금지
vendor/phase2/     실전 2 submodule — kosis_rag(src/)만 import한다 (evals/는 이 레포 사본을 쓴다). 직접 수정 금지
plans/             태스크별 plan (옵션·검증·결정·결과 기록)
docs/              제출 문서, current-state.md
```

- 실전 1·2 코드 수정이 필요하면 해당 레포에 PR → 여기서 `git submodule update --remote vendor/phase1`(또는 phase2) 후 커밋.
  `vendor/phase1`은 실전 2가 고정한 커밋과 같게 맞춘다 (`kosis_ai.schemas` 계약 일치).
- 실전 2 `backend.app`(API 모듈)은 이 레포 `backend.app`에 가려져 import되지 않는다. 실전 2는 `kosis_rag.*` 로직만 import한다.
- 실전 2 `kosis_rag`는 `data/`·`.env`를 `vendor/phase2/` 아래에서 읽는다(경로 고정). 실전 2 PR로 `KOSIS_WORKSPACE`를 따르게 하기 전까지
  로컬에서는 `vendor/phase2/data` → `../../data` 심볼릭 링크, compose는 같은 폴더를 두 곳에 마운트한다.

## 4. 코딩 규칙

- 코드·주석·식별자는 영어. 문서(md)는 한국어.
- Python 3.12 (`uv`, `.python-version`), Pydantic v2, pandas, pytest. 비밀값은 `.env` — 커밋 금지.
- 셋업: `git submodule update --init && uv sync && npm install && (cd frontend && npm install) && cp .env.example .env`
  (`--recursive` 불필요: 실전 2 안의 phase1 사본은 쓰지 않는다)
- 실행: 백엔드 `PYTHONPATH=.:src:vendor/phase2/src:vendor/phase1:vendor/phase1/src uv run uvicorn backend.app.main:app --reload`,
  프론트 `cd frontend && npm run dev`, 전체 `docker compose up --build` (Jaeger: `--profile obs`)
- import 경로: `pytest.ini`의 `pythonpath = . src vendor/phase2/src vendor/phase1 vendor/phase1/src`.
- 평가: `PYTHONPATH=<위와 같음> uv run python -m evals.run_eval --layer {l0,l1,l2,l012,l3,e2e} --stage <TASK-ID>-<slug>`. 채점은 결정적 코드가 한다. 실행 결과(`evals/reports/<run>/`)는 커밋하지 않고, `ops_history.jsonl`(집계만)은 커밋한다.
- 검증: `bash scripts/verify.sh` (오프라인 pytest + 하네스·프론트 타입체크 + 데이터 미추적 확인). CI(GitHub Actions)도 같은 스크립트를 돈다.
- 제공 데이터는 `data/`(gitignored)에 두고 Docker 이미지에 넣지 않는다 (compose가 읽기 전용 마운트).
- 비자명한 로직(툴 선택·계산·채점)은 pytest 하나 이상을 남긴다.
- frontend는 Next.js 16 — 코드 작성 전 `frontend/AGENTS.md` 지침대로 `node_modules/next/dist/docs/`를 확인한다.
