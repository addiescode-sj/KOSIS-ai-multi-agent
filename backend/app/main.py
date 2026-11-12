"""KOSIS agent API: the agent graph (phase 2 search -> phase 1 stat -> chart) and its tools over HTTP. Logic lives in
src/kosis_agent; this only exposes it. Settings: <repo>/.env loaded at import (shell values win), then
backend/app/config.py reads os.environ.

Observability (T3-30, KOS-114): every request is one trace under service `kosis-agent` (backend/app/telemetry.py).
The request handler, not build_agent(), attaches the span callback, so the eval layer (which calls build_agent and
uses its own provider) never writes to the service trace and the service never writes to eval reports.
Handlers are plain `def`: phase 1 / 2 are blocking I/O, so FastAPI runs them (and the SSE generator) in its threadpool.
"""

from __future__ import annotations

import re
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Any, Iterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from opentelemetry.trace import Span, StatusCode, Tracer
from pydantic import BaseModel, Field

import kosis_agent  # noqa: F401  (sets KOSIS_WORKSPACE before phase 1 / phase 2 modules load)
from backend.app import telemetry
from kosis_agent.envfile import load_env_file
from kosis_agent.graph.main_graph import build_graph, call_tool
from kosis_agent.graph.state import ChatResult, chat_result, new_checkpointer
from kosis_agent.observability.tracing import LiveSpans, start_turn, trace_id
from kosis_agent.retrieval.search import make_search_tables_tool
from kosis_agent.services.chart import make_chart_tool, to_vega_lite
from kosis_agent.services.statistics import make_summarize_stat_tool
from kosis_ai.schemas import StatAnswer
from kosis_rag.schemas.io import SearchOutput

load_env_file()  # phase 3 reads its .env; phase 1 / 2 get objects from agent_config()

