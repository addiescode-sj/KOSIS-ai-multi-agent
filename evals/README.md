# evals — 계층별 평가와 실패 단계 추적

실전 2부터 쓰는 평가 모듈이다. 계층마다 골든셋과 채점기를 따로 두고, 채점은 LLM 없이 코드가 한다.
여기서는 **실패한 문항이 어느 단계에서 왜 실패했는지**(T2-15, `trace.py`)를 읽고 고치는 방법을 정리한다.
서비스·에이전트 trace와 토큰·종료 이유 메트릭(T3-30·T3-31)을 읽는 법은 §8.
설계 배경과 옵션 비교는 [plans/T2-15.md](../plans/T2-15.md)에 있다.

## 0. 테스트 방법 — 준비부터 종단 간까지 (T3-21)

처음 평가를 돌리는 사람은 이 절만 따라 하면 된다. 아래 절(1~7)은 계층별 세부 규칙이다.

### 0.1 계층 한눈에

| run (스위트 이름) | 계층 | 무엇을 재나 | 필요한 것 | 비용 |
|---|---|---|---|---|
| `l0` | L0 | 메타데이터 채움률·미매핑 | 데이터 | 0 |
| `l0-load` | L0 | + 벡터 컬렉션 적재 일치 | 데이터·벡터 DB | 0 |
| `l1-bm25-record` · `l1-v2-bm25-record` | L1 | BM25 검색 (골든 v1 · v2) | 데이터 | 0 |
| `l1-hybrid` | L1 | 임베딩 + BM25 + 조건 필터 | 데이터·벡터 DB | 0 |
| `l1-staged` | L1 | + 1차 리랭커 | 데이터·벡터 DB·`RERANK_CKPT` | 0 (CPU 시간) |
| `l1-graph` | L1 | 실제 LangGraph 검색 그래프 | 데이터·벡터 DB·`RERANK_CKPT` | 0 |
| `ablation` | L1 | 슬롯 필터 on/off별 검색 (dev) | 데이터·벡터 DB | 0 |
| `l2-v1` · `l2-v2` | L2 | 질의 파서(규칙) | 데이터 | 0 |
| `l3` | L3 | 에이전트 그래프 한 턴, 검색은 정답 표(oracle) → `summarize_stat` 실호출 → Parquet 대조 (T3-32). **해석기·멀티쿼리 분해 LLM도 같이 돈다**(hybrid, gpt-4o-mini): 실전 1만 보는 순수 oracle이 아니라 "정답 표를 준 에이전트 한 턴"이다. 실전 1만 떼어 보려면 `INTERPRETER=rules`로 돌린다 | 데이터·HCX 키 (+ `OPENAI_API_KEY`) | **HCX 90문항** (재시도 포함 약 135회로 추정) + 해석기 호출 |
| `e2e` | L4 | 서비스 에이전트 그래프(`build_agent`) 한 턴: 검색 → `summarize_stat` (T3-32) | 데이터·벡터 DB·`RERANK_CKPT`·HCX 키 | **HCX 90문항 + 2차 리랭커** |
| `agent` | 종합 | 멀티턴 시나리오 → 에이전트 → 툴 (§6) | `--agent`·시나리오 파일 | 에이전트가 부르는 LLM |
| `ragas` | 심판 | 저장된 e2e run의 Faithfulness·Response Relevancy·Context Precision·Recall + 코드 ID P/R, 저점 → 그래프 노드, CI 게이트 기록 (§9) | e2e run 폴더·`OPENAI_API_KEY`·`--group ragas` | 심판 약 350회 (gpt-4.1-mini, 디스크 캐시) |

- L3·e2e는 `--llm mock`으로 **비용 없이 경로만** 확인할 수 있다. mock은 모델 대신 코드 템플릿이 답해서 점수가 그럴듯하게 나온다(예: L3 PASS 85/90).
  **점수로 쓰지 않는다**: 리포트에 "llm mock: 경로 확인용, 점수 아님"이 붙고, `ops_history.jsonl`에는 기록되지 않는다.
- "e2e"는 단일턴 파이프라인(질문 → 검색 1위 표 → 계산)이다. 종합 프로젝트의 대화형 종단 간 평가는 `agent`다(에이전트 T3-03·04와 시나리오 T3-05가 생기면 돈다).

### 0.2 준비 (처음 한 번)

```bash
cd ~/work/KOSIS-ai-multi-agent                      # 반드시 레포 루트에서 (PYTHONPATH의 . 과 src 는 상대 경로)
git submodule update --init                         # vendor/phase1·phase2 를 고정 커밋으로
uv sync --group embed --group ragas                 # 임베딩·리랭커(torch) + Ragas. 한 그룹만 sync하면 다른 그룹이 지워진다
export PYTHONPATH=.:src:vendor/phase2/src:vendor/phase1:vendor/phase1/src
```

- 데이터: `data/`(gitignored)에 `tables.jsonl`·`values/`가 있어야 한다. 이 PC에서는 `data -> ../KOSIS-ai-RAG/data` 링크, `vendor/phase2/data -> ../../data` 링크.
  골든 파일은 `evals/datasets/`(gitignored).
- `data/sigungu.json`이 없으면 시군구가 든 질문의 regions를 못 읽는다(점검에서 ⚠). 만들기: `python -m kosis_rag_build.cli sigungu --data-dir <제공 데이터>`.
  만들면 결과가 바뀌므로, 없이 만든 기준 run과는 비교하지 말고 새 기준을 다시 잡는다.
- 벡터 DB(검색 계층, T3-47: PostgreSQL + pgvector, 옛 Chroma): Docker Desktop을 켜고 이 레포의 postgres를 띄운다.
  ```bash
  docker compose up -d postgres && docker compose run --rm migrate    # localhost:5432, 벡터 테이블은 alembic 0003_pgvector
  ```
  벡터는 다시 임베딩하지 않고 옮긴다: 옛 Chroma 볼륨이 있으면 `vendor/phase2/scripts/chroma_to_pgvector.py`, 없으면 덤프 파일을 받는다(`vendor/phase2/docs/migration.md` T2-44).
  `.env`의 `DATABASE_URL`(대화 기록과 같은 DB)과 `VECTOR_COLLECTION`(옛 `CHROMA_COLLECTION`, 값 그대로)을 쓴다. Windows에서 연결이 멈추면 `localhost` 대신 `127.0.0.1`.
- 설정 값: `python -m evals.run_eval`·`evals.suite`는 시작할 때 레포 루트 `.env`를 읽는다(T3-23, `src/kosis_agent/envfile.py`). 셸에 이미 있는 값이 이긴다.
  `run_eval`은 검색 설정을 인자로도 받는다(우선순위 **인자 > 셸 > `.env` > 기본값**): `--collection` `--embed-ckpt` `--rerank-ckpt` `--database-url` `--text-format` `--rerank-mode` `--answer-k` `--hq-build` `--hcx-thinking`, `--env-file <파일>` · `--no-env-file`. 체크포인트에 `base`를 주면 학습 전 기반 모델이다.
  실전 1·2 모듈은 여전히 `.env`도 환경도 읽지 않고, 이 레포가 만든 설정 객체를 받는다. `KOSIS_DOTENV=off`면 `.env`를 건너뛴다(pytest는 항상 끔).
  이름과 기본값은 아래 "검색 설정" 절. 값이 없으면 기본값으로 **조용히** 돈다 → 스위트 사전 점검으로 먼저 확인한다.
- Windows: `set PYTHONUTF8=1`, `export` 대신 `set`.

### 0.3 권장 순서

1. **오프라인 테스트**: `bash scripts/verify.sh` (pytest·타입체크, 서버·키 없음).
2. **경로 확인 (비용 0)**: `uv run python -m evals.suite --stage <접두어>-mock --llm mock --baseline <기준 접두어>`
   — 준비 안 된 run은 이유와 함께 건너뛴다. 점검 표에서 ✕를 없앤 뒤 다음으로.
3. **실측 (HCX)**: `uv run python -m evals.suite --stage <접두어> --baseline <기준 접두어>` — 기준 run과 지표 비교 표가 나온다.
4. **에이전트**: `... --only agent --agent <module:function> --scenarios <시나리오.jsonl> --repeat 3` (pass^3).

`<접두어>`는 `<TASK-ID>-<무엇>` (예: `T3-07-react`). 각 run의 stage는 `<접두어>-<run 이름>`이 되어 `evals/reports/<시각>-<stage>/`에 저장된다.

### 0.4 한 번에 돌리기 — `python -m evals.suite`

```bash
uv run python -m evals.suite --stage T3-07-react --llm mock --baseline T3-06-parity          # 경로 확인
uv run python -m evals.suite --stage T3-07-react --baseline T3-06-parity                     # 실측
uv run python -m evals.suite --stage T3-07-react --only l1-graph e2e                          # 일부만
uv run python -m evals.suite --stage T3-07-react --baseline evals/reports/20261105-170435-KOS-117-e2e-graph-staged-hcx   # 기준 run 폴더 직접
```

| 옵션 | 뜻 |
|---|---|
| `--stage` | 모든 run stage의 접두어 (필수) |
| `--llm hcx\|mock` | L3·e2e 모델. mock이면 모든 모델을 끄고(2차 LLM 포함), 실측은 `.env`의 검색 설정 그대로(T3-32) |
| `--baseline` | 비교할 기준: stage 접두어(여럿 가능) 또는 run 폴더 |
| `--only` | 돌릴 run 이름만 (0.1 표의 이름) |
| `--interval` | L3·e2e 문항 사이 대기(초). 기본 hcx 5, mock 0 |
| `--agent`·`--scenarios`·`--repeat` | agent run (§6) |

- 순서: 0.1 표 위에서 아래로. 한 run이 실패해도(예외·`SystemExit`) 기록하고 다음 run으로 간다. 실패가 있으면 종료 코드 1(건너뜀은 실패가 아님).
- 결과: `evals/reports/<시각>-<접두어>-suite/`의 `suite.html`(읽는 법, run별 report.html 링크, 점검·실행·비교 표), `suite.md`, `suite.json`.
- **기준 run은 조건이 같은 것만** 고른다: 계층·검색기·골든 해시·split·k·재채점 여부·llm(옛 실측 run은 hcx)·L0 적재 확인 여부.
  같은 접두어 안에서 조건이 같은 가장 최근 run이 기준이고, 없으면 "조건 같은 기준 run 없음". mock run은 hcx run과 절대 비교하지 않는다.
- 서버·키 없이 이 PC에서 확인한 실행: `T3-21-suite-mock` — 6 run 실행(l0·l1 BM25 2개·l2 2개·l3 mock), 7 run은 Chroma·체크포인트·에이전트가 없어 건너뜀. 기준(`T3-06-parity`)이 있는 5 run은 지표가 모두 같았고, l3 mock은 조건 같은 기준이 없다(mock은 hcx와 비교하지 않음).

### 0.5 계층 하나만 돌리기 — `python -m evals.run_eval`

스위트가 부르는 것과 같은 명령이다. 결과 폴더·report.html·ops_history 기록은 똑같이 생긴다.

```bash
S=T3-07-react
uv run python -m evals.run_eval --layer l0 --retriever dense --stage $S-l0-load
uv run python -m evals.run_eval --layer l1 --retriever bm25-record --stage $S-l1-bm25-record          # oracle | bm25-catalog | bm25-record | dense | 프리셋 | graph:<프리셋>
uv run python -m evals.run_eval --layer l1 --retriever graph:staged --stage $S-l1-graph
uv run python -m evals.run_eval --layer ablation --retriever dense --split dev --stage $S-ablation
uv run python -m evals.run_eval --layer l2 --split all --stage $S-l2-v1
uv run python -m evals.run_eval --layer l3 --llm mock --stage $S-l3-mock                              # 경로 확인
uv run python -m evals.run_eval --layer l3 --interval 5 --stage $S-l3                                 # HCX
uv run python -m evals.run_eval --layer l3 --from-run <과거 L3 run 폴더> --stage $S-l3-regrade        # 저장된 답 재채점, 호출 0
uv run python -m evals.run_eval --layer e2e --interval 5 --stage $S-e2e                               # 서비스 그래프, 검색 설정 = .env
uv run python -m evals.run_eval --layer e2e --from-run <과거 e2e run 폴더> --stage $S-e2e-regrade      # 저장된 답·검색 순위 재채점, 호출 0 (T3-39)
uv run python -m evals.run_eval --layer agent --oracle --scenarios <시나리오.jsonl> --split all --stage $S-agent-oracle   # 채점기 자기검증
```

