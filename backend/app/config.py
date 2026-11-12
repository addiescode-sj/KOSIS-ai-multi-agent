"""The agent's settings from the shell environment: phase 2 search (IndexSpec, SearchOptions, stage 2 LLMConfig) and
phase 1 stat (its own LLMConfig), and the follow-up reader's LLM (interpreter_llm). The phase 1 and 2 modules read
no .env and no environment; this is where the app declares them.

Phase 3 reads its .env: the entry points (backend/app/main.py, evals, scripts) call kosis_agent.envfile.load_env_file()
first, which puts <repo>/.env into os.environ without overriding the shell (so `set X=...` still wins). This module
then reads os.environ. In the container compose passes the same file as `env_file`.

    KOSIS_WORKSPACE                                 repo root; data files default to <root>/data (phase 1 reads it too)
    CHROMA_HOST CHROMA_PORT CHROMA_COLLECTION      Chroma server and the dense collection (default localhost:8001)
    TEXT_FORMAT EMBED_CKPT RERANK_CKPT RERANK_MODE  table text check, query embedder, stage 1 cross-encoder
    STAGE2 COND_FILTER ANSWER_K                     stage 2 LLM (clova | hcx | gpt | none), conditions, k default
    STAGE2_MODEL                                    stage 2 model name (default phase 2's per STAGE2); a *gemma* name runs
                                                    on the self-hosted server Gemma_BASE_URL with Gemma_API_KEY (STAGE2=gpt, T3-33)
    HQ_BUILD HQ_WEIGHT HQ_BM25                      reverse HyDE question index (empty HQ_BUILD = off)
    HCX_API_KEY HCX_THINKING OPENAI_API_KEY         stage 2 keys (clova / hcx: HCX_API_KEY, gpt: OPENAI_API_KEY)
    STAT_PROVIDER STAT_MODEL STAT_RETRY_INTERVAL    phase 1 model (hcx = HCX-DASH-002 default | openai | gemma | mock), seconds before a retry
    HCX_API_KEY                                     phase 1 key too (None: LLMKeyMissing when a phase 1 LLM step runs)
    ALT_TABLE_BUDGET                                candidate tables tried after a table-mismatch refusal (default 0 = off, T3-23)
    INTERPRETER INTERPRETER_MODEL                   follow-up reading and multi-query split (T3-28): rules | hybrid | llm (default hybrid, T3-27) and its
                                                    model (default gpt-4o-mini with OPENAI_API_KEY; HCX-* on ChatClovaX, *gemma* at Gemma_BASE_URL;
                                                    no key = no LLM step: hybrid reads by rules)

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
    interpreter_model: str  # T3-27: the LLM step's model (default gpt-4o-mini, interpreter_llm below)


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


def workspace() -> Path:
    """KOSIS_WORKSPACE, else this repo's root (src/kosis_agent/__init__.py sets the same default for phase 1)."""
    return Path(_env("KOSIS_WORKSPACE") or REPO_ROOT)


def stage2_llm(name: str) -> SearchLLMConfig | None:
    """The stage 2 LLM: phase 2's default config for the name with its key and HCX_THINKING; None when off.

    STAGE2_MODEL swaps the model (phase 2 PR #48, T3-33). A *gemma* model is the self-hosted OpenAI-compatible server,
    as for phase 1 (STAT_PROVIDER=gemma) and the interpreter: Gemma_BASE_URL with Gemma_API_KEY instead of OpenAI's
    key. Phase 2 uses base_url only on its openai provider, i.e. STAGE2=gpt. evals.config calls this too."""
    if name in OFF or name not in selectors.LLM_RERANKERS:
        return None
    model = _env("STAGE2_MODEL")
    gemma = bool(model) and "gemma" in model.lower()
    return selectors.default_config(name, api_key=_env("Gemma_API_KEY" if gemma else STAGE2_KEY_ENV[name]),
                                    thinking=(_env("HCX_THINKING") or "none").strip().lower(),
                                    model=model, base_url=_env("Gemma_BASE_URL") if gemma else None)


