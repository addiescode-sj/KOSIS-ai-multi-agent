# Plan: ReAct 아키텍처 툴래핑 고도화

- id: 20261127-react-tool-wrapping-hardening
- status: DRAFT
- date: 2026-11-27
- 관련: [docs/react-agent-architecture.md](../docs/react-agent-architecture.md) · [src/kosis_agent/graph/main_graph.py](../src/kosis_agent/graph/main_graph.py) · [src/kosis_agent/graph/react.py](../src/kosis_agent/graph/react.py) · [plans/T3-03.md](T3-03.md)

## Intent

T3-03 계획(옵션 B, 2026-11-24 범위 변경 포함)과 실제 메인 그래프를 대조한다. 단계마다 세 가지를 확인한다.
- 툴로 감싸서 부르는가
- LLM이 직접 개입하는가
- 그래프 노드 안에 코드로만 있는가

그다음 계획과 다르게 되었거나 구현하지 않은 것을 목록으로 만들고, 고칠 범위를 옵션으로 정한다.

## 1. 한눈에 보기

### 1-1. 툴로 감싼 것 (LangChain `@tool`, 노드가 `call_tool()`로 부름 → `execute_tool` span = agent 채점 대상)

| # | 툴 | 파일 | 감싼 대상 | 부르는 노드 | 툴 안의 LLM |
|---|---|---|---|---|---|
| T1 | `search_tables` | [retrieval/search.py](../src/kosis_agent/retrieval/search.py) | 실전 2 `SearchModule.stream` 전체 | search, multi | 실전 2 2차 리랭커(Qwen) |
| T2 | `summarize_stat` | [services/statistics.py](../src/kosis_agent/services/statistics.py) | 실전 1 `summarize_stat_run` | statistics, multi | 실전 1 답 문장(HCX) |
| T3 | `make_chart` | [services/chart.py](../src/kosis_agent/services/chart.py) | 실전 1 `redraw` | chart (차트를 요청한 턴만) | 없음 |
| T4 | `combine_values` | [services/combine.py](../src/kosis_agent/services/combine.py) | 코드 계산 (diff·sum·ratio) | multi (표가 다른 하위 답 두 개가 모두 답한 턴만) | 없음 |

- **LLM이 툴을 고르는 곳은 없다.** 그래프 전체에 `bind_tools` · `ToolNode` · `create_react_agent`가 없다. 어떤 툴을 언제 부를지는 노드와 조건부 경로(코드)가 정한다.
- **T2는 조건 인자를 기록만 한다.** 조건 인자 4개(`period_start`·`period_end`·`regions`·`dimensions`)는 툴 안에서 `del`로 버린다. 파라미터 정확도 채점용 기록이고 실전 1 동작에는 영향이 없다. 실전 1은 질문 문장에서 조건을 직접 읽는다.

### 1-2. LLM이 직접 개입하는 곳 (그래프 쪽, 툴 아님 → chat span만 남고 툴 채점에는 안 잡힘)

| # | LLM 호출 | 노드 | 하는 일 | 출력 검증 (코드) | 턴당 상한 | 끄는 방법 |
|---|---|---|---|---|---|---|
| L1 | `Judge.understand` | understand | 다음 행동: template · clarify · rewrite · search | Pydantic `Literal`. 실패하면 search | 1 (되묻기 다음 턴은 0) | `AGENT_STEPS=off` |
| L2 | `read_turn`의 LLM 단계 | understand | 지시어·이월·차트 요청 해석. 규칙이 못 정한 턴만 | `verify_reading` | 0~1 | `INTERPRETER=rules` |
| L3 | `split_question`의 splitter | understand | 멀티쿼리 분해. 규칙이 판단을 보류한 턴만 | `verify_split` | 0~1 | `INTERPRETER=rules` |
| L4 | `Judge.split` | understand | 규칙 분해 결과 검토: keep · merge | Pydantic `Literal`. 실패하면 규칙 결과 유지 | 0~1 | `AGENT_STEPS=off·a` |
| L5 | `Judge.rewrite` | rewrite | 검색용 한 문장 작성 | `grounded`: 원문에 없는 숫자·시도·낱말이면 버리고 원문 사용 | 0~2 | `AGENT_STEPS=off` (원문 이어 붙이기) |
| L6 | `Judge.sufficiency` | rewrite | 재작성 문장이 충분한가: sufficient · retry | Pydantic `Literal`. 실패하면 sufficient | 0~2 | `AGENT_STEPS=off·a` |

