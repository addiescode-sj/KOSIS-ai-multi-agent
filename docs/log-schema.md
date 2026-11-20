# 사용자 로그 스키마 (T3-36, Linear KOS-169)

질문 1건이 한 행인 `request_logs` 테이블과, 그 주변(화면 행동, 피드백, 수신 탈락)이 지켜야 할 규칙을 정한다.
모델은 `src/kosis_store/log_models.py`, 서비스 trace → 한 행 변환은 `src/kosis_store/log_rows.py`, 지표 SQL은
`src/kosis_store/log_metrics.py`, 테스트는 `tests/test_log_schema.py`.

- **로그 버전 2 (11/18):** 서비스의 실시간 trace(T3-30~32)에 맞췄다. 질문 키 = OTel trace id, 대화 ID는 문자열, 턴은 0부터,
  결과에 `failed` 추가, 모델은 단계마다. 바뀐 점과 이유는 9장.
- **로그 버전 3 (11/20):** 노드·툴·LLM 단계 계측은 DB가 아니라 **OpenTelemetry 메트릭 → Prometheus**로 쌓는다. `request_logs`는
  질문 단위 열만 두고 단계 JSON 열(`spans`)을 뺐다. 메트릭 목록·속성·조회식·수집 구성은 10장.

- **초안이다.** `request_logs`에는 아직 마이그레이션이 없다. 번호를 백엔드(KOS-158)와 정한 뒤 넣는다(7장).
- 범위 밖: 수집 API·writer(KOS-170), 정제 배치(KOS-167), 멀티턴 품질 탐지 로직(KOS-176), 메트릭 화면.

## 1. 조사한 것과 가져온 것