L1 프리셋(`retrieval.STAGED`): `hybrid` · `hybrid+cond` · `staged` · `clova-only` · `staged-clova` · `hybrid+cond-hcx` · `staged-hcx` · `hybrid+cond-gpt` · `staged-gpt`(1차 + OpenAI 호환 2차: `STAGE2_MODEL`·`STAGE2_BASE_URL`·`STAGE2_API_KEY`가 있으면 그 서버, T3-33). `graph:`를 앞에 붙이면 같은 설정을 LangGraph 검색 그래프로 돌린다.
골든 v2는 `--golden evals/datasets/retrieval_golden_v2.jsonl --pool evals/datasets/retrieval_pool_v2.csv` / `--query-golden evals/datasets/query_golden_v2.jsonl`.

### 0.6 결과 읽기

- run 폴더: `report.html`(맨 위 읽는 법 → 결과 → 실패 단계·trace → 운영 지표·추이 → 행별 결과), `report.md`, `results.jsonl`(질문 원문 포함, git에 안 올림), `manifest.json`(조건·해시), `ops.json`.
- 운영 지표: 지연 p50·p95, 입력·출력 토큰, 비용(`evals/pricing.json` 단가 × 모델별 토큰, T3-39), 실패율. 실행 간 추이는 `ops_history.jsonl`(집계만, 커밋함).
  T3-39 이전 run은 행에 모델 이름이 `HCX-DASH-002,gpt-4o-mini`처럼 합쳐 있고 토큰이 모델별로 없어 단가를 채워도 비용이 나오지 않는다(`--from-run` 재채점도 그 행을 그대로 복사).
- trace: l1·l3·e2e는 문항별 span 경로 + **trace 카드**(span 이름별 횟수·지연 p50·p95·비중 표, 문항별 워터폴 60건까지·문제 문항 먼저, T3-32), agent는 턴별 span 워터폴. `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`과 `docker compose --profile obs up -d jaeger`면 Jaeger 링크가 생긴다(아래 "Jaeger로 보기").
- 스위트: `suite.html`의 비교 표에서 차이(이번 − 기준)를 본다. "무엇을 바꿔서 몇 → 몇"은 이 표에서 뽑는다.

### 0.7 코드를 고친 뒤 결과가 같은지 확인하기 (패리티)

1. 고치기 전에 같은 접두어로 스위트를 돌려 기준을 만든다(예: `--stage T3-xx-before`).
2. 고친 뒤 `--stage T3-xx-after --baseline T3-xx-before`. 차이가 0이 아니면 고친 코드가 결과를 바꾼 것이다.
3. 이미 고친 뒤라면, 고치기 전 커밋을 `git worktree add`로 따로 꺼내 같은 평가를 돌려 비교한다(T3-06에서 이렇게 확인: 이 레포 `4804bdc` + phase2 `484bd06` + phase1 `e248a45`).
- 기준과 조건(골든 해시·`tables.jsonl` 해시·셸 값·sigungu.json 유무)이 다르면 숫자 차이가 수정 탓인지 알 수 없다. manifest의 `golden`·`source_snapshot`을 먼저 맞춘다.

### 0.8 자주 나는 오류

| 증상 | 원인 → 해결 |
|---|---|
| `ModuleNotFoundError: No module named 'kosis_agent'` (`evals/__init__.py`) | `PYTHONPATH` 미설정이거나 레포 루트가 아닌 곳에서 실행 → 0.2의 export, 루트에서 실행 |
| `No module named 'kosis_rag.query'` 같은 옛 이름 | 서브모듈이 옛 커밋 → `git submodule update` |
| 점검 `PostgreSQL … 응답 없음` | Docker 꺼짐 / postgres 안 띄움 → 0.2. Windows에서 멈추면 주소를 `127.0.0.1`로 |
| `ValueError: no collection` · 점검 `VECTOR_COLLECTION 미설정` | `.env`·셸 모두 값 없음 → `.env`에 넣거나 `set VECTOR_COLLECTION=...` (옛 `CHROMA_COLLECTION`과 같은 값) |
| `LLMKeyMissing` · 점검 `HCX_API_KEY 미설정` | 셸에 키 없음, 또는 `--llm mock`으로 경로만 확인 |
| 프로세스가 `exit 137`로 죽음 | 큰 Parquet 표(10MB 넘는 7개) 셀을 읽다 메모리 부족 → 다른 프로그램을 닫거나 그 문항 제외(별도 이슈) |
| `uv sync` 뒤 torch가 없어짐 | 그룹 하나만 sync함 → `uv sync --group embed --group ragas` |
| L3 mock 점수가 높음 | 정상: 코드 템플릿 답. 점수로 쓰지 않는다 |
| 포트 5432 충돌 | 이 레포 compose의 postgres와 실전 2 postgres를 같이 띄움 → 하나만 (또는 실전 2를 `POSTGRES_PORT=5433`) |

## 1. 실행

```bash
export PYTHONPATH=.:src:vendor/phase2/src:vendor/phase1:vendor/phase1/src   # 없으면 ModuleNotFoundError: kosis_ai

uv run python -m evals.run_eval --layer l3 --stage <이름> --from-run evals/reports/T1-27-run   # 과거 run 재채점, 모델 호출 0
uv run python -m evals.run_eval --layer l3 --stage <이름> --interval 5                         # 에이전트 그래프 + 정답 표 검색
uv run python -m evals.run_eval --layer e2e --stage <이름> --interval 5                         # 서비스 에이전트 그래프 (벡터 DB 필요)
uv run python -m evals.run_eval --layer e2e --agent <module:function> --stage <이름>           # 다른 그래프로 (예: 검색기 실험)
```

**T3-32부터 l3·e2e는 에이전트 그래프 한 턴이다.** 질문마다 새 대화(`thread_id = <qid>#0`)로 그래프를 돌리고, 끝난 그래프 상태에서
실전 1 답(`state["stat"]`)·검색 후보(`state["search"]`)·실전 1에 넘긴 표(`table_key`)를 읽어 L3 채점기에 넣는다.
- l3 그래프 = `evals.runners.multiturn_dev:build`에 골든 문항의 "질문 → 정답 표"를 검색으로 준 것. e2e 그래프 = `backend.app.main:build_agent`(서비스와 같은 검색 설정, 벡터 DB 필요). `--agent`로 바꾼다.
- `--retriever` 프리셋은 e2e에 더 이상 쓰이지 않는다(검색 설정은 `.env`의 `STAGE2` 등). 검색기만 비교하려면 L1을 쓴다.
- 검색 gate가 거절한 질문은 실전 1까지 가지 않는다. 그 거절(`out_of_scope`·`no_item`)을 실전 1 거절처럼 채점한다.
- `--llm mock`은 실전 1 mock·stage 2 끔·해석기 규칙으로 바꿔 어떤 모델도 부르지 않는다(`.env`가 `STAGE2=hcx`여도).
- T3-32 이전 l3·e2e run과 수치를 비교하지 않는다(계층 정의가 바뀜). l3는 같은 mock 입력에서 판정·답이 90/90 같았다(`plans/T3-32.md`).
- **재채점** (T3-39): `--from-run <run 폴더>`는 l3·e2e 모두 그 run의 `results.jsonl`(답·검색 순위·span)을 다시 채점한다. 채점 규칙을 고친 뒤 HCX를 다시 부르지 않고 전→후를 잰다.
  T3-32 이전 형식(`question_id`·`stat_answer`, 실전 1 하네스)도 읽는다. T3-39 이전 e2e run은 상위 5개 순위(`top_keys`)만 저장돼 정답 순위가 5 밖이면 "밖"으로 나온다. 재채점은 `ops_history.jsonl`에 쓰지 않는다.
- **정답 표 판정** (§6.3, e2e): 수치 골든 문항과 같은 질문의 검색 골든 항목을 qid → `phase1_id` → 질문 원문 순으로 찾아, 그 정답 슬롯의 표 **전부**를 정답 표로 본다.
  실전 1 골든(v1, `S-*`)은 qid가 달라 T3-39 전에는 연결이 0이었다(리포트 "L1 정답 슬롯과 연결된 문항"). 지금은 검색 골든이 실전 1 질문을 가져온 10문항이 연결된다(대체 표가 있는 문항 5).
  다른 정답 표로 맞게 답하면 `right_alternate`로 세고 검색 성공이다. 연결 안 된 문항은 여전히 L3 대표 표만 정답이다 → 검색 골든에 `phase1_id`를 더 달면 연결이 늘어난다.
- **큰 표** (T3-39): 채점기는 표 전체가 아니라 골든 사실과 답의 셀(`calculation.grade_refs`)만 Parquet에서 스트리밍으로 읽는다(`read_cells(path, refs)`).
  10 MB 표 하나가 4 GB·70초였던 것이 25 MB 표에서 0.4 GB·1.2초. 10 MB 넘는 표 7개(29문항)를 빼던 이유가 없어졌다. 실전 2의 초대형 표 청킹(KOS-179)은 Chroma 색인 쪽이라 이것과 무관하다.

```bash
```

### L3·L4 골든 파일 (T2-31)

| 파일 | 만드는 명령 | 내용 |
|---|---|---|
| `calculation_golden.jsonl` | `python -m evals.builders.phase1` | 실전 1 골든 90문항, 10표. clarify·default·causal 같은 어려운 유형 |
| `calculation_golden_diverse.jsonl` | `python -m evals.builders.diverse --rewrites evals/datasets/calculation_diverse_rewrites.jsonl` | 14개 조사, 72표, 264문항 (`D-` qid). value·compare·trend·거절 |
| `calculation_golden_wide.jsonl` (T3-46) | `python -m evals.builders.diverse --total 500 --seed 46 --out evals/datasets/calculation_golden_wide.jsonl --rewrites evals/datasets/calculation_wide_rewrites.jsonl` | 숫자 답이 있는 500문항(value 241 · compare 176 · trend 83), 382표, 14개 조사. 조사별 균등 할당(표가 모자란 조사만 표당 2문항), 조사 안에서는 메뉴 경로별로 돌아가며 표당 1문항. 한 표 최대 0.4%, 한 조사 최대 9.8%. 거절 문항 없음 |

기본값: l3·ragas는 실전 1 파일(`calculation_golden.jsonl`, l3의 오라클 검색이 그 질문으로 표를 찾음), e2e는 `--calc-golden`이 없으면 e2e 골든 시나리오(아래 "e2e 전용 골든셋"). wide는 e2e 골든의 재료다.
다른 파일을 쓰려면 `--calc-golden`에 넘긴다. 90문항 기준선과 비교할 때는 `--calc-golden evals/datasets/calculation_golden.jsonl`.
새 문항을 붙인 뒤에는 대체 정답 표 라벨을 다시 만든다: `python -m evals.builders.alternates --calc-golden evals/datasets/calculation_golden.jsonl evals/datasets/calculation_golden_wide.jsonl`.

```bash
uv run python -m evals.run_eval --layer l3 --stage <이름> --interval 5 \
  --calc-golden evals/datasets/calculation_golden.jsonl evals/datasets/calculation_golden_diverse.jsonl
```

diverse 문항의 숫자는 빌더가 Parquet 셀에서 직접 뽑는다. 질문 문장은 사람(또는 Claude)이 다시 쓰는데,
`check_question`이 연도·라벨·항목명이 남아 있고 다른 숫자가 없는지 확인한 뒤에만 반영한다. 통과하지 못하면 템플릿 문장을 그대로 쓴다.
`calculation_diverse_review_sample.csv`(5문항마다 1개)의 `ok`·`note` 열은 사람이 검수할 때 채운다.
`--extend`로 만든 파일은 새 문항만 `<out>.draft.csv`·`<out>.review.csv`에 쓴다 (wide: `calculation_golden_wide.review.csv`).