TAGS = [
    {"name": "chat", "description": "에이전트 그래프 전체: context → search → statistics → chart → finalize. "
                                    "같은 `thread_id`를 보내면 이전 턴을 이어서 답한다."},
    {"name": "tools", "description": "그래프 노드가 부르는 툴을 하나씩 직접 부른다(디버깅·연동용). 대화 상태는 쓰지 않는다."},
    {"name": "threads", "description": "checkpointer에 저장된 대화 상태."},
    {"name": "ops", "description": "운영."},
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    telemetry.start()
    try:
        yield
    finally:
        telemetry.stop()


app = FastAPI(
    title="KOSIS Agent API", version="0.2.0", lifespan=lifespan, openapi_tags=TAGS,
    description=(
        "국가데이터처(KOSIS) 통계 AI 에이전트 API.\n\n"
        "- 숫자는 실전 1이 원자료에서 코드로 계산하고 검증한 값만 나간다. 자료가 없으면 거절(`outcome: refused`)한다.\n"
        "- 요청마다 OpenTelemetry trace 하나가 남는다(service `kosis-agent`). 챗 응답의 `trace_id`로 Jaeger에서 찾는다.\n"
        "- 툴·그래프가 준비되지 않으면(데이터·Chroma·키 없음) 503."))

_agent: tuple[Any, dict[str, BaseTool]] | None = None
_lock = threading.Lock()
UNAVAILABLE = {503: {"description": "에이전트를 열 수 없음 (데이터·Chroma·키 확인)"}}


def _build() -> tuple[Any, dict[str, BaseTool]]:
    """The graph and the tools its nodes call, over one phase 2 SearchModule (loaded once)."""
    from backend.app.config import agent_config, interpreter_llm, splitter_llm
    from kosis_rag.api import SearchModule

    cfg = agent_config()
    search = SearchModule(cfg.spec, cfg.options, cfg.stage2_llm)
    # ponytail: in-memory checkpointer, lost on restart and kept per thread for the process lifetime;
    # a Postgres saver once the compose Postgres is wired (KOS-121)
    graph = build_graph(search, cfg.stat_llm, retry_interval=cfg.stat_retry_interval, checkpointer=new_checkpointer(),
                        alt_table_budget=cfg.alt_table_budget, interpreter=cfg.interpreter, llm=interpreter_llm(cfg),
                        splitter=splitter_llm(cfg))
    tools = [make_search_tables_tool(search),
             make_summarize_stat_tool(cfg.stat_llm, retry_interval=cfg.stat_retry_interval), make_chart_tool()]
    return graph, {t.name: t for t in tools}


def build_agent():
    """The agent graph over the phase 2 search module and phase 1, with a checkpointer that carries each thread_id's
    conversation. The service's graph, and the agent eval layer's entry point (T3-23):
    run_eval --layer agent --agent backend.app.main:build_agent. No callbacks attached: whoever runs it brings its own
    (the request handlers below, or the eval runner). Opens the phase 2 resources (records, Chroma, models)."""
    return _build()[0]


def _loaded() -> tuple[Any, dict[str, BaseTool]]:
    """_build() on first use (not at startup, so /health works without Chroma, data/ or keys). A failure is a 503;
    the next request tries again."""
    global _agent
    with _lock:
        if _agent is None:
            try:
                _agent = _build()
            except Exception as e:  # noqa: BLE001 - any load failure means the agent is unavailable
                raise HTTPException(503, f"agent not available: {e}") from e
        return _agent


def get_graph():
    return _loaded()[0]


def get_tools() -> dict[str, BaseTool]:
    return _loaded()[1]


# ---------------------------------------------------------------- request / response models

class ChatRequest(BaseModel):
    question: str = Field(min_length=1, description="사용자 질문 (한국어)", examples=["2023년 시도별 출생아 수"])
    thread_id: str | None = Field(default=None, description="대화 ID. 같은 값을 보내면 이전 턴을 이어서 답한다. "
                                  "없으면 새 대화로 처리하고, 만든 ID를 응답에 돌려준다.", examples=["c0ffee"])


class ChatResponse(BaseModel):
    thread_id: str = Field(description="이 턴의 대화 ID (다음 턴에 그대로 보낸다)")
    trace_id: str = Field(description="이 요청의 OpenTelemetry trace ID (32자리 hex, Jaeger 검색)")
    result: ChatResult


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, description="통계표를 찾을 질문", examples=["2023년 시도별 출생아 수"])


class StatRequest(BaseModel):
    question: str = Field(min_length=1, description="표 하나로 답할 질문", examples=["2023년 출생아 수"])
    table_key: str = Field(min_length=1, description="tables.csv 키 `<org_id>_<tbl_id>` (search_tables의 picked[].key)",
                           examples=["101_DT_1B8000H"])


class StatResponse(BaseModel):
    answer: StatAnswer = Field(description="실전 1 답: answered | refused(이유 포함) | clarify")
    chart: dict | None = Field(description="실전 1 ChartSpec. `/tools/make_chart`의 `chart`로 그대로 넘긴다")
    vega_lite: dict | None = Field(description="chart를 기본 종류로 그린 Vega-Lite spec")


class ChartRequest(BaseModel):
    table_key: str = Field(min_length=1, description="차트를 만든 표 키", examples=["101_DT_1B8000H"])
    chart: dict | None = Field(default=None, description="`/tools/summarize_stat` 응답의 chart (ChartSpec)")
    chart_type: str | None = Field(default=None, description="bar | line | pie. 없으면 실전 1이 고른 종류",
                                   examples=["bar"])


class ChartResponse(BaseModel):
    vega_lite: dict | None = Field(description="Vega-Lite spec. 차트 데이터가 없으면 null")
    kind: str | None = Field(description="실제로 그린 종류")
    note: str | None = Field(description="요청한 종류로 못 그렸을 때의 안내")


class ThreadMessage(BaseModel):
    role: str = Field(description="user | assistant")
    content: str


class ThreadState(BaseModel):
    thread_id: str
    turns: int = Field(description="사용자 질문 수")
    active_table: str | None = Field(description="대화가 지금 보고 있는 표 키 (다음 턴의 \"그 표\")")
    messages: list[ThreadMessage]


