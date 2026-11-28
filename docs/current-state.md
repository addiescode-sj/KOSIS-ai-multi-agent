# Current State

> A new session starts here. Keep this short and current — it is the handoff
> packet between sessions.

## T3-62 배포 설정·인프라 문서 (2026-11-27, 브랜치 T3-62-infra-docs)

- 서버 운영은 `docs/infra/`(한눈에 보기·운영 절차·결정·이력), 설정은 `deploy/`(k8s·scripts·gpu). 공개 저장소라 주소·IP·키 없음: 서버별 값은 node-1 `~/deploy/site.env`, k8s는 `scripts/05-make-site.sh`가 만든 `deploy/overlay`(둘 다 gitignore)를 적용한다.
- 앱 배포는 GHCR 이미지 + `deploy/scripts/60-deploy.sh`(자동 실행 timer는 꺼져 있음). 저장소 설정의 결과가 11/27 서버와 같음을 확인(렌더 차이 0, `kubectl diff -k` 0).
- 인프라 담당 인계: 김병석 → 전민기. 접속 키·ssh 설정·`site.env`는 저장소 밖에서 따로 전달.

## E2E 골든 라벨 검토 (2026-11-21, 완료)

- `evals/datasets/e2e_golden.review.csv` 42행의 판정과 근거를 기록했다. `ok` 29건, `wrong` 13건이다. `wrong`은 `needs_review` 18건 중 8건, 규칙과 골드가 일치한 24건 중 5건이다.
- `bash scripts/verify.sh` 종료 코드 0. Python 338건 통과 및 2건 건너뜀, Jest 5개 스위트에서 27건 통과. 재생성한 기준 CSV와 비교해 BOM, 헤더, 행 순서, 원본 7개 열을 확인했다.
- 검토 CSV는 생성 산출물이어서 빌더 실행 시 덮어쓸 수 있다. 원본 및 검토본 SHA-256과 검증 근거는 보고서에 기록했다. 골든 데이터에 라벨 수정을 반영하는 일은 별도 범위다.

### Context Index

- `docs/reports/20261121-e2e-golden-review-REPORT.md` / contains 검증 결과 및 SHA-256 / use_for 검토본 식별 및 결과 확인.
- `evals/datasets/e2e_golden.review.csv` / contains 42행의 판정과 근거 / use_for 개별 라벨 수정 검토.
- `docs/plans/20261121-e2e-golden-review/` / contains 승인 범위와 완료된 작업 목록 / use_for 수용 기준 및 작업 이력 확인.

## T3-27 멀티턴 조건 이월 · 멀티쿼리 (2026-11-09, 브랜치 T3-27-multiturn-context, PR #9 위, 진행 중)

- 해석기: `src/kosis_agent/graph/context.py` `read_turn` — 규칙(남는 내용 낱말 · 슬롯 바꿔 끼우기 · 차트어) 우선, 판단 못 한 턴만 LLM(`INTERPRETER`=hybrid, `INTERPRETER_MODEL` 있을 때). LLM 독립 질문은 숫자·시도·낱말이 두 질문 안에 있을 때만 쓴다.
- 차트: `make_chart(table_key, chart, chart_type)` 툴, 차트만 요청하면 `active_chart`를 다시 그림.
- 측정: `python -m evals.builders.multiturn` → `run_eval --layer agent --agent evals.runners.multiturn_dev:build --scenarios evals/datasets/agent_scenarios_multiturn_dev_structure.jsonl` (`STAT_PROVIDER=mock`). 대화 성공률 0.340 → 0.915. 상세 `plans/T3-27.md` Result.
- HCX 측정: 해석기는 JSON 한 줄 방식(HCX가 langchain-naver 구조화 출력을 400으로 거절). 실전 1 HCX 수치 정확도 0.867, 툴·인자·상태 1.000. LLM 단독 모드는 규칙보다 낮아 hybrid + HCX-005 권장.
- 남은 것: 사용자 검증용 표현, 실시간 검색(이 PC에 `data/models/unseen-e1.pt`·`e2.pt` 없음, Docker 꺼짐), 실전 1 오답 4문항(S-EDG-09·16·17·21) → 실전 1 레포.

