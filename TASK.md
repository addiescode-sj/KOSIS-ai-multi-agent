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
- [~] **T3-06** ⭐ 하네스 확장 — 툴 선택·파라미터 정확도, 최종 수치 정확도, 멀티턴 성공률, 요청당 비용·지연 (Linear KOS-120)
- [ ] **T3-07** 단일 에이전트 베이스라인 측정
- [ ] **T3-08** Plan-and-Execute 변형 — 동일 평가셋 측정
- [ ] **T3-09** 멀티에이전트 변형 — 동일 평가셋 측정
- [ ] **T3-10** ⭐ 아키텍처 3종 비교 (포트폴리오 P2) — 정확도·비용·지연 표, 채택 근거, ADR
- [ ] **T3-11** 통합 서비스 UI — 답변, 출처, Vega-Lite 차트, 멀티턴, 실행 가능한 데모 (산출물①)
- [ ] **T3-12** README — 누구나 그대로 실행 가능, 신규 환경에서 재현 확인
- [ ] **T3-13** 시스템 구성도 + ADR (산출물⑦) — `docs/architecture.md`, `docs/adr/`
- [ ] **T3-14** ⭐ 전체 기능 테스트 결과 (산출물⑥) — `docs/final_eval.md`
- [ ] **T3-15** 최종 시연 자료 (산출물⑧) — 문제·접근, 아키텍처, 시연, 실데이터 검증, 개선 방향
- [x] **T3-17** Fetch & Normalize 입출력 계약 고정 (Linear KOS-143, M1 11/9, 임도유) — 표 ID → tidy DataFrame 계약 문서·Pydantic 스키마, 규칙 4개(셀 키 중복·단위 미상·5년 주기 보존·큰 표 읽기)와 결측 기호·계/소계 규칙, 같은 표본 200표 전→후(로드 성공률·단위 미상 표 비율·주기 손실 표 수·대용량 표 처리), 규칙별 pytest → 결과: 표본 200표 로드 195 → 200 (clean 196·모호 셀 표시 3·조건 필요 1), 메모리 부족 1 → 0, 5년 주기 손실 33 → 0, 단위 미상 표 38 → 38(지어내지 않음, 출처 추적), 실전 1과 값 대조 20/20 (`src/kosis_data/fetch.py`, `docs/data-contract.md`, `plans/T3-17.md`)
- [x] **T3-20** ⭐ 평가 리포트 HTML 전 계층 — 모든 `run_eval` 실행(l0·l1·l2·ablation·l012·l3·e2e·agent)이 report.html을 남긴다: 평가 결과, 운영 지표(지연·토큰·비용·실패율), trace별 측정(턴·문항마다 span 경로·지연·Jaeger 링크)을 읽는 법과 함께 → 결과: report.html 계층 3/8 → 8/8, trace 화면 계층 3 → 4(agent: 대화별 화면 + 턴별 span 워터폴·Jaeger 링크), 기존 l012 run 렌더 실패 → 성공, pytest 78 → 82 (`evals/report_html.py`, `plans/T3-20.md`)
- [x] **T3-21** ⭐ 종단 간 평가 한 번에 돌리기 — 명령 하나로 사전 점검(데이터·Chroma·체크포인트·키) → L0~L4(+agent)를 같은 stage 접두어로 실행하고, 준비 안 된 계층은 이유와 함께 건너뛰며, 기준 run 대비 비교 표를 남긴다. L3·e2e는 `--llm mock`으로 비용 없이 경로를 먼저 확인할 수 있다. 테스트 방법은 `evals/README.md`에 정리 → 결과: 평가 실행 명령 9 → 1(`python -m evals.suite`), L3·e2e 경로 확인 HCX 약 270회 → 0(`--llm mock`), 조건 맞춘 기준 비교 자동(이 PC: 6 run 실행·7 run 사유와 함께 건너뜀, 기준 같은 5 run 지표 차이 0), pytest 82 → 86, `evals/README.md` §0 (`plans/T3-21.md`)
- [ ] **T3-16** (선택) PDF 보고서·차트 옵션 지정·분석 결과 저장 — 필수 산출물을 모두 끝낸 뒤에만
