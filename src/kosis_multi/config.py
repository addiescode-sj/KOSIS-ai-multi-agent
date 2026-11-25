"""멀티에이전트 실험 설정: .env(셸 값 우선, 진입점이 이미 읽음)의 환경변수 → 설정 객체.

① 판단 Qwen은 2차 리랭커와 같은 서버를 쓴다(사용자 결정 11/24): STAGE2_BASE_URL · STAGE2_MODEL · STAGE2_API_KEY.
새 환경변수는 실험 스위치만 둔다. 값은 build_agent()가 그래프를 만들 때 한 번 읽는다(실행 중에는 안 바뀜).
동시 호출 상한은 두지 않는다(결정 6). 계산용 다듬기(S4b)를 켜면 ③ 턴에서만 Qwen이 둘(rerank2 + 다듬기) 동시에 돈다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


@dataclass(frozen=True)
class JudgeConfig:
    model: str | None
    base_url: str | None
    api_key: str | None
    timeout: float = 8.0  # 판단은 JSON 한 줄이라 짧게. 넘기면 rag로 돌아간다(재시도 없음)
    max_tokens: int = 512  # 답이 JSON 한 줄이라 작게. 생각(thinking) 단계를 켠 서버면 STAGE2_MAX_TOKENS로 늘림


@dataclass(frozen=True)
class MultiConfig:
    judge: JudgeConfig
    judge_on: bool = True  # MULTI_JUDGE: ① 판단에 Qwen을 쓸지 (off = 규칙만, 판단 보류는 rag)
    prefetch: bool = True  # MULTI_PREFETCH: ①과 동시에 원문으로 미리 검색 (비동기 A, S4)
    calc_rewrite: bool = False  # MULTI_CALC_REWRITE: ③과 동시에 계산용 질문 다듬기 (S4b, 기본 끔: S5에서 켠 run과 비교)


def multi_config() -> MultiConfig:
    # 키는 2차 리랭커와 같은 규칙: STAGE2_API_KEY, 없으면 OpenAI 호환 경로(STAGE2=gpt)의 OPENAI_API_KEY
    key = _env("STAGE2_API_KEY") or _env("OPENAI_API_KEY")
    judge = JudgeConfig(model=_env("STAGE2_MODEL"), base_url=_env("STAGE2_BASE_URL"), api_key=key,
                        max_tokens=_int("STAGE2_MAX_TOKENS", 512))
    return MultiConfig(judge=judge, judge_on=_on("MULTI_JUDGE"), prefetch=_on("MULTI_PREFETCH"),
                       calc_rewrite=_on("MULTI_CALC_REWRITE", default=False))


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _on(name: str, default: bool = True) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() not in {"off", "0", "false", "no"}


def judge_chat_model(cfg: JudgeConfig):
    """Qwen(OpenAI 호환 서버) chat model. 모델·주소가 없으면 None(① 판단은 규칙만)."""
    if not cfg.model or not cfg.base_url:
        return None
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=cfg.model, base_url=cfg.base_url, api_key=cfg.api_key or "EMPTY", temperature=0,
                      max_tokens=cfg.max_tokens, timeout=cfg.timeout, max_retries=0,
                      # vLLM Qwen: 생각 단계를 끈다(JSON 한 줄 판단, 지연·잘림 방지). 서버 기본이 이미 꺼져 있어도 무해
                      extra_body={"chat_template_kwargs": {"enable_thinking": False}})