| 출처 | 내용 | 우리 스키마에 옮긴 것 |
|---|---|---|
| [Langfuse 데이터 모델](https://langfuse.com/docs/observability/data-model.md), [Scores](https://langfuse.com/docs/evaluation/scores/data-model.md) | session → trace → observation 3단. 피드백·평가는 score로 trace·observation·session 중 하나에 붙는다(`name`, `value`, `stringValue`, `source`, `comment`) | 질문 1건 = 1행(`request_logs`), 단계는 메트릭·Jaeger(로그 버전 3, 10장), 피드백은 `request_id`에 붙인다 |
| [OpenTelemetry GenAI 규약](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-events/) ([저장소](https://github.com/open-telemetry/semantic-conventions-genai)) | `gen_ai.conversation.id`, `gen_ai.usage.input_tokens`·`output_tokens`, `error.type`. 아직 안정 단계가 아니고 별도 저장소로 옮겨졌다 | 서비스가 이 이름으로 span을 남긴다(T3-30~32). 로그는 그 값을 우리 열 이름으로 꺼낸다(`conversation_id`, `turn_index`, `input_tokens`, `output_tokens`, 단계의 `error_type`·`model`). 규약이 바뀌면 `log_rows.py` 한 곳만 고친다 |
| [Snowplow 추적 설계](https://docs.snowplow.io/docs/understanding-tracking-design/introduction-to-tracking-design/best-practices), [중복 이벤트](https://snowplow.io/blog/dealing-with-duplicate-event-ids) | 서로 다른 행동을 한 이벤트에 `type`으로 뭉치지 않는다. 재전송 때문에 같은 `event_id`가 다시 온다(1% 미만은 정상). 중복은 `event_id`와 수집 시각으로 지운다. 기기 시각과 수집 시각을 둘 다 둔다 | 화면 이벤트 정의(4장): `event_id` 기본 키, `client_ts` + `received_at`, 이벤트 종류별 이름 |
| 암묵 피드백 연구: [ACL 2024](https://aclanthology.org/2024.acl-long.598.pdf), [EMNLP 2021 재질문 탐지](https://preview.aclanthology.org/moar-dois/2021.emnlp-main.143.pdf), [arXiv 2010.12251](https://arxiv.org/pdf/2010.12251v2.pdf) | 같은 질문을 바꿔 다시 묻기, 중간 이탈, "아니" 같은 정정 표현이 불만족 신호다. 대화 맥락과 턴 사이 시간 간격을 쓰면 판별이 좋아진다 | `prev_request_id`, `turn_index`, `created_at`, `input_method`, `outcome=aborted`, 탐지 결과 자리 `rephrase_of`·`negative_reaction` |
| 개인정보보호위원회 생성형 AI 개인정보 처리 안내서(2025-08, [보도](https://byline.network/2025/08/6-277/)) | 처리 목적·범위를 먼저 정하고, 서비스 단계에서 이용자에게 투명하게 알린다 | 원문이 들어가는 열을 한정하고(3장), 보관 기간과 "대화를 지워도 로그는 남는다"를 개인정보 안내에 적는다 |

- 확인하지 못한 것: 프롬프트(질문 원문) 보관 기간의 구체 기준. 안내서 원문을 직접 읽지 못했다. **30일은 팀 제안값**이다.
- 검토하고 쓰지 않은 구조
  - 이벤트 한 줄씩 쌓는 단일 `events` 테이블: 원문이 JSON 안에 섞여 "원문만 지운다"가 어렵고, 규칙을 DB 제약으로 걸 수 없다.
  - Langfuse 자체 호스팅: 서비스가 여러 개 늘어 인프라 범위이고, 원문이 도구 DB에 들어가 삭제를 우리 코드로 보장하기 어렵다.
  - 단계·화면 행동·품질 신호 테이블을 지금 모두 만들기: 쓰는 코드가 없고 화면 명세가 확정 전이다.

## 2. `request_logs` — 질문 1건 = 1행

기능명세 US-10의 "질문마다 `request_id`로 원문, 고른 표, 판정, 단계별 지연이 한 묶음"이 이 한 행이다.

| 열 | 뜻 | 비고 |
|---|---|---|
| `request_id` | 질문 1건의 ID = 그 턴의 **OTel trace id** (32자리 hex, 기본 키) | 서비스가 SSE 첫 이벤트 `meta`로 이미 보낸다. 피드백·화면 이벤트가 이 값으로 붙고, 그대로 Jaeger 검색어다. 추적이 꺼진 no-op 값(0)은 막는다 |
| `conversation_id`, `turn_index` | 대화(`thread_id` = `gen_ai.conversation.id`, 최대 64자 문자열)와 턴 번호(`kosis.turn.index`, **0부터**) | 외래 키가 아닌 값이다(아래 설명). `(conversation_id, turn_index)` 인덱스, 재시도가 있어 unique가 아니다 |
| `prev_request_id` | 같은 대화의 직전 질문 | 재질문 탐지(KOS-176) |
| `session_id` | 브라우저 세션(임의 UUID) | 사람을 식별하지 않는다. 하네스 요청은 비어 있다 |
| `is_member`, `is_internal` | 회원 여부, 팀·하네스 트래픽 여부 | 지표는 `is_internal`을 뺀다 |
| `input_method` | `typed` · `suggestion`(추천 질문 버튼) | |
| `question`, `question_len` | 가린 질문 원문, 글자 수 | 원문은 30일 뒤 NULL, 글자 수는 남는다 |
| `outcome` | `answered` · `refused` · `clarify` · `failed` · `error` · `aborted` | 앞의 넷은 `ChatResult.outcome`, `error`는 예외, `aborted`는 답이 끝나기 전에 떠남 |
| `reason_code` | 거절·되묻기 사유 코드, 오류면 예외 이름 | 거절이면 꼭 있어야 한다. 에이전트가 사유를 주지 않은 거절(멀티쿼리 등)은 `unknown` |
| `failed_stage` | 처음으로 정상 종료하지 않은 단계(`search`·`stat`·`chart`·`agent`) | 쓰는 순간 단계에서 계산(`failed_stage`) |
| `picked_key`, `survey_name` | 답에 쓴 통계표 키, 조사명 | |
| `has_numbers`, `has_chart` | 답에 숫자·차트가 있는지 | 출처 클릭률의 분모 |
| `latency_ms`, `first_token_ms` | 질문 받음 → 답 끝, 첫 글자 | |
| `llm_calls`, `input_tokens`, `output_tokens` | LLM 호출 수·토큰 (턴 합계) | `chat` span 기준, 평가 ops와 같은 셈. 모델별 토큰·비용은 Prometheus 메트릭(10장) |
| `rephrase_of`, `negative_reaction` | 재질문 대상 요청, 부정 반응 여부 | KOS-176이 원문이 있는 동안 채운다 |
| `log_version`, `created_at` | 스키마 버전, 저장 시각(UTC) | 날짜 집계는 KST로 바꿔서 한다 |

**대화 ID가 외래 키가 아닌 이유**
- 비회원 대화는 `conversations` 테이블에 없다(Redis 세션에만 있다).
- 회원이 대화를 지워도 운영 로그는 보관 기간까지 남는다(US-09).
- Redis가 만료돼 대화가 끊긴 비회원은 `session_id`로 앞뒤 질문을 잇는다.

**DB가 막는 것 (CHECK 제약)**
- `is_internal`이면 `question`은 NULL이어야 한다. 하네스가 보낸 골든 문항이 원문으로 남지 않는다.
- `outcome`·`input_method`는 정해진 값만, 거절에는 사유 코드, `turn_index`는 0 이상, `request_id`는 32자이고 0이 아니다.

**단계 기록 — 행에 저장하지 않는다 (로그 버전 3)**
- 노드·툴·LLM 단계의 지연·상태·토큰은 Prometheus 메트릭으로 쌓는다(10장). 질문 1건의 단계를 보려면 `request_id`(= trace id)로 Jaeger를 연다.
- 쓰는 순간에만 단계를 읽는다(`step_spans`, 허용 키만): `failed_stage`(처음으로 `ok`가 아닌 단계)와 LLM 호출 수·토큰 합계를 채우는 데 쓴다.
- `evals.trace.blame()`은 골든 채점 결과가 있어야 해서 사용자 요청에는 쓸 수 없다. `failed_stage`는 정답 없이 알 수 있는 것만 본다. 오류가 없는 거절은 거절한 곳(실전 1이면 `stat`, 검색 gate면 `search`).

### 2.1 서비스 trace에서 한 행 만들기 (`row_from_turn`)

서비스는 턴마다 `invoke_agent` 루트 span 아래에 노드·툴·LLM span을 남긴다(`kosis_agent/observability/tracing.py`).
`row_from_turn(끝난 span 목록, ChatResult)`이 그 값을 한 행으로 옮긴다. 언제 부르고 어디에 쓸지는 수집(KOS-170)이 정한다.

| 열 | 가져오는 곳 |
|---|---|
| `request_id` | 루트 span의 trace id |
| `conversation_id`, `turn_index` | 루트 속성 `gen_ai.conversation.id`, `kosis.turn.index` |
| `outcome` | 이탈이면 `aborted`, 아니면 루트 속성 `kosis.outcome`(예외면 `error`), 없으면 `ChatResult.outcome` |
| `reason_code` | 루트 속성 `kosis.no_answer_reason`(없으면 `ChatResult.no_answer_reason`), 오류면 `error.type`, 사유 없는 거절은 `unknown` |
| `picked_key`, `survey_name` | `ChatResult.table_key`, 같은 키의 `picked[].survey_name` |
| `has_numbers`, `has_chart` | 답함이고 `stat_answer`가 있음, `chart`가 있음 |
| `latency_ms` | 루트 span 길이 |
| `failed_stage` | 루트 아래 span을 시작 순서대로 읽은 단계(`step_spans`, 저장 안 함) 중 처음으로 `ok`가 아닌 것. `kind`: `gen_ai.operation.name`이 `chat`이면 `llm`, `execute_tool`이면 `tool`, 나머지 `node`. `stage`: 자기 이름 또는 가장 가까운 상위 span 이름으로 정한다(`search`·`search_tables` → search, `statistics`·`summarize_stat` → stat, `chart`·`make_chart` → chart, 그 밖 → agent) |
| `llm_calls`, 토큰 | 같은 단계 중 `kind = llm`의 수, `gen_ai.usage.*` 합 |
| `question` | 수집이 가린 원문. 하네스면 버린다 |
| `first_token_ms`, `session_id`, `is_member`, `input_method`, `prev_request_id` | 화면·수집이 넘긴 값 |

- span 이름 상수는 tracing 모듈과 같은지 테스트가 확인한다(저장소가 에이전트 패키지를 import하지 않게 복사해 둠).
- 테스트는 서비스와 같은 `start_turn`·`LiveSpans`로 만든 턴(검색 노드 → `search_tables` 툴, 통계 노드 → chat 모델 1회)을 변환해, 그 행이 테이블 제약을 모두 통과하는지 본다. 툴 예외 메시지(질문 조각)는 단계 기록에 남지 않는다.

<details><summary>DDL (PostgreSQL, 모델에서 생성)</summary>

```sql
CREATE TABLE request_logs (
    request_id VARCHAR(32) NOT NULL,
    conversation_id VARCHAR(64) NOT NULL,
    turn_index INTEGER NOT NULL,
    prev_request_id VARCHAR(32),
    session_id UUID,
    is_member BOOLEAN NOT NULL,
    is_internal BOOLEAN NOT NULL,
    input_method VARCHAR(16) NOT NULL,
    question TEXT,
    question_len INTEGER,
    outcome VARCHAR(16) NOT NULL,
    reason_code VARCHAR(64),
    failed_stage VARCHAR(32),
    picked_key VARCHAR(64),
    survey_name VARCHAR(200),
    has_numbers BOOLEAN NOT NULL,
    has_chart BOOLEAN NOT NULL,
    latency_ms INTEGER,
    first_token_ms INTEGER,
    llm_calls INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    rephrase_of VARCHAR(32),
    negative_reaction BOOLEAN,
    log_version INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (request_id),
    CONSTRAINT ck_request_logs_outcome CHECK (outcome IN ('answered', 'refused', 'clarify', 'failed', 'error', 'aborted')),
    CONSTRAINT ck_request_logs_input_method CHECK (input_method IN ('typed', 'suggestion')),
    CONSTRAINT ck_request_logs_internal_no_text CHECK (NOT is_internal OR question IS NULL),
    CONSTRAINT ck_request_logs_refusal_reason CHECK (outcome <> 'refused' OR reason_code IS NOT NULL),
    CONSTRAINT ck_request_logs_turn_index CHECK (turn_index >= 0),
    CONSTRAINT ck_request_logs_request_id CHECK (length(request_id) = 32 AND request_id <> '00000000000000000000000000000000')
);
CREATE INDEX ix_request_logs_conversation_turn ON request_logs (conversation_id, turn_index);
CREATE INDEX ix_request_logs_created_at ON request_logs (created_at);
CREATE INDEX ix_request_logs_picked_key ON request_logs (picked_key);
CREATE INDEX ix_request_logs_session_id ON request_logs (session_id);
```
</details>

## 3. 개인정보·보관 규칙

- **원문이 들어가는 곳은 두 열뿐이다:** `request_logs.question`, `feedback.reason`. 둘 다 저장 전에 전화번호·이메일 패턴을 가린다(가림 함수는 KOS-170).
- **단계는 허용 키만 읽는다:** `name`, `kind`, `stage`, `ms`, `status`, `error_type`, `input_tokens`, `output_tokens`, `model` (`clean_spans`). 로그 버전 3부터 행에 저장하지 않지만, 메트릭 속성도 같은 원칙을 따른다(10장). 툴 인자·결과(`gen_ai.tool.call.arguments`·`.result`)는 질문과 답 원문이 들어 있어 옮기지 않는다. 예외 메시지, 검색 상위 표 목록, 실전 1의 `unresolved` 같은 값은 넣지 않는다. 질문 조각이 섞일 수 있고, 섞이면 30일 삭제가 닿지 않는다.
- **보관:** 30일(제안값)이 지나면 `question`만 NULL로 바꾼다. 행과 나머지 열은 남는다(`purge_questions`, 다시 돌려도 결과가 같다). 실행 주기는 정제 배치(KOS-167)가 정한다.
- **하네스·팀 트래픽:** `is_internal=true`, 원문은 저장하지 않는다(제약). 골든 문항·정답·문항 ID는 어느 열에도 넣지 않는다.
- IP, 기기 식별자, 이름·연락처는 저장하지 않는다. `session_id`는 브라우저가 만든 임의 값이다.
- Jaeger로 나가는 span 속성은 이 규칙 밖이다. 사용자 요청을 추적으로 내보낼 때도 같은 허용 목록을 쓰도록 KOS-170에서 맞춘다.

## 4. 화면 이벤트 `ui_events` — 정의만 (테이블은 KOS-170)

원문이 없는 화면 행동이다. 그래서 보관 기간 없이 둔다.

| 열 | 뜻 |
|---|---|
| `event_id` | 화면이 만든 UUID, 기본 키. 같은 값이 다시 오면 무시한다(재전송) |
| `event_type` | `page_view` · `suggestion_impression` · `suggestion_click` · `citation_click` · `chart_view` · `chart_interact` · `copy_answer` · `stream_abort` |
| `client_ts`, `received_at` | 브라우저 시각, 서버 수신 시각. 5분 넘게 어긋나면 서버 시각을 쓴다 |
| `session_id`, `request_id` | 세션, 관련 질문(있을 때) |
| `screen_id`, `feature_id` | 화면 명세 S-xx, 기능 명세 F-xx |
| `props` | 이벤트별 값 (JSON): 버튼 위치, 표 키와 순위, 차트 종류. **자유 입력 글자는 넣지 않는다** |
| `is_internal`, `log_version` | |

- 질문 보냄, 첫 글자, 답 끝은 화면 이벤트로 만들지 않는다. 서버가 `request_logs`에 남긴다(같은 값을 두 번 세지 않는다).
- 정제 규칙(같은 세션·같은 이벤트·같은 대상이 2초 안에 반복되면 1건, 30분 무응답이면 새 세션)은 KOS-167에서 구현한다.
- 수신 검사에서 떨어진 이벤트는 버리지 않고 사유와 함께 따로 둔다(`ingest_rejects`: 받은 시각, 사유 코드, 이벤트 종류, 건수). 원문은 넣지 않는다.

## 5. 백엔드(KOS-158)에 넘기는 요구 사항

`feedback` 테이블과 마이그레이션 `0002`는 KOS-158 범위다(KOS-155 결정). 로그 쪽에서 필요한 것만 적는다.

1. ~~`request_id`를 SSE 첫 이벤트로 보낸다~~ → **이미 된다(로그 버전 2).** 첫 이벤트 `meta`의 `trace_id`가 질문 키다. 화면 이벤트·피드백은 이 값을 `request_id`로 보낸다. 서비스에서 추적을 끄지 않는다(끄면 키가 0이 되어 로그 행이 거부된다).
2. `feedback.request_id`는 unique다(같은 답에 다시 누르면 마지막 값으로 갱신). `request_logs`로 외래 키를 걸지 않는다. 로그 쓰기가 실패해도 피드백은 남아야 한다.
3. `feedback.message_id`는 nullable이고, 대화가 지워지면 NULL이 된다(`ON DELETE SET NULL`). 비회원 답에는 메시지 행이 없다.
4. 제약: 값은 `up`·`down`, 선택지는 값에 맞는 것만(없어도 됨: 창을 닫은 경우), "기타"면 이유 필수, 이유 200자.
5. `feedback.reason`도 30일 뒤 NULL로 바꾼다. 값과 선택지는 남긴다.
6. 요청이 끝날 때(정상·거절·오류·이탈 모두) `row_from_turn`으로 `request_logs`에 한 행을 쓴다. 쓰기가 실패해도 답은 나가야 한다. 이탈은 스트림이 닫힌 것(`GeneratorExit`)으로 안다.

## 6. 메트릭 화면 지표 18개와 스키마

`확인`은 지어낸 요청 12건을 실제 PostgreSQL에 넣고 손으로 센 값과 SQL 결과가 같은지 본 것이다(`tests/test_log_schema.py`).

| 탭 | 지표 | 어디서 계산 | 상태 |
|---|---|---|---|
| 요약 | 질문 수 | `request_logs` | 확인 (`question_count`) |
| | 답함·거절·되묻기·오류 비율 | `request_logs.outcome` | 확인 (`outcome_share`) |
| | 👍 비율 | `feedback` | SQL만 (KOS-158 뒤) |
| | 응답 지연 p50·p95 | `request_logs.latency_ms` | 확인 (`latency`) |
| 답 품질 | 거절 사유 분포 | `reason_code` | 확인 (`refusal_reasons`) |
| | 👎 이유 분포 | `feedback` | SQL만 |
| | 👍 이유 분포 | `feedback` | SQL만 |
| | 실패 단계 | `failed_stage` | 확인 (`failed_stages`) |
| | 👎 답 목록 | `feedback` ⋈ `request_logs` | SQL만 |
| 사용 패턴 | 많이 쓰인 표·조사 상위 20 | `picked_key`, `survey_name` | 확인 (`top_tables`) |
| | 추천 질문 클릭률 | `ui_events` | SQL만 (KOS-170 뒤) |
| | 대화당 턴 수 | `conversation_id`, `turn_index` | 확인 (`turns_per_conversation`) |
| | 출처 클릭률 | `ui_events` ÷ `has_numbers` | SQL만 |
| | 답 전 이탈률 | `outcome = 'aborted'` | 확인 (`outcome_share`의 한 줄) |
| 성능·비용 | 단계별 지연 p50·p95, 오류율 | Prometheus `kosis.agent.step.duration` | 조회식만 (10장, 계측은 에이전트 담당) |
| | 첫 글자까지 시간 | `first_token_ms` | 확인 (`first_token`) |
| | 질문당 LLM 호출·토큰 | `llm_calls`, 토큰 | 확인 (`llm_usage`) |
| | 모델별 토큰·비용 | Prometheus `gen_ai.client.token.usage` (이미 계측됨) | 조회식만 (10장), 비용은 모델별 토큰에 단가표를 곱함 |
| | 로그 품질 | `ingest_rejects`, 짝 없는 질문 | SQL만 (KOS-170 뒤) |

- DB 확인 9개(SQL 9개), DB SQL만 7개, Prometheus 조회식 2개(로그 버전 3에서 옮김). 지표는 19줄이 됐다("질문당 LLM 호출·토큰·비용"을 DB·Prometheus 두 줄로 나눔). 초안의 "답 전 이탈률"은 화면 이벤트 `stream_abort`로 세려 했으나, 서버가 아는 `outcome = 'aborted'`로 바꿔 지금 확인할 수 있게 했다.
- 모든 지표는 `is_internal`을 빼고, UTC로 저장된 시각을 KST 날짜로 바꿔 하루 단위로 센다.

<details><summary>아직 테이블이 없어 SQL만 적어 둔 지표</summary>

```sql
-- 👍 비율
SELECT count(*) FILTER (WHERE value = 'up')::numeric / nullif(count(*), 0) FROM feedback WHERE <day>;
-- 👎 · 👍 이유 분포
SELECT value, choice, count(*) FROM feedback WHERE <day> AND choice IS NOT NULL GROUP BY value, choice;
-- 👎 답 목록 (원문은 보관 기간 안에서만 보인다)
SELECT r.created_at, r.question, r.picked_key, r.outcome, f.choice, f.reason, r.request_id
FROM feedback f JOIN request_logs r USING (request_id) WHERE f.value = 'down' AND NOT r.is_internal;
-- 추천 질문 클릭률
SELECT count(*) FILTER (WHERE event_type = 'suggestion_click')::numeric
     / nullif(count(*) FILTER (WHERE event_type = 'suggestion_impression'), 0) FROM ui_events WHERE <day>;
-- 출처 클릭률: 출처를 누른 답 수 ÷ 숫자가 있는 답 수
SELECT (SELECT count(DISTINCT request_id) FROM ui_events WHERE event_type = 'citation_click' AND <day>)::numeric
     / nullif((SELECT count(*) FROM request_logs WHERE has_numbers AND <day>), 0);
-- 로그 품질: 탈락 이벤트 수
SELECT reason, sum(n) FROM ingest_rejects WHERE <day> GROUP BY reason;
```
</details>

## 7. 정해야 할 것

- **마이그레이션 번호:** `request_logs`를 `0002`(KOS-158) 다음 번호로 넣을지, 같은 마이그레이션에 넣을지 김병석 님과 정한다. 정해지면 모델을 `kosis_store.models.Base`로 옮기고 `test_migration_matches_models`가 함께 본다.
- 질문 원문 보관 기간(30일 제안)과 개인정보 안내 문구.
- 사유 코드 목록(`reason_code`): 실전 1의 거절 사유와 검색의 답 없음 사유를 하나의 목록으로 묶는다(KOS-170에서 writer와 함께).
- LLM 단가표를 어디에 둘지(설정 파일 제안).
- Prometheus를 어디에 띄울지와 보관 기간(10.4, 인프라 담당).
- Linear KOS-169 본문과 이 문서의 범위가 맞는지 확인(작성 때 본문을 읽지 못했다).

## 8. 실행

```bash
TEST_DATABASE_URL=postgresql+psycopg://kosis:kosis@localhost:5432/kosis_test PYTHONPATH=src pytest tests/test_log_schema.py
```

- `TEST_DATABASE_URL`이 없으면 PostgreSQL 테스트 2개는 건너뛰고, 규칙 테스트(SQLite)만 돈다.

## 9. 로그 버전 2: 서비스 trace에 맞춘 변경 (11/18)

버전 1(11/10)을 만든 뒤 main에 실시간 trace(T3-30), LLM span·턴 종료 이유(T3-31), 계측 통일(T3-32)이 들어왔다. 실제 서비스 기록과 대조해 어긋난 7곳을 고쳤다.

| # | 서비스 | 버전 1 | 버전 2 |
|---|---|---|---|
| 1 | 대화 ID `thread_id`는 문자열(클라이언트가 임의 값을 보낼 수 있음) | UUID 열 | 문자열(최대 64자) |
| 2 | 턴 번호 `kosis.turn.index`는 0부터 | `turn >= 1` | `turn_index >= 0` |
| 3 | 결과에 `failed`가 있다 | 없음(행 거부) | 추가 |
| 4 | 사유 없는 거절이 있다(멀티쿼리 등) | 행 거부 | `unknown`으로 채움 |
| 5 | SSE 첫 이벤트에 `trace_id`가 이미 있다 | 서버 UUID + 별도 `trace_id`, 백엔드 요구 1개 | `request_id` = trace id, 백엔드 요구 삭제 |
| 6 | 단계 = `invoke_agent` 아래 노드·툴·chat span | 허용 키만 정의 | `row_from_turn`이 span → 단계로 옮김 |
| 7 | LLM 호출 = `chat` span, 한 턴에 여러 모델 | `llm_model` 열 1개 | 단계마다 `model`, 모델별 토큰 지표 `llm_by_model` |

- 쌓인 로그가 아직 없어(마이그레이션 전) 버전 1 → 2 변환 규칙은 필요 없다.

## 10. 로그 버전 3: 노드 계측은 OpenTelemetry 메트릭 → Prometheus (11/20)

서비스는 노드마다 trace span을 남긴다(`kosis_agent/observability/tracing.py`). 운영 지표로 쓸 값을 어디에 쌓을지 세 안을 비교해 **메트릭 + Prometheus**로 정했다(`plans/T3-36.md` 보강 2).

- **DB `request_logs`**: 질문 1건 = 1행, 질문 단위 열만. 피드백·화면 이벤트·원문이 붙는 곳이다.
- **Prometheus**: 노드·툴·LLM 단계의 지연·오류·재방문·토큰. 시간별로 쌓인 집계값이며, 질문 한 건은 가리키지 않는다.
- **Jaeger**: 질문 한 건의 단계 전체. `request_id`(= trace id)로 연다.

검토하고 쓰지 않은 안: 단계를 질문 행의 JSON으로 저장(로그 버전 2 방식), 단계 테이블 `request_steps` 분리. 질문이 많아지면 단계 행이 빠르게 늘어난다. 노드 지연은 집계로만 보면 충분하다. 그래서 고빈도 단계 값은 메트릭으로 보냈다.

### 10.1 계측 → 어디에 쌓는가

| trace 계측 | DB `request_logs` | Prometheus 메트릭 |
|---|---|---|
| 루트 `invoke_agent` 지연 | `latency_ms` | (DB로 충분) |
| `kosis.outcome`, `kosis.no_answer_reason`, `error.type` | `outcome`, `reason_code` | `kosis.agent.turns` (이미 있음) |
| `gen_ai.conversation.id`, `kosis.turn.index`, `kosis.turn.table_key` | `conversation_id`, `turn_index`, `picked_key` | 넣지 않음(값이 무한히 많음) |
| 노드·툴·실전 1 단계 span 지연·상태 | `failed_stage`만 | `kosis.agent.step.duration` (**추가 요청**) |
| 메인 그래프 노드 재방문 (statistics 대체 표 재시도, statistics → search 재검색) | — | `kosis.agent.node.visits` (**추가 요청**) |
| chat span 모델·토큰 | `llm_calls`, `input_tokens`, `output_tokens` (턴 합계) | `gen_ai.client.token.usage` (이미 있음) |
| chat span 지연·실패 `error.type` | — | `gen_ai.client.operation.duration` (이미 있음) |
| `llm.attempts` (실전 1 형식·HTTP 재시도, rerank2 HTTP 재시도) | — | `kosis.llm.attempts` (**추가 요청**) |
| `gen_ai.response.finish_reasons` (LangChain 호출만) | — | `kosis.llm.finishes` (**추가 요청**) |
| `kosis.stage2` | — | 넣지 않음(`gen_ai.request.model`과 같은 정보) |
| 원문: `kosis.turn.question`·`answer`·`resolved_question`, 툴 인자·결과, 예외 메시지 | `question`만, 가린 값 | **절대 넣지 않음** |

### 10.2 메트릭 목록

이미 계측된 3개(T3-31)는 그대로 쓴다. 아래 4개는 에이전트 계측 담당에게 요청한다. 계측 위치는 같은 콜백 `LiveSpans`다.

| 메트릭 | 종류·단위 | 속성 | 기록 시점 |
|---|---|---|---|
| `kosis.agent.step.duration` | 히스토그램, s | `kosis.step.kind`(node·tool·llm), `kosis.step.name`(span 이름), `kosis.stage`(search·stat·chart·agent), `kosis.step.status`(ok·error), `error.type`(실패 시) | 노드·툴 span이 끝날 때, 실전 1 단계 기록을 옮길 때 |
| `kosis.agent.node.visits` | 히스토그램, {visit} | `kosis.step.name`: **메인 그래프 노드만**(`context`·`search`·`statistics`·`multi`·`chart`·`finalize`) | 턴이 끝날 때 노드마다 한 번(그 턴의 방문 수). `multi`의 툴 반복과 실전 1·2 안쪽 노드는 세지 않는다 |
| `kosis.llm.attempts` | 히스토그램, {request} | `gen_ai.request.model` | `llm.attempts`가 있는 단계(실전 1 HCX, rerank2)마다 |
| `kosis.llm.finishes` | 카운터, {call} | `gen_ai.request.model`, `gen_ai.response.finish_reason` | `finish_reasons`가 있는 chat 호출마다 |

**속성 규칙** (개인정보와 시계열 수)
- 넣지 않는다: `request_id`·trace id·`conversation_id`·`session_id`, 표 키, 질문·답·조건, 예외 메시지. 값이 끝없이 늘어 시계열이 폭발하고, 원문이 섞이면 지울 수 없다.
- 이름에는 고정된 값만 넣는다: 노드·툴·모델 이름, 상태, 예외 **이름**(`error.type`).
- 하네스 트래픽: 평가는 별도 provider(`deployment.environment.name=eval`)라 서비스 메트릭에 섞이지 않는다. HTTP로 서비스를 부르는 하네스 요청은 `kosis.internal=true` 속성을 붙인다(요청 표시는 수집 KOS-170이 정함).
- 질문 한 건과 잇기: 히스토그램에 trace id를 속성으로 넣지 않는다. 대신 exemplar로 붙인다. `LiveSpans`는 현재 context 없이 부모를 직접 넘기므로, 기록할 때 해당 span의 context를 넘겨야 exemplar가 붙는다.
- 히스토그램 구간(초): `0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 45, 60, 120`. 기본 구간은 rerank1(CPU 25~40초)을 구분하지 못한다. View로 지정한다.

### 10.3 조회식 (PromQL)

Prometheus OTLP 수신은 이름의 `.`을 `_`로 바꾸고 단위 접미사를 붙인다(`kosis.agent.step.duration` → `kosis_agent_step_duration_seconds`). 서비스·평가 구분(`deployment.environment.name`)은 resource 속성이라, 수신 설정에서 라벨로 올려야 한다(10.4). 아래 `$env`는 `deployment_environment_name="service"`다.

```promql
# 단계별 지연 p95 (하루)
histogram_quantile(0.95, sum by (le, kosis_step_name) (rate(kosis_agent_step_duration_seconds_bucket{$env}[1d])))
# 단계별 오류율
sum by (kosis_step_name) (rate(kosis_agent_step_duration_seconds_count{$env, kosis_step_status="error"}[1d]))
  / sum by (kosis_step_name) (rate(kosis_agent_step_duration_seconds_count{$env}[1d]))
# 정책 PL-KDMYOY 상한 대조: 메인 노드가 턴당 2번 이상 돈 비율 (statistics 대체 표 재시도·재검색)
1 - sum by (kosis_step_name) (rate(kosis_agent_node_visits_bucket{$env, le="1"}[1d]))
  / sum by (kosis_step_name) (rate(kosis_agent_node_visits_count{$env}[1d]))
# 모델별 토큰 (비용 = 이 값 × 단가표)
sum by (gen_ai_request_model, gen_ai_token_type) (increase(gen_ai_client_token_usage_sum{$env}[1d]))
# LLM 잘림 비율: finish_reason이 있는 호출만 분모
sum by (gen_ai_request_model) (increase(kosis_llm_finishes_total{$env, gen_ai_response_finish_reason="length"}[1d]))
  / sum by (gen_ai_request_model) (increase(kosis_llm_finishes_total{$env}[1d]))
# LLM 요청 재시도: 한 단계에서 모델 요청이 2번 이상인 비율
1 - sum by (gen_ai_request_model) (rate(kosis_llm_attempts_bucket{$env, le="1"}[1d]))
  / sum by (gen_ai_request_model) (rate(kosis_llm_attempts_count{$env}[1d]))
```

- 메트릭 화면 성능·비용 탭은 이 조회식을 Prometheus HTTP API로 읽는다. 요약·답 품질·사용 패턴 탭은 지금처럼 DB SQL이다.
- 일별 집계(`daily_metrics`, KOS-167)에 노드 지표를 함께 남기려면 정제 배치가 하루 한 번 위 조회식 결과를 받아 적는다. Prometheus 보관 기간이 지나도 일별 값이 남는다.
- 손 계산 대조는 계측(10.2)이 들어온 뒤 메모리 메트릭 리더로 한다. 이 문서 단계에서는 조회식만 있다.

### 10.4 수집 구성 (인프라 담당에게 요청)

- 서비스는 이미 `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`가 있으면 메트릭을 내보낸다(`backend/app/telemetry.py`). 내보내기 방식은 **gRPC**다.
- 가장 단순한 구성: Prometheus 3.x를 `--web.enable-otlp-receiver`로 띄운다. Prometheus OTLP 수신은 **HTTP(protobuf)만** 받으므로 둘 중 하나가 필요하다.
  - ① 서비스 메트릭 내보내기를 HTTP 방식으로 바꾼다(`opentelemetry-exporter-otlp-proto-http`, endpoint `http://prometheus:9090/api/v1/otlp/v1/metrics`).
  - ② 사이에 OpenTelemetry Collector를 둔다(OTLP gRPC 수신 → Prometheus 내보내기).
- 수신 설정에서 `deployment.environment.name`을 라벨로 올린다(`otlp.promote_resource_attributes`). 그래야 서비스와 평가를 나눌 수 있다.
- 보관 기간: 메트릭에는 원문이 없어 30일 규칙 대상이 아니다. 일별 값은 `daily_metrics`가 따로 남긴다.
- Jaeger: 질문 한 건의 단계를 볼 곳이다. 루트 span에 원문(`kosis.turn.question` 등, T3-43)이 있으므로 **Jaeger 보관 기간을 30일 이하**로 두거나, 서비스 trace에서 원문 속성을 끈다.

### 10.5 담당자에게 넘기는 요구 사항

| # | 누구 | 내용 |
|---|---|---|
| 1 | 에이전트 계측 (T3-30~32 담당) | 10.2의 메트릭 4개 추가, 구간 View, exemplar용 span context 전달, 하네스 HTTP 요청의 `kosis.internal` |
| 2 | 인프라 (KOS-158 / compose) | Prometheus(OTLP 수신) 또는 Collector 추가, 내보내기 방식 맞추기, resource 속성 라벨, Jaeger 보관 기간 |
| 3 | 수집 (KOS-170) | 쓰는 순간 `step_spans`로 `failed_stage`·LLM 합계 계산. 행에는 단계 저장 안 함 |
| 4 | 정제 (KOS-167) | `daily_metrics`에 10.3 조회식의 하루 값 적재 |

### 10.6 로그 버전 2 → 3 바뀐 것

| | 버전 2 | 버전 3 |
|---|---|---|
| 단계 기록 | `request_logs.spans` (JSONB) | 열 없음. Prometheus 메트릭 + Jaeger |
| 단계별 지연 지표 | SQL `stage_latency` | PromQL (10.3) |
| 모델별 토큰 | SQL `llm_by_model` | PromQL `gen_ai_client_token_usage` |
| `failed_stage`, LLM 합계 | `spans`에서 계산해 저장 | 쓰는 순간 span에서 계산해 저장(같은 값) |

- 쌓인 행이 아직 없어(마이그레이션 전) 버전 2 → 3 변환 규칙은 필요 없다.