- 모델 설정
  - L1·L4·L5·L6: `AGENT_MODEL`·`AGENT_BASE_URL`. 비우면 STAGE2 서버의 Qwen을 쓴다.
  - L2·L3: `INTERPRETER_MODEL`. 기본은 gpt-4o-mini 또는 stage2다.
  - 두 계열이 서로 다르다. `AGENT_STEPS=off`로 꺼도 L2·L3는 계속 돈다.
- 그래프 쪽 LLM 호출 상한은 한 턴에 최대 8회다(L1 1 + L2 1 + L3 1 + L4 1 + L5 2 + L6 2). 대부분의 턴은 L1 한 번만 부른다.
- LLM은 숫자도, 최종 답 문장도 쓰지 않는다. 최종 답 문장은 T2 안의 실전 1이 쓰거나 코드 고정 문장이다.

### 1-3. 그래프 노드 안에 코드로만 있는 것 (LLM·툴 없음)

| # | 위치 | 하는 일 |
|---|---|---|
| C1 | `understand_node` 되묻기 분기 | `pending_clarify`가 있으면 L1 없이 바로 rewrite로 보냄 |
| C2 | `chart_request` | 차트 요청 여부·종류를 규칙으로 읽음 |
| C3 | `rewrite_node` 대체 문장 | LLM이 없거나 grounded 검사에 실패하면 원문(+되묻기 답)을 이어 붙임 |
| C4 | `search_node` 질문 선택 | 새 질문은 원문으로, 재작성·이월 턴은 다시 쓴 문장으로 검색 |
| C5 | `research_node` (`rewrite_after_search`) | gate 거절 이유와 후보 표 이름을 L5 입력 문자열로 만듦 |
| C6 | `next_table` · `candidate_keys` | 실전 1에 넘길 표 키를 고름 (실전 2 결과 안에서만) |
| C7 | `ask` | T2의 조건 인자를 `parse_query` 규칙으로 채움 |
| C8 | `chart_payload` | 요청하지 않은 차트는 실전 1 ChartSpec을 그대로 보냄 (툴로 부르면 extra_call 감점) |
| C9 | `review_node` | 출처 줄이 없으면 붙임. 차트를 그렸는지 확인. `numbers_verified`는 기록만 함 |
| C10 | `finalize_node` | outcome 결정, 고정 문장(`TEMPLATES`·`clarify_text`·`NO_TABLE_TEXT`), `active_*`·`pending_clarify` 저장 |
| C11 | `after_*` 조건부 경로 전부 | 다음 노드 결정: 재시도 상한, no_item 재검색, 이월 재검색, 대체 표 예산 |

## 2. 툴래핑이 필요한 곳과 필요 없는 곳

| 대상 | 지금 | 판단 | 이유 |
|---|---|---|---|
| 실전 1·2 호출 (T1·T2) | 툴 | **유지** | agent 채점의 근거(`execute_tool` span)이자 T3-07 ReAct `ToolNode`에서 그대로 쓸 계약 |
| 차트 다시 그리기 (T3) | 요청한 턴만 툴 | **유지** | 요청하지 않은 턴까지 툴로 부르면 extra_call |
| 하위 답 합치기 (T4) | 툴 | **유지** | 하네스가 툴 결과 숫자를 "근거 있는 숫자"로 인정함 |
| understand·rewrite·sufficiency·split (L1·L4~L6) | 노드 안 LLM 직접 호출 | **툴로 만들지 않음** | 툴로 만들면 extra_call 감점. 채점 허용 목록을 바꿔야 함(T3-03 옵션 C를 고르지 않은 이유) |
| review·finalize (C9·C10) | 코드 | **툴로 만들지 않음** | 결정적 코드 검수. LLM이 고를 일이 없음 |
| 판단 단계를 LLM이 툴로 고르는 구조 | 없음 | **T3-07·T3-10 비교군에서만** | TASK.md 완료 기준 "LLM이 툴 선택·순서 결정"에 가장 가까움. 하네스 허용 목록 변경이 필요해 별도 태스크로 둠 |

