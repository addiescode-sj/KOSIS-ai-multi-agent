"""진입점: 서비스와 같은 설정으로 멀티에이전트 그래프를 만든다.

평가: python -m evals.run_eval --layer e2e --agent kosis_multi.build:build_agent ...
설정은 기존 backend/app/config.py가 .env(셸 값 우선)를 읽어 만든 객체를 그대로 쓴다(검색 모듈·실전 1·해석기 LLM).
TODO(S2): ① 판단 Qwen 설정(STAGE2_BASE_URL·STAGE2_MODEL·STAGE2_API_KEY)을 kosis_multi/config.py에서 읽어 넘김.
"""

from __future__ import annotations

from kosis_agent.envfile import load_env_file
from kosis_agent.graph.state import new_checkpointer
from kosis_multi.orchestrator import build_graph


def build_agent():
    """메모리 checkpointer를 단 멀티에이전트 그래프 (evals --agent용, 기존 backend.app.main:build_agent와 같은 역할)."""
    load_env_file()
    from backend.app.config import agent_config, interpreter_llm, splitter_llm
    from kosis_rag.api import SearchModule

    cfg = agent_config()
    search = SearchModule(cfg.spec, cfg.options, cfg.stage2_llm)
    return build_graph(search, cfg.stat_llm, retry_interval=cfg.stat_retry_interval, checkpointer=new_checkpointer(),
                       alt_table_budget=cfg.alt_table_budget, interpreter=cfg.interpreter, llm=interpreter_llm(cfg),
                       splitter=splitter_llm(cfg))