# ---------------------------------------------------------------- helpers

def _snapshot(graph, thread_id: str) -> dict:
    """The thread's saved state ({} when the thread is new or the graph has no checkpointer)."""
    try:
        return graph.get_state({"configurable": {"thread_id": thread_id}}).values or {}
    except ValueError:  # graph compiled without a checkpointer
        return {}


def _turn_index(graph, thread_id: str) -> int:
    """Questions already in the thread = this turn's index (0-based, the eval runner's turn number)."""
    return sum(isinstance(m, HumanMessage) for m in _snapshot(graph, thread_id).get("messages") or [])


def tokens(text: str) -> list[str]:
    """The answer split at word starts, whitespace kept, so "".join(tokens(t)) == t."""
    return [piece for piece in re.split(r"(?<=\s)(?=\S)", text) if piece]


def _fail(span: Span, error: BaseException) -> None:
    span.record_exception(error)
    span.set_status(StatusCode.ERROR, f"{type(error).__name__}: {error}")


def _run_tool(tracer: Tracer, name: str, tools: dict[str, BaseTool], args: dict) -> Any:
    """One tool call as its own trace: `POST /tools/<name>` root, `execute_tool <name>` under it (the callback)."""
    root = tracer.start_span(f"POST /tools/{name}", attributes={"http.route": f"/tools/{name}"})
    try:
        return call_tool(tools[name], args, {"callbacks": [LiveSpans(tracer, root, telemetry.get_meter())]}).artifact
    except Exception as e:
        _fail(root, e)
        raise
    finally:
        root.end()


# ---------------------------------------------------------------- endpoints

@app.get("/health", tags=["ops"], summary="서버 상태")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat/stream", tags=["chat"], summary="에이전트 답변 (SSE, 토큰 스트리밍)",
          response_class=EventSourceResponse, responses=UNAVAILABLE)
def chat_stream(req: ChatRequest, graph=Depends(get_graph),
                tracer: Tracer = Depends(telemetry.get_tracer)) -> Iterator[ServerSentEvent]:
    """이벤트 순서:

    1. `meta` — `{thread_id, trace_id}`
    2. `<노드 이름>` — 실전 2 검색 노드가 끝날 때마다 그 노드의 trace 항목(`seconds` 포함)
    3. `stat` — 실전 1이 돌았으면 `{seconds, key, spans}` (표마다)
    4. `token` — 답 문장을 어절 단위로 `{delta}`. 실전 1이 숫자 검증을 끝낸 문장만 흘린다(검증 전 LLM 출력 아님)
    5. `result` — `ChatResult` (답·표·출처·StatAnswer·Vega-Lite). 화면의 최종 답은 이것으로 바꾼다

    실행 중 예외는 `error` (`{type, detail}`)로 끝난다(상태 줄은 이미 200으로 나감)."""
    thread = req.thread_id or uuid.uuid4().hex  # no thread_id: a new conversation
    root, spans = start_turn(tracer, thread, _turn_index(graph, thread), telemetry.get_meter())
    final: dict = {}
    try:
        yield ServerSentEvent(event="meta", data={"thread_id": thread, "trace_id": trace_id(root)})
        for mode, chunk in graph.stream({"messages": [("user", req.question)]},
                                        {"configurable": {"thread_id": thread}, "callbacks": [spans]},
                                        stream_mode=["custom", "values"]):
            if mode == "custom":
                name, payload = chunk
                yield ServerSentEvent(event=name, data=payload)
            else:
                final = chunk
        result = chat_result(final)
        for piece in tokens(result.answer):
            yield ServerSentEvent(event="token", data={"delta": piece})
        yield ServerSentEvent(event="result", data=result)
    except Exception as e:  # noqa: BLE001 - the status line is already sent; say it in the stream instead
        _fail(root, e)
        yield ServerSentEvent(event="error", data={"type": type(e).__name__, "detail": str(e)})
    finally:  # also on client disconnect (GeneratorExit): the root span always ends
        root.end()