### e2e 전용 골든셋 (T3-46)

`python -m evals.builders.e2e_golden` → `e2e_golden.jsonl`(AgentScenario 617개, 722턴) + `e2e_golden_calc.jsonl`(숫자 정답 CalcItem).
wide 계산 골든 525문항(145표·14조사)을 재료로, 실패 유형 네 가지와 턴별 정답 라벨을 코드로 만든다(LLM 0).

| 실패 유형 (sid) | 문항 | 내용 |
|---|---|---|
| ① 숫자 추출·계산 (`E2E-N`) | 250 | value·compare, 조사별 균등 |
| ② 수식 선택 (`E2E-F`) | 132 | trend(% 계열은 pp_diff, pct_change 금지 / 수량은 pct_change), "몇 배"(ratio), "연평균 증가율"(cagr) |
| ③ 재작성 (`E2E-R`) | 125 | 2턴: 시도 이월·연도 정정·상대 연도("작년은?")·지시어·차트만·주제 전환, 멀티쿼리(두 표, "둘의 차이는?") |
| ④ 질문 분류 (`E2E-C`) | 110 | 거절(no_period·missing_value·범위 밖)·인과("왜")·평가("높은 편이야?" → 되묻기)·비율 합산(not_aggregatable)·연도 생략(기본값) |

턴마다 `expect.labels`(정답)와 `expect.rule_pred`(지금 규칙을 정답 표로 오프라인 실행한 값)를 담는다. 라벨 어휘는 코드의 규칙 분류
그대로다: `route`(rag·rewrite·clarify·refuse) · `reading`(new·chart_only·reference·carry_over·defer, context.py 규칙 1~6) ·
`resolved_question` · `sub_questions`·`op` · `outcome`·`judge_rule`·`refusal_reasons`·`default_applied`(실전 1 judge) ·
`qtype`(rule_plan) · `formula`·`formula_forbidden`(CombineFn) · `chart_request`. 정의는 `plans/T3-46.md` "정답 라벨".

```bash
python -m evals.run_eval --layer rules --stage <이름>          # 오프라인: rule_pred vs 정답 라벨 (LLM·검색 0)
python -m evals.run_eval --layer e2e --stage <이름>            # live: 서비스 그래프로 시나리오 실행, 툴·상태·숫자 + 라벨 채점
python -m evals.run_eval --layer e2e --stage <이름> --oracle   # 자기검증 (모든 비율 1.000)
python -m evals.run_eval --layer e2e --stage <이름> --calc-golden evals/datasets/calculation_golden.jsonl  # 예전 한 턴 계산 경로
```

e2e는 `--calc-golden`이 없으면 이 시나리오를 돈다. 오프라인 스위트(`evals.suite`, `scripts/dev.sh suite`)의 `e2e` → Ragas → 회귀 게이트는
기준선 연속성을 위해 계속 `calculation_golden.jsonl` 경로로 돌고(Ragas는 한 턴 계산 행을 읽음), 이 골든셋은 스위트의 `rules`(오프라인)와
`e2e-scenarios`(live) 단계가 돈다. 하나만: `bash scripts/dev.sh suite --stage <ID> --only rules e2e-scenarios`. 규칙을 고친 뒤에는 빌더를 다시 돌려 `rule_pred`를 새로 만든다.

### 검색기를 바꿀 때 판정 풀 넓히기 (T3-22)

검색 골든의 판정 풀은 BM25 두 종류의 상위 10개로 만들었다. 그래서 임베딩·리랭커가 새로 올린 표는 판정 없이 오답으로 채점된다(`unjudged@10`). L1 리포트의 "판정 구간"은 판정 없는 표를 오답으로 본 하한과 정답으로 본 상한이고, 실제 값은 그 사이에 있다. 구간을 좁히려면 사람이 판정한다. T2-02 동결 규칙에 따라 사람이 확인한 표만 대체 key로 **추가**하고, split·질문·답 없음 여부는 바꾸지 않는다.

```bash
# 1. 판정 시트: 저장된 L1 run의 상위 k개 중 아무도 판정하지 않은 (문항, 표)
uv run python -m evals.builders.pool sheet --runs evals/reports/<l1 run> [<l1 run> ...] --top 1 \
  --out evals/datasets/retrieval_pool_sheet_<ID>.csv
# 2. 사람이 slot 열을 채운다: 대체할 슬롯 번호(1~3) 또는 x(답이 안 됨). 빈칸은 아직 판정 안 함 → 건너뜀
# 3. 반영: 판정 행을 풀에 붙이고, 정답 행은 골든 슬롯에 대체 key로 추가, 바뀐 문항의 label_version +1
uv run python -m evals.builders.pool adjudicate --sheet evals/datasets/retrieval_pool_sheet_<ID>.csv
# 4. 재채점: 저장된 순위를 새 라벨로 다시 채점 (검색 다시 안 함, retriever·k·tau는 그 run 값)
uv run python -m evals.run_eval --layer l1 --from-run evals/reports/<l1 run> --stage <ID>-regrade
```

- 답 없음 문항에서 "답이 되는 표"로 판정한 행은 라벨을 바꾸지 않고 경고만 낸다.
- 재채점 run은 지연 시간을 원래 run에서 복사하므로 `ops_history.jsonl`에 다시 쓰지 않는다.

### 골든셋 v2 (T2-32, 전 계층)

표 하나에 질문 하나를 만들고, 같은 qid(`V2-<표 키>`)를 L1·L2·L3에 함께 쓴다. 14개 조사 전체에서 표를 뽑았다. 기본 경로는 v1 그대로이므로 v2를 쓰려면 플래그로 넘긴다.

```bash
uv run python -m evals.run_eval --layer l1 --retriever bm25-record --stage <이름> \
  --golden evals/datasets/retrieval_golden_v2.jsonl --pool evals/datasets/retrieval_pool_v2.csv
uv run python -m evals.run_eval --layer l2 --stage <이름> --split all --query-golden evals/datasets/query_golden_v2.jsonl
uv run python -m evals.run_eval --layer l3 --stage <이름> --interval 5 --calc-golden evals/datasets/calculation_golden_v2.jsonl
```

다시 만드는 순서는 `python -m evals.builders.golden_v2 draft → pool → finish`다(`golden_v2_texts.jsonl`에 문장을 쓰고, `retrieval_pool_v2.csv`의 `slot`을 채운다).

- 숫자, L2의 기간·연도·지역, 풀의 `rule:*` 행은 코드가 만든다.
- 질문 문장, measures·dimensions, `auto=claude` 행의 판정은 Claude가 했다.
- 그래서 v2의 `unjudged@10`은 "Claude도 규칙도 보지 않은 표"의 비율이다.
- `golden_v2_review_sample.csv`(5문항마다 1개)는 사람이 검수할 표본이다.

실패 단계 추적(`failure`, Jaeger 내보내기)은 `l3`·`e2e`에만 붙는다. `l0`·`l1`·`l2`·`ablation` 행에는 판정·span이 없다.

결과는 `evals/reports/<시각>-<stage>/`에 저장된다. 이 폴더에는 질문 원문이 들어 있어 git에 올리지 않는다.

| 파일 | 내용 |
|---|---|
| `report.md` | 판정표, 오류 유형, **실패 단계 (trace)** 표, 운영 지표 |
| `report.html` | **모든 계층**(T3-20). 맨 위 "읽는 법", 결과, 운영 지표(지연·토큰·비용·실패율·실행 간 추이), 행별 결과(주의 행 먼저, 200행까지). l1·l3·e2e는 차트·오류 유형·실패 span 경로·Jaeger 링크, agent는 대화별 화면(턴마다 툴 칩·답·판정)과 턴별 span 워터폴, l0·l2·ablation·l012는 report.md를 HTML로. 다시 그리기: `python -m evals.report_html --run <run 폴더>` |
| `results.jsonl` | 문항별 행. `errors`, `spans`, `failure`, `trace_id`가 들어 있다 |
| `manifest.json` | 골든·데이터 해시, git, 요약(`summary.failures` = 단계별 건수) |

### 검색 설정 (`.env` → 환경변수 → 설정 객체)

실전 2 모듈은 `.env`도 환경도 읽지 않는다. 진입점이 레포 루트 `.env`를 환경변수로 올리고(셸 값 우선), `evals/config.py`가 환경변수로 `IndexSpec`·`SearchOptions`를 만들어 넘긴다(이름·기본값은 실전 2 `search_config_from_shell`과 같음, 데이터 경로는 이 레포 `data/`).
값이 없으면 기본값(`localhost:8001`, 컬렉션 없음, stage 2 끔)으로 조용히 돈다. 옛 결과와 비교하려면 같은 값을 둔다.

`DATABASE_URL` `VECTOR_COLLECTION` `EMBED_CKPT` `RERANK_CKPT`(staged*) `TEXT_FORMAT` `HQ_BUILD`(비우면 끔) · HCX 프리셋·L3 HCX는 `HCX_API_KEY`, gpt 프리셋은 `OPENAI_API_KEY`.
L2·ablation(`--slot-source pred`)은 `data/sigungu.json`을 `parse_query(sigungu=…)`에 넘긴다(넘기지 않으면 시군구 질문의 `regions`만 조용히 떨어진다).

### Jaeger로 보기 (선택)

실시간 계층(l1·l3·e2e·agent)은 `scripts/dev.sh`로 돌린다(T3-33). API 서버와 같은 준비를 한다: Docker Desktop이 꺼져 있으면 켜고,
PostgreSQL(pgvector)·Jaeger를 띄우고(`--wait`), `OTEL_EXPORTER_OTLP_ENDPOINT`를 맞추고, 출력을 버퍼 없이 화면과 `evals/reports/logs/<시각>-eval.log`에 같이 쓴다.
문항(턴)마다 `[번호/전체] qid 초` 진행 줄이 나온다.

```bash
bash scripts/dev.sh eval --layer l1 --retriever graph:staged-gpt --stage <이름>   # run_eval 인자 그대로
bash scripts/dev.sh eval --layer e2e --interval 5 --stage <이름>
bash scripts/dev.sh suite --stage <이름> --only l1-graph e2e                       # evals.suite 인자 그대로
```

`uv run python -m evals.run_eval`로 직접 돌리면 Docker·Jaeger가 꺼져 있어도 그대로 시작한다: 검색 계층은 벡터 DB 연결에서 실패하고,
`.env`에 OTLP 주소가 있으면 trace 전송 실패 로그만 쌓인다(리포트의 trace 카드는 메모리 span이라 그대로 나온다).

http://localhost:16686 에서 서비스 `kosis-agent`를 고르고 Tags에 `deployment.environment.name=eval kosis.eval.layer=l3`처럼 넣는다(§8.1).
리포트의 Jaeger 링크는 이 필터가 걸린 검색이다. 문항 1개가 trace 1개이고, 판정은 채점 뒤 `eval.question` span이 링크로 붙인다.
- `OTEL_EXPORTER_OTLP_ENDPOINT`가 없으면 아무것도 보내지 않는다. 평가는 그대로 돈다.
- Jaeger는 메모리에 저장하므로 컨테이너를 재시작하면 trace가 사라진다. 실패 단계는 run 폴더의 리포트에 남아 있다.
- trace는 실행 중에 열린다(T3-32, 서비스와 같은 콜백). `--from-run` 재채점은 새로 실행하지 않으므로 trace를 만들지 않는다.

## 2. trace 구조 (문항 1개 = trace 1개)

