"""Search settings for evals from the shell environment (phase 2 T2-41: the module reads no .env and no environment).

Same variable names and defaults as phase 2 `backend/app/config.py` `search_config_from_shell` (that module is shadowed
by this repo's `backend.app`, and CLAUDE.md §3 allows importing only `kosis_rag.*` from phase 2). Data paths point at
`<KOSIS_WORKSPACE>/data`, not `vendor/phase2/data`. `python -m evals.run_eval` / `evals.suite` load <repo>/.env into
os.environ first (kosis_agent.envfile, shell values win); this module reads os.environ.

    CHROMA_HOST CHROMA_PORT CHROMA_COLLECTION · TEXT_FORMAT EMBED_CKPT RERANK_CKPT RERANK_MODE
    STAGE2 COND_FILTER ANSWER_K · HQ_BUILD HQ_WEIGHT HQ_BM25 · HCX_API_KEY HCX_THINKING OPENAI_API_KEY

ponytail: replace with this repo's backend config (agent_config()) once T3-01 adds it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from kosis_rag.llm.config import LLMConfig
from kosis_rag.schemas.resources import OFF, ChromaConfig, IndexSpec, SearchOptions


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


def data_dir() -> Path:
    """<KOSIS_WORKSPACE>/data (evals/__init__ imports kosis_agent, which defaults KOSIS_WORKSPACE to this repo)."""
    return Path(os.environ["KOSIS_WORKSPACE"]) / "data"


def stage2_llm(name: str) -> LLMConfig | None:
    """The stage 2 LLM config for `name` (clova | hcx | gpt); None when off or unknown. The service's function, so an
    L1 stage 2 preset runs the same model and server as the service (STAGE2_MODEL / STAGE2_BASE_URL / STAGE2_API_KEY, T3-33)."""
    from backend.app.config import stage2_llm as service_stage2_llm

    return service_stage2_llm(name)


def search_config(**overrides: Any) -> tuple[IndexSpec, SearchOptions]:
    """(IndexSpec, SearchOptions) from the shell, with overrides by field name (e.g. an L1 preset's
    cond_filter / rerank_ckpt / stage2). TypeError for an unknown override."""
    data = data_dir()
    spec_values: dict[str, Any] = {
        "tables": data / "tables.jsonl", "sigungu": data / "sigungu.json", "hq_root": data / "hq",
        "text_format": _env("TEXT_FORMAT"), "embed_ckpt": _env("EMBED_CKPT"), "rerank_ckpt": _env("RERANK_CKPT"),
        "hq_build": (_env("HQ_BUILD") or "").strip() or None,
        "chroma": ChromaConfig(host=_env("CHROMA_HOST") or "localhost", port=int(_env("CHROMA_PORT") or 8001),
                               collection=_env("CHROMA_COLLECTION")),
    }
    option_values: dict[str, Any] = {
        "cond_filter": (_env("COND_FILTER") or "on").strip().lower() not in OFF,
        "rerank_mode": _env("RERANK_MODE") or "blend",
        "stage2": (_env("STAGE2") or "none").strip().lower(),
        "answer_k": int(_env("ANSWER_K") or 5),
        "hq_weight": float(_env("HQ_WEIGHT") or 1.0),
        "hq_bm25": (_env("HQ_BM25") or "off").strip().lower() not in OFF,
    }
    for key, value in overrides.items():
        if key in IndexSpec.model_fields:
            spec_values[key] = value
        elif key in SearchOptions.model_fields:
            option_values[key] = value
        else:
            raise TypeError(f"unknown search setting {key!r}")
    return IndexSpec(**spec_values), SearchOptions(**option_values)