## 3. 계획(T3-03) 대비 단계별 대조

| 단계 | 계획 | 실제 | 상태 |
|---|---|---|---|
| understand | Qwen이 행동을 고름. 질문 종류(단일·계산·멀티표·후속)도 판단. 예측·인과·개인 정보는 템플릿 | L1이 행동만 고름. 질문 종류는 규칙(C2·L2·L3). 템플릿은 greeting·not_statistics 2종 | **부분** (X3) |
| 되묻기 이어받기 | `pending_clarify` = 원 질문 + 빠진 슬롯. read_turn보다 먼저 처리 | 원 질문만 저장. read_turn보다 먼저 처리함. 실전 1이 clarify로 답한 경우는 저장 안 함 | **부분** (X4) |
| 분해 검토 | search·rewrite를 고른 뒤 분해를 봄 | search일 때만. rewrite는 분해보다 먼저 반환 | **부분** (X5) |
| rewrite | Qwen 재작성, 원문 낱말만 허용, 충분성 판단 후 1회 더 | L5 + `grounded` + L6, 상한 2회 | ✓ |
| 검색 + 검수 | `search_tables`, 2차 리랭커·gate가 검수, 답할 수 없으면 툴 추가 호출 없음 | T1. 거절이면 finalize | ✓ |
| 거절 후 재작성 | Observation(거절 이유·후보 표)을 보고 1회 재작성·재검색 | C5 → L5·L6 → T1. no_item만, 처음에 rewrite를 거친 턴은 제외 | ✓ (조건은 문서에 덜 적힘, X8) |
| 답변 → 차트 | 순차 | statistics → chart 순차 | ✓ |
| review | 출처 줄 검사 **+ "답 숫자 ⊂ 실전 1 사실값"을 코드로 검사** | 출처 줄만 검사. 숫자는 실전 1의 `numbers_verified`를 기록만 함 | **부분** (X2) |
| 출처 보장 | 모든 답 | 단일 표 경로만. multi(표 다름)·chart_only는 review를 지나지 않음 | **부분** (X6) |
| A/B 동시 측정 | env 플래그 | `AGENT_STEPS=off·a·b` | ✓ |
| 판단 장애 | 규칙 경로로 대신 | `react.safe` → search | ✓ |
| LLM 호출 상한 | 8 | 8 (1-2 참고) | ✓ |
| 완료 기준 "LLM이 툴 선택·순서 결정" | 옵션 B로 구체화 | LLM은 행동 값만 고르고, 툴 순서는 코드가 정함 | **해석 차이** (X1) |

## 4. 차이 목록

종류: **문서** = 문서만 고침 · **동작** = 코드 변경, plan-gate 필요.