```
eval.question             qid, kind, layer, verdict, errors, failure.stage / failure.span / failure.reason
├─ retrieve               e2e만: retriever, top_keys, gold_rank, right_table
└─ tool.summarize_stat    table_key, outcome, numbers_origin            ← 어떤 도구를 불렀는가
   ├─ judge               outcome, rule, final                          ← 질문 판정 (답함/거절/되묻기)
   ├─ analyze.rules       type, unresolved                              ← 규칙으로 슬롯·계획 만들기
   ├─ analyze.llm_fill(_retry) / analyze.llm_plan(_retry)               ← 규칙이 못 채우면 LLM이 계획
   ├─ event_lookup                                                      ← 사건 시점 조회 (LLM)
   ├─ compute             facts | outcome=refused, reason               ← 숫자 계산 모듈 (pandas, 결정적)
   ├─ compose ×n / verify ×n                                            ← 문장 생성(LLM) → 숫자 검증
   ├─ template                                                          ← 검증 실패 시 코드 템플릿으로 대체
   └─ fallback_chatbot    gen_ai.*                                      ← 계획 실패 시 LLM이 숫자까지 씀 (계산 모듈 우회)
```

- `tool.summarize_stat` 아래 span은 실전 1 파이프라인이 기록한다(T1-22, `decision_metadata["spans"]`).
- LLM span에는 OTel GenAI 속성(`gen_ai.request.model`, `gen_ai.usage.input_tokens/output_tokens`)이 붙는다.
- 토큰·비용 집계는 Jaeger가 아니라 `ops.json`과 `reports/ops_history.jsonl`(`ops.py`)에서 한다.
- span 상태 `error`가 곧 실패를 뜻하지는 않는다. 예를 들어 `verify` error 뒤에 compose를 다시 시도하는 것, `analyze.rules` error 뒤에 llm_fill로 넘어가는 것은 정상 흐름이다.
  실패로 판정한 span은 아래 규칙으로 따로 고른다(리포트에서 굵은 테두리).

## 3. 실패 단계 판정 규칙 (`trace.blame`)

PASS가 아닌 문항마다, **위에서부터 첫 번째로 맞는 규칙 하나**로 단계를 정한다.
오류가 여러 개면 순서로 정하고, 나머지 오류는 사유 끝에 "(같이 나온 오류: …)"로 적는다.

| 순서 | 단계 (`stage`) | 리포트 표기 | 조건 | 실패 span |
|---|---|---|---|---|
| 1 | `retrieval` | 검색 실패 | e2e에서 검색 1위가 정답 표가 아니거나 검색 결과가 없다 | `retrieve` |
| 2 | `tool_call` | 도구 호출 실패 | 답이 없다 (`run_failure`: 예외·모델 호출 실패) | 마지막 error span, 없으면 `tool.summarize_stat` |
| 3 | `tool_skipped` | 계산 모듈 건너뜀 | `fallback_chatbot` span이 있다: compute를 거치지 않고 LLM이 숫자를 썼다 | `fallback_chatbot` (사유에 앞서 실패한 `analyze.*` 기록) |
| 4 | `compute` | 계산 거절·실패 | 답할 수 있는 문항인데 `compute`가 거절(refused)하거나 error | `compute` |
| 5 | `understanding` | 질문 이해 실패 | 판정 오류 → judge가 바로 결론 냈으면 `judge`, 아니면 마지막 `analyze.*` | `judge` / `analyze.*` |
|   |  |  | 사실 오류(계획이 고른 셀이 틀림) | 마지막 `analyze.*` |
| 6 | `compose` | 문장 생성 실패 | `unsupported_claim`만 남았다: 문장에 근거 없는 숫자 | 마지막 `compose` |
| 7 | `unknown` | 미분류 | 위 규칙에 안 맞음 → **규칙을 보강할 대상** (0이 목표) | — |

이 순서로 정한 이유:
- **`tool_skipped`가 `understanding`보다 앞이다.** 계획을 못 만들어서(질문 이해 실패) fallback으로 빠지는 경우가 대부분이다.
  하지만 고쳐야 할 행동은 "숫자를 계산 모듈 없이 LLM이 썼다"는 것이므로 그쪽을 단계로 보여준다. 근본 원인인 analyze 단계는 사유에 함께 적는다.
- **`compute`가 `understanding`보다 앞이다.** 계산 모듈이 거절해서 생긴 `false_refusal`이 판정 오류로 잘못 분류되지 않게 하려는 것이다.
- **사실 오류는 `compute`가 아니라 `understanding`이다.** compute는 계획이 고른 셀을 그대로 계산하는 결정적 코드다.
  값·단위·차원·시점이 틀렸다면 계획(analyze)이 다른 셀을 고른 것이다.
- **답 없는 질문(`not_answerable`·`clarify`)은 판정 오류가 1번보다 앞이다 (T3-45).** 이 문항의 "정답 표"는 가장 가까운 표일 뿐이다.
  `missed_refusal`·`wrong_reason`·`assumed_condition`은 표와 상관없이 거절·되묻기 판단의 문제라서 `understanding`으로 둔다.

T3-45 보강:
- **실행 중 예외 뒤 검색 후보 복원.** 검색이 끝난 뒤 실전 1이 예외를 던지면(예: `101_DT_1B8000G` SchemaError) 그래프 상태가 비어 행의 `keys`·`used_table`이 사라진다.
  `trace.restore_search`가 검색 노드 span(마지막 순위 노드 = rerank2)의 앞 k개로 채운다. 러너와 재채점(`--from-run`)이 같은 함수를 쓴다.
  조건: error가 있고, gate route=found이고, statistics span이 있는 행만. 같은 run의 정상 행에서 규칙(keys = 그 노드 앞 k개)이 맞지 않으면 아무것도 채우지 않는다.
- **오류 태그를 맞힌 표 / 틀린 표로 나눠 본다** (`summary.errors_by_top1`, report.html "오류 유형 — 맞힌 표 / 틀린 표").
  틀린 표에서 나온 셀 오류는 대부분 다른 표의 셀을 정답 셀과 비교한 결과다. 계획의 셀 선택 문제는 맞힌 표 열을 본다.
- **검색 노드별 정답 순위** (`summary.search_nodes`, `trace.node_ranks`): 답할 문항 전체에서 노드마다 정답 표가 1위·top k·후보 밖인 수. 문항별 대표 노드와 달리 모든 행을 센다.

## 4. 오류 유형 (`evaluators/calculation.py`)

L3·e2e 채점기가 붙이는 오류다. 정답 표의 Parquet 셀과 골든 사실(`required_facts`, `aux_facts`)에 비교해 정한다.
문항 종류(`kind`)별 FAIL 오류 목록(`FAIL`)에 들어 있으면 FAIL, 오류가 없으면 PASS, 나머지는 PARTIAL이다.
설명 문구는 `ERROR_INFO`에서 가져와 report.html의 "오류 유형" 카드 아래에도 표시된다.

| 오류 | 의미 | 판정 | 보통 판정되는 단계 |
|---|---|---|---|
| `wrong_value` | 정답 셀은 골랐는데 값이 다르다 (파생값은 반올림 범위 밖) | FAIL | 질문 이해 (`analyze.*`) |
| `wrong_unit` | 값은 같은데 단위가 다르다. 보통 같은 숫자를 가진 다른 항목(예: '년' 대신 '%')을 골랐다 | FAIL | 질문 이해 (`analyze.*`) |
| `wrong_period` | 항목·분류는 맞는데 시점이 다른 셀을 썼다 | FAIL | 질문 이해 (`analyze.*`) |
| `wrong_dims` | 필요한 셀이 답에 없다. 다른 항목·지역·분류 셀을 골랐거나 파생값의 입력 셀이 다르다 | FAIL | 질문 이해 (`analyze.*`) |
| `missing_aux` | 주 숫자는 맞지만 함께 보여야 할 보조 셀(비교 대상·분모 등)이 빠졌다 | PARTIAL | 질문 이해 (`analyze.*`) |
| `unsupported_claim` | 답에 근거 없는 숫자가 있다: 표 셀과 다르거나, 파생값이 다시 계산되지 않거나, 연결 문장에 숫자를 지어냈다 | FAIL | 문장 생성 (`compose`) |
| `false_refusal` | 답할 수 있는 질문인데 거절·되묻기를 했다 | FAIL | 계산 거절(`compute`) 또는 질문 이해(`judge`·`analyze.*`) |
| `missed_refusal` | 답할 수 없는 질문(기간·범위 밖)인데 답을 했다 | FAIL | 질문 이해 |
| `wrong_reason` | 거절은 맞지만 사유가 정답 사유 목록에 없다 | FAIL | 질문 이해 |
| `assumed_condition` | 되물어야 할 모호한 질문인데 조건을 임의로 정해 답했다 | FAIL | 질문 이해 |
| `missing_clarification` | 되묻기는 했지만 선택지가 2개 미만이다 | FAIL | 질문 이해 |
| `disallowed_default` | 기본값이 허용된 질문에서 답하지 않았거나, 허용되지 않은 기본값을 적용했다 | FAIL | 질문 이해 |
| `unstated_default` | 기본값을 적용했는데 무엇을 적용했는지 밝히지 않았다 | PARTIAL | 질문 이해 |
| `missing_source` | 답했는데 출처가 없다: `source`가 비었거나 답 문장(`text`)에 조사명·통계표명이 없다 (T3-40). 실전 1은 `final_text`가 본문 뒤에 `출처: KOSIS / 조사명 / 통계표명 (url)`을 붙이고, `parts`에는 출처가 없으므로 `text`를 본다. 거절·되묻기는 검사하지 않는다 | PARTIAL | 문장 생성 (`compose`) |
| `run_failure` | 답이 없다: 예외, 모델 호출 실패, 검색 결과 없음 | FAIL | 도구 호출 실패 (e2e에서 검색 결과가 없으면 검색 실패) |

e2e에서는 1위 표가 틀리면 위 오류와 상관없이 1번 규칙(검색 실패)이 먼저 걸린다.

## 5. 리포트 읽는 법

### 고칠 담당 (T2-34)

`l0`·`l1`·`l2`·`l3`·`e2e` 리포트는 제목 바로 아래에 실패를 담당별로 묶어 보여 준다(문항 ID 5개까지).

| 담당 | 묶이는 실패 |
|---|---|
| 데이터 (메타데이터·조건 파서) | L0 정답 표 measure·dimension 미매핑, 적재 키 불일치 / L2 `parse_query` 필드별 오답 |
| AI — 검색·답 없음 판정 | L1 상위 5에 정답 표 없음, 답 없음 판정 틀림 / L4 검색 1위가 다른 표 |
| AI — summarize_stat 계산 | L3 정답 표를 주고도 틀림 / L4 정답 표인데 틀림 |
| AI — summarize_stat 질의 분석 | L4 답의 연도·지역이 L2 정답과 다름 (`StatAnswer.query`) |

기준 문서 §6.3·§4.3·§5에 맞춘 항목:
- L4 정답 표: `--golden`의 같은 qid L1 문항이 있으면 그 정답 슬롯의 키 전부를 정답으로 본다. 골든 v2만 qid가 이어진다.
- L4 `{연도}`·`{지역}`: `--query-golden`의 같은 qid L2 라벨과 대조한다.
- no_period 거절률: L1은 사유별 답 없음 판정률, L4는 `allowed_reasons`에 no_period가 있는 문항의 거절률·사유 일치율.
- L2 목록 필드 P/R: regions·dimensions·years. P가 낮으면 지어낸 조건, R이 낮으면 놓친 조건이다. measures는 검색어 확장 규칙이라 P/R을 내지 않는다.

1. **오류 유형** 카드에서 무엇이 틀렸는지 본다. 막대 아래에 각 오류의 의미, 판정된 단계, 해당 문항이 나온다.
2. **실패 단계 (trace)** 카드에서 어느 단계가 가장 많이 실패했는지 본다. 개선할 곳의 우선순위가 된다.
3. **실패 문항별 원인** 표에서 문항마다 실패 단계·span·사유와 span 경로를 본다.
   - 경로는 `retrieve → summarize_stat → judge → analyze.rules → …` 순서다. 굵은 칩이 실패로 판정한 span이고, ✕는 상태가 error인 span이다.
   - 칩에 마우스를 올리면 지연이 보인다. trace 링크를 누르면 Jaeger에서 span 속성까지 볼 수 있다.

예시 (T1-27 재채점, `reports/20261031-200757-T2-15-regrade`): non-PASS 4건이 모두 `질문 이해 실패 @ analyze.rules`로 판정됐다.
그중 S-EDG-09의 경로는 `verify✕ → compose → verify✕ → template✕`로, 계획 오류가 문장 단계까지 번졌다.

