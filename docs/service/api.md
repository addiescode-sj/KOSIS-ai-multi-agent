# REST API 초안 (KOS-155)

> 상태: **확정 · 리뷰 반영** (2026-11-23, 이선주 리뷰: 사용자용 대화 주소 `/chat`으로 통일 · 개발용 `/dev/*`로 구분)
> 기준: Manyfast `KOSIS_통계프로젝트` 11/19 기능 명세·와이어프레임(30쪽), 11/18 기획 결정, 코드 `7342c45`(main)
> 코드에서 뽑은 문서: [openapi.json](openapi.json) · [swagger.html](swagger.html)(브라우저로 열기). 아직 만들지 않은 API(로그인·회원 대화)는 이 문서에만 있다. API를 바꾸면 `scripts/export_openapi.py`를 다시 돌린다
> 경로: Web (Next.js) → REST API (FastAPI) → AI 파이프라인(LangGraph) · PostgreSQL(+pgvector)

이 문서는 중반 이슈(KOS-159 AI 파이프라인 · KOS-160 FE · KOS-161 DB query · KOS-162 인프라)가 같은 계약을 보고 일하도록 엔드포인트와 요청·응답 형식을 정한다.
표시 규칙: **[현재]** 코드에 이미 있음 · **[신규]** 이 초안에서 제안 · **[결정 필요]** 팀이 정해야 함.

### 11/12 초안에서 바뀐 것

| 항목 | 11/12 초안 | 지금 |
| -- | -- | -- |
| 추천 질문·FAQ | `GET /suggestions` · `GET /faqs` | **삭제** (11/18 결정, 나중 단계). 첫 화면은 입력창 예시 문구로 대신함 |
| 답 문장 흘려보내기 | 1차는 안 함 | **`token` 이벤트 사용** [현재]. 숫자 검증을 끝낸 문장만 어절 단위로 보냄 |
| `request_id` | 서버가 만드는 UUID | **OTel `trace_id`와 같은 값** (F-QRZCBS). `meta`의 `trace_id`가 곧 `request_id` |
| 진행 이벤트 | 공통 `progress` 이벤트 제안 | **코드 이름 그대로**(노드 이름 이벤트 + `stat`). 단계 묶음은 FE가 표(2장)로 처리. 재시도 중 표시만 `retry` 이벤트 신규 |
| 질문 길이 | 500자 제안 | **200자** (F-OMBGQM) |
| 비회원 세션 만료 | 30분 | **1시간** (11/18 결정) |
| 만료 뒤 같은 `thread_id` | 410 vs 새 대화 | **새 대화로 시작** + `meta.expired: true` (F-SGTERZ) |
| 멀티쿼리 차트 | 마지막 하위 답 하나만 | **하위 답마다** `sub_results[].chart` (11/18 결정) |
| 피드백 필드 | `choice` | **`category`** (F-RPMLSR 데이터 명세 이름), 분당 10회 제한(429) 추가 |
| 카카오 로그인 | 2차 | **1차(기능 동결 전 포함)** — 11/19 김병석 결정. F-DCIQJH·홈 와이어프레임(11/19)과 같음 |
| `/me` | `GET /me` | **`GET /auth/me`** (F-DCIQJH) |
| 운영자 화면 | `GET /admin/metrics` · `GET /admin/feedback` | **도구 결정 전까지 보류** (F-PVOWFN 추천안은 Grafana 같은 BI 도구 → DB 직접 조회라 API가 필요 없을 수 있음) |
| 회원 대화 주소 | `/conversations`, `/conversations/{id}` | **`/chat`, `/chat/{id}`** — 프론트 화면 주소(`/chat`, `/chat/[id]`)와 통일 (11/23 리뷰) |
| 대화 삭제 | 회원 `DELETE /conversations/{id}` · 비회원 `DELETE /threads/{id}` | **`DELETE /chat/{id}` 하나**. 서버가 로그인 쿠키로 회원·비회원을 가름 (11/23 리뷰) |
| 개발용 주소 | `POST /chat` · `/tools/*` · `GET /threads/{id}` | **`/dev/chat` · `/dev/tools/*` · `/dev/threads/{id}`** — 사용자용 `/chat`과 겹치지 않게, 배포에서는 `/dev/*`를 통째로 막음 (11/23 리뷰) |

---

## 0. 공통 규칙

| 항목 | 규칙 |
| -- | -- |
| 형식 | 요청·응답 본문은 JSON(UTF-8). 스트리밍은 SSE(`text/event-stream`) |
| 세션 | 비회원은 브라우저가 만든 임의 UUID `session_id`를 `X-Session-Id` 헤더로 보낸다. 사람을 식별하지 않는다. 회원은 httpOnly 쿠키 세션 |
| 대화 | `thread_id` = 대화 하나(UUID v4). 같은 값을 보내면 이전 턴을 이어서 답한다(LangGraph checkpointer 키). 회원은 `thread_id` = `conversations.id` |
| 요청 ID | 질문 한 건 = `request_id`. **OTel trace ID(32자리 hex)와 같은 값**이라 Jaeger에서 바로 찾을 수 있다. 로그·피드백·trace를 이 값으로 묶는다 |
| 시간 | 저장은 UTC(ISO 8601), 집계는 KST 날짜 |
| 표 ID | `table_key` = `"<org_id>_<tbl_id>"`. 표 ID·조사명·표 이름·링크는 서버 통계표 레지스트리에서만 나온다. 레지스트리에 없는 키는 422 |
| 내부 트래픽 | 평가 하네스·팀원은 `X-Internal: true` 헤더를 보낸다(T3-48의 `X-Kosis-Internal`도 받음) → 로그에 `is_internal=true`, 사용자 지표에서 제외. **[결정 필요]** F-PVOWFN 추천안은 IP 대역 식별 |
| 비회원 세션 | 마지막 사용 후 **1시간** 지나면 만료(PL-BIWRBV). 만료 판정은 세션 테이블 `last_used_at` |