| # | 종류 | 내용 | 위치 |
|---|---|---|---|
| X1 | 문서 | LLM이 툴을 직접 고르지 않는다는 점을 아키텍처 문서 1장에 명시. 툴로 고르는 구조는 T3-07·T3-10 비교군 | `docs/react-agent-architecture.md` 1장 |
| X2 | 동작 | review가 "답 숫자 ⊂ 실전 1 사실값"을 직접 검사하지 않고, `numbers_verified=False`여도 막지 않음 | `review_node` |
| X3 | 동작 | 예측·인과·개인 정보 템플릿 없음. 검색·실전 1까지 간 뒤 실전 1이 거절 | `react.TEMPLATES`·`UnderstandStep.kind` |
| X4 | 동작 | `pending_clarify`에 빠진 슬롯이 없음. 실전 1의 clarify는 다음 턴으로 이어지지 않음 | `finalize_node` |
| X5 | 동작 | rewrite 경로는 멀티쿼리 분해를 하지 않음 | `understand_node` 188-189행 |
| X6 | 동작 | 출처 줄 보장이 multi(표 다름)·chart_only 턴에 없음 | `after_multi`·`after_chart` |
| X7 | 문서 | 아키텍처 문서에 L2·L3 LLM과 별도 모델 설정이 없음. 지연·비용 계산에서 빠짐 | 아키텍처 문서 3·4장 |
| X8 | 문서 | no_item 재검색 조건 3개(judge 있음·이월 아님·처음에 rewrite를 거치지 않음)가 덜 적힘 | 아키텍처 문서 2장 |
| X9 | 문서 | Vega-Lite 언급 (KOS-202 이후 없음) | 아키텍처 문서 2·4장 |
| X10 | 문서 | "툴 3종" → 4종 (`combine_values`) | 아키텍처 문서 9장 |
| X11 | 문서 | T2 조건 인자가 채점 기록용이라는 설명이 없음 | 아키텍처 문서 4장 |
| X12 | 동작(선택) | trace 누락: multi 같은 표 경로의 answer 단계, chart 노드 | `multi_node`·`chart_node` |
| X13 | 동작(선택) | 되묻기 다음 턴이 L1 없이 무조건 rewrite로 감 (새 질문·인사도) | `understand_node` C1 |
| X14 | 문서 | `main_graph.py` docstring의 "규칙 판단 없음"이 C1·C2·L2 규칙 설명과 어긋남 | `main_graph.py` 21행 |

## 5. 옵션

예상 수치는 측정 전 추정이다. 동작 변경은 `python -m evals.compare` 판정(baseline = 실험 시점 main)을 따른다.

### 옵션 A: 문서만 맞춤 (X1·X7~X11·X14)

- 내용
  - 아키텍처 문서에 1장(툴·LLM·코드 목록)과 2장(툴래핑 판단)을 넣는다.
  - 어긋난 곳을 고친다.
  - 동작 차이(X2~X6)는 "알려진 한계"로 적는다.
- 예상 성능: 변화 없음.
- 실질 이점: 팀원과 발표 자료가 LLM 호출 수(지연·비용)와 툴 채점 범위를 정확히 안다.
- 비용·리스크: 반나절. 리스크 없음.

### 옵션 B: A + 정확성 보장 (X2·X6)

- 내용
  - X6: review의 출처 보완을 함수로 빼서 multi 하위 답과 chart_only 턴에도 적용한다.
  - X2: review가 답 문장의 숫자를 실전 1 사실값과 코드로 대조한다. 하네스의 `unsupported_final_number`와 같은 규칙이다. 불일치면 `failed` + 고정 문장으로 바꾼다.
  - 툴은 추가하지 않는다.
- 예상 성능
  - 출처 포함률이 multi·chart_only 턴에서 오른다.
  - 근거 없는 숫자가 나가는 턴이 0으로 줄어드는 대신 answered 비율이 조금 내려갈 수 있다.
- 비용·리스크: 1일. 오탐이 정상 답을 막을 수 있다. 오프라인 재생으로 불일치 건수부터 센다.

### 옵션 C: B + 이해 단계 보강 (X3·X4·X5·X12·X13)

- 내용
  - X3: `UnderstandStep.kind`에 unsupported(예측·인과·개인 정보)를 추가하고 템플릿을 하나 더 만든다.
  - X4: `pending_clarify`에 빠진 슬롯도 남기고, 실전 1 clarify에서도 저장한다.
  - X5: rewrite 뒤에도 분해를 본다.
  - X13: 되묻기 다음 턴에도 L1을 한 번 부른다.
  - X12: trace를 채운다.
