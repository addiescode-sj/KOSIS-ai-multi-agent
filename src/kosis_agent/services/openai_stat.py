"""실전 1 LLM 공급자 "openai": OpenAI 호환 chat completions(gpt-4o-mini, 자체 호스팅 Gemma)로 실전 1을 돌린다 (T3-28 범위 추가).

실전 1 코드는 고치지 않는다. 실전 1이 공개한 공급자 등록 API(kosis_chat.llm.providers.register_provider)로 이 레포에서
클라이언트 하나를 등록한다. 프롬프트·숫자 검증·답 문장 조립은 실전 1 그대로이고, 바뀌는 것은 문장을 쓰는 모델뿐이다.

- 설정: LLMConfig(provider="openai", model, api_key, base_url). base_url이 실전 1 기본값(CLOVA Studio)이면 OpenAI 공식
  주소를 쓴다(backend/app/config.stat_llm이 STAT_PROVIDER=openai | gemma로 만듦).
- 실패는 예외가 아니라 상태로 돌려준다(LLMClient 계약): 시간 초과 → timeout, 그 밖의 API 오류 → api_error.
- usage는 HCX와 같은 키(promptTokens · completionTokens)로 남긴다. 실전 1 span(kosis_chat/graph/spans.py)이 이 키를 읽는다.
"""

from __future__ import annotations

from kosis_chat.llm.model import STATUS_API_ERROR, STATUS_OK, STATUS_TIMEOUT, LLMClient, LLMResult
from kosis_chat.llm.providers import PROVIDERS, register_provider
from kosis_chat.schemas.resources import DEFAULT_BASE_URL, LLMConfig

PROVIDER = "openai"


class OpenAICompatClient(LLMClient):
    name = PROVIDER

    def __init__(self, config: LLMConfig):
        from openai import OpenAI

        base_url = None if config.base_url == DEFAULT_BASE_URL else config.base_url
        self.config = config
        self.client = OpenAI(api_key=config.require_key(), base_url=base_url, timeout=config.timeout_seconds,
                             max_retries=2)

    def generate(self, system: str, user: str) -> LLMResult:
        import openai

        try:
            reply = self.client.chat.completions.create(
                model=self.config.model, temperature=self.config.temperature, max_tokens=self.config.max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        except openai.APITimeoutError as e:
            return LLMResult(text="", status=STATUS_TIMEOUT, error=str(e))
        except openai.OpenAIError as e:
            return LLMResult(text="", status=STATUS_API_ERROR, error=f"{type(e).__name__}: {e}")
        usage = reply.usage
        tokens = {"promptTokens": usage.prompt_tokens, "completionTokens": usage.completion_tokens} if usage else {}
        text = reply.choices[0].message.content or "" if reply.choices else ""
        if not text:
            return LLMResult(text="", status=STATUS_API_ERROR, usage=tokens, error="empty response")
        return LLMResult(text=text, status=STATUS_OK, usage=tokens)


def register() -> None:
    """실전 1 공급자 표에 "openai"를 넣는다(여러 번 불러도 같음)."""
    if PROVIDER not in PROVIDERS:
        register_provider(PROVIDER, OpenAICompatClient)