# STAT_PROVIDER=openai | gemma (T3-28): phase 1 on an OpenAI-compatible chat model through the provider this repo
# registers (kosis_agent.services.openai_stat); default model, key variable and base URL variable per name
OPENAI_STAT = {"openai": ("gpt-4o-mini", "OPENAI_API_KEY", None),
               "gemma": ("google/gemma-4-31B-it-qat-w4a16-ct", "Gemma_API_KEY", "Gemma_BASE_URL")}


def stat_llm() -> StatLLMConfig:
    """The phase 1 model, HCX-DASH-002 by default (STAT_PROVIDER hcx, HCX_API_KEY). Keys are SecretStr (never in repr,
    logs or dumps); mock needs none and keeps that model name, as the phase 1 mock baseline (s0-mock-c) recorded it.
    openai / gemma run phase 1 on gpt-4o-mini / the self-hosted Gemma (OPENAI_STAT), for model comparisons (T3-28)."""
    provider = (_env("STAT_PROVIDER") or "hcx").strip().lower()
    if provider in OPENAI_STAT:
        from kosis_agent.services.openai_stat import PROVIDER, register

        register()
        model, key_var, url_var = OPENAI_STAT[provider]
        key = _env(key_var)
        extra = {"base_url": _env(url_var)} if url_var and _env(url_var) else {}
        return StatLLMConfig(api_key=SecretStr(key) if key else None, model=_env("STAT_MODEL") or model,
                             provider=PROVIDER, **extra)
    key = _env("HCX_API_KEY")
    return StatLLMConfig(api_key=SecretStr(key) if key and provider != "mock" else None,
                         model=_env("STAT_MODEL") or STAT_MODEL, provider=provider)


def interpreter_chat_model(cfg):
    """The chat model behind the LLM steps (T3-27 follow-up reader, T3-28 multi-query splitter): INTERPRETER_MODEL
    (default gpt-4o-mini) on ChatOpenAI with OPENAI_API_KEY for gpt-*, on ChatOpenAI at Gemma_BASE_URL with
    Gemma_API_KEY for *gemma*, else ChatClovaX with HCX_API_KEY; None (rules only) when the interpreter is "rules".
    A missing key is not an error here: the hybrid steps then read by rules, as with no model set."""
    model = cfg.interpreter_model or ""
    gpt = model.lower().startswith("gpt")
    gemma = "gemma" in model.lower()  # self-hosted OpenAI-compatible server: Gemma_BASE_URL + Gemma_API_KEY
    key = _env("OPENAI_API_KEY" if gpt else "Gemma_API_KEY" if gemma else "HCX_API_KEY")
    if not model or not key or cfg.interpreter == "rules":
        return None
    if gpt or gemma:
        from langchain_openai import ChatOpenAI

        base_url = _env("Gemma_BASE_URL") if gemma else None
        return ChatOpenAI(model=model, api_key=key, base_url=base_url, temperature=0, timeout=30, max_retries=2)
    from langchain_naver import ChatClovaX

    return ChatClovaX(model=model, api_key=key, temperature=0, timeout=30, max_retries=2)


def interpreter_llm(cfg):
    """The follow-up reader's LLM step (T3-27) on interpreter_chat_model, None for rules only."""
    from kosis_agent.graph.context import make_llm_reader

    model = interpreter_chat_model(cfg)
    return make_llm_reader(model) if model is not None else None


def splitter_llm(cfg):
    """The multi-query splitter's LLM step (T3-28) on the same model, None for rules only. It runs only on turns the
    rules defer (two measures joined by a connective ending)."""
    from kosis_agent.graph.multiquery import make_llm_splitter

    model = interpreter_chat_model(cfg)
    return make_llm_splitter(model) if model is not None else None


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
                       # T3-27: gpt-4o-mini beat HCX-005 on the LLM-only reading (dev 0.798 vs 0.723) at ~1/5 of Gemma's latency
                       interpreter_model=_env("INTERPRETER_MODEL") or "gpt-4o-mini")
