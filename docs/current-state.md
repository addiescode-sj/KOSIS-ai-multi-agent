# Current State

> A new session starts here. Keep this short and current — it is the handoff
> packet between sessions.

## T3-23 워크플로 그래프 + 6장 폴더 구조 (2026-11-09, 브랜치 T3-23-workflow-graph, 진행 중)

- 폴더: `src/kosis_agent/{graph,retrieval,services,observability}` + `runtime.py`(연구 문서 6장 구조). `src/kosis_data/fetch.py` → `services/parquet_repository.py`. 지도는 `src/kosis_agent/retrieval/README.md`. 주석은 한국어(CLAUDE.md §4 예외).
- 그래프(옵션 A, supervisor 없음): context → search(`search_tables` 툴) → statistics(`summarize_stat` 툴, 대체 표 재시도 `ALT_TABLE_BUDGET` 기본 0) → chart → finalize(outcome 4종). 진입점 `backend.app.main:build_agent`(checkpointer, `/chat/stream`의 `thread_id`).
- 툴 계약: content = 채점 대상 JSON(SearchOutput / StatAnswer), artifact = 노드용 객체. `tracing.to_json`은 ToolMessage의 content를 기록.
- 확인한 것: 옛 그래프 대비 패리티 90/90, pytest 137. 남은 것: Chroma·HCX로 agent 계층 실측(시나리오는 T3-05), T3-06에 `refused()` 기준 확인 요청. 상세는 `plans/T3-23.md` Result.

## 실전 1·2 리팩터 모듈 연결 (2026-11-06, 브랜치 T3-01-connect-modules)

- 핀: `vendor/phase1` = `907b703`(T1-28 머지), `vendor/phase2` = `56b4252`(T2-41 머지). 두 모듈 모두 `.env`·환경변수를 읽지 않는다.
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
  - Postgres·Redis run in compose but no code uses them yet (KOS-86, M3).
  - Live phase 2 search not yet checked end to end: local Docker disk I/O error broke the embed image build. After Docker recovers: start the phase 2 Chroma (:8001), `uv sync --group embed`, run the backend locally with `data` linked to `../KOSIS-ai-RAG/data`.

### Context Index

- `plans/T3-00.md` / contains: options, review, decision (B) / use_for: scope review.
- `TASK.md` / contains: Phase 3 task list T3-00~16 / use_for: picking the next task.
