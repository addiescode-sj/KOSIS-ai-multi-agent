"""backend/app/config.py: the shell environment -> phase 2 IndexSpec / SearchOptions and phase 1 LLMConfig."""

from pathlib import Path

import pytest

from backend.app import config

NAMES = ("KOSIS_WORKSPACE", "CHROMA_HOST", "CHROMA_PORT", "CHROMA_COLLECTION", "TEXT_FORMAT", "EMBED_CKPT",
         "RERANK_CKPT", "RERANK_MODE", "STAGE2", "COND_FILTER", "ANSWER_K", "HQ_BUILD", "HQ_WEIGHT", "HQ_BM25",
         "HCX_API_KEY", "HCX_THINKING", "OPENAI_API_KEY", "STAT_PROVIDER", "STAT_MODEL", "STAT_RETRY_INTERVAL")


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KOSIS_WORKSPACE", str(tmp_path))
    return monkeypatch


def test_defaults_are_phase2_defaults_with_data_under_the_workspace(env, tmp_path):
    from kosis_rag.schemas.resources import SearchOptions

    cfg = config.agent_config()
    assert cfg.options == SearchOptions()  # unset variables = phase 2 defaults (stage 2 off, answer_k 5, ...)
    assert cfg.spec.tables == tmp_path / "data" / "tables.jsonl"
    assert cfg.spec.sigungu == tmp_path / "data" / "sigungu.json"
    assert cfg.spec.hq_root == tmp_path / "data" / "hq" and cfg.spec.hq_build is None
    assert (cfg.spec.chroma.host, cfg.spec.chroma.port, cfg.spec.chroma.collection) == ("localhost", 8001, None)
    assert cfg.stage2_llm is None and cfg.stat_retry_interval == 0.0


def test_shell_values_reach_the_settings(env):
    env.setenv("CHROMA_COLLECTION", "kosis_a_bgeft_x")
    env.setenv("CHROMA_PORT", "8002")
    env.setenv("EMBED_CKPT", "data/models/e.pt")
    env.setenv("RERANK_CKPT", "data/models/r.pt")
    env.setenv("COND_FILTER", "off")
    env.setenv("STAGE2", "hcx")
    env.setenv("HCX_API_KEY", "nv-test-key")
    env.setenv("STAT_RETRY_INTERVAL", "5")
    cfg = config.agent_config()
    assert cfg.spec.chroma.collection == "kosis_a_bgeft_x" and cfg.spec.chroma.port == 8002
    assert cfg.spec.embed_ckpt == "data/models/e.pt" and cfg.spec.rerank_ckpt == "data/models/r.pt"
    assert cfg.options.cond_filter is False and cfg.options.stage2 == "hcx"
    assert cfg.stage2_llm is not None and cfg.stat_retry_interval == 5.0


def test_phase1_llm_is_the_baseline_model_and_hides_its_key(env):
    env.setenv("HCX_API_KEY", "nv-test-key")
    llm = config.stat_llm()
    assert (llm.provider, llm.model, llm.temperature, llm.max_tokens) == ("hcx", "HCX-DASH-002", 0.0, 1024)
    assert llm.api_key.get_secret_value() == "nv-test-key" and "nv-test-key" not in repr(llm)


def test_phase1_mock_needs_no_key(env):
    env.setenv("STAT_PROVIDER", "mock")
    env.setenv("HCX_API_KEY", "nv-test-key")
    llm = config.stat_llm()
    assert llm.provider == "mock" and llm.api_key is None and llm.require_key() is None
    assert llm.model == "HCX-DASH-002"  # as the mock baseline run recorded it


def test_multiturn_and_multiquery_llm_step_defaults_to_gpt_4o_mini(env, monkeypatch):
    """T3-27 follow-up reader and T3-28 multi-query splitter share INTERPRETER_MODEL; phase 1 stays on HCX."""
    monkeypatch.delenv("INTERPRETER_MODEL", raising=False)
    monkeypatch.delenv("INTERPRETER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key")
    cfg = config.agent_config()
    assert cfg.interpreter_model == "gpt-4o-mini" and cfg.stat_llm.provider == "hcx"
    assert config.interpreter_chat_model(cfg).model_name == "gpt-4o-mini"
    assert config.splitter_llm(cfg) is not None