@app.post("/chat", tags=["chat"], summary="에이전트 답변 (한 번에)", response_model=ChatResponse,
          responses={**UNAVAILABLE, 500: {"description": "실행 중 오류 (trace에 기록됨)"}})
def chat(req: ChatRequest, graph=Depends(get_graph), tracer: Tracer = Depends(telemetry.get_tracer)) -> ChatResponse:
    """`/chat/stream`과 같은 그래프·같은 답. 스트림 없이 끝난 뒤 `ChatResult`를 돌려준다."""
    thread = req.thread_id or uuid.uuid4().hex
    root, spans = start_turn(tracer, thread, _turn_index(graph, thread), telemetry.get_meter())
    try:
        state = graph.invoke({"messages": [("user", req.question)]},
                             {"configurable": {"thread_id": thread}, "callbacks": [spans]})
        return ChatResponse(thread_id=thread, trace_id=trace_id(root), result=chat_result(state))
    except Exception as e:
        _fail(root, e)
        raise HTTPException(500, f"{type(e).__name__}: {e}  (trace_id {trace_id(root)})") from e
    finally:
        root.end()


@app.post("/tools/search_tables", tags=["tools"], summary="통계표 검색 (실전 2)", response_model=SearchOutput,
          responses=UNAVAILABLE)
def search_tables(req: SearchRequest, tools=Depends(get_tools),
                  tracer: Tracer = Depends(telemetry.get_tracer)) -> SearchOutput:
    """search 노드가 부르는 `search_tables` 툴. `route`가 `refuse`면 `refusal_text`가 사용자에게 보일 문장이다."""
    return _run_tool(tracer, "search_tables", tools, {"question": req.question})


@app.post("/tools/summarize_stat", tags=["tools"], summary="표 하나로 수치 답 (실전 1)", response_model=StatResponse,
          responses=UNAVAILABLE)
def summarize_stat(req: StatRequest, tools=Depends(get_tools),
                   tracer: Tracer = Depends(telemetry.get_tracer)) -> StatResponse:
    """statistics 노드가 부르는 `summarize_stat` 툴. 숫자는 원자료에서 코드가 계산한다. 표 키가 tables.csv에 없으면
    `answer.outcome = refused` (out_of_scope)."""
    out = _run_tool(tracer, "summarize_stat", tools, {"question": req.question, "table_key": req.table_key})
    chart = out.chart.model_dump(mode="json") if out.chart is not None else None
    return StatResponse(answer=out.answer, chart=chart, vega_lite=to_vega_lite(out.chart))


@app.post("/tools/make_chart", tags=["tools"], summary="Vega-Lite 차트", response_model=ChartResponse,
          responses=UNAVAILABLE)
def make_chart(req: ChartRequest, tools=Depends(get_tools),
               tracer: Tracer = Depends(telemetry.get_tracer)) -> ChartResponse:
    """chart 노드가 부르는 `make_chart` 툴. 요청한 종류로 그릴 수 없으면 원래 종류로 그리고 `note`에 이유를 적는다."""
    return ChartResponse(**_run_tool(tracer, "make_chart", tools, req.model_dump()))


@app.get("/threads/{thread_id}", tags=["threads"], summary="대화 상태", response_model=ThreadState,
         responses={**UNAVAILABLE, 404: {"description": "저장된 대화 없음"}})
def thread_state(thread_id: str, graph=Depends(get_graph)) -> ThreadState:
    """checkpointer에 저장된 이 대화의 메시지와 지금 보고 있는 표. 서버를 다시 띄우면 비어 있다(메모리 checkpointer)."""
    state = _snapshot(graph, thread_id)
    messages = state.get("messages") or []
    if not messages:
        raise HTTPException(404, f"no conversation {thread_id!r}")
    return ThreadState(thread_id=thread_id, turns=sum(isinstance(m, HumanMessage) for m in messages),
                       active_table=state.get("active_table"),
                       messages=[ThreadMessage(role="user" if isinstance(m, HumanMessage) else "assistant",
                                               content=str(m.content)) for m in messages])
