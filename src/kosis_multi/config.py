"""멀티에이전트 실험 설정: .env(셸 값 우선, 진입점이 이미 읽음)의 환경변수 → 설정 객체.

① 판단 Qwen은 2차 리랭커와 같은 서버를 쓴다(사용자 결정 11/24): STAGE2_BASE_URL · STAGE2_MODEL · STAGE2_API_KEY.
새 환경변수는 실험 스위치만 둔다.
TODO(S2): judge_chat_model()을 orchestrator에 연결. TODO(결정 6): 동시 호출 상한 기본값 확정(지금 2).
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
    timeout: float = 30.0


@dataclass(frozen=True)
class MultiConfig:
    judge: JudgeConfig
    max_concurrency: int = 2  # MULTI_MAX_CONCURRENCY: 노드 안 병렬 실행의 동시 상한 (HCX 429)
    prefetch: bool = True  # MULTI_PREFETCH: ①과 동시에 원문으로 미리 검색 (비동기 A)


def multi_config() -> MultiConfig:
    judge = JudgeConfig(model=_env("STAGE2_MODEL"), base_url=_env("STAGE2_BASE_URL"), api_key=_env("STAGE2_API_KEY"))
    return MultiConfig(judge=judge, max_concurrency=int(_env("MULTI_MAX_CONCURRENCY") or 2),
                       prefetch=(_env("MULTI_PREFETCH") or "on").lower() not in {"off", "0", "false", "no"})


def judge_chat_model(cfg: JudgeConfig):
    """Qwen(OpenAI 호환 서버) chat model. 모델·주소가 없으면 None(① 판단은 규칙만)."""
    if not cfg.model or not cfg.base_url:
        return None
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=cfg.model, base_url=cfg.base_url, api_key=cfg.api_key or "EMPTY", temperature=0,
                      timeout=cfg.timeout, max_retries=1)
