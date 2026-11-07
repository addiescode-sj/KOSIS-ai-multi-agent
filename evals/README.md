# evals — 계층별 평가와 실패 단계 추적

실전 2부터 쓰는 평가 모듈이다. 계층마다 골든셋과 채점기를 따로 두고, 채점은 LLM 없이 코드가 한다.
여기서는 **실패한 문항이 어느 단계에서 왜 실패했는지**(T2-15, `trace.py`)를 읽고 고치는 방법을 정리한다.
설계 배경과 옵션 비교는 [plans/T2-15.md](../plans/T2-15.md)에 있다.

## 0. 테스트 방법 — 준비부터 종단 간까지 (T3-21)

처음 평가를 돌리는 사람은 이 절만 따라 하면 된다. 아래 절(1~7)은 계층별 세부 규칙이다.

### 0.1 계층 한눈에

| run (스위트 이름) | 계층 | 무엇을 재나 | 필요한 것 | 비용 |
|---|---|---|---|---|
| `l0` | L0 | 메타데이터 채움률·미매핑 | 데이터 | 0 |
| `l0-load` | L0 | + Chroma 컬렉션 적재 일치 | 데이터·Chroma | 0 |
| `l1-bm25-record` · `l1-v2-bm25-record` | L1 | BM25 검색 (골든 v1 · v2) | 데이터 | 0 |
| `l1-hybrid` | L1 | 임베딩 + BM25 + 조건 필터 | 데이터·Chroma | 0 |
| `l1-staged` | L1 | + 1차 리랭커 | 데이터·Chroma·`RERANK_CKPT` | 0 (CPU 시간) |
| `l1-graph` | L1 | 실제 LangGraph 검색 그래프 | 데이터·Chroma·`RERANK_CKPT` | 0 |
| `ablation` | L1 | 슬롯 필터 on/off별 검색 (dev) | 데이터·Chroma | 0 |
| `l2-v1` · `l2-v2` | L2 | 질의 파서(규칙) | 데이터 | 0 |
| `l3` | L3 | 정답 표를 준 채로 `summarize_stat` 실호출 → Parquet 대조 | 데이터·HCX 키 | **HCX 90문항** (재시도 포함 약 135회로 추정) |
| `e2e` | L4 | 검색 그래프 1위 표 → `summarize_stat` | 데이터·Chroma·`RERANK_CKPT`·HCX 키 | **HCX 90문항 + 2차 리랭커** |
| `agent` | 종합 | 멀티턴 시나리오 → 에이전트 → 툴 (§6) | `--agent`·시나리오 파일 | 에이전트가 부르는 LLM |

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
- Chroma(검색 계층): Docker Desktop을 켜고 **실전 2 레포의** chroma를 띄운다(임베딩이 들어 있는 볼륨은 실전 2 쪽).
  ```bash
  (cd ~/work/KOSIS-ai-RAG && docker compose up -d chroma)    # localhost:8001
  ```
  이 레포 compose의 chroma는 포트가 같고 볼륨이 비어 있어 동시에 띄우지 않는다.
- 셸 값: 평가 코드는 `.env`를 읽지 않는다(실전 2 T2-41 이후 모듈도 안 읽음). 셸에 올린다.
  ```bash
  set -a; source .env; set +a        # CHROMA_COLLECTION, EMBED_CKPT, RERANK_CKPT, TEXT_FORMAT, HCX_API_KEY ...
  ```
  이름과 기본값은 위 "검색 설정은 셸 환경변수로" 절. 값이 없으면 기본값으로 **조용히** 돈다 → 스위트 사전 점검으로 먼저 확인한다.
