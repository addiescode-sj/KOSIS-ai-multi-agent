# TASK — 종합 프로젝트 마이크로 태스크 로드맵

Phase 0·1 태스크는 실전 1 레포(`vendor/phase1/TASK.md`), Phase 2는 실전 2 레포(`vendor/phase2/TASK.md`)에 있다. 이 파일은 Phase 3만 관리한다.
전체 로드맵·마일스톤(M0~M9)·트랙·담당은 Linear 프로젝트 [종합 프로젝트](https://linear.app/kosis-ai-08/project/종합-프로젝트-045c4bbf9eb0/overview)가 기준이다.

상태: `[ ]` 대기 · `[~]` 진행 중 (plan-gate 시작) · `[x]` 완료 (결과 수치 기록 필수)
형식: `- [ ] **ID** 제목 — 완료 기준` · ⭐ = AI 평가 엔지니어링 · 완료 시 줄 끝에 `→ 결과: <지표 전→후>`
트랙: 데이터(임도유) · AI 파이프라인(전민기) · 평가·아키텍처(이선주, 보조 임도유) · 인프라(신규 서비스 엔지니어)

## Phase 3 — 종합: 국가통계 AI 챗봇 + 시각화 (11/4~12/10, 기능 동결 11/30)

목표 질문: "찾아서, 계산해서, 그려줘" · 핵심 지표: 툴·파라미터 정확도, 최종 수치 정확도, 멀티턴 성공률, 요청당 비용·지연

- [x] **T3-00** 레포 초기 세팅 — 실전 1·2 submodule(`vendor/phase1`, `vendor/phase2`), 하네스(skill·hook·agents·AGENTS.md·CLAUDE.md) 팀 공유 이식, 기술 스택 골격(FastAPI·LangGraph·Next.js·Docker Compose·CI), `scripts/verify.sh` OK → 결과: 인프라 태스크라 지표 없음, 질문 → supervisor → 실전 2 검색 그래프 → SSE 경로 0 → 1, `scripts/verify.sh` OK (pytest 66 pass: 골격 5 + 실전 2에서 복사한 평가 모듈 `evals/` 61, 하네스·프론트 타입체크 통과). Docker compose 5서비스 healthy·frontend→backend SSE 확인(1노드 골격 기준). 실전 2 검색 실연결은 Docker 복구 후 확인
- [ ] **T3-01** 툴 래핑 — `search_tables`, `summarize_stat`, `make_chart`를 툴 스키마로 등록 (실전 1·2 모듈 재사용, 검증기 유지)
- [ ] **T3-02** 차트 모듈 — 시간 변화=꺾은선, 항목 비교=막대, 구성비=원형·누적막대 자동 선택, Vega-Lite spec (산출물④)
- [ ] **T3-03** 단일 에이전트 루프 — LLM이 툴 선택·순서 결정, 1턴 시나리오 동작
- [ ] **T3-04** 대화 상태 — 선택한 표·조건 기억, "이거"·"그 표" 지시어 해석 (산출물⑤)
- [ ] **T3-05** ⭐ 에이전트 평가셋 — 단일턴 + 멀티턴 시나리오(과제 문서 시나리오 포함)
- [ ] **T3-06** ⭐ 하네스 확장 — 툴 선택·파라미터 정확도, 최종 수치 정확도, 멀티턴 성공률, 요청당 비용·지연 (Linear KOS-120)
- [ ] **T3-07** 단일 에이전트 베이스라인 측정
- [ ] **T3-08** Plan-and-Execute 변형 — 동일 평가셋 측정
- [ ] **T3-09** 멀티에이전트 변형 — 동일 평가셋 측정
- [ ] **T3-10** ⭐ 아키텍처 3종 비교 (포트폴리오 P2) — 정확도·비용·지연 표, 채택 근거, ADR
- [ ] **T3-11** 통합 서비스 UI — 답변, 출처, Vega-Lite 차트, 멀티턴, 실행 가능한 데모 (산출물①)
- [ ] **T3-12** README — 누구나 그대로 실행 가능, 신규 환경에서 재현 확인
- [ ] **T3-13** 시스템 구성도 + ADR (산출물⑦) — `docs/architecture.md`, `docs/adr/`
- [ ] **T3-14** ⭐ 전체 기능 테스트 결과 (산출물⑥) — `docs/final_eval.md`
- [ ] **T3-15** 최종 시연 자료 (산출물⑧) — 문제·접근, 아키텍처, 시연, 실데이터 검증, 개선 방향
- [x] **T3-18** PostgreSQL + SQLAlchemy ORM (Linear KOS-86, M2 11/14, 임도유) — 대화 기록·평가 결과 ORM 모델, compose `postgres`에 테이블 생성, 실제 PostgreSQL에서 쓰기·읽기 확인, pytest. API·대화 상태 연결(T3-04)은 범위 밖 → 결과: PostgreSQL 사용 코드 0 → ORM 3개 테이블 + Alembic `0001`, 실제 PostgreSQL에서 생성·쓰기·읽기·연쇄 삭제 통과, 모델-마이그레이션 차이 0, ops_history 53줄 → 47행 적재, 5432 공개 → 127.0.0.1만 (`src/kosis_store`, `migrations/`, `docs/store.md`, `plans/T3-18.md`)
- [ ] **T3-16** (선택) PDF 보고서·차트 옵션 지정·분석 결과 저장 — 필수 산출물을 모두 끝낸 뒤에만
