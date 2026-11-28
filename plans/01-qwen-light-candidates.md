# Qwen 호출부 경량 모델 적용 정리 (11/27)

기준: GitHub main 38e32c0 (T3-60 #58, ReAct T3-03 포함). 멀티에이전트 브랜치(min-multiagent-service, main 미머지)는 맨 아래 따로 적음.
목표: 응답 지연 p95 줄이기 (ReAct 도입 뒤 15.2s → 23s대).

## 1. Qwen을 부르는 곳과 판단

| # | 호출 지점 | 코드 | 언제 부르나 | 하는 일 | 판단 |
|---|---|---|---|---|---|
| 1 | 2차 리랭커 | phase2 stage2 (`STAGE2_MODEL`·`STAGE2_BASE_URL`) | 검색하는 턴마다 1회 | 후보 표 중 고르기 + 거절 | **유지** (성능 핵심, 사용자 결정) |
| 2 | ReAct understand | `src/kosis_agent/graph/react.py` `Judge.understand` | **매 턴 맨 처음 1회** (11/24부터 규칙 판단 없음) | template / clarify / rewrite / search 중 하나 + 근거 한 줄 (JSON) | **경량으로 교체** (1순위) |
| 3 | ReAct rewrite | `Judge.rewrite` | understand=rewrite이거나 검색 거절 뒤 | 재작성 질의 | **경량으로 교체**. `grounded()`가 원문에 없는 숫자·시도·낱말을 막아 줌 |
| 4 | ReAct sufficiency | `Judge.sufficiency` (AGENT_STEPS=b) | 검색 뒤 | sufficient / retry | **경량으로 교체** |
| 5 | ReAct split | `Judge.split` (AGENT_STEPS=b) | 여러 질문으로 나뉠 때 | keep / merge | **경량으로 교체** |
| 6 | 해석기·분해기 (T3-27·28) | `backend/app/config.py` `interpreter_llm`·`splitter_llm` | `INTERPRETER_MODEL=stage2`이고 hybrid에서 규칙이 못 읽은 후속 질문 | 후속 질문 읽기, 다중 질문 나누기 | **경량 가능**, 단 지금 `stage2` 값은 STAGE2_* 서버로만 감 → 경량 서버를 가리키려면 config 작은 수정 필요 |
| - | Phase 1 summarize_stat | `STAT_PROVIDER` (기본 hcx) | - | 실전 1 답 문장 | Qwen 아님, 대상 아님 |

### 왜 2~5가 경량 대상인가
- 출력이 정해진 집합 중 하나인 JSON 한 줄 분류이고, 답 문장은 LLM이 쓰지 않음(코드 템플릿 또는 실전 1).
- 집합 밖 값·형식 오류는 `safe()`가 버리고 검색으로 감 → 경량 모델이 틀려도 최악은 "ReAct 전 동작".
- understand는 매 턴 리랭커 앞에서 직렬로 돎 → 27B 대신 작은 모델이면 턴마다 그대로 줄어듦. p95를 늘린 주 원인 후보.
- **코드 변경 없이 바꿀 수 있음**: 2~5는 모두 같은 `Judge` 하나이고, `AGENT_MODEL`·`AGENT_BASE_URL`·`AGENT_API_KEY`가 비어 있을 때만 STAGE2_* 를 씀 (`config.py` agent_judge). .env에 AGENT_* 세 줄만 넣으면 경량 서버로 감.

### 위험
- understand가 통계 질문을 template(not_statistics)으로 잘못 보내면 답 대신 고정 문장이 나감 → 오거절이 늘 수 있음. 교체 전후 e2e(기준 11/23 run 등)에서 거절·오거절·p95 비교 필요.
- 작은 모델은 "연도" 같은 목록 밖 슬롯 이름을 더 자주 낼 수 있음 → `_slots()`가 걸러 주지만 clarify 문장이 달라질 수 있음.

## 2. Hugging Face 10GB급 후보 (공식 Qwen 위주)

크기는 파라미터 수로 계산한 추정치(BF16≈2B/파라미터, FP8≈1B, 4bit≈0.55B)이고 실제 파일 크기는 모델 카드에서 확인 필요.

| 순위 | 모델 | 크기(추정) | 메모 |
|---|---|---|---|
| **1** | `Qwen/Qwen3-8B-FP8` | ~9GB | 공식 FP8. 생각 단계 끄기(`enable_thinking: false`) 지원, vLLM 사용 사례 많음. 분류 JSON에 충분할 가능성 높음 |
| 2 | `Qwen/Qwen3-4B-Instruct-2507-FP8` | ~4–5GB | 생각 없는 지시형. 가장 빠름. 2와 1을 dev로 비교해 볼 가치 있음 |
| 3 | `Qwen/Qwen3-14B-AWQ` | ~9–10GB | 4bit. 판단 정확도 여유는 가장 크지만 8B보다 느림 |
| 보류 | `Qwen/Qwen3.5-9B` | BF16 ~19GB | 공식 양자화 없음, 이미지-텍스트 모델. 10GB에 맞추려면 비공식 양자화 필요 |
| 보류 | `Qwen/Qwen3-30B-A3B-GPTQ-Int4` | ~16GB | MoE(활성 3B)라 빠르지만 10GB 초과 |
| 비추천 | `empero-ai/Qwen3.8-9B-Distill` | BF16 ~20GB | 비공식 증류, 항상 `<think>`로 시작 → 지연 목적과 반대 |

- Qwen3.8에는 27B보다 작은 공식 모델이 없음 (2.4T-A95B, 27B뿐).
- 한국어 수치는 어느 모델도 공식 표기 없음 → 우리 dev로 확인해야 함.

## 3. 서빙 구성 메모
- GPU 서버에 vLLM 인스턴스 하나 더(경량, 예: :8003). 27B INT4 + 임베더·리랭커와 같은 GPU면 `--gpu-memory-utilization`을 나눠야 함(GPU 메모리 확인 필요).
- .env: `AGENT_MODEL`·`AGENT_BASE_URL`(·`AGENT_API_KEY`)만 경량 서버로. STAGE2_* 는 그대로 27B.

## 4. 멀티에이전트 브랜치(min-multiagent-service, main 미머지)
- ① judge_route·answer_direct (`kosis_multi/agents/judge.py`), S4b 계산용 다듬기(`agents/rewrite.py`, 기본 끔) 모두 `kosis_multi/config.py`가 STAGE2_* 로 만듦 → 같은 이유로 경량 대상. direct 답 문장은 `safe_reply` 숫자 검사가 있음. 쓰려면 MULTI 전용 모델·주소 변수 추가 필요.

## 출처
- [Qwen3.8 README](https://raw.githubusercontent.com/QwenLM/Qwen3.8/main/README.md)
- [Qwen3 컬렉션](https://huggingface.co/collections/Qwen/qwen3) · [Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B)
- [unsloth Qwen3.8 컬렉션](https://huggingface.co/collections/unsloth/qwen38)
- [empero-ai/Qwen3.8-9B-Distill](https://huggingface.co/empero-ai/Qwen3.8-9B-Distill)
