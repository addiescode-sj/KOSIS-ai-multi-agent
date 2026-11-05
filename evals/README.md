# evals — 계층별 평가와 실패 단계 추적

실전 2부터 쓰는 평가 모듈이다. 계층마다 골든셋과 채점기를 따로 두고, 채점은 LLM 없이 코드가 한다.
여기서는 **실패한 문항이 어느 단계에서 왜 실패했는지**(T2-15, `trace.py`)를 읽고 고치는 방법을 정리한다.
설계 배경과 옵션 비교는 [plans/T2-15.md](../plans/T2-15.md)에 있다.

## 1. 실행

```bash
export PYTHONPATH=.:src:vendor/phase1:vendor/phase1/src   # 없으면 ModuleNotFoundError: kosis_ai

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
| `report.html` | 같은 내용을 차트로 보여준다. 오류 유형 설명, 실패 문항별 span 경로, Jaeger 링크 포함 |
| `results.jsonl` | 문항별 행. `errors`, `spans`, `failure`, `trace_id`가 들어 있다 |
| `manifest.json` | 골든·데이터 해시, git, 요약(`summary.failures` = 단계별 건수) |

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

## 6. 규칙을 바꿀 때

- 판정 규칙: `trace.py`의 `blame()`. 단계를 추가하면 `STAGES`에도 넣는다. 리포트 표기와 판정 순서 표시가 여기서 나온다.
- 오류 유형: `evaluators/calculation.py`의 `grade()`·`fact_errors()`. 새 오류를 만들면 `ERROR_INFO`와 이 문서 4절에 설명을 추가한다.
- 테스트: `tests/test_evals_trace.py`에 규칙마다 합성 행이 하나씩 있다. 규칙을 바꾸면 해당 케이스를 고치거나 추가한다.
- `unknown`이 생기면 그 문항의 `errors`와 `spans`(results.jsonl)를 보고, 맞는 규칙을 넣거나 기존 규칙의 조건을 넓힌다.
