"""KOSIS agent API (T3-00 skeleton): the multi-agent graph streamed as SSE. Logic lives in src/kosis_agent; this only exposes it."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

import kosis_agent  # noqa: F401  (sets KOSIS_WORKSPACE before phase 1 / phase 2 modules load)
from kosis_agent.graph import build_graph

ROOT = Path(__file__).resolve().parents[2]

app = FastAPI(title="KOSIS Agent API", version="0.1.0", description="국가데이터처(KOSIS) 통계 AI 에이전트 API.")

_graph: Any = None
_lock = threading.Lock()


def get_graph():
    """Load the phase 2 search resources and HCX on first use (not at startup, so /health works without Chroma,
    data/ or keys) and compile the agent graph over the phase 2 search graph. A failure is a 503; the next request
    tries again."""
    global _graph
    with _lock:
        if _graph is None:
            from kosis_chat.config import load_env
            from kosis_rag.chain import clova_chat
            from kosis_rag.graph import build_graph as build_search_graph
            from kosis_rag.pipeline import load_resources

            load_env(ROOT / ".env")  # this repo's keys first; existing variables are kept
            try:
                resources = load_resources()
                search_graph = build_search_graph(resources, clova_chat(), k=resources.settings.answer_k)
                _graph = build_graph(search_graph)
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
    """Phase 2 search events as they are: `event: <node>` (data = that node's trace, e.g. seconds) per finished
    search node, `event: token` (data = `{"delta": "..."}`) while guide writes its checked reply, then
    `event: result` (data = `SearchResult`, the answer with its citations)."""
    final: dict = {}
    try:
        for mode, chunk in graph.stream({"messages": [("user", req.question)]}, stream_mode=["custom", "values"]):
            if mode == "custom":
                name, payload = chunk
                yield ServerSentEvent(event=name, data=payload)
            else:
                final = chunk
        yield ServerSentEvent(event="result", data=final["search"])
    except Exception as e:  # noqa: BLE001 - the status line is already sent; say it in the stream instead
        yield ServerSentEvent(event="error", data={"type": type(e).__name__, "detail": str(e)})
