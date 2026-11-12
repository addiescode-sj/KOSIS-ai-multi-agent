# REST API 초안 (KOS-155)

> 상태: **초안 · 팀 리뷰 대기** (2026-11-12)
> 기준: Manyfast `KOSIS_통계프로젝트` v12 기능 명세, Linear 종합 프로젝트 11/08 결정, 코드 `04319f7`
> 경로: Web (Next.js) → REST API (FastAPI) → AI 파이프라인(LangGraph) · PostgreSQL

이 문서는 중반 이슈(KOS-159 AI 파이프라인 · KOS-160 FE · KOS-161 DB query · KOS-162 인프라)가 같은 계약을 보고 일하도록 엔드포인트와 요청·응답 형식을 정한다.
표시 규칙: **[현재]** 코드에 이미 있음 · **[신규]** 이 초안에서 제안 · **[결정 필요]** 팀이 정해야 함.

---

## 0. 공통 규칙

| 항목 | 규칙 |
| -- | -- |
| 형식 | 요청·응답 본문은 JSON(UTF-8). 스트리밍은 SSE(`text/event-stream`) |
| 세션 | 비회원은 브라우저가 만든 임의 값 `session_id`를 `X-Session-Id` 헤더로 보낸다. 사람을 식별하지 않는다. 서버 상태는 PostgreSQL에 session id 기준으로 둔다(11/08 결정: Redis 없음) |
| 대화 | `thread_id` = 대화 하나. 같은 값을 보내면 이전 턴을 이어서 답한다(LangGraph checkpointer 키) |
| 요청 ID | 서버가 질문 한 건마다 `request_id`(UUID)를 만든다. 로그·피드백·trace를 이 값으로 묶는다 |
| 시간 | 저장은 UTC(ISO 8601), 집계는 KST 날짜 |
| 표 ID | `table_key` = `"<org_id>_<tbl_id>"`. 표 ID·이름·링크는 서버 통계표 레지스트리에서만 나온다. 레지스트리에 없는 키는 422 |
| 내부 트래픽 | 평가 하네스는 `X-Internal: true` 헤더를 보낸다 → 로그에 `is_internal=true`, 사용자 지표에서 제외 |

### 오류 응답 (SSE가 아닌 엔드포인트)

```json
{ "error": { "code": "invalid_feedback", "message": "기타를 고르면 이유를 입력해야 합니다." } }
```

| 상태 | 쓰는 경우 |
| -- | -- |
| 401 | 로그인이 필요한 엔드포인트(2차 회원 기능, 운영자 화면) |
| 404 | 없거나 **남의** 대화·요청 ID (존재 여부를 알려 주지 않는다) |
| 410 | 비회원 세션 만료(30분 미사용) |
| 422 | 입력 검증 실패(빈 질문, 레지스트리에 없는 표 키, 선택지 조합 오류) |
| 503 | 에이전트 자원(Chroma·모델·키)을 아직 못 엶. 다음 요청에서 다시 시도 |

---

## 1. 엔드포인트 한눈에 보기

| 단계 | 메서드 · 경로 | 용도 | 관련 기능(Manyfast) |
| -- | -- | -- | -- |
| 1차 | `GET /health` **[현재]** | 서버 상태 | — |
| 1차 | `POST /chat/stream` **[현재 + 확장]** | 질문 → 진행·답 SSE | F-OMBGQM, F-JJWJTZ, F-ZTUGMI, F-GSBTLX, F-IKACRA |
| 1차 | `POST /feedback` **[신규]** | 👍/👎 · 선택지 · 이유 저장 | F-RPMLSR, F-WFJGDJ |
| 1차 | `GET /suggestions` **[신규]** | 추천 질문 카드 | F-VWIMTN |
| 1차 | `GET /faq` **[신규]** | FAQ 목록 | F-RTZMZZ |
| 1차 | `DELETE /threads/{thread_id}` **[신규]** | 비회원 대화 삭제(서버 상태) | F-EULYHK |
| 1차 | `POST /events` **[신규]** | 화면 이벤트 로그 일괄 전송 | F-TTIAJU |
| 1차 | `GET /admin/metrics` **[신규]** | 운영자 메트릭(팀 전용) | F-PVOWFN, F-CGNPEM 외 |
| 1차 | `GET /admin/feedback` **[신규]** | 👎 답 목록 + trace 링크 | F-RDOKFK |
| 2차 | `GET /auth/kakao/login` · `GET /auth/kakao/callback` · `POST /auth/logout` · `GET /me` **[신규]** | 카카오 로그인 | F-DCIQJH, F-JFIVUV |
| 2차 | `GET /conversations` · `GET /conversations/{id}` · `DELETE /conversations/{id}` **[신규]** | 회원 대화 목록·복원·삭제 | F-JVQZTL, F-EULYHK |
| 내부 | `POST /search` · `POST /search/stream` · `/nodes/*` (실전 2) | 검색 단독·노드별 개발용 | F-BTULHD, F-XRSCJK |