## T3-23 워크플로 그래프 + 6장 폴더 구조 (2026-11-09, 브랜치 T3-23-workflow-graph, 진행 중)

- 폴더: `src/kosis_agent/{graph,retrieval,services,observability}` + `runtime.py`(연구 문서 6장 구조). `src/kosis_data/fetch.py` → `services/parquet_repository.py`. 지도는 `src/kosis_agent/retrieval/README.md`. 주석은 한국어(CLAUDE.md §4 예외).
- 그래프(옵션 A, supervisor 없음): context → search(`search_tables` 툴) → statistics(`summarize_stat` 툴, 대체 표 재시도 `ALT_TABLE_BUDGET` 기본 0) → chart → finalize(outcome 4종). 진입점 `backend.app.main:build_agent`(checkpointer, `/chat/stream`의 `thread_id`).
- 툴 계약: content = 채점 대상 JSON(SearchOutput / StatAnswer), artifact = 노드용 객체. `tracing.to_json`은 ToolMessage의 content를 기록.
- 확인한 것: 옛 그래프 대비 패리티 90/90, pytest 145. agent 채점은 대체 표 시도를 빼고 최종 호출로만 채점(`evals/evaluators/agent.py` `final_calls`), summarize_stat 인자 = question·table_key + L2 조건 4개(실전 2 parse_query). 남은 것: Chroma·HCX로 agent 계층 실측(시나리오는 T3-05). 상세는 `plans/T3-23.md` Result.

## 실전 1·2 리팩터 모듈 연결 (2026-11-06, 브랜치 T3-01-connect-modules)

- 핀: `vendor/phase1` = `9e570a1`(T1-29 머지), `vendor/phase2` = `56b4252`(T2-41 머지). 두 모듈 모두 `.env`·환경변수를 읽지 않는다.
- 설정: `backend/app/config.py` `agent_config()` — 셸 환경 → 실전 2 `IndexSpec`(데이터 `<KOSIS_WORKSPACE>/data`)·`SearchOptions`·2차 리랭커 `LLMConfig`, 실전 1 `LLMConfig`(HCX-DASH-002). 이름과 기본값은 실전 2 `search_config_from_shell`과 같다.
- 그래프: `src/kosis_agent/graph.py` supervisor → search_agent(`SearchModule.stream`) → refuse면 `refusal_text`로 끝, found면 stat_agent(`summarize_stat_run(질문, picked[0].key, llm=)`) → `ChatResult`(답·표·StatAnswer·Vega-Lite). SSE: 검색 노드들 → `stat` → `result`. `result`는 옛 SearchResult 필드 이름을 유지해 프런트는 그대로 동작.
- 재현 도구: `scripts/stat_oracle.py`(골든 표 키로 그래프를 지나 실전 1 run 폴더 생성 → `eval.grade_run`/`eval.parity`), `scripts/search_l1.py`(실전 2 `parity_l1` dump/compare를 이 레포 설정으로). 기준·판정은 프로젝트 문서 `01-reproduce-baselines.md`.
- 열린 문제: `evals/`와 `tests/test_evals_*`는 실전 2 옛 import(`kosis_rag.query` 등)를 써서 핀을 올린 뒤 깨짐 — 평가 담당이 수정 가이드대로 고침. `uv.lock` 재생성(google-genai 제거) 필요. Docker 빌드 미확인.

## T3-00 repo initial setup (2026-11-04)