- Windows: `set PYTHONUTF8=1`, `export` 대신 `set`, `source` 대신 값을 직접 `set`.

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
| `--llm hcx\|mock` | L3·e2e 모델. mock이면 e2e 검색은 2차 LLM 없는 `graph:staged`, 실측은 `graph:staged-hcx` |
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
uv run python -m evals.run_eval --layer l0 --retriever chroma --stage $S-l0-load
uv run python -m evals.run_eval --layer l1 --retriever bm25-record --stage $S-l1-bm25-record          # oracle | bm25-catalog | bm25-record | chroma | 프리셋 | graph:<프리셋>
uv run python -m evals.run_eval --layer l1 --retriever graph:staged --stage $S-l1-graph
uv run python -m evals.run_eval --layer ablation --retriever chroma --split dev --stage $S-ablation
uv run python -m evals.run_eval --layer l2 --split all --stage $S-l2-v1
uv run python -m evals.run_eval --layer l3 --llm mock --stage $S-l3-mock                              # 경로 확인
uv run python -m evals.run_eval --layer l3 --interval 5 --stage $S-l3                                 # HCX
uv run python -m evals.run_eval --layer l3 --from-run <과거 L3 run 폴더> --stage $S-l3-regrade        # 저장된 답 재채점, 호출 0
uv run python -m evals.run_eval --layer e2e --retriever graph:staged-hcx --interval 5 --stage $S-e2e
uv run python -m evals.run_eval --layer agent --oracle --scenarios <시나리오.jsonl> --split all --stage $S-agent-oracle   # 채점기 자기검증
```

L1 프리셋(`retrieval.STAGED`): `hybrid` · `hybrid+cond` · `staged` · `clova-only` · `staged-clova` · `hybrid+cond-hcx` · `staged-hcx` · `hybrid+cond-gpt`. `graph:`를 앞에 붙이면 같은 설정을 LangGraph 검색 그래프로 돌린다.
골든 v2는 `--golden evals/datasets/retrieval_golden_v2.jsonl --pool evals/datasets/retrieval_pool_v2.csv` / `--query-golden evals/datasets/query_golden_v2.jsonl`.

### 0.6 결과 읽기

- run 폴더: `report.html`(맨 위 읽는 법 → 결과 → 실패 단계·trace → 운영 지표·추이 → 행별 결과), `report.md`, `results.jsonl`(질문 원문 포함, git에 안 올림), `manifest.json`(조건·해시), `ops.json`.
- 운영 지표: 지연 p50·p95, 입력·출력 토큰, 비용(`evals/pricing.json` 단가), 실패율. 실행 간 추이는 `ops_history.jsonl`(집계만, 커밋함).
- trace: l1(그래프)·l3·e2e는 문항별 span 경로, agent는 턴별 span 워터폴. `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317`과 `docker compose --profile obs up -d jaeger`면 Jaeger 링크가 생긴다(아래 "Jaeger로 보기").
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
| 점검 `Chroma localhost:8001 응답 없음` | Docker 꺼짐 / 실전 2 chroma 안 띄움 → 0.2 |
| `ValueError: no collection` · 점검 `CHROMA_COLLECTION 미설정` | 셸에 값 없음 → `set -a; source .env; set +a` |
| `LLMKeyMissing` · 점검 `HCX_API_KEY 미설정` | 셸에 키 없음, 또는 `--llm mock`으로 경로만 확인 |
| 프로세스가 `exit 137`로 죽음 | 큰 Parquet 표(10MB 넘는 7개) 셀을 읽다 메모리 부족 → 다른 프로그램을 닫거나 그 문항 제외(별도 이슈) |
| `uv sync` 뒤 torch가 없어짐 | 그룹 하나만 sync함 → `uv sync --group embed --group ragas` |
| L3 mock 점수가 높음 | 정상: 코드 템플릿 답. 점수로 쓰지 않는다 |
| 포트 8001 충돌 | 이 레포 compose의 chroma와 실전 2 chroma를 같이 띄움 → 하나만 |

## 1. 실행

```bash
export PYTHONPATH=.:src:vendor/phase2/src:vendor/phase1:vendor/phase1/src   # 없으면 ModuleNotFoundError: kosis_ai

uv run python -m evals.run_eval --layer l3 --stage <이름> --from-run evals/reports/T1-27-run   # 과거 run 재채점, 모델 호출 0
uv run python -m evals.run_eval --layer l3 --stage <이름> --interval 5                         # summarize_stat 실시간 호출
uv run python -m evals.run_eval --layer e2e --retriever bm25-record --stage <이름> --interval 5 # 검색 1위 표 → summarize_stat
```

### L3·L4 골든 파일 (T2-31)

| 파일 | 만드는 명령 | 내용 |
|---|---|---|
| `calculation_golden.jsonl` | `python -m evals.builders.phase1` | 실전 1 골든 90문항, 10표. clarify·default·causal 같은 어려운 유형 |
| `calculation_golden_diverse.jsonl` | `python -m evals.builders.diverse --rewrites evals/datasets/calculation_diverse_rewrites.jsonl` | 14개 조사, 72표, 264문항 (`D-` qid). value·compare·trend·거절 |

기본값은 실전 1 파일만 읽는다. 둘 다 쓰려면 `--calc-golden`에 두 파일을 함께 넘긴다.

```bash
uv run python -m evals.run_eval --layer l3 --stage <이름> --interval 5 \
  --calc-golden evals/datasets/calculation_golden.jsonl evals/datasets/calculation_golden_diverse.jsonl
