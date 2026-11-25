"""진입점: 서비스와 같은 설정으로 멀티에이전트 그래프를 만든다.

평가: python -m evals.run_eval --layer e2e --agent kosis_multi.build:build_agent ...
서비스(임시): .env에 AGENT_GRAPH=multi를 두면 backend/app/main.py가 build_multi_graph와 result.multi_chat_result를 쓴다.
설정은 기존 backend/app/config.py가 .env(셸 값 우선)를 읽어 만든 객체를 그대로 쓴다(검색 모듈·실전 1·해석기 LLM).
① 판단 Qwen은 kosis_multi/config.py가 STAGE2_BASE_URL·STAGE2_MODEL·STAGE2_API_KEY로 만든다(MULTI_JUDGE=off면 규칙만).
계산용 다듬기(S4b)도 같은 Qwen을 쓴다(MULTI_CALC_REWRITE=on일 때만, 기본 끔).
"""

from __future__ import annotations

from kosis_agent.envfile import load_env_file
from kosis_agent.graph.state import new_checkpointer
from kosis_multi.agents.judge import make_judge_llm
from kosis_multi.agents.rewrite import make_rewrite_llm
from kosis_multi.config import judge_chat_model, multi_config
from kosis_multi.orchestrator import build_graph


def build_multi_graph(search, cfg, checkpointer):
    """멀티에이전트 그래프: 검색 모듈·backend 설정(agent_config)은 받은 것을 쓰고, Qwen 설정만 kosis_multi/config에서 읽는다.
    평가(build_agent)와 서비스 임시 연결(backend/app/main.py, AGENT_GRAPH=multi)이 같이 쓴다."""
    from backend.app.config import interpreter_llm, splitter_llm

    multi = multi_config()
    qwen = judge_chat_model(multi.judge) if (multi.judge_on or multi.calc_rewrite) else None
    return build_graph(search, cfg.stat_llm, retry_interval=cfg.stat_retry_interval, checkpointer=checkpointer,
                       alt_table_budget=cfg.alt_table_budget, interpreter=cfg.interpreter, llm=interpreter_llm(cfg),
                       splitter=splitter_llm(cfg),
                       judge_llm=make_judge_llm(qwen) if qwen is not None and multi.judge_on else None,
                       rewrite_llm=make_rewrite_llm(qwen) if qwen is not None and multi.calc_rewrite else None,
                       prefetch=multi.prefetch)


def build_agent():
    """메모리 checkpointer를 단 멀티에이전트 그래프 (evals --agent용, 기존 backend.app.main:build_agent와 같은 역할)."""
    load_env_file()
    from backend.app.config import agent_config
    from backend.app.search_open import open_search

    cfg = agent_config()
    search = open_search(cfg)  # 서비스와 같은 열기(EMBED_URL·RERANK_URL)
    return build_multi_graph(search, cfg, new_checkpointer())