비회원 대화 **목록**은 서버에 없다. 브라우저 `sessionStorage`에만 둔다(정책 PL-BIWRBV). 그래서 비회원용 `GET /threads`는 만들지 않는다.

---

## 2. `POST /chat/stream` — 질문하고 답 받기

### 요청

```http
POST /chat/stream
Content-Type: application/json
X-Session-Id: 3f1c…   (비회원 필수, 회원은 쿠키로 대체)
```

```json
{
  "question": "최근 출생아 수가 얼마나 줄었어?",
  "thread_id": "c0ffee",
  "retry_of": null
}
```

| 필드 | 타입 | 필수 | 설명 |
| -- | -- | -- | -- |
| `question` | string(1~500자) | 예 | 빈 값이면 422. **[결정 필요]** 최대 길이(500자 제안) |
| `thread_id` | string | 아니요 | 없으면 새 대화. 있으면 이전 턴의 표·조건을 이어받는다 [현재] |
| `retry_of` | UUID | 아니요 | **[신규]** "다시 시도"로 보낼 때 원 요청의 `request_id`. 서버는 새 `request_id`를 만들고 원 요청과 연결한다 (F-ZTUGMI 추천안) |

### 응답 이벤트 순서

```
event: meta        ← [신규] 맨 처음 1번
event: <노드 이름>  ← [현재] 진행 단계마다 (표 찾기 단계들)
event: stat        ← [현재] 계산(실전 1)이 끝났을 때
event: result      ← [현재] 마지막 1번 (정상 종료)
event: error       ← [현재] 장애 종료 시 result 대신
```

**[결정 필요]** 문장 토큰 스트리밍(`event: token`)은 지금 코드에 없다. 검증기를 통과한 문장만 보내야 하므로(PL-QYJPEQ) 토큰 단위로 흘리면 검증 전 숫자가 보일 수 있다. 제안: **1차는 `result`에서 문장을 한 번에 보내고, 진행 이벤트로 기다림을 메운다.**

#### `meta` [신규]

```json
{ "request_id": "9b2e…", "thread_id": "c0ffee", "started_at": "2026-11-12T00:10:03Z" }
```

#### 진행 이벤트 [현재 · 이름 정리 필요]

지금은 실전 2 검색 노드 이름이 그대로 이벤트 이름이 된다(`data` = 그 노드의 trace와 `seconds`). FE가 노드 이름을 알 필요가 없도록 **[신규]** 공통 `progress` 이벤트를 함께 보내는 안을 제안한다.

```
event: progress
data: {"step": "search", "status": "done", "seconds": 3.2}
```

| `step` | 화면 문구(F-JJWJTZ) |
| -- | -- |
| `interpret` | 질문 이해하는 중 |
| `search` | 표 찾는 중 |
| `compute` | 계산하는 중 |
| `chart` | 차트 그리는 중 |
| `retry` | 다시 연결하는 중 / 다시 찾는 중 (재검색·재연결, 일반 진행과 구분) |

`status`: `running` | `done`. 첫 `progress`는 질문 후 2초 안에 보낸다(F-OMBGQM 표시 규칙).

#### `result` [현재 `ChatResult`]

판정은 `outcome` 4종으로 나뉜다. FE는 이 값으로 답 카드 종류를 고른다.