```

diverse 문항의 숫자는 빌더가 Parquet 셀에서 직접 뽑는다. 질문 문장은 사람(또는 Claude)이 다시 쓰는데,
`check_question`이 연도·라벨·항목명이 남아 있고 다른 숫자가 없는지 확인한 뒤에만 반영한다. 통과하지 못하면 템플릿 문장을 그대로 쓴다.
`calculation_diverse_review_sample.csv`(5문항마다 1개)의 `ok`·`note` 열은 사람이 검수할 때 채운다.

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

### 검색 설정은 셸 환경변수로 (실전 2 T2-41 이후)

실전 2 모듈은 `.env`도 환경도 읽지 않는다. `evals/config.py`가 셸 변수로 `IndexSpec`·`SearchOptions`를 만든다(이름·기본값은 실전 2 `search_config_from_shell`과 같음, 데이터 경로는 이 레포 `data/`).
**`.env`는 읽지 않는다.** 값이 없으면 기본값(`localhost:8001`, 컬렉션 없음, stage 2 끔)으로 조용히 돈다. 옛 결과와 비교하려면 같은 값을 셸에 둔다.

`CHROMA_HOST` `CHROMA_PORT` `CHROMA_COLLECTION` `EMBED_CKPT` `RERANK_CKPT`(staged*) `TEXT_FORMAT` `HQ_BUILD`(비우면 끔) · HCX 프리셋·L3 HCX는 `HCX_API_KEY`, gpt 프리셋은 `OPENAI_API_KEY`.
L2·ablation(`--slot-source pred`)은 `data/sigungu.json`을 `parse_query(sigungu=…)`에 넘긴다(넘기지 않으면 시군구 질문의 `regions`만 조용히 떨어진다).

### Jaeger로 보기 (선택)

```bash
docker compose --profile obs up -d jaeger
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 uv run python -m evals.run_eval --layer l3 --stage <이름> --from-run evals/reports/T1-27-run
```

http://localhost:16686 에서 서비스 `kosis-evals`를 고른다. 문항 1개가 trace 1개이고, 실패한 문항은 error로 표시된다.
- `OTEL_EXPORTER_OTLP_ENDPOINT`가 없으면 아무것도 보내지 않는다. 평가는 그대로 돈다.
- Jaeger는 메모리에 저장하므로 컨테이너를 재시작하면 trace가 사라진다. 실패 단계는 run 폴더의 리포트에 남아 있다.
- trace는 실행이 끝난 뒤 기록된 span을 재생해 만든다. 그래서 각 span의 길이와 순서는 실제 값이지만, 시작 시각은 실제가 아니다.

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

- 시나리오: `evals/datasets/agent_scenarios.jsonl` (T3-05, `--scenarios`로 바꿈). 계약은 `evals/schemas.py`의 `AgentScenario`.
  턴마다 `expect`에 부를 툴(순서대로, 채점할 인자 필드만), 턴이 끝날 표(`table_key`), 거절 여부(`refuse`), L3 문항(`calc_qid`)을 적는다.
  한 턴에 여러 요청("찾아서, 계산해서, 그려줘")은 `tools`에 여러 개를 적고, "이거"·"그 표"는 다음 턴의 `table_key`로 채점된다.
- 실행 기록: 턴마다 그래프를 `thread_id = <sid>#<run>`으로 `invoke`하고, 실행 중 열린 OTel span(`kosis_agent.tracing.LiveSpans`)에서 툴 이름·인자·결과·지연·LLM 호출·토큰을 읽는다.
  span 이름·속성은 OTel GenAI 규약 그대로다: `invoke_agent`(`gen_ai.conversation.id`, `kosis.turn.index`) · `execute_tool <name>`(`gen_ai.tool.name`, `gen_ai.tool.call.arguments`·`.result`) · `chat <model>`(`gen_ai.request.model`, `gen_ai.usage.input_tokens`·`output_tokens`).
  `OTEL_EXPORTER_OTLP_ENDPOINT`가 있으면 같은 span이 Jaeger로 가고, 채점 뒤 `eval.scenario` span이 턴 root span들에 링크로 판정(`kosis.verdict`, `kosis.failure.turn`)을 붙인다.

