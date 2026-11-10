"""T3-28: phase 1 on an OpenAI-compatible model through a provider this repo registers (phase 1 code unchanged)."""

import openai

from backend.app.config import stat_llm
from kosis_agent.services.openai_stat import OpenAICompatClient
from kosis_chat.llm.model import STATUS_API_ERROR, STATUS_OK
from kosis_chat.llm.providers import build_client


def test_stat_provider_names_map_to_the_registered_client(monkeypatch):
    monkeypatch.setenv("STAT_PROVIDER", "gemma")
    monkeypatch.setenv("Gemma_API_KEY", "k")
    monkeypatch.setenv("Gemma_BASE_URL", "https://gemma.example/v1")
    monkeypatch.delenv("STAT_MODEL", raising=False)
    cfg = stat_llm()
    assert (cfg.provider, cfg.model, cfg.base_url) == ("openai", "google/gemma-4-31B-it-qat-w4a16-ct",
                                                       "https://gemma.example/v1")
    client = build_client(cfg)
    assert isinstance(client, OpenAICompatClient) and str(client.client.base_url).startswith("https://gemma.example")
    monkeypatch.setenv("STAT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert stat_llm().model == "gpt-4o-mini"


class Reply:
    class usage:
        prompt_tokens, completion_tokens = 12, 3

    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})})]


def test_generate_returns_a_status_instead_of_raising(monkeypatch):
    monkeypatch.setenv("STAT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    client = build_client(stat_llm())
    monkeypatch.setattr(client.client.chat.completions, "create", lambda **kw: Reply('{"a": 1}'))
    ok = client.generate("s", "u")
    assert (ok.status, ok.text, ok.usage) == (STATUS_OK, '{"a": 1}', {"promptTokens": 12, "completionTokens": 3})

    def fail(**kw):
        raise openai.APIConnectionError(request=None)
    monkeypatch.setattr(client.client.chat.completions, "create", fail)
    assert client.generate("s", "u").status == STATUS_API_ERROR