| `outcome` | 화면 | 숫자 | 피드백 |
| -- | -- | -- | -- |
| `answered` | 숫자 답 카드(문장 + 차트 + 출처) | 있음 | 받음 |
| `refused` | 거절·안내 카드(`no_answer_reason`별 문구) | **없음** | 받음 |
| `clarify` | 되묻기 카드(선택지) 또는 조건 보완 요청 | 없음 | 받음 |
| `failed` | 오류·다시 시도 카드 | 없음 | 받지 않음 **[결정 필요]** |

```json
{
  "request_id": "9b2e…",
  "question": "최근 출생아 수가 얼마나 줄었어?",
  "outcome": "answered",
  "no_answer": false,
  "no_answer_reason": null,
  "answer": "2023년 출생아 수는 23만 28명으로 2022년보다 7.7% 줄었습니다.",
  "answer_origin": "stat",
  "table_key": "101_DT_1B8000F",
  "picked": [ { "key": "101_DT_1B8000F", "title": "…", "survey": "…", "url": "https://kosis.kr/…" } ],
  "candidates": [ … ],
  "stat_answer": { … },
  "chart": { "$schema": "https://vega.github.io/schema/vega-lite/v5.json", … },
  "chart_type": null,
  "chart_note": null,
  "resolved_question": null,
  "interpreted_by": "rule",
  "reused_table": false,
  "degraded": false,
  "attempted_keys": ["101_DT_1B8000F"],
  "sub_results": null,
  "combined": null,
  "route": "found"
}
```

필드 설명(지금 코드 `src/kosis_agent/graph/state.py` `ChatResult` 기준):

| 필드 | 설명 | 기능 |
| -- | -- | -- |
| `request_id` | **[신규]** `meta`와 같은 값. 피드백을 이 값으로 보낸다 | F-WFJGDJ |
| `answer` | 검증기를 통과한 문장, 또는 코드 고정 문장 | F-GVVAVT |
| `answer_origin` | `stat` 실전 1 문장 · `refusal` 코드 거절 문장 · `chart` 차트만 다시 그린 턴 · `multi` 멀티쿼리 하위 답을 이은 문장 | |
| `picked` | 답이 **실제로 쓴** 표. 출처는 이것만 쓴다(후보 `candidates`는 출처에 넣지 않음) | F-XDXRJB |
| `chart` | Vega-Lite v5. 차트가 없으면 `null`. 값은 답 문장과 같은 계산 결과에서 나온다 | F-IUDWVF, F-KHTNMQ |
| `chart_type` · `chart_note` | 사용자가 요청한 차트 종류, 요청대로 못 그렸을 때 안내 | T3-27 |
| `resolved_question` · `interpreted_by` | 이월 질문을 채운 독립 질문, 해석 방식(`rule`·`llm`·`none`). "부산은?" → "2023년 부산 출생아 수" 표시용 | F-GSBTLX, F-JRXVYZ |
| `reused_table` | 지시어("그 표")로 직전 표를 다시 씀 | F-GSBTLX |
| `sub_results` · `combined` | 멀티쿼리 턴의 하위 답 목록과 코드가 합친 값 | T3-28 |
| `degraded` | 2차 리랭커 실패로 1차 순서로 대신 답함(운영 지표용, 화면 표시 안 함) | |

**[결정 필요]** `picked[]`에 출처 표시용 `survey`(조사명)·`url`(원표 링크)이 레지스트리에서 채워지는지 확인 필요. 출처 형식은 `KOSIS / 조사명 / 통계표명`.

**[결정 필요]** 되묻기(`clarify`)의 선택지 필드가 아직 없다. 제안: `clarify_options: [{ "label": "전국", "question": "2023년 전국 출생아 수" }]` — 누르면 `question`을 그대로 보낸다.

#### `error` [현재]

```json
{ "type": "TimeoutError", "detail": "…", "retryable": true, "request_id": "9b2e…" }
```

`retryable` **[신규]**: `true`면 FE가 자동 재연결 후 "다시 시도" 버튼을 보인다. 재연결 횟수는 정책 PL-KDMYOY를 따른다. 미완성 답은 확정하지 않는다.

### 예외