## 6. 에이전트 지표 (`--layer agent`, T3-06 · KOS-120)

멀티턴·멀티쿼리 대화를 **툴 호출 기록**으로 채점한다. LLM 판정은 쓰지 않는다. 아키텍처(ReAct·Plan-and-Execute·멀티에이전트, T3-07~10)를 바꿔도 같은 채점기를 쓴다.

```bash
uv run python -m evals.run_eval --layer agent --stage T3-06-oracle --oracle --split all              # 자기검증: 모든 비율 1.000
uv run python -m evals.run_eval --layer agent --stage <이름> --agent <module:function> --repeat 3    # 실행, pass^3
uv run python -m evals.run_eval --layer agent --stage <이름> --from-run evals/reports/<run>          # 과거 실행 재채점
```

**멀티턴 dev 시나리오 (T3-27, `plans/T3-27.md`)**: T3-05 정식 평가셋 전까지 쓰는 dev 세트다. 빌더가 만드는 시나리오는 **전부 `split=dev`**다(T3-39):
표현이 빌더 템플릿이고 해석 규칙을 이 세트로 고쳤으므로 held-out이 아니다(그전에는 검색 골든의 test split을 물려받아 50개가 test로 보였다). 아키텍처 비교(T3-07~10)의 test는 사람이 쓴 T3-05 파일만 쓴다. `calculation_golden`의 같은 표 문항을 이어 붙여 6유형(R 지시어 · E 생략 이월 · X 정정 · S 주제 전환 · C 차트 후속 · M 한 턴 여러 요청)을 코드로 만든다. E·X의 정답은 원천 문항의 셀 키에서 지역 코드·연도만 바꿔 Parquet에서 읽는다(합성 정답, 셀이 없거나 기호면 만들지 않음).

```bash
uv run python -m evals.builders.multiturn     # → agent_scenarios_multiturn_dev(_structure).jsonl, calculation_golden_multiturn.jsonl
# 구조 지표 (mock 실전 1, calc_qid 뺀 파일). 검색은 골든 조회(원문이 들어 있으면 그 표, 아니면 관련 없는 표 하나)
STAT_PROVIDER=mock INTERPRETER=hybrid uv run python -m evals.run_eval --layer agent --stage <이름> --split dev \
  --agent evals.runners.multiturn_dev:build --scenarios evals/datasets/agent_scenarios_multiturn_dev_structure.jsonl
# 수치 지표 (HCX 실전 1, 합성 정답을 같이 넘김)
STAT_PROVIDER=hcx uv run python -m evals.run_eval --layer agent --stage <이름> --split dev \
  --agent evals.runners.multiturn_dev:build --scenarios evals/datasets/agent_scenarios_multiturn_dev.jsonl \
  --calc-golden evals/datasets/calculation_golden.jsonl evals/datasets/calculation_golden_multiturn.jsonl
```

`INTERPRETER`는 rules | hybrid | llm이다. 유형(R·E·X…)별 비율은 `results.jsonl`의 `sid`(`MT-<유형>-<번호>`)로 나눠 본다.

**차트 자동 선택 시나리오 (T3-37, KOS-197, `plans/T3-37.md`)**: 종류 말이 없는 차트 요청("그래프로 보여줘", "그려줘")을 C(차트 후속)·M(한 턴 여러 요청)으로 30개 만든다.
기대 계열(`expect.auto_chart`)은 계산 골든 문항 `type`에서 코드로 정한다(`builders/multiturn.AUTO_CHART`, 실전 1 `kosis_chat/chart/builders.py`의 종류 선택과 같다):
value → 막대 · trend → 꺾은선 · compare → 막대 · share → 원형 · extreme → 막대. 그린 실전 1 종류는 계열로 묶어 비교한다(bar·timeline·grouped_bar = 막대, line·multi_line = 꺾은선, pie = 원형).
기존 파일은 건드리지 않고 아래 두 파일만 쓴다(sid `MT-C-A001`·`MT-M-A001`).

```bash
uv run python -m evals.builders.multiturn --auto-chart   # → agent_scenarios_multiturn_chart_auto(_structure).jsonl
STAT_PROVIDER=mock INTERPRETER=rules uv run python -m evals.run_eval --layer agent --stage <이름> --split dev \
  --agent evals.runners.multiturn_dev:build --scenarios evals/datasets/agent_scenarios_multiturn_chart_auto_structure.jsonl
```

**평가 태스크별 성공률 (T3-29)**: `--scenarios`는 파일을 여러 개 받는다(sid 중복 금지). 멀티턴·멀티쿼리 파일을 한 실행에 넣으면
리포트의 "평가 태스크" 표와 `summary.by_task`에 태스크마다(`multiturn` = `MT-*`, `multiquery` = `MQ-*`, 그 밖 `other`) 대화 성공률·턴 통과율·툴 선택·상태·
최종 수치·합산·근거 없는 숫자 턴(답변 품질)이 따로 나온다. 파일 하나만 주면 표에 한 줄이다.

```bash
uv run python -m evals.run_eval --layer agent --stage <이름> --split dev --agent evals.runners.multiturn_dev:build \
  --scenarios evals/datasets/agent_scenarios_multiturn_dev_structure.jsonl evals/datasets/agent_scenarios_multiquery_dev_structure.jsonl
```

- 시나리오: `evals/datasets/agent_scenarios.jsonl` (T3-05, `--scenarios`로 바꿈). 계약은 `evals/schemas.py`의 `AgentScenario`.
  턴마다 `expect`에 부를 툴(순서대로, 채점할 인자 필드만), 턴이 끝날 표(`table_key`), 거절 여부(`refuse`), L3 문항(`calc_qid`)을 적는다.
  한 턴에 여러 요청("찾아서, 계산해서, 그려줘")은 `tools`에 여러 개를 적고, "이거"·"그 표"는 다음 턴의 `table_key`로 채점된다.
  **부분 거절 (T3-38, KOS-199)**: 멀티쿼리 턴("A 그리고 B")에서 하위 질문 일부만 자료가 없으면 `refuse_parts`(하위 질문 순서의 bool 목록, 예: `[false, true]`)를 적는다.
  k번째 하위 질문 = k번째 `search_tables`. 그 하위 질문의 거절 = 검색이 표를 못 찾음(그래프는 그 하위 질문의 `summarize_stat`을 부르지 않음) 또는 그 `summarize_stat`이 `refused`.
  틀린 하위 질문마다 `missed_refusal`/`false_refusal` 하나, 행의 `refusal_errors`에 하위 질문 번호(`part`)가 남는다. 턴이 나뉘지 않았거나(검색 수 ≠ 하위 질문 수) 같은 표로 되돌려 원문 한 번 물었으면 하위 질문마다 턴 전체 거절 여부를 쓴다.
  이때 `calc_qids`는 답하는 하위 질문(false)에만 순서대로 붙는다. `refuse_parts`가 없으면 지금 규칙(턴 전체 `refuse`) 그대로다.
  한계: 하위 질문 1이 실전 1에서 거절되고 바로 다른 표로 하위 질문 2를 물으면 `final_calls`가 1을 대체 표 시도로 접는다(T3-23 규칙). 빌더 R 유형은 답하는 하위 질문을 앞에 둔다.
  빌더: `python -m evals.builders.multiquery` → `agent_scenarios_multiquery_dev_refusal.jsonl`(R: 답할 수 있는 문항 + `not_answerable` 문항, 20개. 기존 B·K·N·P 파일은 바이트 그대로).
  `summarize_stat`의 인자는 `question`·`table_key`와 조건 4개(`period_start`·`period_end`·`regions`·`dimensions`)다. 조건 이름은 L2 라벨(`QueryItem`)·실전 2 `ParsedQuery`와 같으므로, 시나리오 `args`에는 L2 라벨 값을 그대로 적으면 된다. 지금 워크플로 그래프는 실전 2 `parse_query`로 조건을 채운다(`src/kosis_agent/graph/main_graph.py`).