### 오류 응답 (SSE가 아닌 엔드포인트)

```json
{ "error": { "code": "invalid_feedback", "message": "기타를 고르면 이유를 입력해야 합니다." } }
```

| 상태 | 쓰는 경우 |
| -- | -- |
| 401 | 로그인이 필요한 엔드포인트(회원 대화) |
| 404 | 없거나 **남의** 대화·요청 ID (존재 여부를 알려 주지 않는다) |
| 409 | 꺼진 노드 호출(개발용 `/dev/nodes/*`) |
| 422 | 입력 검증 실패(빈 질문, 200자 초과, 레지스트리에 없는 표 키, 선택지 조합 오류, 선택지 누락) |
| 429 | 요청 제한 초과(피드백: 같은 IP 분당 10회) |
| 503 | 에이전트 자원(벡터 DB PostgreSQL+pgvector·모델·키)을 아직 못 엶. 다음 요청에서 다시 시도. `/health`는 이때도 200 |

입력 검증 오류는 FastAPI 기본값대로 **422 하나로 통일**한다. F-WFJGDJ의 "선택지 없이 제출하면 400"도 422로 맞추자는 제안이다(**[결정 필요]**, 아래 10장).

---

## 1. 엔드포인트 한눈에 보기

| 단계 | 메서드 · 경로 | 용도 | 관련 기능(Manyfast) |
| -- | -- | -- | -- |
| 1차 | `GET /health` **[현재]** | 서버 상태 | F-BTULHD |
| 1차 | `POST /chat/stream` **[현재 + 확장]** | 질문 → 진행·문장·답 SSE | F-OMBGQM, F-JJWJTZ, F-ZTUGMI, F-DWYZEC, F-VJBKPX, F-QWCVPP, F-IKACRA, F-GSBTLX, F-JRXVYZ, F-SGTERZ |
| 1차 | `POST /feedback` **[신규]** | 👍/👎 · 선택지 · 이유 저장 | F-RPMLSR, F-WFJGDJ |
| 1차 | `DELETE /chat/{id}` **[신규]** | 대화 삭제(회원·비회원 공통, 4장) | F-EULYHK |
| 1차 | `POST /events` **[신규]** | 화면 이벤트 로그 일괄 전송 | F-TTIAJU, F-QRZCBS |
| 1차 | `GET /auth/kakao/login` · `GET /auth/kakao/callback` · `POST /auth/logout` · `GET /auth/me` **[신규]** | 카카오 로그인 | F-DCIQJH, F-JFIVUV |
| 1차 | `GET /chat` · `GET /chat/{id}` **[신규]** | 회원 대화 목록·복원 | F-JVQZTL |
| 보류 | `GET /admin/metrics` · `GET /admin/feedback` | 운영자 메트릭 | F-PVOWFN, F-CGNPEM, F-RDOKFK, F-JNISZQ, F-BDKPCR |
| 개발 | `POST /dev/chat` · `POST /dev/tools/{search_tables,summarize_stat,make_chart}` · `GET /dev/threads/{thread_id}` **[현재, 이름 변경]** | 스트림 없는 답, 툴 단독 실행, 대화 상태 조회 | 평가·디버깅용, 표 검색 화면 |
| 개발 | `POST /dev/search` · `POST /dev/search/stream` · `/dev/nodes/*` (실전 2, 종합 백엔드에는 아직 없음) | 검색 단독·노드별 | F-BTULHD, F-XRSCJK |

