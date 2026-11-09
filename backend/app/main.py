"""KOSIS agent API: the agent graph (phase 2 search -> phase 1 stat) streamed as SSE. Logic lives in src/kosis_agent;
this only exposes it. Settings: <repo>/.env loaded at import (shell values win), then backend/app/config.py reads os.environ."""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

import kosis_agent  # noqa: F401  (sets KOSIS_WORKSPACE before phase 1 / phase 2 modules load)
from kosis_agent.envfile import load_env_file
from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.state import chat_result, new_checkpointer

load_env_file()  # phase 3 reads its .env; phase 1 / 2 get objects from agent_config()

app = FastAPI(title="KOSIS Agent API", version="0.1.0", description="국가데이터처(KOSIS) 통계 AI 에이전트 API.")

_graph: Any = None
_lock = threading.Lock()


def build_agent():
    """The agent graph over the phase 2 search module and phase 1, with a checkpointer that carries each thread_id's
    conversation. The service's graph, and the agent eval layer's entry point (T3-23):
    run_eval --layer agent --agent backend.app.main:build_agent. Settings come from the shell (backend/app/config.py).
    Opens the phase 2 resources (records, Chroma, models); a missing key fails only when an LLM step runs."""
    from backend.app.config import agent_config
    from kosis_rag.api import SearchModule

    cfg = agent_config()
    search = SearchModule(cfg.spec, cfg.options, cfg.stage2_llm)
    # ponytail: in-memory checkpointer, lost on restart and kept per thread for the process lifetime;
    # a Postgres saver once the compose Postgres is wired (KOS-86)
    return build_graph(search, cfg.stat_llm, retry_interval=cfg.stat_retry_interval, checkpointer=new_checkpointer(),
                       alt_table_budget=cfg.alt_table_budget, interpreter=cfg.interpreter, llm=interpreter_llm(cfg))


def interpreter_llm(cfg):
    """The follow-up reader's LLM step (T3-27): ChatClovaX on INTERPRETER_MODEL with HCX_API_KEY, else None (rules only).
    A missing key is not an error here: the hybrid reader then reads by rules, as with no model set."""
    key = os.environ.get("HCX_API_KEY")
    if not cfg.interpreter_model or not key or cfg.interpreter == "rules":
        return None
    from langchain_naver import ChatClovaX

    from kosis_agent.graph.context import make_llm_reader

    return make_llm_reader(ChatClovaX(model=cfg.interpreter_model, api_key=key, temperature=0))


def get_graph():
    """build_agent() on first use (not at startup, so /health works without Chroma, data/ or keys). A failure is a
    503; the next request tries again."""
    global _graph
    with _lock:
        if _graph is None:
            try:
                _graph = build_agent()
            except Exception as e:  # noqa: BLE001 - any load failure means the agent is unavailable
                raise HTTPException(503, f"agent not available: {e}") from e
        return _graph


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, description="사용자 질문 (한국어)", examples=["2023년 시도별 출생아 수"])
    thread_id: str | None = Field(default=None, description="대화 ID. 같은 값을 보내면 이전 턴을 이어서 답한다. "
                                  "없으면 새 대화로 처리한다.", examples=["c0ffee"])


@app.get("/health", tags=["ops"], summary="서버 상태")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat/stream", tags=["chat"], summary="에이전트 답변 (SSE)", response_class=EventSourceResponse)
def chat_stream(req: ChatRequest, graph=Depends(get_graph)) -> Iterator[ServerSentEvent]:
    """`event: <node>` (data = that node's trace, with seconds) per finished phase 2 search node, then
    `event: stat` (data = {seconds, key, spans}) when phase 1 ran, then `event: result` (data = ChatResult: the
    answer, the tables, phase 1's StatAnswer and the Vega-Lite chart). An exception becomes `event: error`."""
    final: dict = {}
    config = {"configurable": {"thread_id": req.thread_id or uuid.uuid4().hex}}  # no thread_id: a new conversation
    try:
        for mode, chunk in graph.stream({"messages": [("user", req.question)]}, config,
                                        stream_mode=["custom", "values"]):
            if mode == "custom":
                name, payload = chunk
                yield ServerSentEvent(event=name, data=payload)
            else:
                final = chunk
        yield ServerSentEvent(event="result", data=chat_result(final))
    except Exception as e:  # noqa: BLE001 - the status line is already sent; say it in the stream instead
        yield ServerSentEvent(event="error", data={"type": type(e).__name__, "detail": str(e)})