| 상황 | 처리 |
| -- | -- |
| 빈 질문 | 422, 화면 "질문을 입력해 주세요" |
| 비회원 세션 만료 후 같은 `thread_id` | **[결정 필요]** 410으로 거절 vs 새 대화로 시작하고 `meta.expired: true`. 제안: 후자(정책 "새 대화로 시작하고 '이전 대화가 만료되었습니다'") |
| 답 생성 중 같은 `thread_id`로 새 질문 | 앞 요청을 중단하고 `stream_abort` 로그 |
| 에이전트 자원 없음 | 503 (지금 코드 그대로) |

---

## 3. `POST /feedback` — 피드백 저장 [신규]

```json
{
  "request_id": "9b2e…",
  "value": "down",
  "choice": "wrong_number",
  "reason": "2023년 숫자가 KOSIS와 다릅니다"
}
```

| 필드 | 값 |
| -- | -- |
| `value` | `up` · `down` |
| `choice` | up: `accurate_number` 수치가 정확함 · `right_table` 원하는 표를 찾음 · `easy` 설명이 이해하기 쉬움 · `chart_helpful` 차트가 도움이 됨 · `other` 기타 / down: `wrong_number` 수치가 틀림 · `wrong_table` 표가 다름 · `hard` 이해하기 어려움 · `other` 기타 |
| `reason` | 0~200자. `choice=other`면 필수 |

- 응답: `200 { "feedback_id": "…", "updated": false }`
- 같은 `request_id`에 다시 보내면 마지막 값으로 바꾼다(`updated: true`).
- 값과 맞지 않는 선택지(예: `up` + `wrong_number`) → 422.
- 남의 `request_id`(다른 세션) → 404.
- `reason`은 저장 전에 전화번호·이메일 패턴을 가린다(PL-HQFIPG, PL-GXUBQX).

---

## 4. `GET /suggestions` · `GET /faq` [신규]

```json
GET /suggestions?limit=4
{ "items": [ { "id": "s1", "label": "출생아 수 추이", "question": "최근 10년 출생아 수 추이 보여줘" } ] }

GET /faq
{ "items": [ { "id": "f1", "question": "어떤 통계를 물어볼 수 있나요?", "answer": "…" } ] }
```

- 초기에는 시드 파일(F-AZGSUA)에서 읽는다. 관리자 화면은 만들지 않는다(PL-EQDDSH).
- 추천 질문은 클릭했을 때 거절·오류가 나는 비율이 0%여야 하므로, 시드 적재 때 골든셋이 아닌 검증용 실행으로 확인한다.
- **[결정 필요]** 카드 개수(4개 제안), 사용 기록이 쌓인 뒤 많이 쓰인 질문으로 바꾸는 주기.

---

## 5. `DELETE /threads/{thread_id}` — 비회원 대화 삭제 [신규]

- 헤더 `X-Session-Id`가 그 `thread_id`를 만든 세션과 같을 때만 지운다. 다르면 404.
- PostgreSQL의 체크포인트·대화 상태를 지운다. 브라우저는 `sessionStorage` 목록에서 항목을 지운다.
- 응답 `204`. 운영 로그(질문 원문·피드백)는 로그 보관 기간(30일)까지 남는다.

---

## 6. `POST /events` — 화면 이벤트 로그 [신규]

```json
{
  "events": [
    { "event_id": "uuid", "event_type": "citation_click", "ts": "2026-11-12T00:10:09Z",
      "request_id": "9b2e…", "thread_id": "c0ffee", "props": { "table_key": "101_DT_1B8000F", "rank": 1 },
      "log_version": 1 }
  ]
}
```

- 응답 `202 { "accepted": 1, "quarantined": 0 }`. 검사에 걸린 이벤트는 버리지 않고 사유와 함께 따로 저장한다(PL-FTYVRR).
- `event_type` 목록과 필드는 공통 이벤트 레코드 구조(F-QRZCBS)와 KOS-169 로그 스키마를 따른다. 예: `question_submit`, `answer_first_token`, `answer_done`, `stream_abort`, `citation_click`, `suggestion_click`, `faq_open`, `feedback_open`, `thread_delete`.
- 서버 이벤트(F-OIPLFF: 단계별 지연, 판정, 표 키)는 이 API를 거치지 않고 서버가 직접 적재한다.

---