- 예상 성능
  - 범위 밖 질문의 툴 호출이 줄어든다(지연 감소, extra_call 감소).
  - 되묻기 시나리오의 멀티턴 성공률이 오른다.
  - X13은 되묻기 다음 턴마다 LLM 1회가 늘어난다.
- 비용·리스크: 2일. X3은 검색 전 LLM 거절이 새 false_refusal을 만들 수 있다. T3-03 공통 원칙이 이 때문에 범위 거절을 gate에 맡겼으므로, false_refusal을 따로 센다.

### 추천

A를 먼저 반영한다. B는 불일치 건수를 센 뒤 결정한다. C는 X4·X5·X12만 먼저 하고, X3·X13은 false_refusal과 지연을 측정한 뒤 결정한다.

## 6. 후속 계획안: 질의 이해·재작성 단계에만 ReAct 에이전트 도입 (하이브리드)

### 6-1. 왜 이 두 단계만인가

지금 그래프는 ReAct의 형식(Thought·Action·Observation 기록)을 빌린 **고정 워크플로**다. LLM은 단계마다 정해진 선택지를 한 번 고를 뿐이다. 툴 선택, 툴 인자, 종료 시점은 모두 코드가 정한다.

그래프 전체를 ReAct 에이전트(`create_react_agent` + 툴 4개)로 바꾸는 경우를 실측 바탕으로 예측해 보면 다음과 같다(측정 전 추정).

| 항목 | 워크플로 (지금) | 전체 ReAct 에이전트 | 근거 |
|---|---|---|---|
| 턴당 Qwen 호출 | 1~2 (상한 8) | 3~6 (상한 없음) | 단순 질문도 툴 선택 2회 + 최종 답 1회 |
| 지연 p50 · p95 | 약 9~11초 · 25~30초 | 약 15~25초 · 40~60초 | Qwen 판단 1회 4.8~6.2초(실측), Observation JSON이 입력에 쌓임 |
| 툴 선택 (per10 0.895) | 유지 | 약 0.80~0.88 | 재사용 턴의 재검색, 요청하지 않은 차트, 중복 summarize_stat → extra_call·missing_call |
| 파라미터 (0.873) | 유지 | 하락 | LLM이 조건 인자 형식을 틀림 ("서울" vs "서울특별시") |
| 수치 정확도 (0.617) | 유지 | ±0 또는 하락 | 오류 원인은 실전 1 계산과 표 선택. 대체 표 재시도는 T3-23에서 틀린 표로 답한 사례가 있음 |
| 근거 없는 숫자 (0) | 0 | 위험이 새로 생김 | 최종 답을 LLM이 씀 |
| false_refusal · lost_state | 4 · 7 | 개선 가능 | 검색어를 여러 번 바꿔 시도, 대화 기록을 유연하게 해석 |

정리하면 ReAct가 이득을 볼 수 있는 곳은 **질의 이해(lost_state)와 재작성(false_refusal)** 두 단계뿐이다. 나머지(표 선택 → 실전 1 → 차트 → 검수)는 결정적 코드가 정확도·지연·숫자 원칙을 모두 더 잘 지킨다.

그래서 뼈대는 워크플로로 두고, 이 두 단계만 **단계마다 종료 조건이 박힌 작은 ReAct 루프**로 바꾼다.

```
understand ─► [Understand Agent: ReAct 루프, 종료 조건 U1~U8] ─► 기존 경로 (template·clarify·reuse·multi·search·rewrite)
rewrite    ─► [Rewrite Agent: ReAct 루프, 종료 조건 R1~R9] ──► statistics | finalize (refused·clarify)
search 이후 statistics → chart → review → finalize 는 그대로 (코드 + 툴 4개)
```

### 6-2. 선행 조건 (P0)

