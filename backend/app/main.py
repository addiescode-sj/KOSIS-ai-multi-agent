"""KOSIS agent API (T3-00 skeleton): the agent graph streamed as SSE. Logic lives in src/kosis_agent; this only exposes it."""

from __future__ import annotations

import threading
import time
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
    """Compile the agent graph with HCX on first use (not at startup, so /health works without keys); failure is a 503."""
    global _graph
    with _lock:
        if _graph is None:
            from kosis_chat.config import load_env
            from kosis_rag.chain import clova_chat

            load_env(ROOT / ".env")  # this repo's HCX_API_KEY first; existing variables are kept
            try:
                _graph = build_graph(clova_chat())
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
    """`event: token` (data = `{"delta": "..."}`) while the LLM writes, `event: <node>` (data = `{"seconds": ...}`)
    when a node finishes, then `event: result` (data = `{"answer": "..."}`)."""
    answer, start = "", time.perf_counter()
    try:
        for mode, chunk in graph.stream({"messages": [("user", req.question)]}, stream_mode=["messages", "updates"]):
            if mode == "messages":
                delta = chunk[0].content
                if delta:
                    answer += delta
                    yield ServerSentEvent(event="token", data={"delta": delta})
                continue
            for name, update in chunk.items():
                now = time.perf_counter()
                yield ServerSentEvent(event=name, data={"seconds": now - start})
                start = now
                answer = update["messages"][-1].content or answer  # the node's final message wins over the deltas
        yield ServerSentEvent(event="result", data={"answer": answer})
    except Exception as e:  # noqa: BLE001 - the status line is already sent; say it in the stream instead
        yield ServerSentEvent(event="error", data={"type": type(e).__name__, "detail": str(e)})