- Submodules: `vendor/phase1` = KOSIS-ai-nlp-summary @ `e248a45` (same commit phase 2 pins), `vendor/phase2` = KOSIS-ai-RAG @ `484bd06`.
- Harness copied from phase 2 unchanged and committed for the team: `.claude/` (agents, hooks `plan_gate.py`·`linear_sync.py`, skills, `settings.json`), `AGENTS.md`, `scripts/*.ts`, `.mcp.json` (Linear), PR template, `docs/templates/`. `CLAUDE.md` rewritten for phase 3.
- Stack skeleton: `src/kosis_agent/graph.py` (multi-agent skeleton: supervisor -> search_agent wrapping the phase 2 search graph unchanged; stat/chart agents join in T3-01/T3-02), `backend/app/main.py` (`/health`, `/chat/stream` SSE with phase 2 node events + `SearchResult`), phase 2 frontend wired to `/chat/stream`, compose (backend · frontend · chroma · postgres · redis · jaeger[obs]), GitHub Actions CI (`verify.sh` + image builds). K8s·AWS deferred to M6.
- `evals/` copied from phase 2 (`vendor/phase2@484bd06`, with `tests/test_evals_*.py` and `ops_history.jsonl`) and continued here; paths now resolve to this repo. Local golden sets copied into `evals/datasets/` (gitignored).
- Open issues:
  - `kosis_rag` reads `vendor/phase2/data` and `vendor/phase2/.env` (fixed `parents[2]`): needs a phase 2 PR before T3-01.
  - Postgres has ORM models and an Alembic migration (`src/kosis_store`, `migrations/`, T3-18 / KOS-86; `docs/store.md`) but the API does not write to it yet (T3-04). Redis is still unused (M3).
  - Live phase 2 search not yet checked end to end: local Docker disk I/O error broke the embed image build. After Docker recovers: start the phase 2 Chroma (:8001), `uv sync --group embed`, run the backend locally with `data` linked to `../KOSIS-ai-RAG/data`.

### Context Index

- `plans/T3-00.md` / contains: options, review, decision (B) / use_for: scope review.
- `TASK.md` / contains: Phase 3 task list T3-00~16 / use_for: picking the next task.

## AI PM Portfolio (2026-11-24 redesign, complete)