## 7. 운영자 API [신규, 팀 전용]

```http
GET /admin/metrics?from=2026-11-23&to=2026-11-29&include_internal=false&tab=summary
GET /admin/feedback?value=down&from=…&to=…&cursor=…
```

- `tab`: `summary` 요약 · `quality` 답 품질 · `usage` 사용 패턴 · `perf` 성능·비용. 일별 집계 `daily_metrics`(F-CAQDLN)에서 읽는다.
- `/admin/feedback` 항목에는 질문 원문, 답, 선택지·이유, 표 키, `request_id`, Jaeger trace 링크가 들어간다.
- **[결정 필요]** 접근 방법: ① 팀 공용 토큰(`Authorization: Bearer`) ② IP 허용 목록 ③ 2차 카카오 로그인 + 운영자 목록. 제안: 1차는 ①.

---

## 8. 2차 — 카카오 로그인 · 회원 대화 [신규]

| 메서드 · 경로 | 설명 |
| -- | -- |
| `GET /auth/kakao/login` | 카카오 인가 페이지로 302 |
| `GET /auth/kakao/callback?code=…` | 토큰 교환 → PostgreSQL 세션 행 + httpOnly 쿠키 → 홈으로 302. 실패 시 로그인 실패 안내 화면으로 |
| `POST /auth/logout` | 세션 행 삭제, 쿠키 만료. 화면은 비회원 대화로 |
| `GET /me` | `{ "user_id": "…", "nickname": "…" }` 또는 401 |
| `GET /conversations?cursor=…` | 내 대화 목록(최근 순) `{ items: [{ id, title, updated_at }], next_cursor }` |
| `GET /conversations/{id}` | 메시지 목록(턴별 질문·`result`) → 이전 대화 복원 |
| `DELETE /conversations/{id}` | 대화와 메시지 연쇄 삭제, 204 |

- 로그인 없이 `/conversations` → 401. 남의 대화 ID → 404.
- 회원 대화는 사용자가 지우기 전까지 보관한다. 로그인 전 비회원 대화는 계정으로 옮기지 않는다(PL-BIWRBV).
- 회원의 `POST /chat/stream`은 `thread_id` = `conversations.id`를 쓰고, 턴이 끝날 때 `messages`에 질문과 `result`를 저장한다(지금 ORM `Message.result` 그대로).

---

## 9. 지금 코드와 다른 점 (중반 작업 목록)

| # | 바꿀 곳 | 내용 | 담당(제안) |
| -- | -- | -- | -- |
| 1 | `backend/app/main.py` | `meta` 이벤트, `request_id`, `retry_of`, `X-Session-Id`, `progress` 이벤트, `error.retryable` | 이선주 · 전민기 |
| 2 | `graph/state.py` `new_checkpointer` | InMemorySaver → PostgreSQL saver(KOS-121), 30분 만료 정리 배치 | 김병석 |
| 3 | `ChatResult` | `request_id`, `clarify_options`, `picked[].survey·url` 확인 | 전민기 |
| 4 | `kosis_store` | `feedback`, `events`, `events_quarantine`, `daily_metrics`, `sessions`(2차 `users`) 테이블 + Alembic | 임도유 · 김병석 |
| 5 | 신규 라우터 | `/feedback`, `/suggestions`, `/faq`, `/threads/{id}`, `/events`, `/admin/*` | 이선주 · 임도유 |
| 6 | `docker-compose.yml` | `redis` 서비스·`REDIS_URL` 제거(11/08 결정) | 김병석 |

## 10. 결정 필요 목록 (리뷰 때 정하기)

1. 문장 토큰 스트리밍 여부 — 제안: 1차는 안 함(검증 전 숫자 노출 방지)
2. `failed` 답에 피드백을 받을지 — 제안: 받지 않음
3. 되묻기 선택지 필드 형식 — 제안: `clarify_options[{label, question}]`
4. 비회원 세션 만료 후 같은 `thread_id` 처리 — 제안: 새 대화로 시작 + `meta.expired: true`
5. 질문 최대 길이 — 제안: 500자
6. 추천 질문 카드 개수 — 제안: 4개
7. 운영자 API 접근 방식 — 제안: 1차는 팀 공용 토큰