- 사용자용 대화 주소는 프론트 화면 주소와 같은 `/chat` 하나로 쓴다. `POST /chat/stream`(질문)과 `GET·DELETE /chat/{id}`(대화)는 메서드가 달라 겹치지 않는다. 대화 ID는 UUID라 `stream`과 섞일 일이 없다.
- 비회원 대화 **목록**은 서버에 없다. 브라우저 `sessionStorage`에만 둔다(F-LXYAOS, PL-BIWRBV). 비회원이 `GET /chat`을 부르면 401이다. 사이드바 항목을 눌렀을 때 보여 줄 질문·답·차트·출처도 `sessionStorage`에 저장한 `result`로 다시 그린다.
- 개발용 엔드포인트는 모두 `/dev/` 아래에 두고 배포 환경에서는 `/dev/*`를 통째로 막는다(F-BTULHD 추천안 "개발 환경에서만 유지", 11/23 결정). 표 검색 화면(`/search`)도 개발 환경에서만 쓴다.

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
  "question": "2024년 출생아 수와 사망자 수 알려 줘",
  "thread_id": "8a1d…",
  "retry_of": null,
  "input_method": "direct"
}
```

| 필드 | 타입 | 필수 | 설명 |
| -- | -- | -- | -- |
| `question` | string(1~200자) | 예 | 빈 값·200자 초과면 422. 지금 코드는 최소 1자만 검사 → **최대 200자 추가** |
| `thread_id` | string | 아니요 | 없으면 새 대화(서버가 만들어 `meta`로 돌려줌). 있으면 이전 턴의 표·조건을 이어받는다 [현재] |
| `retry_of` | string | 아니요 | **[신규]** "다시 시도"로 보낼 때 원 요청의 `request_id`. 서버는 새 `request_id`를 만들고 원 요청과 연결한다(F-ZTUGMI) |
| `input_method` | `direct` \| `clarify_option` | 아니요 | **[신규]** 직접 입력 / 되묻기 선택지 버튼. 서버 이벤트 로그용(F-TTIAJU) |

### 응답 이벤트 순서 [현재]

```
event: meta         ← 맨 처음 1번
event: <노드 이름>   ← 표 찾기 단계가 끝날 때마다 (실전 2 검색 노드)
event: stat         ← 계산(실전 1)이 끝났을 때 (표마다)
event: retry        ← [신규] 재검색·재계산·재연결을 시작할 때 (있을 때만)
event: token        ← 검증을 끝낸 답 문장을 어절 단위로
event: result       ← 마지막 1번 (정상 종료)
event: error        ← 장애 종료 시 result 대신
```

#### `meta` [현재 + 확장]

```json
{ "thread_id": "8a1d…", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "expired": false }
```

- `trace_id` = 이 질문의 `request_id`. 피드백과 이벤트 로그에 이 값을 쓴다.
- `expired` **[신규]**: 보낸 `thread_id`의 비회원 세션이 1시간이 지나 만료됐으면 `true`. 이때 서버는 **새 `thread_id`로 새 대화를 시작**하고, FE는 "이전 대화가 만료되었습니다"를 보여 준다(F-SGTERZ). 만료는 첫 전송에만 알리고 이후는 새 대화의 이어지는 턴이다.

#### 노드 이름 이벤트 [현재]

실전 2 검색 노드가 끝날 때마다 그 노드 이름이 이벤트 이름이 되고, `data`는 그 노드의 trace 항목(`seconds` 포함)이다. FE는 아래 표로 화면 단계(F-JJWJTZ: 질문 이해 → 표 찾기 → 계산 → 차트)에 묶는다.

| 이벤트 이름 | 화면 단계 |
| -- | -- |
| `parse`, `condition` | 질문 이해 |
| `embed`, `retrieve_*`, `fuse`, `rerank1`, `rerank2`, `gate` | 표 찾기 |
| `stat` | 계산 (`{seconds, key, spans}`, 표마다 한 번) |
| (`result.chart`가 있으면) | 차트 — 차트가 없는 답은 차트 단계를 표시하지 않는다 |

- 화면은 **단계 이름 목록 + 끝난 단계에 완료 체크**로 보여 준다. 첫 진행 이벤트는 질문 후 2초 안에 뜬다.
- 지시어로 직전 표를 다시 쓴 턴(`reused_table`)은 검색 노드 이벤트 없이 `stat`부터 온다.
- `embed`·`rerank1`·`rerank2`·`retrieve_hq*`는 설정에 따라 없을 수 있다. FE는 모르는 이름도 "표 찾기"로 처리한다.

#### `retry` [신규 · 이름 제안]

F-JJWJTZ는 "재검색·재연결 중임을 일반 진행과 구분해 보여 준다"고 하고, 이벤트 이름은 개발팀이 정하라고 남겨 두었다. 제안:

```json
{ "stage": "search", "attempt": 2, "max": 3, "reason": "coverage_missing" }
```

| `stage` | 화면 문구 | 상한(PL-KDMYOY) |
| -- | -- | -- |
| `search` | 다시 찾는 중 | 하위 질문마다 3회 |
| `plan` | 질문을 다시 나누는 중 | 전체 계획 보완 2회 |
| `compute` · `chart` · `answer` | 다시 계산하는 중 / 차트 다시 그리는 중 / 답 다시 쓰는 중 | 각 2회 |
| `connect` | 다시 연결하는 중 | **[결정 필요]** 연결 자동 재시도 횟수(추천 1회). FE가 스트림 끊김을 감지해 직접 표시 |

`connect`는 서버가 보내는 이벤트가 아니라 FE 상태다(스트림이 끊기면 서버는 보낼 수 없다).

#### `token` [현재]

```json
{ "delta": "2024년 " }
```

실전 1이 숫자 검증을 끝낸 문장만 어절 단위로 흘린다(검증 전 LLM 출력이 아니다). 화면의 최종 답은 `result`로 바꾼다.

#### `result` [현재 `ChatResult` + 확장]

판정은 `outcome` 6종이다(11/18 확정). FE는 이 값으로 답 카드(와이어프레임 화면)를 고른다.

| `outcome` | 화면(와이어프레임) | 숫자 | 차트 | 피드백 |
| -- | -- | -- | -- | -- |
| `answered` | 숫자 답변 화면(문장 + 차트 + 출처) | 있음 | 값 2개 이상일 때 | 받음 |
| `partial` **[신규]** | 부분 답변 화면("일부만 답변했어요" + 답한 부분 + `limits`) | 답한 부분만(검증 통과) | 답한 부분에서 | 받음 |
| `general` **[신규]** | 일반 답변 화면(숫자 없는 안내 문장, 통계 질문 예시를 문장 안에) | **없음** | 없음 | 받음 |
| `refused` | 자료 없음 안내 / 조건 불일치 / 인과·예측 거절 화면(코드 고정 문구) | **없음** | 없음 | 받음 |
| `clarify` | 되묻기 답변 화면(선택지 버튼) 또는 조건 보완 요청 화면 | 없음 | 없음 | 받음 |
| `failed` | 오류·다시 시도 안내 화면 | 없음 | 없음 | 받지 않음 **[결정 필요 · 제안값]** |

- 지금 코드 `outcome`은 4종(answered·refused·clarify·failed)이라 **`partial`·`general` 추가가 필요하다**.
- 거절 화면 3개 구분: `no_answer_reason`의 사유 코드(실전 1 `RefusalReason`, 실전 2 검색 거절 사유)로 나눈다.

  | 화면 | 사유 코드 |
  | -- | -- |
  | 자료 없음 안내 | `out_of_scope`, 실전 2 검색 거절(맞는 표 없음) |
  | 조건 불일치 | `no_period`, `no_item`, `class_mismatch`, `unit_mismatch`, `not_aggregatable`, `missing_value` |
  | 인과·예측 거절 | `causal` (예측 질문도 여기에 묶음, 11/09 결정) |
- 차트가 없는 답은 차트 영역을 숨긴다(`chart: null`). 차트를 못 그려도 검증된 숫자가 있으면 문장과 출처로 답한다.
- `general` 답 문장에 숫자가 들어 있으면 그 문장 대신 코드 고정 안내 문구를 보낸다. LLM 호출이 실패해도 고정 문구로 답한다.

```json
{
  "request_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "question": "2023년 합계출산율은?",
  "outcome": "answered",
  "no_answer": false,
  "no_answer_reason": null,
  "answer": "2023년 합계출산율은 0.72명으로 2022년보다 0.06명 줄었습니다.",
  "answer_origin": "stat",
  "table_key": "101_DT_1B81A12",
  "picked": [ { "key": "101_DT_1B81A12", "title": "…", "survey": "…", "url": "https://kosis.kr/…" } ],
  "candidates": [ … ],
  "stat_answer": { … },
  "chart": { "$schema": "https://vega.github.io/schema/vega-lite/v5.json", … },
  "chart_type": null,
  "chart_note": null,
  "resolved_question": null,
  "interpreted_by": "rule",
  "reused_table": false,
  "degraded": false,
  "attempted_keys": ["101_DT_1B81A12"],
  "limits": [],
  "clarify_options": [],
  "sub_results": null,
  "combined": null,
  "route": "found"
}
```

필드 설명(코드 `src/kosis_agent/graph/state.py` `ChatResult` 기준):

| 필드 | 설명 | 기능 |
| -- | -- | -- |
| `request_id` | **[신규]** `meta.trace_id`와 같은 값. 피드백을 이 값으로 보낸다 | F-WFJGDJ, F-QRZCBS |
| `answer` | 검증기를 통과한 문장, 또는 코드 고정 문장 | F-GVVAVT |
| `answer_origin` | `stat` 실전 1 문장 · `refusal` 코드 거절 문장 · `chart` 차트만 다시 그린 턴 · `multi` 멀티쿼리 하위 답을 이은 문장 · `general` **[신규]** 역할 기반 일반 답변 | |
| `picked` | 답이 **실제로 쓴** 표. 출처는 이것만 쓴다(후보 `candidates`는 출처에 넣지 않음). `general`이면 빈 목록 | F-XDXRJB |
| `chart` | Vega-Lite v5. 차트가 없으면 `null`. 값은 답 문장과 같은 계산 결과에서 나온다 | F-IUDWVF, F-KHTNMQ |
| `chart_type` · `chart_note` | 사용자가 요청한 차트 종류, 요청대로 못 그렸을 때 안내 | T3-27 |
| `resolved_question` · `interpreted_by` | 이월 질문을 채운 독립 질문, 해석 방식(`rule`·`llm`·`none`). "앞에서 본 ○○표 기준" 한 줄 표시용 | F-GSBTLX, F-JRXVYZ |
| `reused_table` | 지시어("그 표")로 직전 표를 다시 씀 | F-GSBTLX |
| `limits` | **[신규]** `partial`일 때 답하지 못한 부분과 이유 `[{part, reason}]`. 다른 결과면 빈 목록 | F-VJBKPX |
| `clarify_options` | **[신규]** 되묻기 선택지 `[{label, question}]`. 후보가 없으면 빈 목록 | F-JRXVYZ |
| `sub_results` · `combined` | 멀티쿼리 턴의 하위 답 목록과 코드가 합친 값 | F-DWYZEC |
| `degraded` | 2차 리랭커 실패로 1차 순서로 대신 답함(운영 지표용, 화면 표시 안 함) | |

**출처 형식**: `KOSIS / 조사명 / 통계표명` + 원표 링크(새 탭). `picked[].survey`(조사명)·`url`(원표 링크)은 레지스트리에서 채운다. 답이 표 두 개 이상을 썼으면 표마다 따로 적는다. **[확인 필요]** 지금 `picked`에 두 값이 채워지는지.

**되묻기 선택지** [신규]: `clarify_options: [{ "label": "전국", "question": "2023년 전국 출생아 수" }]`. 버튼을 누르면 `question`을 새 질문으로 그대로 보낸다(`input_method: "clarify_option"`). 지시어 해석은 같은 `thread_id` 안에서 최근 3턴까지 본다.

**부분 답 한계** [신규]: `limits: [{ "part": "사망자 수", "reason": "2024년 자료가 없습니다. 2015~2023년 자료만 있습니다." }]`. 한계 문구에는 레지스트리의 연도 범위 말고 숫자를 쓰지 않는다. 검증된 부분이 하나도 없으면 `partial`이 아니라 `refused`·`clarify`·`failed`로 끝낸다.

**조건 보완 요청**(`clarify`, 해석 실패): 신뢰할 수 없는 후보 표 이름은 보내지 않는다(`clarify_options` 빈 목록). FE는 기존 질문을 입력창에 남기고, 고쳐 보낸 질문은 새 턴으로 처리한다(F-IKACRA). 해석 실패는 `no_answer_reason`으로 자료 없음 거절·서버 장애와 구분한다.

#### 멀티쿼리 답 (`answer_origin = "multi"`)

```json
"sub_results": [
  { "question": "2024년 출생아 수", "table_key": "101_DT_1B8000F", "outcome": "answered",
    "reason": null, "text": "…", "answer": { … }, "chart": { … } },
  { "question": "2024년 사망자 수", "table_key": null, "outcome": "refused",
    "reason": "no_period", "text": "…", "answer": null, "chart": null }
]
```

- 하위 답마다 `chart`를 따로 보낸다(**지금 코드는 하나만 → 추가 필요**). 단위가 다른 두 답을 한 차트에 섞지 않는다.
- 하위 질문 중 하나라도 거절되면 전체 `outcome`은 `partial`이다.
- 출처는 하위 답마다 붙인다. 최상위 `chart`는 멀티쿼리 턴에서 `null`로 두고 FE는 `sub_results[].chart`만 그리는 안을 제안한다.
- **[결정 필요]** 하위 질문 최대 개수(추천 3개, F-DWYZEC 질문 칸).

#### `error` [현재 + 확장]

```json
{ "type": "TimeoutError", "detail": "…", "retryable": true, "request_id": "4bf92f…" }
```

- `retryable`·`request_id` **[신규]**: `true`면 FE가 자동 재연결(정책 상한 안에서) 뒤 "다시 시도" 버튼을 보인다. 재연결을 소진하면 "응답을 마치지 못했습니다. 잠시 뒤 다시 시도해 주세요."
- 최종 `result`를 받기 전에 끝난 응답은 완료된 답으로 확정하지 않는다. 이미 받은 `token` 문장은 미완성 경고와 함께 둔다(F-ZTUGMI).
- 입력 오류(422)는 자동 재연결 대상이 아니다. 연결 장애를 "자료 없음"으로 바꾸어 거절하지 않는다.

### 예외

| 상황 | 처리 |
| -- | -- |
| 빈 질문 · 200자 초과 | 422, 화면 "질문을 입력해 주세요" (200자 초과는 FE 입력 단계에서 막음) |
| 비회원 세션 만료 후 같은 `thread_id` | 새 대화로 시작하고 `meta.expired: true`, 새 `thread_id` |
| 답 생성 중 새 질문 | 와이어프레임·F-JJWJTZ 결정: **답이 끝날 때까지 전송 버튼 비활성화**. 창을 닫으면 FE가 `stream_abort` 로그 |
| 답 생성 중 "새 대화" 버튼 | 답이 끝난 뒤 새 대화로 전환(F-IRBJCC) |
| 에이전트 자원 없음 | 503 (지금 코드 그대로) |
| 한 질문 총 실행 시간 상한 초과 | **[결정 필요]** 추천 30초. 넘으면 검증된 부분까지만 답(`partial`) |

---

## 3. `POST /feedback` — 피드백 저장 [신규]

```json
{
  "request_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "value": "down",
  "category": "wrong_number",
  "reason": "2023년 숫자가 KOSIS와 다릅니다"
}
```

| 필드 | 값 |
| -- | -- |
| `value` | `up` · `down` (필수) |
| `category` | up: `accurate_number` 수치가 정확함 · `right_table` 원하는 표를 찾음 · `easy` 설명이 이해하기 쉬움 · `chart_helpful` 차트가 도움이 됨 · `other` 기타 / down: `wrong_number` 수치가 틀림 · `wrong_table` 표가 다름 · `hard` 이해하기 어려움 · `other` 기타 |
| `reason` | 0~200자, 선택. `category=other`면 필수 |

- 응답: `200 { "feedback_id": "…", "updated": false }`
- 같은 `request_id`에 다시 보내면 마지막 값으로 바꾼다(`updated: true`).
- 값과 맞지 않는 선택지(예: `up` + `wrong_number`) → 422.
- 남의 `request_id`(다른 세션) → 404. 비회원 `request_id`는 **24시간** 동안 피드백을 받는다(F-WFJGDJ).
- 같은 IP에서 분당 10회 넘게 보내면 429.
- `reason`은 저장 전에 전화번호·이메일 패턴을 가린다(PL-HQFIPG, PL-GXUBQX).
- 거절·되묻기·일반·부분 답에도 받는다. `failed`는 받지 않는 안(제안값).
- **[결정 필요]** 선택지 없이 창을 닫았을 때: F-RPMLSR은 "창을 닫아도 👍/👎 값은 저장", F-WFJGDJ는 "선택지 없이 제출하면 거부"로 서로 다르다. 제안: `category: null`을 허용해 값만 저장하고, `null`은 "선택지 없음"으로 집계한다.

---

## 4. `DELETE /chat/{id}` — 대화 삭제 (회원·비회원 공통) [신규]

`{id}`는 `thread_id`다(회원은 `thread_id` = `conversations.id`라 같은 값). 서버가 요청을 보고 둘 중 하나로 처리한다.

| 요청 | 지우는 것 | 권한 확인 |
| -- | -- | -- |
| 로그인 쿠키 있음(회원) | 대화와 턴 연쇄 삭제(hard delete) + 체크포인트 | 내 대화가 아니면 404 |
| 쿠키 없음(비회원) | PostgreSQL의 체크포인트·세션 행(이전 턴). 브라우저는 `sessionStorage` 목록에서 항목을 지운다 | 헤더 `X-Session-Id`가 그 `thread_id`를 만든 세션과 다르면 404 |

- 응답 `204`. 운영 로그(질문 원문·피드백 이유)는 로그 보관 기간(**90일**, 11/23 김병석 결정 — T3-48 로그 설계와 같음)까지 남는다(삭제 확인 창·개인정보 안내에 적음). 90일이 지나면 정리 배치(`python -m kosis_store.cleanup`)가 원문만 비운다.

---

## 5. `POST /events` — 화면 이벤트 로그 [신규]

```json
{
  "events": [
    { "event_id": "uuid", "event_type": "citation_click", "ts": "2026-11-19T00:10:09.120Z",
      "session_id": "3f1c…", "thread_id": "8a1d…", "turn": 1, "request_id": "4bf92f…",
      "screen_id": "n18", "feature_id": "F-XDXRJB",
      "table_keys": ["101_DT_1B81A12"], "latency_ms": null, "is_internal": false,
      "props": { "table_key": "101_DT_1B81A12", "rank": 1 }, "log_version": 1 }
  ]
}
```

- 응답 `202 { "accepted": 1, "rejected": 0 }`. 서버 수신 시각을 함께 저장한다. 검사에 걸린 이벤트는 **사유별 건수만** 남기고 내용은 저장하지 않는다(개인정보, `ingest_rejects`, T3-48 — 11/23 김병석 결정으로 코드 기준 유지).
- 같은 `event_id`가 다시 오면 처음 것을 유지한다(중복 무시).
- 전송은 백그라운드로 하고, 실패해도 답에 영향을 주지 않는다(FE는 버리고 건수만 센다).
- 공통 필드는 F-QRZCBS와 KOS-169 로그 스키마를 따른다. IP·기기 식별자·이름·연락처는 넣지 않는다.

| `event_type` | `props` |
| -- | -- |
| `citation_click` | `table_key`, `rank` |
| `copy_answer` | (공통 필드만) |
| `stream_abort` | `elapsed_ms` |
| `feedback` | `value`, 선택지, `reason`(가린 값) |

- **화면 이벤트는 위 4종**이다(11/23 김병석 결정, T3-48 범위와 같음). 질문 전송·첫 글자·답 완료 시각과 입력 방식은 서버가 `request_logs`에 이미 남기므로 화면에서 다시 보내지 않는다. 페이지·차트 보기와 `history_opened`는 1차에서 받지 않는다.
- `feedback` 이벤트의 선택지 필드 이름·코드는 지금 `choice`·`accurate`·`found_table`(T3-48)이고 `POST /feedback`의 `category` 코드(3장)로 맞출 예정이다(PR #37 병합 뒤, T3-51 K2). 피드백 저장의 기준은 `POST /feedback`이다.

- 11/12 초안의 `suggestion_click`·`faq_open`은 추천 질문·FAQ 삭제로 뺐다.
- 서버 이벤트(F-OIPLFF: `node_done`, `tool_call`, `llm_call`, `answer_result`, `error`)는 이 API를 거치지 않고 서버가 직접 적재한다.

---

## 6. 카카오 로그인 · 회원 대화 [신규 · 1차]

**11/19 결정: 카카오 로그인과 회원 대화는 기능 동결(11/22) 전에 넣는다.** F-DCIQJH 규칙, 11/19 홈 와이어프레임("로그인 후 대화 시작"·"카카오로 로그인" 버튼)과 같다. Manyfast R-GYWJRI·PL-EQDDSH·PRD 위험 칸의 "2차, 미정" 문구는 이 결정에 맞춰 고친다.

동결까지 기간이 짧으므로 카카오 앱 등록(REST API 키, Redirect URI를 로컬·배포 주소 둘 다 등록)과 비밀값(`KAKAO_CLIENT_ID`·`KAKAO_CLIENT_SECRET`·쿠키 서명 키) 주입을 먼저 끝낸다. 비밀값은 배포 환경에서 Secrets Manager로 넣는다(KOS-162).

| 메서드 · 경로 | 설명 |
| -- | -- |
| `GET /auth/kakao/login` | 카카오 동의 화면으로 302 |
| `GET /auth/kakao/callback?code=…` | 토큰 교환 → `users`(카카오 회원번호·닉네임만, 이메일 없음) 확인·생성 → PostgreSQL 세션 행 + httpOnly 쿠키 → 원래 화면으로 302. 동의 취소·카카오 오류면 원래 화면에 "로그인하지 못했습니다", 비회원으로 계속 사용 |
| `POST /auth/logout` | **현재 기기의** 세션 행 삭제, 쿠키 만료, 204. 세션이 이미 없어도 204. 다른 기기 세션은 유지 |
| `GET /auth/me` | `{ "user_id": "…", "nickname": "…" }` 또는 401 |
| `GET /chat?cursor=…` | 내 대화 목록(최근 순) `{ items: [{ id, title, created_at, updated_at }], next_cursor }`. `title`은 첫 질문 일부(F-JVQZTL은 AI 생성 제목도 언급 → **[결정 필요]**) |
| `GET /chat/{id}` | 턴 목록 `{ id, title, turns: [{ turn, question, result, created_at }] }` → 질문·답·차트·출처 복원(차트 spec은 `result`에 저장됨) |
| `DELETE /chat/{id}` | 대화와 턴 연쇄 삭제(hard delete), 204. 비회원과 같은 주소(4장) |

- 로그인 없이 `GET /chat`·`GET /chat/{id}` → 401. 남의 대화 ID → 404.
- DB 테이블 이름 `conversations`는 그대로 둔다(주소만 `/chat`).
- 로그인 전 비회원 대화는 계정으로 옮기지 않는다(PL-BIWRBV, 로그인 화면에 안내).
- 회원의 `POST /chat/stream`은 `thread_id` = `conversations.id`를 쓰고, 턴이 끝날 때 질문과 `result`를 저장한다. 출처 표시는 저장된 표 키로 레지스트리를 다시 조회한다(F-XDXRJB).
- 로그인 장애가 질문·답변을 막으면 안 된다(비회원 경로는 항상 동작).
- 회원 대화 보관: **90일** (11/19 결정). 90일이 지난 대화와 턴은 매일 정리 배치가 함께 지우고, 그 전에 사용자가 지우면 바로 지운다. 90일을 세는 기준은 마지막으로 이어 물은 시각(`updated_at`)을 제안한다. 로그인 화면·개인정보 안내에 90일 보관을 적는다.
- **[결정 필요]** 대화 수 상한: F-IRBJCC "한 사용자 최대 10개". 넘을 때 동작(가장 오래된 것 자동 삭제 / 새 대화 막기)이 없다.

---

## 7. 운영자 메트릭 [보류]

F-PVOWFN의 도구 질문(프론트 관리 페이지 / Metabase·Grafana 같은 BI 도구 / Streamlit)이 아직 열려 있고 추천안은 BI 도구다. 인프라(KOS-140)가 Grafana를 띄우므로 **Grafana가 `daily_metrics`와 👎 목록을 DB에서 직접 읽는 안**을 제안한다. 그러면 이 API는 만들지 않는다.

프론트 관리 페이지로 정하면 아래 형식으로 만든다.

```http
GET /admin/metrics?from=2026-11-23&to=2026-11-29&user_type=all&include_internal=false&tab=summary
GET /admin/feedback?value=down&from=…&to=…&cursor=…
```

- `tab`: `summary` 요약 · `quality` 답 품질 · `usage` 사용 패턴 · `perf` 성능·비용. 일별 집계 `daily_metrics`(F-CAQDLN)에서 읽는다. 기본 기간은 최근 7일.
- `/admin/feedback` 항목: 날짜, 가린 질문 원문(보관 기간이 지나면 빈 값), 쓴 표, 결과, 선택지·이유, `request_id`, Jaeger trace 링크.
- 접근: F-PVOWFN 추천안은 팀원 로그인. 1차는 팀 공용 토큰(`Authorization: Bearer`)을 제안한다. **[결정 필요]**

---

## 8. 개발용 엔드포인트 [현재 · `/dev/`로 이름 변경]

| 지금 코드 | 바꾼 이름 | 설명 |
| -- | -- | -- |
| `POST /chat` | `POST /dev/chat` | `/chat/stream`과 같은 그래프·같은 답을 스트림 없이 `{thread_id, trace_id, result}`로 |
| `POST /tools/search_tables` · `/tools/summarize_stat` · `/tools/make_chart` | `POST /dev/tools/…` | 툴 하나를 단독 실행(요청마다 trace 하나) |
| `GET /threads/{thread_id}` | `GET /dev/threads/{thread_id}` | checkpointer에 저장된 대화 상태(메시지·지금 보는 표). **세션 검사가 없어** 배포 환경에 그대로 두면 남의 대화를 볼 수 있다 → `/dev/*`는 배포에서 막는다 |

배포에서 `/dev/*`를 막으면 지금 프론트 두 곳이 깨지므로 같이 바꾼다(9장 6-3).

| 프론트 코드 | 지금 부르는 주소 | 바꿀 내용 |
| -- | -- | -- |
| `frontend/app/api/chat/route.ts` `threadLost()` | `GET /threads/{id}` (404면 만료로 봄) | 개발용 주소를 부르지 않고 `/chat/stream`의 `meta.expired`로 만료를 판단 |
| `frontend/lib/search/search-tables.server.ts` (표 검색 화면 `/search`) | `POST /tools/search_tables` | `POST /dev/tools/search_tables`. 표 검색 화면은 개발 환경에서만 쓴다 |

---

## 9. 지금 코드와 다른 점 (중반 작업 목록)

| # | 바꿀 곳 | 내용 | 담당(제안) |
| -- | -- | -- | -- |
| 1 | `backend/app/main.py` `ChatRequest` | `question` 최대 200자, `retry_of`, `input_method`, `X-Session-Id` 헤더 | ✅ T3-51 (옛 본문 `session_id`도 받음) |
| 2 | `backend/app/main.py` `/chat/stream` | `meta.expired`, `retry` 이벤트, `error.retryable`·`request_id`, `result.request_id`(= trace_id) | ✅ T3-51 (`retry` 이벤트만 그래프 담당 3-1로 남음) |
| 3 | `graph/state.py` `ChatResult` | `outcome`에 `partial`·`general`, `answer_origin`에 `general`, `limits`, `clarify_options`, `sub_results[].chart`, `picked[].survey·url` 확인 | 전민기 |
| 3-1 | 그래프 | 통계 외 질문 → 일반 답변 경로(숫자 검사 포함), 부분 답변 종료 경로, 재시도 상한(PL-KDMYOY) | 전민기 · 이선주 |
| 4 | `graph/state.py` `new_checkpointer` | InMemorySaver → PostgreSQL saver(KOS-121), 세션 테이블 `last_used_at`, 1시간 만료 정리 배치 | ✅ T3-51 (`postgres_checkpointer`, `chat_sessions`·`chat_requests`, `python -m kosis_store.cleanup`). 평가 `build_agent()`는 메모리 그대로 |
| 5 | `kosis_store` | `feedback`, `events`, `events_quarantine`, `daily_metrics`, `sessions`, `users` 테이블 + Alembic. 회원 대화(지금 `conversations`·`messages`에 `user_id`·`title`·`updated_at` 추가. F-JVQZTL의 `conversation_turns` 이름과 맞출지 결정) | 임도유 · 김병석 |
| 6 | 신규 라우터 | `/feedback`, `DELETE /chat/{id}`(회원·비회원 공통), `/events`, `/auth/*`, `GET /chat` · `GET /chat/{id}` | `/feedback`·비회원 `DELETE /chat/{id}` ✅ T3-51 (`feedback` 테이블, alembic `0005_service_api`), `/events` ✅ T3-48. 로그인·회원 대화는 별도 태스크 |
| 6-0 | 회원 대화 정리 배치 | `conversations.updated_at` 기준 90일 지난 대화·턴 삭제(매일), 비회원 세션 정리 배치와 같이 돈다 | 김병석 |
| 6-1 | 카카오 앱 · 비밀값 | 카카오 개발자 앱 등록(로컬·배포 Redirect URI), `KAKAO_*`·쿠키 서명 키를 `.env.example`과 Secrets Manager에 추가 | 김병석 |
| 6-2 | `frontend/` | 홈·사이드바 로그인 버튼, 로그인 실패 안내, 회원 사이드바 목록·이전 대화 복원, 로그아웃 | Addie Lee |
| 6-3 | 개발용 주소 이름 변경 | `backend/app/main.py`의 `POST /chat`·`/tools/*`·`GET /threads/{id}`를 `/dev/` 아래로, 프론트 `threadLost()`는 `meta.expired`로, 표 검색은 `/dev/tools/search_tables`로(8장) | 백엔드 ✅ T3-51 (옛 주소는 deprecated로 남김 → 프론트가 바뀌면 삭제). 프론트 2곳은 임도유 |
| 7 | 배포 설정 | 개발용 엔드포인트 `/dev/*` 배포에서 막기 | 스위치 ✅ T3-51 (`KOSIS_DISABLE_DEV_ROUTES=1`이면 `/dev/*`와 옛 주소 404). 배포 환경 값 넣기는 김병석 |
| 8 | `docker-compose.yml` · `pyproject.toml` | 아직 남은 `redis` 서비스·`REDIS_URL`·`redis>=5.0` 제거(11/08 결정) | ✅ T3-51 |

## 10. 결정 목록

### 결정됨 (11/12 · 11/18)

- 결과 유형 6종: `answered`·`partial`·`general`·`refused`·`clarify`·`failed`
- 답 문장은 검증 끝난 문장만 `token` 이벤트로 흘림
- 비회원 세션 1시간, 만료 뒤에는 새 대화 + 만료 안내
- 멀티쿼리는 하위 답마다 차트, 단위가 다르면 섞지 않음
- 재시도 상한: 하위 검색 3회, 전체 계획 보완 2회, 계산·차트·답 생성 각 2회
- 추천 질문·FAQ는 1차에서 삭제
- **카카오 로그인·회원 대화는 기능 동결 전에 포함 (11/19 김병석 결정)**
- **회원 대화 보관 90일 (11/19 결정)**
- 👎 선택지 4개 유지(수치가 틀림·표가 다름·이해하기 어려움·기타), 👍 5개, 이유 200자
- 되묻기: 후보가 있으면 `clarify_options` 버튼, 없으면 문장
- 조건 보완: 후보 표 숨김 · 기존 질문 입력창 유지 · 고친 질문은 새 턴
- 진행 표시: 단계 이름 목록 + 완료 체크, 답 생성 중에는 전송 버튼 비활성화
- 거절: 코드에 미리 작성한 고정 문구, 예측 질문은 `causal`에 묶음
- 이름: 대화 ID `thread_id`, 차트는 `result.chart`, 요청 ID = trace ID
- **사용자용 대화 주소는 `/chat`·`/chat/{id}`, 삭제는 회원·비회원 공통 `DELETE /chat/{id}` (11/23 리뷰 반영)**
- **개발용 엔드포인트는 `/dev/*` 아래, 배포에서 막음. 표 검색 화면(`/search`)은 개발 환경만 (11/23)**

### 아직 결정 필요 (제안값으로 우선 진행)

1. 한 사용자 대화 10개 상한(F-IRBJCC)을 둘지, 넘으면 어떻게 할지
2. 회원 대화 제목 — 첫 질문 일부 vs AI 생성 제목(F-JVQZTL)
3. 피드백 창을 선택지 없이 닫았을 때 — 제안: `category: null`로 값만 저장
4. 입력 검증 오류 상태 코드 — 제안: 400 대신 422로 통일
5. `failed` 답에 피드백을 받을지 — 제안: 받지 않음
6. 한 질문 총 실행 시간 상한 — 추천 30초
7. 하위 질문 최대 개수 — 추천 3개
8. 연결 자동 재시도 횟수 — 추천 1회
9. 재시도 이벤트 이름 — 제안: `retry` `{stage, attempt, max, reason}`
10. 운영자 메트릭 도구 — 제안: Grafana가 DB 직접 조회(API 없음)
11. 내부 트래픽 식별 — `X-Internal` 헤더 vs IP 대역(F-PVOWFN 추천)