- 실행 기록: 턴마다 그래프를 `thread_id = <sid>#<run>`으로 `invoke`하고, 실행 중 열린 OTel span(`kosis_agent.observability.tracing.LiveSpans`)에서 툴 이름·인자·결과·지연·LLM 호출·토큰을 읽는다.
  span 이름·속성은 OTel GenAI 규약 그대로다: `invoke_agent`(`gen_ai.conversation.id`, `kosis.turn.index`, 턴 종료 이유 `kosis.outcome` 등) · `execute_tool <name>`(`gen_ai.tool.name`, `gen_ai.tool.call.arguments`·`.result`) · LLM 호출(`gen_ai.operation.name=chat`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`·`output_tokens`). 읽는 법은 §8.
  `OTEL_EXPORTER_OTLP_ENDPOINT`가 있으면 같은 span이 Jaeger로 가고, 채점 뒤 `eval.scenario` span이 턴 root span들에 링크로 판정(`kosis.verdict`, `kosis.failure.turn`)을 붙인다.

| 지표 | 정의 | 채점 |
|---|---|---|
| 툴 선택 정확도 | 부른 툴 이름 순서 == 정답 순서인 턴의 비율. **지금 메인 흐름(결정적 워크플로)에서는 코드가 순서를 정하므로 이 값과 툴 순서 일치는 거의 1.000 고정이다** — 해석(상태·인자)·수치가 실제 신호이고, 툴 선택은 LLM이 툴을 고르는 변형(T3-07~09)에서 값이 생긴다 | 결정적 |
| 툴 순서 일치 | 정답 툴이 그 순서대로 나타난 턴의 비율 (불필요 호출 허용) | 결정적 |
| 빠진 호출 · 불필요 호출 | 툴별 개수 (정답과 실제 호출의 다중집합 차) | 결정적 |
| 파라미터 정확도 | 정답 인자 필드(`table_key`·`period_start`·`period_end`·`regions`·`dimensions`·`chart_type`)별 일치 비율. 같은 툴의 k번째 호출과 비교, L2 규칙(`query.field_correct`: 목록은 집합, 나머지는 문자열 일치) | 결정적 |
| 상태(표 키) 정확도 | 턴이 끝난 표 == 정답 `table_key`. 끝난 표 = 마지막 `summarize_stat`/`make_chart`의 `table_key`, 없으면 마지막 검색 1위 | 결정적 |
| 최종 수치 정확도 | `calc_qid` 턴의 `summarize_stat` 답을 L3 채점기(`calculation.grade`, Parquet 셀 대조) 그대로 채점해 PASS인 비율 | 결정적 |
| 근거 없는 숫자 | 최종 답 문장의 숫자 중 근거가 없는 것이 있는 턴 수 (0이 목표, T3-40). 근거 = 이 대화의 질문 숫자 · `summarize_stat` 사실 값(반올림 허용, 증감은 크기로 비교) · 사실의 시점·라벨·단위 숫자 · `combine_values` 결과(반올림 허용) · 기본값 조건(`default_applied`) 숫자. 출처(인용 문자열·url·조사명·통계표명·표 ID)와 실전 1 코드 줄(`참고:` 줄, 거절 메시지)은 문장에서 그대로 지운 뒤 센다. 검색 결과(`picked`·`candidates`)·셀 참조·항목 코드·툴 인자는 근거가 아니다. "약 10만"·"3천 명"·"1억 2천만"은 한 숫자로 읽고 단위 반올림(10만 → ±5,000)을 허용한다 | 결정적 |
| 출처 빠진 턴 | 답한 `summarize_stat`(멀티쿼리면 하위 답마다)의 조사명·통계표명이 최종 답에 없는 턴 수 (`missing_source`, T3-40). 거절·되묻기 답, 답이 없는 턴, 예외로 끝난 턴(`run_failure`)은 뺀다 | 결정적 |
| 대체 표 시도 | `summarize_stat`이 거절(또는 툴 오류)된 뒤 바로 **다른** `table_key`로 다시 부른 호출 수(`alt_attempts`)와 그런 턴 수. 시도는 감점하지 않고 위 지표에서 모두 뺀다. 툴·인자·상태·거절·수치는 **최종 호출**로만 채점하므로, 최종 표가 정답 표와 다르면 `lost_state`, 최종 답이 틀리면 `wrong_numbers`다 (T3-23). 같은 표를 다시 부르거나 답한 뒤 다시 부른 호출은 시도가 아니라 불필요 호출이다 | 결정적 |
| 차트 데이터 일치 (T3-37) | make_chart가 그린 ChartSpec 점의 숫자가 같은 턴(차트만 다시 그리는 턴이면 대화의 마지막) `summarize_stat` 사실 값 안에 있는 차트의 비율. 툴 content에는 `kind`·`drawn`만 있으므로 인자 `chart`(실전 1 ChartSpec)의 점을 읽는다. 다시 그리기(실전 1 `redraw`)는 종류를 바꾸고 점을 자를 뿐 숫자를 더하지 않는다(Vega-Lite 없음, KOS-202). 실전 1 계약상 사실이 아닌 맥락 점(`fact_id` 없음: 같은 표의 다른 기간·후보 셀, 원형의 "그 외" = 합계 사실 − 조각)은 통과시키고 `chart_context_values`로 센다 | 결정적 |
| 차트 자동 선택 일치 (T3-37) | `expect.auto_chart`가 있는 턴(종류 말 없음)에서 그린 종류의 계열 == 기대 계열인 비율. 리포트에 `기대→실제` 분포 | 결정적 |
| 차트 턴 통과율 (T3-37) | make_chart를 기대한 턴 중 오류가 없는 턴의 비율 (`by_task`에도) | 결정적 |
| 턴 통과율 | 오류가 하나도 없는 턴의 비율 | 결정적 |
| 대화 성공률 | 모든 턴이 통과한 대화(실행)의 비율. 리포트에 처음 실패한 턴 분포 | 결정적 |
| pass^k | `--repeat k`번 모두 성공한 시나리오의 비율 | 결정적 |
| 요청당 비용·지연 | 턴 = 요청 1건. `ops.json`의 p50·p95 지연, LLM 호출 수, 토큰, 비용 (`ops_history.jsonl`에 누적). 비용은 턴의 **모델별 토큰**(`tokens_by_model`: 해석기 gpt-4o-mini + 실전 1 HCX + rerank2)에 `evals/pricing.json` 단가를 모델마다 곱한다(T3-39). 단가 없는 모델이 하나라도 있거나 통화가 섞이면 합계는 없음으로 두고 모델별 토큰만 보인다. HCX 단가(KRW)는 아직 null: 공식 가격표(ncloud.com CLOVA Studio `#pricing`)가 스크립트로 그려져 자동으로 읽지 못했다(T3-38, 출처는 `pricing.json` `_note`). 브라우저로 확인해 `_source`와 함께 채운다. Gemma(자체 호스팅)는 0원 vs GPU 시간 환산을 팀이 정할 때까지 null | 결정적 |

턴 오류: `run_failure` 실행 실패 · `missing_call` · `extra_call` · `wrong_order` · `wrong_args` · `lost_state`(지시어·조건 이월 실패 포함) · `missed_refusal` · `false_refusal`
(거절 = 검색 `route` `refuse` 또는 `summarize_stat` `outcome` `refused`, 또는 툴을 하나도 안 부름. `refuse_parts`가 있으면 하위 질문마다) · `wrong_numbers` · `unsupported_final_number` · `missing_source` ·
`chart_data_mismatch`(차트에 사실·표 데이터가 아닌 숫자, T3-37) · `wrong_chart_type`(종류 말 없는 턴에서 make_chart를 불렀는데 계열이 다름, T3-37; 안 불렀으면 `missing_call`만).
에이전트 계층의 `wrong_numbers`(L3 채점)는 출처 검사를 끄고(`grade(..., cite=False)`) 최종 답의 출처는 `missing_source`로 따로 센다. 예외가 난 실행은 거기서 멈추고 남은 턴은 `run_failure`다.

- LLM 호출·토큰 (T3-31부터): 해석기·멀티쿼리(LangChain), 실전 1 HCX 단계, 실전 2 rerank2를 모두 센다(§8.3). T3-31 이전 agent run은 해석기 호출만 세었으므로 `ops_history.jsonl`에서 그 전후의 토큰·비용을 비교하지 않는다.
- 테스트: `tests/test_evals_agent.py` (oracle 1.000, 빠진·불필요 호출, 인자 불일치, 멀티턴 중간 실패와 pass^k, 거절, L3 연결·근거 없는 숫자, 실제 LangGraph 그래프 → span → 기록 왕복), `tests/test_evals_partial_refusal.py` (부분 거절 양방향·`refuse_parts` 없을 때 기존 규칙·R 유형 oracle, T3-38).

## 7. 규칙을 바꿀 때

- 코드 위치 (T3-29): `run_eval.py`는 CLI와 결과 폴더 기록만 한다. 계층마다 `evals/tasks/<계층>.py`가 `run(args) -> (rows, summary, report)`,
  `owners()`(고칠 담당), `target()`(ops_history 대상)을 갖는다 — `l0_metadata` · `l1_retrieval` · `l2_query` · `ablation` · `l012` · `l3_calculation` · `l4_e2e` · `agent`, 공용 조각은 `common`.
  새 계층은 파일 하나를 만들고 `run_eval.TASKS`에 한 줄 넣는다(헤드라인 지표는 `suite.metrics`, HTML 본문은 `report_html`).
- 에이전트 채점기 `evaluators/agent/`: `tools.py`(툴 선택·순서·인자) · `state.py`(멀티턴 상태·거절) · `numbers.py`(L3 수치·멀티쿼리 하위 답·합산) ·
  `chart.py`(차트 데이터·자동 선택, T3-37) · `answer.py`(답변 품질: 근거 없는 숫자·출처). `__init__.py`의 `grade_turn`이 이 순서로 오류를 이어 붙이고 `aggregate`가 `by_task`를 더한다.

- 판정 규칙: `trace.py`의 `blame()`. 단계를 추가하면 `STAGES`에도 넣는다. 리포트 표기와 판정 순서 표시가 여기서 나온다.
- 오류 유형: `evaluators/calculation.py`의 `grade()`·`fact_errors()`. 새 오류를 만들면 `ERROR_INFO`와 이 문서 4절에 설명을 추가한다.
- 테스트: `tests/test_evals_trace.py`에 규칙마다 합성 행이 하나씩 있다. 규칙을 바꾸면 해당 케이스를 고치거나 추가한다.
- `unknown`이 생기면 그 문항의 `errors`와 `spans`(results.jsonl)를 보고, 맞는 규칙을 넣거나 기존 규칙의 조건을 넓힌다.

## 8. 계측 읽는 법 — trace·메트릭 (T3-30 · T3-31, KOS-114 · KOS-115)

채점(§3~6)은 "맞았나"를, 계측은 "어디서 얼마나 걸리고 얼마 들었고 왜 끝났나"를 본다. 숫자 판정은 언제나 채점기가 하고, trace는 원인을 찾는 데 쓴다.

### 8.1 trace가 어디서 오나 — 서비스 하나, environment로 구분 (T3-32)

모든 trace의 서비스는 `kosis-agent` 하나이고 계측 코드도 하나(`kosis_agent.observability.tracing.LiveSpans`)다.
실사용과 평가는 resource 속성으로 나눈다(`kosis_agent.observability.tracing.resource`). Jaeger UI Tags 검색은 이 process 태그도 찾는다.

| 누가 만드나 | `deployment.environment.name` | `kosis.eval.layer` | trace 단위 | 판정 |
|---|---|---|---|---|
| API 서버 (`backend/app/telemetry.py`) | `service` | — | 요청 1건 (응답의 `trace_id`) | — |
| `--layer agent` | `eval` | `agent` | 턴 1개 (`invoke_agent`) | 채점 뒤 `eval.scenario` span이 턴 루트들에 링크 |
| `--layer l3`·`e2e` | `eval` | `l3`·`e2e` | 문항 1개 = 에이전트 한 턴 (`invoke_agent`) | 채점 뒤 `eval.question` span이 링크 (`kosis.verdict`, `kosis.failure.*`) |
| `--layer l1` | `eval` | `l1` | 문항 1개 (`execute_tool search_tables`, 그래프 프리셋이면 노드 span이 그 아래) | 채점 뒤 `eval.question` (놓친 노드) |

- 실사용 요청만 보기: Tags `deployment.environment.name=service`. 평가가 서비스 메트릭(비용·거절률)을 오염시키지 않게 대시보드도 이 필터를 기본으로 건다.
- provider는 서비스·평가가 따로 만든다(전역 provider 안 씀). 그래서 같은 프로세스에서 섞이지 않고, 같은 질문이면 trace 모양이 같다.
- `l0`·`l2`·`ablation`은 trace를 남기지 않는다.

### 8.2 에이전트 trace 한 턴의 모양

```
invoke_agent                       gen_ai.conversation.id (= thread_id), kosis.turn.index
│                                  kosis.outcome, kosis.no_answer_reason, error.type   ← 이 턴이 어떻게 끝났나
├─ context / search / statistics / chart / finalize      ← 그래프 노드 (이름 = LangGraph 노드 이름)
│  └─ chat <model>                 해석기·멀티쿼리 LLM (LangChain). gen_ai.response.finish_reasons
├─ execute_tool search_tables      gen_ai.tool.call.arguments / .result (SearchOutput JSON)
│  └─ chat <stage2 모델>           실전 2 rerank2 (CLOVA reranker / HCX 직접 호출). llm.attempts = stage2_calls
└─ execute_tool summarize_stat     gen_ai.tool.call.result (StatAnswer JSON)
   ├─ analyze.llm_plan …           실전 1 LLM 단계 (gen_ai.operation.name=chat, 이름은 실전 1 단계 이름)
   ├─ compute                      LLM 아님 (pandas)
   └─ compose / verify …
```

- 노드 span은 툴 span의 부모가 아니라 형제처럼 보일 수 있다. 부모는 "span을 가진 가장 가까운 상위 실행"이라서, 아키텍처가 중간에 무엇을 끼워도 트리가 깨지지 않게 했다.
- 실전 1 단계와 rerank2 span은 툴이 끝난 뒤 툴 결과의 기록으로 만든다(실전 1·2는 urllib로 LLM을 불러 LangChain 콜백이 오지 않음). 길이는 실제 값이다. 시작 시각은 실전 1은 실제 오프셋이고, rerank2는 "툴 끝 − 뒤 노드(gate) 시간"에서 거꾸로 잡은 근사다(ms 단위).
- span 상태 `error`가 곧 턴 실패는 아니다(§2와 같음). 실전 1 형식 재시도, rerank2 실패 후 1차 순서로 답함(`degraded`)은 정상 흐름이다. 턴의 결과는 루트의 `kosis.outcome`으로 본다.

### 8.3 LLM 호출·토큰을 세는 규칙

LLM 호출 = `gen_ai.operation.name == "chat"`인 span 하나. agent 리포트의 `llm_calls`·토큰·비용은 이 span들의 합이다.

| 경로 | 호출 1회 | 토큰 출처 | 모를 때 |
|---|---|---|---|
| 해석기·멀티쿼리 | LangChain chat model 호출 1회 | 응답 `usage_metadata` (없으면 `llm_output.token_usage`) | 0 |
| 실전 1 HCX | 실전 1 LLM 단계 1개 (`analyze.llm_plan_retry` 같은 형식 재시도도 각각 1회) | 단계 속성 `gen_ai.usage.*` (HCX `promptTokens`·`completionTokens`) | 속성 없음 |
| 실전 2 rerank2 | rerank2 1회 | `SearchOutput.spans`의 `stage2_input_tokens`·`stage2_output_tokens` | 속성 없음 (CLOVA reranker는 usage를 안 줄 수 있음) |

- 한 호출 안의 추가 요청은 호출 수에 넣지 않고 `llm.attempts` 속성으로만 남긴다. 그래서 `llm_calls`는 "논리 호출 수"다.
  - 실전 1 `llm.attempts` = 그 단계가 보낸 모델 요청 수 전체: JSON 형식이 틀려 한 번 더 묻는 **형식 재시도**(`ask_structured`, 최대 2회)와 HTTP 재시도(429·5xx)를 합친 값.
  - rerank2 `llm.attempts` = `stage2_calls` (HTTP 시도 수).
  - `retried`(리포트 "재시도 비율") = `llm.attempts > 1`인 LLM 호출이 있었던 행. mock 실전 1은 형식 재시도가 잦아 이 비율이 높다.
- **모든 계층이 같은 규칙으로 센다** (T3-31에서 맞춤). 같은 질문이면 계층이 달라도 같은 호출은 같은 수로 잡힌다.
  - `l1`(stage 2 프리셋): LangChain 호출 + rerank2 1회. rerank2 HTTP 재시도는 `retried`로만 남는다.
  - `l3`·`e2e`·`agent`: 에이전트 그래프의 chat span에서 센다(T3-32). 해석기 + 실전 1 + rerank2, 위 표 그대로.
  - T3-32 이전 l3·e2e는 실전 1 클라이언트 래퍼가 모델 요청마다 셌다(형식 재시도 포함). 같은 l3 mock 90문항에서 요청 기준 138 → 단계 기준 66.
  - rerank2는 L1이 `evals.ops.stage2_usage`, 나머지가 `LiveSpans._stage2_span`으로 읽는다. 같은 기록(`stage2_*`)을 같은 규칙으로 읽는다.
  - 비교 기준이 끊기는 run: T3-31 이전의 `l1` stage 2 run(재시도가 있으면 `llm_calls`가 HTTP 시도 수였음), `agent` run(해석기만 셈), T3-32 이전의 모든 `l3`·`e2e` run(계층 정의·호출 기준·재시도 기준이 바뀜). 이 run들과 `ops_history.jsonl`에서 호출 수·재시도·비용을 비교하지 않는다.
- agent 리포트 첫머리의 **"토큰 대조 (span 합 / 메트릭 합)"** 줄이 `불일치`면 계측 버그다. 둘은 같은 콜백에서 나오므로 항상 같아야 한다.

### 8.4 턴이 어떻게 끝났나 — 종료 이유

에이전트는 결정적 워크플로라서 턴이 끝나는 이유를 LLM이 아니라 `finalize` 코드가 정한다. 그 결과를 루트 span `invoke_agent`에 남긴다.

| `kosis.outcome` | 뜻 | 함께 보는 속성 |
|---|---|---|
| `answered` | 실전 1이 숫자를 검증해 답함 | — |
| `refused` | 자료 없음으로 거절 (검색 gate 또는 실전 1) | `kosis.no_answer_reason` (예: `out_of_scope`, 기간·주기 불일치 사유) |
| `clarify` | 되물음 | `kosis.no_answer_reason` |
| `failed` | 검색은 찾았는데 넘길 표가 없음 등 코드 경로의 실패 | `kosis.no_answer_reason` |
| `error` | 그래프가 예외로 멈춤 (벡터 DB 연결, 키 없음 등) | `error.type` (예외 클래스 이름), 루트 span 상태 ERROR·exception 이벤트 |

- `kosis.no_answer_reason`은 API 응답 `ChatResult.no_answer_reason`과 같은 값이다. 사용자 제보의 `trace_id`로 찾은 trace와 화면 답의 사유가 일치한다.
- Jaeger 검색: Tags에 `kosis.outcome=error` 또는 `kosis.no_answer_reason=out_of_scope`를 넣으면 그런 턴만 모인다.
- `gen_ai.response.finish_reasons`는 턴이 아니라 **LLM 응답 한 번**의 종료 이유다(`stop`, `length`, `content_filter` …). LangChain 모델 응답에 있을 때만 chat span에 붙는다. `length`가 보이면 해석기 JSON이 잘린 것이다.
  실전 1·rerank2는 기록에 종료 이유가 없어서 지어내지 않고, 실패만 `error.type`으로 남긴다(실전 1은 `llm.status`: `timeout`·`format_error`·`api_error`·`input_too_long`, rerank2는 예외 이름).

### 8.5 메트릭 (OTel meter)

| 메트릭 | 종류 | 속성 | 읽는 법 |
|---|---|---|---|
| `gen_ai.client.token.usage` | Histogram, `{token}` | `gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.token.type`(input·output) | 모델별 토큰 합 = 비용. `evals/pricing.json` 단가를 곱한다 |
| `gen_ai.client.operation.duration` | Histogram, `s` | 위 + 실패면 `error.type` | 모델별 LLM 지연 p50·p95, 실패율 |
| `kosis.agent.turns` | Counter, `{turn}` | `kosis.outcome`, `kosis.no_answer_reason`, `error.type` | 거절·실패·예외 비율. 예외 종류별 건수 |

- 서비스: Jaeger는 메트릭을 받지 않는다. `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`(OTel Collector나 Prometheus OTLP 수신기)를 둘 때만 내보내고, 없으면 계측은 no-op이다.
- 평가: agent 실행은 메트릭을 메모리에만 모아 8.3의 토큰 대조에 쓴다.
- 토큰·비용의 기준 숫자는 계속 run 폴더의 `ops.json`과 `ops_history.jsonl`이다. 메트릭은 서비스 운영(실사용 트래픽) 쪽 집계용이다.

### 8.6 문제별로 어디를 보나

| 궁금한 것 | 먼저 볼 곳 | 다음 |
|---|---|---|
| 이 답이 왜 틀렸나 | 리포트 실패 턴 표의 오류(§6) | 그 턴 trace에서 `execute_tool`의 arguments·result |
| 왜 거절했나 | 루트 `kosis.no_answer_reason` | `search_tables` result의 `route`·`refusal_reason`, 또는 `summarize_stat`의 `refusal` |
| 왜 느린가 | 리포트 span 워터폴 / Jaeger 타임라인 | 가장 긴 노드 → 그 아래 `chat` span의 길이와 `llm.attempts`(재시도 대기) |
| 왜 비싼가 | `ops.json` 토큰·비용 | 모델별 `gen_ai.client.token.usage`, 턴당 chat span 수 |
| 예외가 얼마나 나나 | `kosis.agent.turns{kosis.outcome=error}` | `error.type`별 건수 → Jaeger에서 `kosis.outcome=error` trace의 exception 이벤트 |
| 해석기 출력이 잘리나 | chat span `gen_ai.response.finish_reasons=length` | `INTERPRETER_MODEL`·max tokens |
| rerank2가 실패했나 | `chat <stage2 모델>` span 상태·`error.type` | `ChatResult.degraded=true`면 1차 순서로 답한 것 |

- 테스트: `tests/test_llm_metrics.py`(세 LLM 경로의 chat span, 메트릭 합 = 원본 usage, 종료 이유·예외), `tests/test_api.py`(서비스 trace, 평가 provider와 분리).

## 9. Ragas 평가 파이프라인 — 골든셋 + Ragas + pytest (T3-19, KOS-116)

Ragas를 따로 돌리는 도구로 쓰지 않고 기존 계층의 하나로 둔다. 역할 분담:

| 구성 | 맡는 일 |
|---|---|
| 골든셋 | 질문, 정답 표 ID(L1 슬롯), 필요한 사실(기간·분류) → Ragas reference를 **코드가** 만든다 |
| e2e run | 질문 → 검색 → `summarize_stat` 한 턴의 답·순위·trace (`results.jsonl`) |
| Ragas (`--layer ragas`) | 심판이 답 문장·검색 상위 표를 채점. 그래프·검색은 다시 돌리지 않는다 |
| pytest | 샘플 생성·노드 귀속·게이트 로직 고정(`tests/test_evals_ragas.py`), CI 회귀 게이트(`tests/test_quality_gate.py`) |

```bash
uv sync --group embed --group ragas
python -m evals.run_eval --layer e2e --stage <ID>-e2e                       # 또는 기존 e2e run을 그대로 쓴다
python -m evals.run_eval --layer ragas --stage <ID>-ragas --from-run evals/reports/<e2e run>
python -m evals.quality                                                    # 기준선 대비 (CI와 같은 비교)
python -m evals.quality --promote evals/reports/<ragas run>                # 의도한 변화일 때만 기준선 갱신, PR에 같이 커밋
```

### 9.1 지표와 입력

| 지표 | user_input | response | retrieved_contexts | reference |
|---|---|---|---|---|
| Faithfulness | 질문 | 답 문장 | pandas가 계산한 사실 줄(`f1: 2025 · 시도별=경기도 = 76,309 명`) + 출처 + 사용한 표 텍스트 | — |
| Response Relevancy (`AnswerRelevancy`) | 질문 | 답 문장(출처 줄 제외) | — | — |
| Context Precision (`ContextPrecisionWithReference`) | 질문 | — | 검색 상위 k(기본 5)개 표 텍스트, 순위대로 | 골든 → 코드 |
| Context Recall | 질문 | — | 같음 | 같음 |
| ID Precision@k · ID Recall@k | — | — | 상위 k개 표 ID | 정답 표 ID (코드 계산, 심판 교차 확인용) |

- 표 텍스트 = 실전 2 `build_doc(record, "n2-L60")["bm25_text"]` 앞 2,000자. reference = 정답 표(대체 표 포함)의 조사명·표명 + 기간 + 필요한 분류 값(숫자·단위·합계 값 없음).
- 거절·되묻기·오류 행은 생성 지표에서, 정답 표가 없는 문항(not_answerable·clarify)은 검색 지표에서 뺀다. 대신 **답한 문항 수**와 PASS율을 게이트에 넣어
  "거절이 늘어 Faithfulness 평균이 오르는" 생존 편향을 막는다.
- 심판은 생성 모델(HCX)·파이프라인 LLM(gpt-4o-mini)과 다른 `RAGAS_JUDGE_MODEL`(기본 gpt-4.1-mini), 임베딩 `RAGAS_EMBED_MODEL`(기본 text-embedding-3-small).
  응답은 `data/cache/ragas`에 캐시되어 같은 입력 재채점은 호출 0. `--no-judge-cache`는 심판 흔들림을 잴 때만.
- Ragas 0.4.3은 `langchain_community.chat_models.vertexai`를 import해서 그대로는 실패한다. `ragas_metrics.load_ragas()`가 빈 모듈을 넣고 import한다(버전 고정).

### 9.2 노드 trace 연결

- 점수 < 0.5인 지표마다 담당 노드를 정한다: 검색 지표 → `search/<정답 표를 밀어낸 검색 노드>`(`trace.lost_at`), Faithfulness →
  `statistics/<답 문장을 쓴 실전 1 span>`(fallback_chatbot · compose_llm · template · answer_final), Response Relevancy → 정답 아닌 표로 답했으면 검색 노드, 아니면 문장 span.
- report.md·html: 지표 × 노드 저점 수, 가장 낮은 문항 10(trace_id), 원 e2e와 같은 문항별 워터폴.
- `OTEL_EXPORTER_OTLP_ENDPOINT`가 있으면 문항마다 `eval.ragas` span이 원래 턴 trace에 link되고 `kosis.ragas.<지표>`·`kosis.ragas.node.<지표>` 속성이 붙는다
  (Jaeger 태그 검색 예: `kosis.ragas.node.faithfulness=statistics/fallback_chatbot`).

### 9.3 CI 회귀 게이트

- ragas run마다 `evals/reports/quality_history.jsonl`에 집계 한 줄(커밋). `evals/quality_baseline.json`(커밋)과 **비교 키**(골든·split·tables.jsonl hash,
  e2e 그래프+모델, 심판·임베딩, ragas 버전, 컨텍스트 포맷, k)가 같은 기록 중 run_id가 가장 큰 것을 지표별로 비교한다. 다른 그래프·모델 실험은 키가 달라 게이트에 안 걸린다.
- 허용폭: 심판 지표는 기준선 파일의 `tolerance`(심판 흔들림 실측) 또는 0.03, 코드 비율 지표는 2문항(2/n), 답한 수는 2.
- CI에는 데이터·키가 없어 평가를 **실행하지 못한다**. PR이 `src/`·`backend/`·`vendor/`를 바꾸고 기록을 안 올리면 경고만 뜬다.

### 9.4 대체 정답 표 (T3-41)

수치 골든 문항은 대표 표 하나로 만들었지만 같은 숫자를 가진 표가 여럿이다(예: 2025년 경기도 출생아 76,309명은 `DT_1B8000H`에도 `DT_1B81A12`에도 있음).

```bash
python -m evals.builders.alternates     # data/values 전수 스캔(약 1분) → evals/datasets/calculation_alternates.jsonl, 자기검증 실패 시 exit 1
```

- 규칙(`evaluators/alternates.py`): 골든의 **모든** 셀 사실(required + aux)이 한 표 안에서 같은 기간·같은 값·같은 시도(전국 문항은 지역 없는 셀)·
  골든 분류 라벨 또는 합계·그 밖의 분류는 합계뿐·단위 호환이고, 골든에서 한 항목인 사실은 대체 표에서도 한 항목. LLM 없음, 숫자는 골든 그대로.
- 라벨 상태: `auto`(값이 모두 정수 ≥ 100) / `needs_review`(작은 값·비율, 우연 일치 위험) / 사람이 바꾸는 `accepted`·`rejected`(재생성해도 유지).
  정답 표 집합(`locate()`, ragas ID 지표)은 auto·accepted만 쓴다. `self_check` 줄 = 대표 표를 규칙이 다시 찾았는지.
- 숫자 채점: e2e 답이 대표 표가 아닌 표에서 왔고 그 표가 규칙을 만족하면, 골든 사실의 source_ref를 그 표 셀(답이 인용한 셀 우선)로 옮겨 같은 채점기로 채점한다
  (`graded_table` 열). L3(대표 표 오라클)는 경로가 바뀌지 않는다.
- agent 계층 (T3-42): 같은 규칙. 숫자(calc_qid·calc_qids 하위 답)는 답의 **출처 표**(`source`, 없으면 호출의 `table_key`)가 대체 표면 그 셀로 채점하고
  (`calc.graded_table`·`via_alternate`), 상태·툴 인자 판정도 그 턴의 대체 표를 정답에 더한다(`value_alternates` 열). 같은 대화에서 인정된 대체 표는
  같은 표를 기대하는 다음 턴(calc_qid 없는 "그 표로 그려줘")에도 이어진다. T3-27 `same_values`(값만 같으면 통과)는 없앴다.
  agent 표 키 판정은 라벨 status가 아니라 규칙만 본다(e2e 숫자 채점과 같고, e2e 정답 표 판정은 auto·accepted 라벨).
- 라벨 파일 hash는 l3·e2e manifest의 `alternates`와 ragas 게이트 비교 키에 들어간다. 라벨이 바뀌면 기준선을 다시 promote한다.

## 10. 평가 파이프라인 구조 — 오프라인 / 온라인 / 리포트 (T3-43, 고정)

```
                         Golden Dataset (질문 · 기준 정답 · 정답 통계표 ID · 기대 계산 결과)
                                   ↓
   Offline Evaluation (개발 / PR / 배포 전)          Online Evaluation (실제 사용자 요청)
   고정 Golden Set · Ragas 평가 · Baseline 비교       Trace 수집 · 표본 추출 평가
   · Regression Gate                                   · 품질 저하 감지 · 실패 케이스 수집
                                   ↓
                 Evaluation Report (지표별 점수 · 실패 유형 · 이전 버전 대비 변화)
```

```bash
bash scripts/dev.sh suite --stage <ID>                    # 오프라인 (exit 1 run 실패 · 2 회귀 게이트 하락)
bash scripts/dev.sh online --stage <ID>-online [--lookback 24h --limit 500 --sample 40]   # 온라인 (exit 3 품질 저하)
bash scripts/dev.sh pipeline --stage <ID> [suite 인자]    # 둘 다, 오프라인 먼저 (exit = 처음 0이 아닌 값)
```

dev.sh는 Docker·PostgreSQL·Jaeger를 띄우고 `uv run --group embed --group ragas`로 실행한다(그룹을 하나만 주면 다른 그룹이 sync로 지워진다).

| 단계 | 오프라인 (`evals.suite`) | 온라인 (`--layer online`) |
|---|---|---|
| 입력 | 고정 골든셋(calculation·retrieval golden, 대체 표 라벨) | 서비스 trace(Jaeger, `deployment.environment.name=service`). 재현: `--from-run <agent run>` |
| 평가 | L0~L4·agent → e2e 답을 `ragas`가 심판(e2e가 mock·skip이면 ragas skip) | 전 턴 코드 검사(최종 답 숫자 근거·출처, agent `answer.py`) + 결과 분포, 답한 턴 표본에 Ragas Faithfulness·Relevancy |
| 비교 | 같은 조건 기준 run(`--baseline`) + **회귀 게이트**(이번 ragas summary vs `evals/quality_baseline.json`, CI와 같은 비교, 기준선 없음·키 다름은 skipped) | 같은 키 이전 온라인 기록(3개 이상)의 중앙값, 허용폭 = max(심판 흔들림 0.03, 2×표본 표준오차) |
| 기록 | `quality_history.jsonl`(집계, 커밋) | `evals/datasets/online_history.jsonl`(로컬, 커밋 안 함: 트래픽이 매일 바뀜) |
| 실패 | run 리포트의 실패 단계(`실패: retrieval` 등) | `evals/datasets/online_failures.jsonl`(로컬 전용, trace 단위 중복 제거, 30일 보존): 오류·근거 없는 숫자·출처 누락·Faithfulness 저점, 거절은 `refusal_review:<사유>`로 분리 → 사람이 검토해 골든셋 후보로 |
| 리포트 | suite.html: 지표별 점수 · 실패 유형 · 기준 대비 차이 · 회귀 게이트 표 | online report.html: 지표 · 표준오차 · 직전 기록 중앙값 · 허용폭 · 저하 · 턴 결과 · 거절 사유 · 실패 케이스 |

- 서비스 trace의 턴 루트 `invoke_agent`에 `kosis.turn.question`·`kosis.turn.answer`·`kosis.turn.table_key`·`kosis.turn.resolved_question`이 있다(T3-43).
  이 속성이 없는 trace(그 전 기록)는 온라인 평가가 읽지 못하고 "질문 없는 trace"로 센다.
- Jaeger(all-in-one)는 메모리 저장이라 재시작하면 trace가 사라진다. 운영에서는 저장소를 붙이거나 매일 온라인 평가를 돌려 기록을 남긴다.
- 온라인 점수 행은 `eval.online` span으로 원래 서비스 trace에 link된다(`kosis.online.faithfulness` 등, OTLP endpoint가 있을 때).


## 11. 전→후 비교 실험 — baseline = main, 짝 bootstrap, 채택 규칙 (T3-63)

아키텍처·프롬프트·모델을 바꿨을 때 채택 여부를 정하는 절차다. 규칙의 근거는 `plans/T3-63.md`에 있다.

**1. 같은 조건으로 두 run을 만든다**
- baseline은 **실험을 시작한 시점의 main**(SHA 고정)이고, after는 실험 브랜치다.
- 같은 골든·같은 세션·같은 벡터 색인으로 **순서대로** 돌린다. Colab에서는 `colab/t3_03_e2e_eval_gpu.ipynb`가 이 절차를 그대로 한다.
- 두 트리는 각자 자기 실전 1·2 pin으로 돈다.
- 로컬에서 돌릴 때는 main worktree와 실험 브랜치에서 같은 `--scenarios`로 `--layer e2e`를 한 번씩 돌린다.
- **Colab 결과를 로컬로**: 노트북은 run·비교가 끝날 때마다 Drive `MyDrive/kosis/reports/`에 복사한다. PC에서 `python scripts/pull_reports.py`를 실행하면 로컬 `evals/reports`에 로컬 run과 같은 형식·자리로 들어온다. 새 폴더만 복사하고 `ops_history.jsonl`에는 그 run의 줄만 `run_id` 기준 한 번 더한다. Drive 데스크톱 앱이 없으면 노트북 마지막 셀의 zip으로 `python scripts/pull_reports.py <zip>`을 쓴다.
- A/A(baseline 한 번 더)는 세션 점검용이다. 같은 코드인데 구간이 0을 벗어나거나 지연 p95가 ±20% 넘게 흔들리면 그 세션의 결과는 쓰지 않는다.

**단계만 따로 재기**: e2e 전체 대신 필요한 단계만 같은 방식(base → after → A/A, 판정)으로 잴 수 있다. 노트북은 설정 셀의 `LAYERS`로 고른다. 로컬에서는 아래 `--layer`로 두 트리에서 한 번씩 돌린 뒤 `evals.compare`에 넘긴다.

| 단계 | 명령 (`--layer`) | 문항 통과 (`evals.compare`) | 정답 |
|---|---|---|---|
| 질의 이해 (슬롯) | `l2` | 슬롯 전부 맞음 (`all_fields`) | `query_golden.jsonl` |
| 질의 이해 (대화 맥락) | `agent --agent backend.app.main:build_agent --split all --scenarios evals/datasets/agent_scenarios_multiturn_dev.jsonl evals/datasets/agent_scenarios_multiquery_dev.jsonl` | 턴 통과 | 멀티턴·멀티쿼리 시나리오 |
| 검색 | `l1 --retriever graph:staged-gpt-condyears` (서비스 검색 설정) | 정답 표 1위, 답할 수 없으면 거절 | `retrieval_golden.jsonl` |
| 계산·답변 생성 | `l3 --calc-golden <e2e 계산 골든 중 table_keys 없는 항목>` | 계산 판정 PASS | 표 하나짜리 54문항 |
| 전체 | `e2e` | 턴 통과 | e2e 골든 120 |

- 여러 표 문항은 검색이 표를 가져와야 해서 `l3`에서 빼고 `e2e`에서만 잰다. Ragas(RAG 판정)는 이 절차 밖이다.

**2. 판정한다**
```bash
python -m evals.compare --base evals/reports/<baseline run> --after evals/reports/<after run> [--aa evals/reports/<A/A run>]
# -> evals/reports/compare-<base>-vs-<after>/compare.md · compare.json (커밋하지 않음). 종료 코드 2 = 비교 거부
```
- 문항(sid, turn)마다 baseline·after의 통과(L4 턴 통과)를 짝짓는다.
- 통과율 차이와 문항별 지연 차이에 **짝 bootstrap 95% 구간**을 낸다. 실전 2 `lab/lab_common.py` `boot()`, T2-40과 같은 방법이고 10,000회·seed 0이다.
- 골든 해시·split·층·문항 집합이 다르면 비교를 거부한다.

**3. 채택 규칙** (결과를 보기 전에 정한 것, 바꾸려면 plan으로)

| 순서 | 조건 | 판정 |
|---|---|---|
| ① 가드레일 | 새로 생긴 위반(근거 없는 숫자·출처 누락·run_failure)이 `quality.py` `COUNT_TOLERANCE`(2)를 넘음 | 기각 |
| ② 정확도 | 통과율 차이 구간 하한 > 0 / 상한 < 0 / 0 포함 | 개선 / 악화(기각) / 차이 미확인 |
| ③ 원천 쏠림 | 개선인데 원천 하나에서 3패 이상·0승 | 보류 |
| ④ 비용·지연 | 개선이면 채택(지연 p95·요청당 비용 +20% 초과는 결과에 기록). 차이 미확인이면 지연·비용이 둘 다 10% 이상 줄 때만 채택, 아니면 기각 | |

- 주 지표는 문항 통과 하나다. 수치·툴 정확도는 보고만 하고 판정에 쓰지 않는다.
- e2e 골든 120문항 1회로 가릴 수 있는 차이는 바뀐 문항 수에 따라 순이득 약 5~12%p 이상이다. 그보다 작으면 "차이 미확인"이다.
- "차이 미확인"이나 "보류"가 나온 변경을 결과가 좋아질 때까지 다시 돌리지 않는다. 다시 잴 거면 새 실험(반복 실행·골든 확장)으로 등록하고 그 결과 하나로 판정한다.
- 판정과 `compare.md` 경로를 그 태스크 plan의 `## Result`와 TASK.md에 남긴다.