| 지표 | 정의 | 채점 |
|---|---|---|
| 툴 선택 정확도 | 부른 툴 이름 순서 == 정답 순서인 턴의 비율 | 결정적 |
| 툴 순서 일치 | 정답 툴이 그 순서대로 나타난 턴의 비율 (불필요 호출 허용) | 결정적 |
| 빠진 호출 · 불필요 호출 | 툴별 개수 (정답과 실제 호출의 다중집합 차) | 결정적 |
| 파라미터 정확도 | 정답 인자 필드(`table_key`·`period_start`·`period_end`·`regions`·`dimensions`·`chart_type`)별 일치 비율. 같은 툴의 k번째 호출과 비교, L2 규칙(`query.field_correct`: 목록은 집합, 나머지는 문자열 일치) | 결정적 |
| 상태(표 키) 정확도 | 턴이 끝난 표 == 정답 `table_key`. 끝난 표 = 마지막 `summarize_stat`/`make_chart`의 `table_key`, 없으면 마지막 검색 1위 | 결정적 |
| 최종 수치 정확도 | `calc_qid` 턴의 `summarize_stat` 답을 L3 채점기(`calculation.grade`, Parquet 셀 대조) 그대로 채점해 PASS인 비율 | 결정적 |
| 근거 없는 숫자 | 최종 답 문장의 숫자 중 이 대화의 툴 결과·질문에 없고 사실 값의 반올림도 아닌 것이 있는 턴 수 (0이 목표) | 결정적 |
| 턴 통과율 | 오류가 하나도 없는 턴의 비율 | 결정적 |
| 대화 성공률 | 모든 턴이 통과한 대화(실행)의 비율. 리포트에 처음 실패한 턴 분포 | 결정적 |
| pass^k | `--repeat k`번 모두 성공한 시나리오의 비율 | 결정적 |
| 요청당 비용·지연 | 턴 = 요청 1건. `ops.json`의 p50·p95 지연, LLM 호출 수, 토큰, 비용 (`ops_history.jsonl`에 누적) | 결정적 |

턴 오류: `run_failure` 실행 실패 · `missing_call` · `extra_call` · `wrong_order` · `wrong_args` · `lost_state`(지시어·조건 이월 실패 포함) · `missed_refusal` · `false_refusal`
(거절 = 검색 `route` `refuse` 또는 `summarize_stat` `outcome` `refused`, 또는 툴을 하나도 안 부름) · `wrong_numbers` · `unsupported_final_number`. 예외가 난 실행은 거기서 멈추고 남은 턴은 `run_failure`다.

- 지금 한계: 실전 1 `summarize_stat` 안의 LLM 호출은 LangChain 콜백 밖이라 토큰에 잡히지 않는다(툴 span 지연에는 포함). `summarize_stat` 툴(T3-01)이 실전 1 span·토큰을 결과에 담으면 그때 툴 span 아래 재생을 붙인다(KOS-115).
- 테스트: `tests/test_evals_agent.py` (oracle 1.000, 빠진·불필요 호출, 인자 불일치, 멀티턴 중간 실패와 pass^k, 거절, L3 연결·근거 없는 숫자, 실제 LangGraph 그래프 → span → 기록 왕복).

## 7. 규칙을 바꿀 때

- 판정 규칙: `trace.py`의 `blame()`. 단계를 추가하면 `STAGES`에도 넣는다. 리포트 표기와 판정 순서 표시가 여기서 나온다.
- 오류 유형: `evaluators/calculation.py`의 `grade()`·`fact_errors()`. 새 오류를 만들면 `ERROR_INFO`와 이 문서 4절에 설명을 추가한다.
- 테스트: `tests/test_evals_trace.py`에 규칙마다 합성 행이 하나씩 있다. 규칙을 바꾸면 해당 케이스를 고치거나 추가한다.
- `unknown`이 생기면 그 문항의 `errors`와 `spans`(results.jsonl)를 보고, 맞는 규칙을 넣거나 기존 규칙의 조건을 넓힌다.