1. **기준 측정:** 매 턴 Qwen이 판단하는 지금 버전을 실서버에서 잰다(`colab/t3_03_e2e_eval_gpu.ipynb`). 이 run이 이 계획의 baseline이다.
2. **tool calling 스모크:** 경량 Qwen 서버에 tool calling 요청 20회를 보내 형식 오류율을 잰다. 결과에 따라 6-5의 구현 방식을 고른다.
3. **오류 분해:** baseline의 lost_state·false_refusal·wrong_args 턴을 understand 원인과 rewrite 원인으로 나눈다. 두 단계가 고칠 수 있는 오류 수가 이 계획의 기대 상한이다.

### 6-3. Understand Agent

**목표:** 이번 턴을 어떻게 처리할지 정한다(행동 + 해석).

**출력 계약:** `finish` 하나. `action`은 template · clarify · search · rewrite 중 하나이고, 다음 필드를 함께 낸다.
- `kind`: template일 때 종류
- `missing`: clarify일 때 빠진 슬롯
- `resolved_question`: 이월 해석 결과
- `use_previous_table`
- `chart_type`
- `sub_questions`

기존 `understand_node`가 상태에 쓰던 필드와 같다. 그래서 뒤쪽 경로는 바꾸지 않는다.

**내부 도구** (읽기 전용 코드 함수, 채점 툴 아님):

| 도구 | 하는 일 | 감싸는 기존 코드 |
|---|---|---|
| `parse` | 질문 → 지역·기간·분류 | `parse_query` |
| `read_context` | 직전 질문·표 이름·차트 유무, 되묻기 대기 여부 | `active_*`·`pending_clarify` |
| `read_rules` | 규칙 해석 결과(지시어·이월·차트 요청) | `read_turn(mode="rules")`·`chart_request` |
| `split_rules` | 규칙 분해 결과와 보류 여부 | `split_by_rules` |
| `check_grounded` | 해석 문장이 원문·직전 질문 낱말만 쓰는지와 위반 낱말 | `react.grounded` |

**종료 조건:**

| # | 조건 | 결과 |
|---|---|---|
| U1 | 스키마가 맞는 `finish` | 정상 종료 → 기존 경로 |
| U2 | `finish(template)`인데 `parse`가 지역·기간을 찾았거나 지표 낱말이 있음 | template을 버리고 search (검색 전 false_refusal 방지) |
| U3 | `finish(search, use_previous_table)`인데 직전 표가 없음 | 새 질문 search |
| U4 | `resolved_question`이 `check_grounded`를 통과하지 못함 | 해석을 버리고 원문으로 search |
| U5 | 도구 호출 3회 도달 | 다음 호출은 `finish`만 허용. 어기면 U8 |
| U6 | 같은 도구를 같은 인자로 다시 부름 | 루프 중단 → U8 |
| U7 | 형식 오류 2회, 또는 단계 시간 8초 초과 | 루프 중단 → U8 |
| U8 | 대체 경로 | 지금의 단발 판단(`Judge.understand` 1회)과 규칙 해석. 그것도 실패하면 search (지금 `safe()`와 같은 동작) |

- 되묻기 다음 턴: `read_context`가 대기 중인 원 질문을 보여 준다. 에이전트가 "되묻기 답"인지 "새 질문·인사"인지 고른다(X13 해결). 되묻기 답이면 `finish(rewrite)`로 원 질문과 답을 넘긴다.
- 상한: Qwen 호출은 최대 4회(도구 3 + finish 1)다. 대부분의 턴은 도구 0~1회 + finish를 예상한다.

### 6-4. Rewrite Agent

**목표:** 색인에서 표를 찾을 수 있는 검색 문장을 만들고 실제 검색 결과로 확인한다. T3-03의 성공 조건 "이 질의로 RAG 색인에서 답을 만들 수 있다"를 LLM 추측이 아니라 **Observation으로 판정**한다.

**출력 계약:** `finish(found)` · `finish(clarify, missing)` · `finish(give_up)` 중 하나.

**도구:**

