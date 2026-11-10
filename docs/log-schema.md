# 사용자 로그 스키마 (T3-29, Linear KOS-169)

질문 1건이 한 행인 `request_logs` 테이블과, 그 주변(화면 행동, 피드백, 수신 탈락)이 지켜야 할 규칙을 정한다.
모델은 `src/kosis_store/log_models.py`, 지표 SQL은 `src/kosis_store/log_metrics.py`, 테스트는 `tests/test_log_schema.py`.

- **초안이다.** `request_logs`에는 아직 마이그레이션이 없다. 번호를 백엔드(KOS-158)와 정한 뒤 넣는다(7장).
- 범위 밖: 수집 API·writer(KOS-170), 정제 배치(KOS-167), 멀티턴 품질 탐지 로직(KOS-176), 메트릭 화면.

## 1. 조사한 것과 가져온 것

| 출처 | 내용 | 우리 스키마에 옮긴 것 |
|---|---|---|
| [Langfuse 데이터 모델](https://langfuse.com/docs/observability/data-model.md), [Scores](https://langfuse.com/docs/evaluation/scores/data-model.md) | session → trace → observation 3단. 피드백·평가는 score로 trace·observation·session 중 하나에 붙는다(`name`, `value`, `stringValue`, `source`, `comment`) | 질문 1건 = 1행(`request_logs`), 단계는 그 행의 `spans`, 피드백은 `request_id`에 붙인다 |
| [OpenTelemetry GenAI 규약](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-events/) ([저장소](https://github.com/open-telemetry/semantic-conventions-genai)) | `gen_ai.conversation.id`, `gen_ai.usage.input_tokens`·`output_tokens`, `error.type`. 아직 안정 단계가 아니고 별도 저장소로 옮겨졌다 | 뜻은 같게 두되 우리 열 이름으로 꺼낸다(`conversation_id`, `input_tokens`, `output_tokens`, span의 `error_type`). 규약이 바뀌어도 테이블은 그대로다 |
| [Snowplow 추적 설계](https://docs.snowplow.io/docs/understanding-tracking-design/introduction-to-tracking-design/best-practices), [중복 이벤트](https://snowplow.io/blog/dealing-with-duplicate-event-ids) | 서로 다른 행동을 한 이벤트에 `type`으로 뭉치지 않는다. 재전송 때문에 같은 `event_id`가 다시 온다(1% 미만은 정상). 중복은 `event_id`와 수집 시각으로 지운다. 기기 시각과 수집 시각을 둘 다 둔다 | 화면 이벤트 정의(4장): `event_id` 기본 키, `client_ts` + `received_at`, 이벤트 종류별 이름 |
| 암묵 피드백 연구: [ACL 2024](https://aclanthology.org/2024.acl-long.598.pdf), [EMNLP 2021 재질문 탐지](https://preview.aclanthology.org/moar-dois/2021.emnlp-main.143.pdf), [arXiv 2010.12251](https://arxiv.org/pdf/2010.12251v2.pdf) | 같은 질문을 바꿔 다시 묻기, 중간 이탈, "아니" 같은 정정 표현이 불만족 신호다. 대화 맥락과 턴 사이 시간 간격을 쓰면 판별이 좋아진다 | `prev_request_id`, `turn`, `created_at`, `input_method`, `outcome=aborted`, 탐지 결과 자리 `rephrase_of`·`negative_reaction` |
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
| `request_id` | 질문 1건의 ID (UUID, 기본 키) | **서버가 만든다.** 피드백과 화면 이벤트가 이 값으로 붙는다 |
| `trace_id` | OTel trace id | 추적이 켜져 있을 때만. Jaeger로 이동할 때 쓴다 |
| `conversation_id`, `turn` | 대화와 턴 | 외래 키가 아닌 값이다(아래 설명). `(conversation_id, turn)` 인덱스, 재시도가 있어 unique가 아니다 |
| `prev_request_id` | 같은 대화의 직전 질문 | 재질문 탐지(KOS-176) |
| `session_id` | 브라우저 세션(임의 UUID) | 사람을 식별하지 않는다. 하네스 요청은 비어 있다 |
| `is_member`, `is_internal` | 회원 여부, 팀·하네스 트래픽 여부 | 지표는 `is_internal`을 뺀다 |
| `input_method` | `typed` · `suggestion`(추천 질문 버튼) | |
| `question`, `question_len` | 가린 질문 원문, 글자 수 | 원문은 30일 뒤 NULL, 글자 수는 남는다 |
| `outcome` | `answered` · `refused` · `clarify` · `error` · `aborted` | `aborted`: 답이 끝나기 전에 떠남 |
| `reason_code` | 거절·되묻기 사유 코드 | 거절이면 꼭 있어야 한다 |
| `failed_stage` | 처음으로 정상 종료하지 않은 단계(`search`·`stat`·`chart`·`agent`) | `failed_stage(spans)`로 계산 |
| `picked_key`, `survey_name` | 답에 쓴 통계표 키, 조사명 | |
| `has_numbers`, `has_chart` | 답에 숫자·차트가 있는지 | 출처 클릭률의 분모 |
| `latency_ms`, `first_token_ms` | 질문 받음 → 답 끝, 첫 글자 | |
| `llm_calls`, `input_tokens`, `output_tokens`, `llm_model` | LLM 호출 수·토큰·모델 | 비용 = 토큰 × 모델 단가(단가표는 설정, 열이 아님) |
| `spans` | 단계 기록 (JSON 배열) | 허용 키만(3장) |
| `rephrase_of`, `negative_reaction` | 재질문 대상 요청, 부정 반응 여부 | KOS-176이 원문이 있는 동안 채운다 |
| `log_version`, `created_at` | 스키마 버전, 저장 시각(UTC) | 날짜 집계는 KST로 바꿔서 한다 |

**대화 ID가 외래 키가 아닌 이유**
- 비회원 대화는 `conversations` 테이블에 없다(Redis 세션에만 있다).
- 회원이 대화를 지워도 운영 로그는 보관 기간까지 남는다(US-09).
- Redis가 만료돼 대화가 끊긴 비회원은 `session_id`로 앞뒤 질문을 잇는다.

**DB가 막는 것 (CHECK 제약)**
- `is_internal`이면 `question`은 NULL이어야 한다. 하네스가 보낸 골든 문항이 원문으로 남지 않는다.
- `outcome`·`input_method`는 정해진 값만, 거절에는 사유 코드, `turn`은 1 이상.

**단계 기록 `spans`**
```json
[{"name": "retrieve", "kind": "node", "stage": "search", "ms": 412, "status": "ok"},
 {"name": "compute", "kind": "tool", "stage": "stat", "ms": 95, "status": "refused"},
 {"name": "compose", "kind": "llm", "stage": "stat", "ms": 2100, "status": "ok", "input_tokens": 812, "output_tokens": 164}]
```
- 평가 trace(`evals/trace.py`)와 같은 단계 이름을 쓴다. `status`는 `ok`·`refused`·`error`.
- `evals.trace.blame()`은 골든 채점 결과가 있어야 해서 사용자 요청에는 쓸 수 없다. `failed_stage`는 정답 없이 알 수 있는 것만 본다: 처음으로 `ok`가 아닌 단계.

<details><summary>DDL (PostgreSQL, 모델에서 생성)</summary>

```sql
CREATE TABLE request_logs (
    request_id UUID NOT NULL,
    trace_id VARCHAR(32),
    conversation_id UUID NOT NULL,
    turn INTEGER NOT NULL,
    prev_request_id UUID,
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
    llm_model VARCHAR(64),
    spans JSONB NOT NULL,
    rephrase_of UUID,
    negative_reaction BOOLEAN,
    log_version INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (request_id),
    CONSTRAINT ck_request_logs_outcome CHECK (outcome IN ('answered', 'refused', 'clarify', 'error', 'aborted')),
    CONSTRAINT ck_request_logs_input_method CHECK (input_method IN ('typed', 'suggestion')),
    CONSTRAINT ck_request_logs_internal_no_text CHECK (NOT is_internal OR question IS NULL),
    CONSTRAINT ck_request_logs_refusal_reason CHECK (outcome <> 'refused' OR reason_code IS NOT NULL),
    CONSTRAINT ck_request_logs_turn CHECK (turn >= 1)
);
CREATE INDEX ix_request_logs_conversation_turn ON request_logs (conversation_id, turn);
CREATE INDEX ix_request_logs_created_at ON request_logs (created_at);
CREATE INDEX ix_request_logs_picked_key ON request_logs (picked_key);
CREATE INDEX ix_request_logs_session_id ON request_logs (session_id);
```
</details>

## 3. 개인정보·보관 규칙

- **원문이 들어가는 곳은 두 열뿐이다:** `request_logs.question`, `feedback.reason`. 둘 다 저장 전에 전화번호·이메일 패턴을 가린다(가림 함수는 KOS-170).
- **단계 기록에는 허용 키만 넣는다:** `name`, `kind`, `stage`, `ms`, `status`, `error_type`, `input_tokens`, `output_tokens` (`clean_spans`). 예외 메시지, 검색 상위 표 목록, 실전 1의 `unresolved` 같은 값은 넣지 않는다. 질문 조각이 섞일 수 있고, 섞이면 30일 삭제가 닿지 않는다.
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

1. `request_id`를 SSE의 **첫** 이벤트로 보낸다. 마지막 이벤트로 보내면 답이 오는 중 떠난 사용자의 이탈 이벤트가 질문과 묶이지 않는다.
2. `feedback.request_id`는 unique다(같은 답에 다시 누르면 마지막 값으로 갱신). `request_logs`로 외래 키를 걸지 않는다. 로그 쓰기가 실패해도 피드백은 남아야 한다.
3. `feedback.message_id`는 nullable이고, 대화가 지워지면 NULL이 된다(`ON DELETE SET NULL`). 비회원 답에는 메시지 행이 없다.
4. 제약: 값은 `up`·`down`, 선택지는 값에 맞는 것만(없어도 됨: 창을 닫은 경우), "기타"면 이유 필수, 이유 200자.
5. `feedback.reason`도 30일 뒤 NULL로 바꾼다. 값과 선택지는 남긴다.
6. 요청이 끝날 때(정상·거절·오류·이탈 모두) `request_logs`에 한 행을 쓴다. 쓰기가 실패해도 답은 나가야 한다.

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
| | 대화당 턴 수 | `conversation_id`, `turn` | 확인 (`turns_per_conversation`) |
| | 출처 클릭률 | `ui_events` ÷ `has_numbers` | SQL만 |
| | 답 전 이탈률 | `outcome = 'aborted'` | 확인 (`outcome_share`의 한 줄) |
| 성능·비용 | 단계별 지연 p50·p95 | `spans` | 확인 (`stage_latency`) |
| | 첫 글자까지 시간 | `first_token_ms` | 확인 (`first_token`) |
| | 질문당 LLM 호출·토큰·비용 | `llm_calls`, 토큰, `llm_model` | 호출·토큰 확인 (`llm_usage`), 비용은 단가표를 곱함 |
| | 로그 품질 | `ingest_rejects`, 짝 없는 질문 | SQL만 (KOS-170 뒤) |

- 확인 11개(SQL 10개), SQL만 7개. 초안의 "답 전 이탈률"은 화면 이벤트 `stream_abort`로 세려 했으나, 서버가 아는 `outcome = 'aborted'`로 바꿔 지금 확인할 수 있게 했다.
- 모든 지표는 `is_internal`을 빼고, UTC로 저장된 시각을 KST 날짜로 바꿔 하루 단위로 센다.

<details><summary>아직 테이블이 없어 SQL만 적어 둔 지표</summary>

```sql
-- 👍 비율
SELECT count(*) FILTER (WHERE value = 'up')::numeric / nullif(count(*), 0) FROM feedback WHERE <day>;
-- 👎 · 👍 이유 분포
SELECT value, choice, count(*) FROM feedback WHERE <day> AND choice IS NOT NULL GROUP BY value, choice;
-- 👎 답 목록 (원문은 보관 기간 안에서만 보인다)
SELECT r.created_at, r.question, r.picked_key, r.outcome, f.choice, f.reason, r.request_id, r.trace_id
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
- Linear KOS-169 본문과 이 문서의 범위가 맞는지 확인(작성 때 본문을 읽지 못했다).

## 8. 실행

```bash
TEST_DATABASE_URL=postgresql+psycopg://kosis:kosis@localhost:5432/kosis_test PYTHONPATH=src pytest tests/test_log_schema.py
```

- `TEST_DATABASE_URL`이 없으면 PostgreSQL 테스트 2개는 건너뛰고, 규칙 테스트(SQLite)만 돈다.