- Latest release: six Korean slides and matching six-section offline HTML for KOSIS 통계 AI and ReadMyCareer. KOSIS identifies the PM and 4-person team's roles and distinguishes personal decisions from team evaluation results. ReadMyCareer covers the solo product work through customer PoC delivery and identifies the next validation question.
- Follow-up copy changes: the HTML label now reads `맡은 역할`; the HTML, editable Spaces deck, and local PPTX cover clarify that approximately five years combine frontend/UX engineering, bootcamp lead instruction, and side-project leadership after the University of Bath HCI master's degree.
- Latest KOSIS copy revision: all personal-role headings in HTML, local PPTX, and the editable Spaces deck read `맡은 역할`. The 클라비 case now presents the four-person team's goals, failure and edge-case definitions, response guardrails and tone, layered evaluation and golden set, trace-based failure location, and AI/backend/frontend boundary and priority coordination. The 40→1 false-refusal result remains a separately qualified team benchmark. Final artifact identities are in the report.
- Delivery files: `docs/artifacts/ai-pm-portfolio-redesign/Addie_Lee_AI_PM_Portfolio.{pptx,html}`. The live editable deck is [on Spaces](https://chatgpt.com/space/page_01e31cc8e6908191aa19ee72d5b3f991) (`page_01e31cc8e6908191aa19ee72d5b3f991`). Direct Spaces export returned `Forbidden`. The local PPTX was reconstructed from the saved slide data and checked by reopening and rendering all six slides.
- Verification: offline Chrome passed at 360, 390, 768 and 1440 px, including keyboard navigation, image proportions, and absence of horizontal overflow or remote requests. The PPTX has 90 editable text shapes, two images matching the source pixel hashes, and six speaker-note sets. LibreOffice with Malgun Gothic passed visual checks. Microsoft PowerPoint was unavailable for a native check. `bash scripts/verify.sh` exited 0 with 5 frontend suites/28 tests and 452 Python tests passing, plus 6 Python skips.
- Evidence boundaries: KOSIS's 95.6% comes from providing the correct table in a 90-question diagnostic. Hit@5 is a separate team search measure with an unconfirmed denominator. False refusals went from 40/60 to 1/60 in the fixed-retrieval benchmark. ReadMyCareer's 90-to-5-minute figure comes from the resume, with sample size and measurement method unavailable. Quality thresholds are targets, and the stored three-fixture agent run failed on content criteria. No product code changes were made for the redesign.
- `docs/reports/20261124-ai-pm-portfolio-redesign-REPORT.md` / contains artifact hashes, acceptance results, and export limitations / use_for delivery and audit.
- `docs/plans/20261124-ai-pm-portfolio-redesign/{PLAN,TASKS,EVIDENCE}.md` / contains the approved scope, work checklist, and source evidence / use_for revision scope and metric provenance.
- `docs/artifacts/ai-pm-portfolio-redesign/build-ppt/validation.json` and `reopened-slide-*.png` / contain structural checks and all six slide renders / use_for reproducing the visual review.

## AI PM Portfolio (2026-11-23, revised and complete)

- Current deliverable: six-page Korean portfolio for KOSIS RAG Multi-Agent and ReadMyCareer. User follow-up requested unified green accents, Korean headings, a concise career/competency introduction and lower density. Both projects retain technology, responsibilities, outcomes and decisions.
- Editable HTML and PDF: `docs/artifacts/ai-pm-portfolio/Addie_Lee_AI_PM_Portfolio.{html,pdf}`. Saved PDF `libfile_c1e520da039481919147212466a14e29`; saved HTML `libfile_12f7ed24a9a481918567d2efa2d4ab76`.
- Both existing saved files now at version 2: annotated spacing edits, removed page 2 microcopy, and labels 맡은 부분 / 의사 결정 결과 applied. Verification: all six pages visually inspected; approximately 40 px minimum content/footer clearance, no horizontal overflow, Korean text and five links validated; `bash scripts/verify.sh` exit 0 again (Python 451 passed / 6 skipped, Jest 28 tests / 5 suites).
- Evidence boundaries: KOSIS .915 is dev/mock structure evaluation; ReadMyCareer quality thresholds are criteria, not claimed passing results. Historical three-fixture FAIL is disclosed. No new live evaluations or runtime code changes.
- `docs/reports/20261123-ai-pm-portfolio-REPORT.md` / contains source revision, verification, saved identities and local hashes / use_for delivery audit and future revisions.
- `docs/plans/20261123-ai-pm-portfolio/EVIDENCE.md` / contains resume, template and repository evidence / use_for updating project claims.

### Portfolio image and editability update

- PDF/HTML now version 3 with KOSIS screenshot on page 2 and ReadMyCareer screenshot on page 5. Native editable six-slide deck: https://chatgpt.com/space/page_22be68daf2dc81919dcc3bfcb57e1c46 . Its Download menu offers PowerPoint (PPTX); no local PPTX was verified.
- Both screenshots retain original proportions. Text and panels in the presentation remain editable. Latest report and hero-verification.log record validation and export limits.

### Current user-edited deck exported without reflow

- User reported native PDF export formatting drift. Created a separate fixed visual PDF from all six slides at source sequence 132, preserving the user's edits without modifying the live deck.
- File: `docs/artifacts/ai-pm-portfolio/Addie_Lee_AI_PM_Portfolio_Fixed.pdf`; saved `libfile_adf8a272483c81918854ebb601e074ba`, version 0. Each page is a 2560 × 1440 rendering plus invisible searchable text and link annotations.
- Reopened-PDF pixel comparison: zero error on all six pages. Source snapshot and validation JSON under `docs/artifacts/ai-pm-portfolio/screen-fixed/`.