| 도구 | 종류 | 하는 일 |
|---|---|---|
| `check_grounded` | 내부 코드 | 후보 문장의 위반 낱말과 숫자 |
| `parse` | 내부 코드 | 후보 문장의 조건 확인 |
| `search_tables` | **채점 툴 (T1 그대로)** | 실제 검색. route·refusal_reason·후보 표 이름만 요약해 Observation으로 돌려줌. 전체 SearchOutput은 상태에 저장 |

**종료 조건:**

| # | 조건 | 결과 |
|---|---|---|
| R1 | `search_tables`가 found이고 넘길 표가 있음 | **즉시 종료** → statistics (search 노드 건너뜀, 검색 결과 그대로 사용) |
| R2 | 거절 이유가 `out_of_scope` | 즉시 종료 → finalize(refused). 다시 쓰지 않음 (지금 규칙 유지) |
| R3 | 거절 이유가 `no_item`이고 검색 예산이 남음 | 거절 이유와 후보 표 이름을 보고 1회 더 재작성 |
| R4 | 검색 호출 2회 도달 | 마지막 결과로 종료. found면 R1, 아니면 finalize(refused) |
| R5 | `check_grounded` 실패 | 검색 예산은 쓰지 않음. 같은 루프에서 2번 실패하면 원문(+되묻기 답)으로 1회 검색 후 R1·R2·R4 |
| R6 | `finish(clarify, missing)` | 검색 전에만 허용 → finalize(clarify) + `pending_clarify` 저장 (빠진 슬롯 포함, X4 해결) |
| R7 | 이전과 같은 검색 문장 | 검색하지 않고 R4로 종료 |
| R8 | 도구 호출 5회, 형식 오류 2회, 또는 단계 시간 15초 초과 | 루프 중단. 검색 결과가 있으면 R4, 없으면 지금 rewrite 노드의 단발 경로 |
| R9 | 이월 턴(`carried_over`)에서 들어옴 | 검색 예산 1 (지금 이월 재검색 1회와 같음) |

- 지금의 `sufficiency` 판단(L6)은 따로 호출하지 않는다. 검색 결과(R1~R4)가 그 역할을 대신한다. 그래서 LLM 호출은 줄고, 판정 근거는 실제 결과가 된다.
- 상한: Qwen 호출 최대 6회, `search_tables` 최대 2회다. 검색 2회는 지금 "거절 후 재검색 1회"와 같은 예산이라 extra_call이 늘지 않는다.

### 6-5. 구현 방식 (P0 스모크 결과로 고름)

| 방식 | 내용 | 장점 | 단점 |
|---|---|---|---|
| **H1** 직접 짠 루프 (JSON 한 줄 프로토콜) | `Judge`처럼 응답을 JSON 한 줄로 받는다: `{"thought", "action": 도구 이름 또는 finish, "input"}`. 코드가 도구를 실행하고 Observation을 붙여 다시 묻는다. 종료 조건은 루프 코드에 있다 | tool calling이 필요 없다. 내부 도구에 `execute_tool` span이 생기지 않아 extra_call 위험이 없다. 종료 조건을 코드로 강제하기 쉽다 | LangGraph 표준 에이전트가 아니라서 직접 유지보수해야 한다 |
| **H2** `create_react_agent` 서브그래프 | 단계마다 내부 도구를 `bind_tools`한 서브그래프. `recursion_limit` + `post_model_hook`으로 종료 조건을 넣는다 | 표준 구현이라 T3-07 단일 ReAct 비교군과 코드를 공유한다 | Qwen tool calling 검증이 필요하다. 내부 도구 span을 하네스 허용 목록에서 빼야 한다(채점 계약 변경, 승인 필요) |

추천: P0 스모크에서 형식 오류율이 5% 이상이면 H1, 그 아래면 H2를 검토한다. 어느 쪽이든 `search_tables`는 지금 T1 툴을 그대로 쓴다.

### 6-6. 공통 규칙

