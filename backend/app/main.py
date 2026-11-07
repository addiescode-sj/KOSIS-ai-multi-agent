"""KOSIS agent API: the agent graph (phase 2 search -> phase 1 stat) streamed as SSE. Logic lives in src/kosis_agent;
this only exposes it. Settings come from the shell environment (backend/app/config.py); no .env is read here."""

from __future__ import annotations

import threading
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

import kosis_agent  # noqa: F401  (sets KOSIS_WORKSPACE before phase 1 / phase 2 modules load)
from kosis_agent.graph import build_graph, chat_result

app = FastAPI(title="KOSIS Agent API", version="0.1.0", description="국가데이터처(KOSIS) 통계 AI 에이전트 API.")

_graph: Any = None
_lock = threading.Lock()


def get_graph():
    """Open the phase 2 search module (records, Chroma, models) on first use (not at startup, so /health works without
    Chroma, data/ or keys) and compile the agent graph over it and phase 1. A failure is a 503; the next request
    tries again. A missing key is not a failure here: phase 1 raises LLMKeyMissing when its LLM step runs."""
    global _graph
    with _lock:
        if _graph is None:
            from backend.app.config import agent_config
            from kosis_rag.api import SearchModule

            try:
                cfg = agent_config()
                search = SearchModule(cfg.spec, cfg.options, cfg.stage2_llm)
                _graph = build_graph(search, cfg.stat_llm, retry_interval=cfg.stat_retry_interval)
            except Exception as e:  # noqa: BLE001 - any load failure means the agent is unavailable
                raise HTTPException(503, f"agent not available: {e}") from e
        return _graph


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, description="사용자 질문 (한국어)", examples=["2023년 시도별 출생아 수"])


@app.get("/health", tags=["ops"], summary="서버 상태")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat/stream", tags=["chat"], summary="에이전트 답변 (SSE)", response_class=EventSourceResponse)
def chat_stream(req: ChatRequest, graph=Depends(get_graph)) -> Iterator[ServerSentEvent]:
    """`event: <node>` (data = that node's trace, with seconds) per finished phase 2 search node, then
    `event: stat` (data = {seconds, key, spans}) when phase 1 ran, then `event: result` (data = ChatResult: the
    answer, the tables, phase 1's StatAnswer and the Vega-Lite chart). An exception becomes `event: error`."""
    final: dict = {}
    try:
        for mode, chunk in graph.stream({"messages": [("user", req.question)]}, stream_mode=["custom", "values"]):
            if mode == "custom":
                name, payload = chunk
                yield ServerSentEvent(event=name, data=payload)
            else:
                final = chunk
        yield ServerSentEvent(event="result", data=chat_result(final))
    except Exception as e:  # noqa: BLE001 - the status line is already sent; say it in the stream instead
        yield ServerSentEvent(event="error", data={"type": type(e).__name__, "detail": str(e)})
