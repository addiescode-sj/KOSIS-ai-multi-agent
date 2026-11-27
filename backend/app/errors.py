"""Error codes of a failed answer (T3-67, docs/service/api.md 2 `error`).

A failure while answering ends the SSE stream with `error` {code, message, retryable, request_id, type, detail}.
`code` is one of CODES; `message` is its fixed sentence (no exception text, no internal address); `retryable` follows
the code, not the exception class. The screen picks its own sentence by `code` (frontend/lib/chat/errors.ts) and
falls back to `message`. `type` and `detail` stay for logs and developers only.

classify() is a fixed table for now. KOS-211 replaces it with the agent's judgement: same input (the exception),
same output (a code from CODES), so the codes, sentences and the screen stay as they are.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

ErrorCode = Literal["model_unavailable", "upstream_busy", "upstream_error", "timeout", "data_error", "config_error",
                    "internal_error"]


class ErrorInfo(NamedTuple):
    message: str  # shown when the screen does not know the code
    retryable: bool  # whether the proxy reconnects once (api.md 10 proposal 8)


CODES: dict[str, ErrorInfo] = {
    "model_unavailable": ErrorInfo("답변에 필요한 AI 서버가 잠시 멈춰 있어요. 1~2분 뒤 다시 시도해 주세요.", True),
    "upstream_busy": ErrorInfo("지금 요청이 많아 답하지 못했어요. 잠시 뒤 다시 시도해 주세요.", True),
    "upstream_error": ErrorInfo("답을 만드는 중 문제가 생겼어요. 다시 시도해 주세요.", True),
    "timeout": ErrorInfo("답을 만드는 데 시간이 너무 오래 걸렸어요. 질문을 나눠 다시 물어봐 주세요.", True),
    "data_error": ErrorInfo("이 통계표 자료를 불러오지 못했어요. 다른 질문을 해 주시면 확인해 볼게요.", False),
    "config_error": ErrorInfo("서비스 설정에 문제가 있어 지금은 답할 수 없어요. 불편을 드려 죄송합니다.", False),
    "internal_error": ErrorInfo("잠시 뒤 다시 시도해 주세요.", True),
}

# The 503 sentence when the agent cannot be opened (any cause); `cause` carries the code (api.md 0)
UNAVAILABLE_MESSAGE = "서비스를 잠시 사용할 수 없어요. 잠시 뒤 다시 시도해 주세요."
# The 500 sentence of the developer `POST /chat`
INTERNAL_MESSAGE = "요청을 처리하지 못했어요. 잠시 뒤 다시 시도해 주세요."

# Phase 1 turns a failed HCX call into RuntimeError("…the model call failed (<status>: …)"), status timeout | api_error
PHASE1_CALL_FAILED = "model call failed"


def _status(e: BaseException) -> int | None:
    """The HTTP status an API client error carries (openai.APIStatusError, httpx.HTTPStatusError), else None."""
    status = getattr(e, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(e, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _is(e: BaseException, module: str, *names: str) -> bool:
    """isinstance against a class of an optional library without importing it here (httpx, openai)."""
    return any(c.__module__.split(".")[0] == module and c.__name__ in names for c in type(e).__mro__)


def classify(e: BaseException) -> ErrorCode:
    """The error code of an exception raised while answering. First match wins; the order matters:

    - both phases' LLMKeyMissing (phase 1's also subclasses ValueError) before anything broader
    - KeyError (a code bug) before LookupError (a missing table), its parent
    - a timeout before a connection error (openai.APITimeoutError subclasses APIConnectionError)
    """
    name = type(e).__name__
    status = _status(e)
    text = str(e)
    if name == "LLMKeyMissing" or status in (401, 403):
        return "config_error"
    if isinstance(e, KeyError):
        return "internal_error"
    if (isinstance(e, TimeoutError) or _is(e, "httpx", "TimeoutException") or _is(e, "openai", "APITimeoutError")
            or (PHASE1_CALL_FAILED in text and "timeout" in text)):
        return "timeout"
    if status == 429 or _is(e, "openai", "RateLimitError") or (PHASE1_CALL_FAILED in text and "429" in text):
        return "upstream_busy"
    if name == "InferenceError" or _is(e, "openai", "APIConnectionError") or _is(e, "httpx", "ConnectError"):
        return "model_unavailable"
    if (status is not None and status >= 400) or PHASE1_CALL_FAILED in text:
        return "upstream_error"
    if isinstance(e, (FileNotFoundError, LookupError)):
        return "data_error"
    return "internal_error"


def error_event(e: BaseException, request_id: str) -> dict:
    """The SSE `error` payload of a failed answer."""
    code = classify(e)
    info = CODES[code]
    return {"code": code, "message": info.message, "retryable": info.retryable, "request_id": request_id,
            "type": type(e).__name__, "detail": str(e)}
