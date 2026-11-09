"""The agent's settings from the shell environment: phase 2 search (IndexSpec, SearchOptions, stage 2 LLMConfig) and
phase 1 stat (its own LLMConfig). Both modules read no .env and no environment; this is where the app declares them.

Phase 3 reads its .env: the entry points (backend/app/main.py, evals, scripts) call kosis_agent.envfile.load_env_file()
first, which puts <repo>/.env into os.environ without overriding the shell (so `set X=...` still wins). This module
then reads os.environ. In the container compose passes the same file as `env_file`.

    KOSIS_WORKSPACE                                 repo root; data files default to <root>/data (phase 1 reads it too)
    CHROMA_HOST CHROMA_PORT CHROMA_COLLECTION      Chroma server and the dense collection (default localhost:8001)
    TEXT_FORMAT EMBED_CKPT RERANK_CKPT RERANK_MODE  table text check, query embedder, stage 1 cross-encoder
    STAGE2 COND_FILTER ANSWER_K                     stage 2 LLM (clova | hcx | gpt | none), conditions, k default
    HQ_BUILD HQ_WEIGHT HQ_BM25                      reverse HyDE question index (empty HQ_BUILD = off)
    HCX_API_KEY HCX_THINKING OPENAI_API_KEY         stage 2 keys (clova / hcx: HCX_API_KEY, gpt: OPENAI_API_KEY)
    STAT_PROVIDER STAT_MODEL STAT_RETRY_INTERVAL    phase 1 model (hcx | mock, HCX-DASH-002), seconds before a retry
    HCX_API_KEY                                     phase 1 key too (None: LLMKeyMissing when a phase 1 LLM step runs)
    ALT_TABLE_BUDGET                                candidate tables tried after a table-mismatch refusal (default 0 = off, T3-23)
    INTERPRETER INTERPRETER_MODEL                   follow-up reading rules | hybrid | llm (default hybrid, T3-27) and its
                                                    HCX model for ChatClovaX (empty = no LLM step: hybrid reads by rules only)

The search names and defaults are phase 2's backend/app/config.py search_config_from_shell @56b4252 (which this repo
cannot import: its backend.app shadows phase 2's), so an unset variable means the phase 2 default. The phase 1 model
defaults are the T1-28 S5 HCX baseline run (HCX-DASH-002, temperature 0, max_tokens 1024, thinking none).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

from pydantic import SecretStr

from kosis_chat.schemas.resources import LLMConfig as StatLLMConfig
from kosis_rag.llm.config import LLMConfig as SearchLLMConfig
from kosis_rag.rerank import selectors
from kosis_rag.schemas.resources import OFF, ChromaConfig, IndexSpec, SearchOptions

REPO_ROOT = Path(__file__).resolve().parents[2]
STAT_MODEL = "HCX-DASH-002"  # the phase 1 baseline model (T1-28 S5 HCX run: PASS 86 / 90)
STAGE2_KEY_ENV = {"clova": "HCX_API_KEY", "hcx": "HCX_API_KEY", "gpt": "OPENAI_API_KEY"}


class AgentConfig(NamedTuple):
    """Everything the agent graph is built from. Read it by name."""

    spec: IndexSpec
    options: SearchOptions
    stage2_llm: SearchLLMConfig | None  # None when stage 2 is off
    stat_llm: StatLLMConfig
    stat_retry_interval: float
    alt_table_budget: int  # T3-23: tables tried after the first one refuses for a table mismatch
    interpreter: str  # T3-27: rules | hybrid | llm (src/kosis_agent/graph/context.py)
    interpreter_model: str | None  # T3-27: ChatClovaX model for the LLM step; None = no LLM step


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


def workspace() -> Path:
    """KOSIS_WORKSPACE, else this repo's root (src/kosis_agent/__init__.py sets the same default for phase 1)."""
    return Path(_env("KOSIS_WORKSPACE") or REPO_ROOT)


def stage2_llm(name: str) -> SearchLLMConfig | None:
    """The stage 2 LLM: phase 2's default config for the name with its key and HCX_THINKING; None when off."""
    if name in OFF or name not in selectors.LLM_RERANKERS:
        return None
    return selectors.default_config(name, api_key=_env(STAGE2_KEY_ENV[name]),
                                    thinking=(_env("HCX_THINKING") or "none").strip().lower())


def stat_llm() -> StatLLMConfig:
    """The phase 1 model. The key is HCX_API_KEY (a SecretStr: never in repr, logs or dumps); mock needs none.
    mock keeps the model name HCX-DASH-002, as the phase 1 mock baseline (s0-mock-c) recorded it."""
    key = _env("HCX_API_KEY")
    provider = (_env("STAT_PROVIDER") or "hcx").strip().lower()
    return StatLLMConfig(api_key=SecretStr(key) if key and provider != "mock" else None,
                         model=_env("STAT_MODEL") or STAT_MODEL, provider=provider)


def agent_config() -> AgentConfig:
    """The agent's settings from os.environ (.env already loaded into it by the entry point, shell values first).

    Data files default to <workspace>/data (tables.jsonl, sigungu.json, hq/); the phase 2 defaults would be
    vendor/phase2/data. EMBED_CKPT / RERANK_CKPT are passed as given (as phase 2 does, so the L1 config fingerprint
    matches phase 2's dumps): a relative path is read from the current folder, so start from the repo root
    (the container's is /app) or give absolute paths.
    """
    root = workspace()
    data = root / "data"
    options = SearchOptions(
        cond_filter=(_env("COND_FILTER") or "on").strip().lower() not in OFF,
        rerank_mode=_env("RERANK_MODE") or "blend",
        stage2=(_env("STAGE2") or "none").strip().lower(),
        answer_k=int(_env("ANSWER_K") or 5),
        hq_weight=float(_env("HQ_WEIGHT") or 1.0),
        hq_bm25=(_env("HQ_BM25") or "off").strip().lower() not in OFF,
    )
    spec = IndexSpec(
        tables=data / "tables.jsonl",
        sigungu=data / "sigungu.json",
        hq_root=data / "hq",
        chroma=ChromaConfig(host=_env("CHROMA_HOST") or "localhost", port=int(_env("CHROMA_PORT") or 8001),
                            collection=_env("CHROMA_COLLECTION")),
        text_format=_env("TEXT_FORMAT"),
        embed_ckpt=_env("EMBED_CKPT"),
        rerank_ckpt=_env("RERANK_CKPT"),
        hq_build=(_env("HQ_BUILD") or "").strip() or None,
    )
    return AgentConfig(spec=spec, options=options, stage2_llm=stage2_llm(options.stage2), stat_llm=stat_llm(),
                       stat_retry_interval=float(_env("STAT_RETRY_INTERVAL") or 0.0),
                       alt_table_budget=int(_env("ALT_TABLE_BUDGET") or 0),
                       interpreter=(_env("INTERPRETER") or "hybrid").strip().lower(),
                       interpreter_model=_env("INTERPRETER_MODEL"))