- **숫자 원칙 유지:** 두 에이전트 모두 숫자와 답 문장을 쓰지 않는다. 결과물은 행동 값과 검색 문장뿐이고, 검색 문장은 `grounded`를 통과해야 쓴다.
- **종료 이유 기록:** 모든 종료에 `stop_reason`(U1~U8·R1~R9)을 `trace`에 남긴다. 평가 리포트에서 종료 이유별 분포와 대체 경로 비율을 센다.
- **끄는 스위치:** `AGENT_SUBAGENTS=off|understand|rewrite|both`(기본 off). 같은 run에서 단계별 효과를 따로 잰다. off면 지금 워크플로와 같아야 한다(오프라인 회귀로 확인).
- **턴 예산:** 두 에이전트를 합친 Qwen 호출 상한은 10회, 단계 시간 상한은 합쳐서 23초다. 넘으면 대체 경로로 간다.

### 6-7. 측정과 채택 규칙

| 지표 | baseline (P0 run) | 기대 (측정 전 추정) | 채택 조건 |
|---|---|---|---|
| lost_state | P0에서 측정 | understand 원인 턴의 절반 이하로 감소 | `evals.compare` 개선 판정 |
| false_refusal | P0에서 측정 | rewrite 원인 턴 일부 회복 | 개선 판정 |
| 툴 선택 · 파라미터 | P0에서 측정 | 유지 (뒤쪽 경로는 그대로) | 악화 판정이 아닐 것 |
| 수치 정확도 · 근거 없는 숫자 턴 | P0에서 측정 · 0 | 유지 · 0 | 악화 없음 · 0 유지 |
| 지연 p95 | P0에서 측정 | 에이전트를 탄 턴만 +5~12초 | p95 증가 +10초 이내 |
| 대체 경로 비율 (U8·R8) | — | 10% 이하 | 20% 넘으면 그 단계는 채택하지 않음 |

### 6-8. 진행 순서

| 단계 | 내용 | 산출물 |
|---|---|---|
| P0 | 기준 측정 + Qwen tool calling 스모크 + 오류 분해 | baseline run, 형식 오류율, 단계별 고칠 수 있는 오류 수 |
| P1 | Rewrite Agent (검색 결과로 판정되어 효과를 재기 쉬움) | `AGENT_SUBAGENTS=rewrite`, pytest: 종료 조건 R1~R9마다 1개 이상 |
| P2 | Understand Agent | `AGENT_SUBAGENTS=understand`, pytest: U1~U8 |
| P3 | 둘 다 켜고 per10 → 전체 골든셋 측정 | `evals.compare` 판정, ADR 초안 (T3-10 비교표의 "하이브리드" 칸) |

이 계획안은 별도 태스크로 TASK.md에 올리고 plan-gate 절차(옵션 · Verify · 승인)를 밟는다. T3-07(단일 ReAct 베이스라인)과는 내부 도구와 측정 run을 공유한다.

## 7. Verify 체크리스트

- [ ] (A) X1·X7~X11·X14가 문서에 반영됨. 1장 표의 파일·함수 이름이 코드에 실제로 있음
- [ ] (B) multi·chart_only 답에 `출처: KOSIS / 조사명 / 통계표명`이 들어감 (pytest)
- [ ] (B) 사실값에 없는 숫자가 들어간 답이 `failed`로 끝남 (pytest)
- [ ] (C) 실전 1 clarify 다음 턴이 rewrite로 감. rewrite 턴도 분해됨 (pytest)
- [ ] `bash scripts/verify.sh` 통과
- [ ] 동작 변경(B·C)은 같은 골든셋으로 `evals.compare` 전→후 판정

- [ ] (6장) P0 baseline run과 tool calling 스모크 결과가 기록됨
- [ ] (6장) 종료 조건 U1~U8·R1~R9마다 pytest 1개 이상, `AGENT_SUBAGENTS=off` 오프라인 회귀가 지금과 같음

## 8. Decision

- 선택 옵션: (미정)
- 승인: (미정)
