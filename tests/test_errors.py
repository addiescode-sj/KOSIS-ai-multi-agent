"""T3-68: the error codes of a failed answer (backend/app/errors.py)."""

from __future__ import annotations

import httpx
import openai
import pytest

from backend.app.errors import CODES, classify, error_event
from inference.client import InferenceError

REQUEST = httpx.Request("POST", "http://10.0.0.5:8000/v1/chat/completions")


def status_error(cls, status: int):
    return cls("x", response=httpx.Response(status, request=REQUEST), body=None)


class LLMKeyMissing(RuntimeError, ValueError):  # phase 1's subclasses ValueError too: it must still be config_error
    pass


@pytest.mark.parametrize(("error", "code"), [
    (LLMKeyMissing("HCX_API_KEY is not set"), "config_error"),
    (status_error(openai.AuthenticationError, 401), "config_error"),
    (InferenceError("embed server unreachable: GET http://10.0.0.5:8002/health"), "model_unavailable"),
    (openai.APIConnectionError(request=REQUEST), "model_unavailable"),
    (httpx.ConnectError("refused", request=REQUEST), "model_unavailable"),
    (status_error(openai.RateLimitError, 429), "upstream_busy"),
    (RuntimeError("stat: the model call failed (api_error: 429 Too Many Requests)"), "upstream_busy"),
    (TimeoutError("phase 1 timed out"), "timeout"),
    (openai.APITimeoutError(request=REQUEST), "timeout"),  # a subclass of APIConnectionError: timeout wins
    (httpx.ReadTimeout("slow", request=REQUEST), "timeout"),
    (RuntimeError("stat: the model call failed (timeout: request timed out)"), "timeout"),
    (status_error(openai.InternalServerError, 502), "upstream_error"),
    (RuntimeError("the fill's model call failed: api_error"), "upstream_error"),
    (FileNotFoundError("data/values/x.parquet"), "data_error"),
    (LookupError("101_X is not in tables.csv"), "data_error"),
    (KeyError("table_key"), "internal_error"),  # a code bug, though KeyError is a LookupError
    (ValueError("bad"), "internal_error"),
    (RuntimeError("boom"), "internal_error"),
])
def test_classify(error, code):
    assert classify(error) == code


def test_every_code_has_a_sentence_without_internal_words():
    assert set(CODES) == {"model_unavailable", "upstream_busy", "upstream_error", "timeout", "data_error",
                          "config_error", "internal_error"}
    for info in CODES.values():
        assert info.message.strip()
        assert not any(word in info.message for word in ("Error", "http", "GPU", "API", "HCX", "Qwen"))


def test_only_data_and_config_errors_are_not_retried():
    assert {code for code, info in CODES.items() if not info.retryable} == {"data_error", "config_error"}


def test_error_event_keeps_the_exception_for_logs_only():
    event = error_event(InferenceError("rerank server unreachable: POST http://10.0.0.5:8003/rerank"), "a" * 32)
    assert event["code"] == "model_unavailable" and event["retryable"] is True and event["request_id"] == "a" * 32
    assert "10.0.0.5" not in event["message"] and event["message"] == CODES["model_unavailable"].message
    assert event["type"] == "InferenceError" and "10.0.0.5" in event["detail"]
