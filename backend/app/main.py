"""KOSIS agent API: the agent graph (phase 2 search -> phase 1 stat -> chart) and its tools over HTTP. Logic lives in
src/kosis_agent; this only exposes it. Settings: <repo>/.env loaded at import (shell values win), then
backend/app/config.py reads os.environ. The contract is docs/service/api.md (KOS-155, T3-51).

Observability (T3-30, KOS-114): every request is one trace under service `kosis-agent` (backend/app/telemetry.py).
The request handler, not build_agent(), attaches the span callback, so the eval layer (which calls build_agent and
uses its own provider) never writes to the service trace and the service never writes to eval reports.
Handlers are plain `def`: phase 1 / 2 are blocking I/O, so FastAPI runs them (and the SSE generator) in its threadpool.

Conversations (T3-51): the service keeps each thread's state in PostgreSQL (LangGraph PostgresSaver) when DATABASE_URL
is set, so a restart or another node continues it; build_agent() (evaluation) keeps the in-memory checkpointer.
Who owns a thread, guest expiry (1 hour) and request owners for feedback are in kosis_store.service_store.
Members (T3-54): Kakao login is in backend/app/auth.py; a member's turns are saved and listed (GET /chat).
A login problem never blocks /chat/stream (it answers as a guest); the member endpoints answer 503.
Developer endpoints live under /dev/* (the old paths stay, deprecated, until the frontend moves); set
KOSIS_DISABLE_DEV_ROUTES=1 in deployment to answer 404 for all of them.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from opentelemetry.trace import Span, Tracer

import kosis_agent  # noqa: F401  (sets KOSIS_WORKSPACE before phase 1 / phase 2 modules load)
from backend.app import auth, telemetry
from kosis_agent.envfile import load_env_file
from kosis_agent.graph.main_graph import build_graph, call_tool
from kosis_agent.graph.state import ChatResult, chat_result, new_checkpointer
from kosis_agent.observability.tracing import LiveSpans, record_error, start_turn, trace_id
from kosis_agent.retrieval.search import make_search_tables_tool
from kosis_agent.services.chart import make_chart_tool, to_vega_lite
from kosis_agent.services.statistics import make_summarize_stat_tool
from kosis_rag.schemas.io import SearchOutput
from kosis_store.auth.schemas import MeResponse, MeUpdate
from kosis_store.chat.schemas import (ChatRequest, ChatResponse, ConversationDetail, ConversationItem,
                                      ConversationList, ConversationTurn, ThreadMessage, ThreadState)
from kosis_store.events.schemas import EventsResponse
from kosis_store.feedback.schemas import FeedbackRequest, FeedbackResponse
from kosis_store.log_collector import MAX_EVENTS_PER_BATCH, TurnLogCollector
from kosis_store.masking import mask
from kosis_store.schemas import ErrorResponse
from kosis_store.service_models import FEEDBACK_CATEGORIES
from kosis_store.service_store import (FEEDBACK_WINDOW, BadCursor, Busy, Member, Opened, ServiceStore,
                                       make_service_store)
from kosis_store.tools.schemas import ChartRequest, ChartResponse, SearchRequest, StatRequest, StatResponse

load_env_file()  # phase 3 reads its .env; phase 1 / 2 get objects from agent_config()
log = logging.getLogger("kosis.api")

TAGS = [
    {"name": "chat", "description": "에이전트 그래프 전체: context → search → statistics → chart → finalize. "
                                    "같은 `thread_id`를 보내면 이전 턴을 이어서 답한다."},
    {"name": "auth", "description": "카카오 로그인 · 로그아웃 · 탈퇴. 로그인 상태는 httpOnly 쿠키."},
    {"name": "feedback", "description": "답 하나에 대한 👍/👎 · 선택지 · 이유."},
    {"name": "dev", "description": "개발·평가용(`/dev/*`). 배포에서는 막는다(`KOSIS_DISABLE_DEV_ROUTES=1`)."},
    {"name": "ops", "description": "운영."},
]

# old developer paths, kept until the frontend uses /dev/* (docs/service/api.md 8)
LEGACY_DEV_PATHS = re.compile(r"^/(tools/.+|threads/.+)$")


@asynccontextmanager
async def lifespan(_: FastAPI):
    auth.check_settings()
    auth.install_log_filter()
    telemetry.start()
    try:
        yield
    finally:
        telemetry.stop()


app = FastAPI(
    title="KOSIS Agent API", version="0.3.0", lifespan=lifespan, openapi_tags=TAGS,
    description=(
        "국가데이터처(KOSIS) 통계 AI 에이전트 API. 계약: docs/service/api.md (KOS-155).\n\n"
        "- 숫자는 실전 1이 원자료에서 코드로 계산하고 검증한 값만 나간다. 자료가 없으면 거절(`outcome: refused`)한다.\n"
        "- 요청마다 OpenTelemetry trace 하나가 남는다(service `kosis-agent`). `request_id` = `trace_id`로 Jaeger에서 찾는다.\n"
        "- 비회원은 `X-Session-Id` 헤더(브라우저가 만든 임의 UUID)를 보낸다. 대화는 그 세션만 이어 묻거나 지울 수 있다.\n"
        "- 툴·그래프가 준비되지 않으면(데이터·벡터 DB·키 없음) 503."))


class ApiError(Exception):
    """An error of a non-SSE endpoint in the contract's shape: {"error": {"code", "message"}} (api.md 0)."""

    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


@app.exception_handler(ApiError)
def _api_error(_: Request, e: ApiError) -> JSONResponse:
    return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status)


@app.middleware("http")
async def _dev_routes_gate(request: Request, call_next):
    """KOSIS_DISABLE_DEV_ROUTES=1 (deployment): /dev/* and the old developer paths answer 404 (api.md 1, 8)."""
    if os.environ.get("KOSIS_DISABLE_DEV_ROUTES", "").strip() == "1":
        path = request.url.path
        if (path.startswith("/dev/") or LEGACY_DEV_PATHS.match(path)
                or (path == "/chat" and request.method == "POST")):
            return JSONResponse({"error": {"code": "not_found", "message": "Not Found"}}, status_code=404)
    return await call_next(request)


_agent: tuple[Any, dict[str, BaseTool]] | None = None
_store: ServiceStore | None = None
_checkpointer: Any = None
_lock = threading.Lock()
UNAVAILABLE = {503: {"description": "에이전트를 열 수 없음 (데이터·벡터 DB·키 확인)"}}
BUSY = {409: {"description": "회원 대화의 앞 질문에 답하는 중", "model": ErrorResponse}}


def _build(checkpointer: Any = None) -> tuple[Any, dict[str, BaseTool]]:
    """The graph and the tools its nodes call, over one phase 2 SearchModule (loaded once). checkpointer None = an
    in-memory one (evaluation); the service passes get_checkpointer()."""
    from backend.app.config import agent_config, interpreter_llm, splitter_llm
    from kosis_rag.api import SearchModule

    cfg = agent_config()
    search = SearchModule(cfg.spec, cfg.options, cfg.stage2_llm)
    graph = build_graph(search, cfg.stat_llm, retry_interval=cfg.stat_retry_interval,
                        checkpointer=checkpointer if checkpointer is not None else new_checkpointer(),
                        alt_table_budget=cfg.alt_table_budget, interpreter=cfg.interpreter, llm=interpreter_llm(cfg),
                        splitter=splitter_llm(cfg))
    tools = [make_search_tables_tool(search),
             make_summarize_stat_tool(cfg.stat_llm, retry_interval=cfg.stat_retry_interval), make_chart_tool()]
    return graph, {t.name: t for t in tools}


def build_agent():
    """The agent graph over the phase 2 search module and phase 1, with an in-memory checkpointer that carries each
    thread_id's conversation. The agent eval layer's entry point (T3-23):
    run_eval --layer agent --agent backend.app.main:build_agent. No callbacks attached: whoever runs it brings its own
    (the request handlers below, or the eval runner). Opens the phase 2 resources (records, vector store, models)."""
    return _build()[0]


def get_checkpointer() -> Any:
    """The service checkpointer: PostgreSQL on DATABASE_URL (T3-51), in memory when it is not set or cannot be opened."""
    global _checkpointer
    with _lock:
        if _checkpointer is None:
            url = os.environ.get("DATABASE_URL", "").strip()
            if url:
                try:
                    from kosis_agent.graph.state import postgres_checkpointer

                    _checkpointer = postgres_checkpointer(url)
                except Exception as e:  # noqa: BLE001 - answers go on; conversations then live in this process only
                    log.warning("PostgreSQL checkpointer unavailable, using memory: %s", e)
            if _checkpointer is None:
                _checkpointer = new_checkpointer()
        return _checkpointer


def get_store() -> ServiceStore:
    """FastAPI dependency: conversation owners, guest expiry, request owners and feedback (tests override it)."""
    global _store
    with _lock:
        if _store is None:
            _store = make_service_store()
        return _store


def disconnect_db() -> None:
    """Close every database connection of this process: queued log rows are written first (telemetry.stop), then the
    checkpointer pool and the store engine. Called by backend/app/server.py on the way out; calling it again is safe.
    One failing close does not stop the others."""
    global _agent, _store, _checkpointer
    store, checkpointer = _store, _checkpointer  # no _lock: a thread stuck while opening must not block the exit
    _agent, _store, _checkpointer = None, None, None
    pool = getattr(checkpointer, "conn", None)  # PostgresSaver's pool; the in-memory checkpointer has none
    for close in (telemetry.stop, getattr(pool, "close", None), getattr(store, "close", None)):
        if close is not None:
            try:
                close()
            except Exception:  # noqa: BLE001 - keep closing the rest
                log.exception("closing the database failed")


def _loaded() -> tuple[Any, dict[str, BaseTool]]:
    """_build() on first use (not at startup, so /health works without the vector store, data/ or keys). A failure is a 503;
    the next request tries again."""
    global _agent
    checkpointer = get_checkpointer()
    with _lock:
        if _agent is None:
            try:
                _agent = _build(checkpointer)
            except Exception as e:  # noqa: BLE001 - any load failure means the agent is unavailable
                raise HTTPException(503, f"agent not available: {e}") from e
        return _agent


def get_graph():
    return _loaded()[0]


def get_tools() -> dict[str, BaseTool]:
    return _loaded()[1]


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


def _record_turn(root: Span, question: str, result: ChatResult | None = None) -> None:
    """What online evaluation reads back from a service trace (T3-43): the user's question and, once the turn ended,
    the final answer, the table it came from and the standalone question phase 1 got. The tool spans hold the
    numbers (summarize_stat result); without these the turn cannot be rebuilt (the search argument is the rewritten
    query, a reused-table turn has no search, and combine / refusal sentences are in no tool result)."""
    root.set_attribute("kosis.turn.question", question)
    if result is not None:
        root.set_attributes({k: v for k, v in {"kosis.turn.answer": result.answer, "kosis.turn.table_key": result.table_key,
                                               "kosis.turn.resolved_question": result.resolved_question}.items() if v})


def _fail(span: Span, error: BaseException) -> None:
    record_error(span, error)  # T3-44: a cut copy in the trace; the response still carries the full error


def _run_tool(tracer: Tracer, name: str, tools: dict[str, BaseTool], args: dict) -> Any:
    """One tool call as its own trace: `POST /dev/tools/<name>` root, `execute_tool <name>` under it (the callback)."""
    root = tracer.start_span(f"POST /dev/tools/{name}", attributes={"http.route": f"/dev/tools/{name}"})
    try:
        return call_tool(tools[name], args, {"callbacks": [LiveSpans(tracer, root, telemetry.get_meter())]}).artifact
    except Exception as e:
        _fail(root, e)
        raise
    finally:
        root.end()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _internal(*values: Any) -> bool:
    """Harness or team traffic: the X-Internal header (contract) or X-Kosis-Internal (T3-48 harness). The browser
    proxy never sends them. A value that is not a str is an unfilled parameter of a direct call (tests)."""
    return any(isinstance(v, str) and v.strip().lower() in {"1", "true", "yes"} for v in values)


def _session(header: Any, body: uuid.UUID | None = None) -> str | None:
    """The guest session: X-Session-Id, else the old body field. None when neither was sent."""
    if isinstance(header, str) and header.strip():
        return header.strip()[:64]
    return str(body) if body is not None else None


def _open(store: Any, req: ChatRequest, session: str | None, member: Member | None = None) -> Opened:
    """The conversation this question continues (owner and expiry checked). When the store fails the owner cannot be
    checked, so a new conversation is used. Without a store (direct calls in tests) the thread sent is used as is.
    Raises Busy when a member's conversation is still answering."""
    if not isinstance(store, ServiceStore):
        return Opened(req.thread_id or uuid.uuid4().hex, expired=False, new=req.thread_id is None)
    try:
        if member is not None:
            return store.open_member_thread(req.thread_id, member.id, _now())
        return store.open_thread(req.thread_id, session, _now())
    except Busy:
        raise
    except Exception as e:  # noqa: BLE001 - answering never waits on the store
        log.warning("service store unavailable, new conversation: %s", type(e).__name__)
        return Opened(uuid.uuid4().hex, expired=False, new=True)


@dataclass(frozen=True)
class Turn:
    """Whose question it is and the conversation it continues, settled before the response starts (so 409 and 403 can
    still be a status code)."""

    opened: Opened
    session: str | None
    member: Member | None


def begin_turn(req: ChatRequest, request: Request, graph=Depends(get_graph),
               store: ServiceStore = Depends(get_store),
               x_session_id: str | None = Header(default=None)) -> Turn:
    session = _session(x_session_id, req.session_id)
    member, token = None, auth.login_token(request)
    if token:
        if auth.foreign_origin(request):
            raise ApiError(403, "forbidden_origin", "허용되지 않은 요청입니다.")
        try:
            member = store.member(token, _now())
        except Exception as e:  # noqa: BLE001 - a login problem must not block the answer
            log.warning("login not checked, answering as a guest: %s", type(e).__name__)
    try:
        return Turn(_open(store, req, session, member), session, member)
    except Busy:
        raise ApiError(409, "conversation_busy", "이 대화의 앞 질문에 답하는 중입니다.") from None


def _save_turn(store: Any, turn: Turn, req: ChatRequest, result: ChatResult) -> None:
    """Keep a member's finished turn. A failure is logged; the answer still goes out."""
    if turn.member is None:
        return
    try:
        store.save_turn(turn.opened.thread_id, turn.member.id, req.question, result.answer,
                        result.model_dump(mode="json"), _now())
    except Exception as e:  # noqa: BLE001
        log.warning("member turn not saved: %s", type(e).__name__)


def _end_turn(store: Any, turn: Turn, graph: Any) -> None:
    """Let the next question of a member's conversation start; if it was deleted meanwhile, drop what this turn left."""
    if turn.member is None:
        return
    try:
        if not store.end_turn(turn.opened.thread_id):
            saver = getattr(graph, "checkpointer", None)
            if saver is not None:
                saver.delete_thread(turn.opened.thread_id)
    except Exception as e:  # noqa: BLE001
        log.warning("member turn not closed: %s", type(e).__name__)


def current_member(request: Request, store: ServiceStore = Depends(get_store)) -> Member | None:
    """The signed-in member, None for a guest. 503 when the login cannot be checked."""
    token = auth.login_token(request)
    if token is None:
        return None
    if request.method not in ("GET", "HEAD") and auth.foreign_origin(request):
        raise ApiError(403, "forbidden_origin", "허용되지 않은 요청입니다.")
    try:
        return store.member(token, _now())
    except Exception as e:  # noqa: BLE001
        log.warning("login not checked: %s", type(e).__name__)
        raise ApiError(503, "unavailable", "잠시 뒤 다시 시도해 주세요.") from None


def require_member(member: Member | None = Depends(current_member)) -> Member:
    if member is None:
        raise ApiError(401, "login_required", "로그인이 필요합니다.")
    return member


def _record_request(store: Any, request_id: str, thread_id: str, session: str | None,
                    member: Member | None = None) -> None:
    if isinstance(store, ServiceStore):
        try:
            store.record_request(request_id, thread_id, session, _now(), member.id if member else None)
        except Exception as e:  # noqa: BLE001 - feedback for this answer will then 404; the answer goes on
            log.warning("request owner not stored: %s", e)


def _log_turn(collector: TurnLogCollector | None, root: Span, req: ChatRequest, result: ChatResult | None, *,
              internal: bool, session: str | None, aborted: bool = False, first_token_ms: int | None = None,
              is_member: bool = False) -> None:
    """Hand the finished turn to the user log (T3-48). Call after root.end(). Never raises: logging must not touch
    the answer. The question goes in masked; harness traffic keeps no text (the row builder drops it)."""
    if not isinstance(collector, TurnLogCollector):  # None (log off) or an unfilled Depends (direct call in tests)
        return
    try:
        session_uuid = uuid.UUID(session) if session else None
    except ValueError:
        session_uuid = None  # the log keeps UUIDs only
    try:
        collector.finish(root, result.model_dump(mode="json") if result is not None else None, aborted=aborted,
                         first_token_ms=first_token_ms, question=mask(req.question), session_id=session_uuid,
                         is_internal=internal, input_method=req.input_method, is_member=is_member)
    except Exception:  # noqa: BLE001 - finish already counts its own failures; this guards the call itself
        pass


def _retryable(e: BaseException) -> bool:
    """Whether "다시 시도" can help: not for bad input."""
    return not isinstance(e, (ValueError, TypeError, KeyError))


# ---------------------------------------------------------------- endpoints

@app.get("/health", tags=["ops"], summary="서버 상태")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ops/log-counts", tags=["ops"], summary="사용자 로그 쓰기 건수 (T3-48)")
def log_counts(collector: TurnLogCollector | None = Depends(telemetry.get_collector)) -> dict[str, Any]:
    """서비스가 뜬 뒤 쓴 행·실패·건너뛴 수 (`written_request_logs`, `failed_ui_events`, `skipped_no_trace` 등).
    로그 쓰기가 꺼져 있으면(`DATABASE_URL` 없음) `enabled: false`."""
    return {"enabled": collector is not None, "counts": dict(collector.counts) if collector else {}}


@app.post("/chat/stream", tags=["chat"], summary="에이전트 답변 (SSE, 토큰 스트리밍)",
          response_class=EventSourceResponse, responses={**UNAVAILABLE, **BUSY})
def chat_stream(req: ChatRequest, graph=Depends(get_graph), tracer: Tracer = Depends(telemetry.get_tracer),
                collector: TurnLogCollector | None = Depends(telemetry.get_collector),
                store: ServiceStore = Depends(get_store), turn: Turn = Depends(begin_turn),
                x_internal: str | None = Header(default=None),
                x_kosis_internal: str | None = Header(default=None)) -> Iterator[ServerSentEvent]:
    """이벤트 순서 (docs/service/api.md 2):

    1. `meta` — `{thread_id, trace_id, expired}`. `trace_id` = 이 질문의 `request_id`. `expired`가 true면 보낸 대화가
       1시간 지나 만료돼 새 대화(새 `thread_id`)로 답한다. 남의 대화 ID를 보내면 새 대화로 답한다(`expired` false)
    2. `<노드 이름>` — 실전 2 검색 노드가 끝날 때마다 그 노드의 trace 항목(`seconds` 포함)
    3. `stat` — 실전 1이 돌았으면 `{seconds, key, spans}` (표마다)
    4. `token` — 답 문장을 어절 단위로 `{delta}`. 실전 1이 숫자 검증을 끝낸 문장만 흘린다(검증 전 LLM 출력 아님)
    5. `result` — `ChatResult` (`request_id` 포함). 화면의 최종 답은 이것으로 바꾼다

    실행 중 예외는 `error` (`{type, detail, retryable, request_id}`)로 끝난다(상태 줄은 이미 200으로 나감)."""
    if not isinstance(turn, Turn):  # a direct call (tests): no dependencies were filled
        session = _session(None, req.session_id)
        turn = Turn(_open(store, req, session), session, None)
    internal = _internal(x_internal, x_kosis_internal)
    session, opened, thread = turn.session, turn.opened, turn.opened.thread_id
    started = time.perf_counter()
    root, spans = start_turn(tracer, thread, _turn_index(graph, thread), telemetry.get_meter())
    request_id = trace_id(root)
    _record_turn(root, req.question)
    if internal:
        root.set_attribute("kosis.internal", True)  # docs/log-schema.md 10.3: Prometheus leaves harness traffic out
    if req.retry_of:
        root.set_attribute("kosis.turn.retry_of", req.retry_of[:32])
    _record_request(store, request_id, thread, session, turn.member)  # before meta: feedback right after the answer finds it
    final: dict = {}
    result: ChatResult | None = None
    aborted, first_token_ms = False, None
    try:
        yield ServerSentEvent(event="meta", data={"thread_id": thread, "trace_id": request_id,
                                                  "expired": opened.expired})
        for mode, chunk in graph.stream({"messages": [("user", req.question)]},
                                        {"configurable": {"thread_id": thread}, "callbacks": [spans]},
                                        stream_mode=["custom", "values"]):
            if mode == "custom":
                name, payload = chunk
                yield ServerSentEvent(event=name, data=payload)
            else:
                final = chunk
        result = chat_result(final).model_copy(update={"request_id": request_id})
        _record_turn(root, req.question, result)
        _save_turn(store, turn, req, result)
        for piece in tokens(result.answer):
            if first_token_ms is None:  # tokens follow the whole answer today, so this is close to the latency
                first_token_ms = round((time.perf_counter() - started) * 1000)
            yield ServerSentEvent(event="token", data={"delta": piece})
        yield ServerSentEvent(event="result", data=result)
    except GeneratorExit:  # the client went away (Starlette closes the stream); not an Exception
        aborted = True
        raise
    except Exception as e:  # noqa: BLE001 - the status line is already sent; say it in the stream instead
        _fail(root, e)
        yield ServerSentEvent(event="error", data={"type": type(e).__name__, "detail": str(e),
                                                   "retryable": _retryable(e), "request_id": request_id})
    finally:  # also on client disconnect (GeneratorExit): the root span always ends
        root.end()
        _end_turn(store, turn, graph)
        _log_turn(collector, root, req, result, internal=internal, session=session, aborted=aborted,
                  first_token_ms=first_token_ms, is_member=turn.member is not None)


@app.post("/feedback", tags=["feedback"], summary="피드백 저장 (👍/👎 · 선택지 · 이유)", response_model=FeedbackResponse,
          responses={404: {"description": "없거나 남의 요청, 또는 24시간 지남", "model": ErrorResponse},
                     422: {"description": "선택지 조합 오류(`error`) 또는 입력 형식 오류(`detail`)", "content": {
                         "application/json": {"schema": {"anyOf": [{"$ref": "#/components/schemas/ErrorResponse"},
                                                                   {"$ref": "#/components/schemas/HTTPValidationError"}]}}}},
                     429: {"description": "같은 IP 분당 10회 초과", "model": ErrorResponse}})
def feedback(req: FeedbackRequest, request: Request, store: ServiceStore = Depends(get_store),
             member: Member | None = Depends(current_member), x_session_id: str | None = Header(default=None)) -> FeedbackResponse:
    """docs/service/api.md 3. 같은 `request_id`에 다시 보내면 마지막 값으로 바꾼다(`updated: true`).
    `reason`은 저장 전에 전화번호·이메일 패턴을 가린다. 선택지 없이 창을 닫으면 `category: null`로 값만 저장한다."""
    if not _rate_ok(request.client.host if request.client else "-"):
        raise ApiError(429, "too_many_requests", "잠시 뒤 다시 보내 주세요.")
    if req.category is not None and req.category not in FEEDBACK_CATEGORIES[req.value]:
        raise ApiError(422, "invalid_feedback", f"{req.value}에 맞지 않는 선택지입니다: {req.category}")
    if req.category == "other" and not (req.reason or "").strip():
        raise ApiError(422, "invalid_feedback", "기타를 고르면 이유를 입력해야 합니다.")
    session = _session(x_session_id)
    owner = store.request_owner(req.request_id)
    if owner is None:
        mine = False
    elif owner[2] is not None:  # a member's question: that member only, whatever session was sent
        mine = member is not None and member.id == owner[2]
    else:
        mine = owner[0] == session and _now() - owner[1] <= FEEDBACK_WINDOW
    if not mine:
        raise ApiError(404, "not_found", "피드백을 남길 답을 찾을 수 없습니다.")
    reason = mask(req.reason.strip()) if req.reason and req.reason.strip() else None
    feedback_id, updated = store.save_feedback(req.request_id, session, req.value, req.category, reason, _now(),
                                               owner[2])
    return FeedbackResponse(feedback_id=feedback_id, updated=updated)


FEEDBACK_PER_MINUTE = 10
_hits: dict[str, deque] = defaultdict(deque)


def _rate_ok(ip: str) -> bool:
    """At most FEEDBACK_PER_MINUTE feedback requests per client IP in any 60 seconds (this process)."""
    now = time.monotonic()
    with _lock:
        hits = _hits[ip]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= FEEDBACK_PER_MINUTE:
            return False
        hits.append(now)
        return True


@app.delete("/chat/{thread_id}", tags=["chat"], summary="대화 삭제", status_code=204,
            responses={404: {"description": "없거나 남의 대화", "model": ErrorResponse},
                       503: {"description": "지우지 못함. 다시 시도", "model": ErrorResponse}})
def delete_chat(thread_id: str, store: ServiceStore = Depends(get_store),
                checkpointer: Any = Depends(get_checkpointer), member: Member | None = Depends(current_member),
                x_session_id: str | None = Header(default=None)) -> Response:
    """docs/service/api.md 4. 로그인했으면 내 회원 대화(저장된 턴 포함), 아니면 이 세션이 만든 비회원 대화를 지운다.
    브라우저는 자기 목록에서 항목을 지운다. 운영 로그(질문 원문·피드백)는 보관 기간까지 남는다."""
    if member is not None:
        stored = store.member_thread(thread_id, member.id)
    else:
        stored = thread_id if store.owns(thread_id, _session(x_session_id)) else None
    if stored is None:
        raise ApiError(404, "not_found", "대화를 찾을 수 없습니다.")
    _drop_checkpoints(checkpointer, [stored])  # first: the rows are what a retry finds it by
    store.delete_threads([stored])
    return Response(status_code=204)


def _drop_checkpoints(checkpointer: Any, thread_ids: list[str]) -> None:
    try:
        for thread_id in thread_ids:
            checkpointer.delete_thread(thread_id)
    except Exception as e:  # noqa: BLE001
        log.warning("checkpoints not deleted: %s", type(e).__name__)
        raise ApiError(503, "unavailable", "잠시 뒤 다시 시도해 주세요.") from None


LOGIN_REQUIRED = {401: {"description": "로그인이 필요함", "model": ErrorResponse},
                  503: {"description": "로그인 확인 불가", "model": ErrorResponse}}


@app.get("/chat", tags=["chat"], summary="내 대화 목록 (회원)", response_model=ConversationList,
         responses={**LOGIN_REQUIRED, 422: {"description": "잘못된 cursor", "model": ErrorResponse}})
def list_chats(cursor: str | None = None, member: Member = Depends(require_member),
               store: ServiceStore = Depends(get_store)) -> ConversationList:
    """최근에 물은 대화부터. `next_cursor`가 있으면 `?cursor=`로 다음 쪽을 부른다."""
    try:
        rows, following = store.conversations(member.id, cursor)
    except BadCursor:
        raise ApiError(422, "invalid_cursor", "잘못된 cursor입니다.") from None
    counts = store.question_counts([c.id for c in rows])
    return ConversationList(next_cursor=following, items=[
        ConversationItem(id=c.id, title=c.title or "", created_at=c.created_at, updated_at=c.updated_at,
                         questions=counts.get(c.id, 0)) for c in rows])


@app.get("/chat/{thread_id}", tags=["chat"], summary="내 대화 다시 열기 (회원)", response_model=ConversationDetail,
         responses={**LOGIN_REQUIRED, 404: {"description": "없거나 남의 대화", "model": ErrorResponse}})
def read_chat(thread_id: str, member: Member = Depends(require_member),
              store: ServiceStore = Depends(get_store)) -> ConversationDetail:
    """저장된 질문과 그때의 `result`(답·차트·출처)를 순서대로."""
    found = store.conversation(thread_id, member.id)
    if found is None:
        raise ApiError(404, "not_found", "대화를 찾을 수 없습니다.")
    conv, messages = found
    questions = {m.turn: m.content for m in messages if m.role == "user"}
    return ConversationDetail(id=conv.id, title=conv.title or "", turns=[
        ConversationTurn(turn=m.turn, question=questions.get(m.turn, ""), result=m.result or {}, created_at=m.created_at)
        for m in messages if m.role == "assistant"])


# ---------------------------------------------------------------- login (docs/service/api.md 6)

@app.get("/auth/kakao/login", tags=["auth"], summary="카카오 로그인 시작", status_code=302, response_class=Response)
def kakao_login(next: str | None = None, kakao: auth.Kakao = Depends(auth.get_kakao)) -> Response:
    """카카오 동의 화면으로 보낸다. `next` = 로그인 뒤 돌아갈 우리 화면 경로(바깥 주소는 무시하고 `/`)."""
    back = auth.safe_return(next)
    if not kakao.ready:
        log.warning("kakao login is not configured")
        return auth.redirect(auth.failed_return(back))
    state = auth.new_state()
    res = auth.redirect(kakao.authorize_url(state))
    auth.set_state(res, state, back)
    return res


@app.get("/auth/kakao/callback", tags=["auth"], summary="카카오에서 돌아옴", status_code=302, response_class=Response)
def kakao_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
                   kakao: auth.Kakao = Depends(auth.get_kakao), store: ServiceStore = Depends(get_store)) -> Response:
    """로그인하고 원래 화면으로 보낸다. 동의 취소·카카오 오류·위조된 요청이면 `/login?error=kakao`(로그인 실패 안내)로 보낸다."""
    expected, back = auth.read_state(request)
    res = auth.redirect(auth.failed_return(back))
    if auth.same_state(expected, state) and code and not error:
        try:
            user = kakao.user(code)
            token = store.login(user.kakao_id, user.nickname, _now(), old_token=auth.login_token(request))
            res = auth.redirect(back)
            auth.set_login(res, token)
        except auth.KakaoError as e:
            log.warning("kakao login failed: %s", e)
        except Exception as e:  # noqa: BLE001 - the guest goes on; the type only, a message could hold the member
            log.warning("kakao login failed: %s", type(e).__name__)
    auth.clear_state(res)
    return res


@app.post("/auth/logout", tags=["auth"], summary="로그아웃 (이 기기만)", status_code=204,
          responses={503: {"description": "쿠키는 지웠지만 서버 세션을 지우지 못함", "model": ErrorResponse}})
def logout(request: Request, store: ServiceStore = Depends(get_store)) -> Response:
    """이 브라우저의 로그인만 끝낸다. 이미 끝났어도 204."""
    if auth.foreign_origin(request):
        raise ApiError(403, "forbidden_origin", "허용되지 않은 요청입니다.")
    res = Response(status_code=204, headers={"Cache-Control": "no-store"})
    try:
        store.logout(auth.login_token(request))
    except Exception as e:  # noqa: BLE001
        log.warning("login session not deleted: %s", type(e).__name__)
        res = _api_error(request, ApiError(503, "unavailable", "잠시 뒤 다시 시도해 주세요."))
    auth.clear_login(res)
    return res


@app.get("/auth/me", tags=["auth"], summary="내 정보", response_model=MeResponse, responses=LOGIN_REQUIRED)
def me(response: Response, member: Member = Depends(require_member),
       store: ServiceStore = Depends(get_store)) -> MeResponse:
    response.headers["Cache-Control"] = "no-store"
    return _me(store.account(member.id))


def _me(user: Any) -> MeResponse:
    if user is None:  # withdrawn on another device meanwhile
        raise ApiError(401, "login_required", "로그인이 필요합니다.")
    return MeResponse(user_id=user.id, nickname=user.nickname, display_name=user.display_name, color=user.color,
                      save_history=user.save_history, retention_days=user.retention_days)


@app.patch("/auth/me", tags=["auth"], summary="내 설정 바꾸기", response_model=MeResponse, responses=LOGIN_REQUIRED)
def update_me(req: MeUpdate, response: Response, member: Member = Depends(require_member),
              store: ServiceStore = Depends(get_store)) -> MeResponse:
    """보낸 항목만 바꾼다. `save_history`를 끄면 새 대화를 저장하지 않는다(이미 저장된 대화는 이어서 저장).
    `retention_days`는 마지막 질문부터 세며 매일 정리 때 적용한다."""
    response.headers["Cache-Control"] = "no-store"
    return _me(store.account(member.id, req.model_dump(exclude_unset=True)))


@app.delete("/auth/me", tags=["auth"], summary="회원 탈퇴", status_code=204, responses=LOGIN_REQUIRED)
def withdraw(member: Member = Depends(require_member), store: ServiceStore = Depends(get_store),
             checkpointer: Any = Depends(get_checkpointer)) -> Response:
    """회원 정보, 모든 기기의 로그인, 저장된 대화, 피드백을 바로 지운다. 운영 로그는 계정과 이어지지 않은 채 보관 기간까지 남는다."""
    _drop_checkpoints(checkpointer, store.member_threads(member.id))
    store.delete_member(member.id)
    res = Response(status_code=204, headers={"Cache-Control": "no-store"})
    auth.clear_login(res)
    return res


@app.post("/dev/chat", tags=["dev"], summary="에이전트 답변 (한 번에)", response_model=ChatResponse,
          responses={**UNAVAILABLE, **BUSY, 500: {"description": "실행 중 오류 (trace에 기록됨)"}})
@app.post("/chat", tags=["dev"], summary="옛 주소 → /dev/chat", response_model=ChatResponse, deprecated=True,
          responses={**UNAVAILABLE, **BUSY, 500: {"description": "실행 중 오류 (trace에 기록됨)"}})
def chat(req: ChatRequest, graph=Depends(get_graph), tracer: Tracer = Depends(telemetry.get_tracer),
         collector: TurnLogCollector | None = Depends(telemetry.get_collector),
         store: ServiceStore = Depends(get_store), turn: Turn = Depends(begin_turn),
         x_internal: str | None = Header(default=None),
         x_kosis_internal: str | None = Header(default=None)) -> ChatResponse:
    """`/chat/stream`과 같은 그래프·같은 답·같은 대화 규칙. 스트림 없이 끝난 뒤 `ChatResult`를 돌려준다."""
    if not isinstance(turn, Turn):  # a direct call (tests)
        session = _session(None, req.session_id)
        turn = Turn(_open(store, req, session), session, None)
    internal = _internal(x_internal, x_kosis_internal)
    session, opened, thread = turn.session, turn.opened, turn.opened.thread_id
    root, spans = start_turn(tracer, thread, _turn_index(graph, thread), telemetry.get_meter())
    request_id = trace_id(root)
    _record_turn(root, req.question)
    if internal:
        root.set_attribute("kosis.internal", True)
    _record_request(store, request_id, thread, session, turn.member)
    result: ChatResult | None = None
    try:
        state = graph.invoke({"messages": [("user", req.question)]},
                             {"configurable": {"thread_id": thread}, "callbacks": [spans]})
        result = chat_result(state).model_copy(update={"request_id": request_id})
        _record_turn(root, req.question, result)
        _save_turn(store, turn, req, result)
        return ChatResponse(thread_id=thread, trace_id=request_id, expired=opened.expired, result=result)
    except Exception as e:
        _fail(root, e)
        raise HTTPException(500, f"{type(e).__name__}: {e}  (trace_id {request_id})") from e
    finally:
        root.end()
        _end_turn(store, turn, graph)
        _log_turn(collector, root, req, result, internal=internal, session=session,
                  is_member=turn.member is not None)


@app.post("/events", tags=["ops"], summary="화면 이벤트 수집 (T3-48)", response_model=EventsResponse, status_code=202)
def events(batch: list[dict] = Body(..., max_length=MAX_EVENTS_PER_BATCH,
                                    description="화면 이벤트 목록. 형식은 docs/log-schema.md 4장"),
           collector: TurnLogCollector | None = Depends(telemetry.get_collector),
           x_internal: str | None = Header(default=None),
           x_kosis_internal: str | None = Header(default=None)) -> EventsResponse:
    """출처 클릭·답 복사·스트림 중단·피드백(👍/👎, 이유는 가려서 저장)을 받는다. 검사에 걸린 이벤트는 사유별 건수만 남긴다.
    저장은 백그라운드라 실패해도 202를 돌려준다(화면 동작에 영향 없음). 로그 쓰기가 꺼져 있으면 검사만 한다."""
    from kosis_store.log_collector import check_events

    internal = _internal(x_internal, x_kosis_internal)
    if collector is None:
        rows, rejects = check_events(batch, _now(), is_internal=internal)
        return EventsResponse(accepted=len(rows), rejected=sum(rejects.values()))
    accepted, rejected = collector.add_events(batch, is_internal=internal)
    return EventsResponse(accepted=accepted, rejected=rejected)


@app.post("/dev/tools/search_tables", tags=["dev"], summary="통계표 검색 (실전 2)", response_model=SearchOutput,
          responses=UNAVAILABLE)
@app.post("/tools/search_tables", tags=["dev"], summary="옛 주소 → /dev/tools/search_tables", response_model=SearchOutput,
          responses=UNAVAILABLE, deprecated=True)
def search_tables(req: SearchRequest, tools=Depends(get_tools),
                  tracer: Tracer = Depends(telemetry.get_tracer)) -> SearchOutput:
    """search 노드가 부르는 `search_tables` 툴. `route`가 `refuse`면 `refusal_text`가 사용자에게 보일 문장이다."""
    return _run_tool(tracer, "search_tables", tools, {"question": req.question})


@app.post("/dev/tools/summarize_stat", tags=["dev"], summary="표 하나로 수치 답 (실전 1)", response_model=StatResponse,
          responses=UNAVAILABLE)
@app.post("/tools/summarize_stat", tags=["dev"], summary="옛 주소 → /dev/tools/summarize_stat",
          response_model=StatResponse, responses=UNAVAILABLE, deprecated=True)
def summarize_stat(req: StatRequest, tools=Depends(get_tools),
                   tracer: Tracer = Depends(telemetry.get_tracer)) -> StatResponse:
    """statistics 노드가 부르는 `summarize_stat` 툴. 숫자는 원자료에서 코드가 계산한다. 표 키가 tables.csv에 없으면
    `answer.outcome = refused` (out_of_scope)."""
    out = _run_tool(tracer, "summarize_stat", tools, {"question": req.question, "table_key": req.table_key})
    chart = out.chart.model_dump(mode="json") if out.chart is not None else None
    return StatResponse(answer=out.answer, chart=chart, vega_lite=to_vega_lite(out.chart))


@app.post("/dev/tools/make_chart", tags=["dev"], summary="Vega-Lite 차트", response_model=ChartResponse,
          responses=UNAVAILABLE)
@app.post("/tools/make_chart", tags=["dev"], summary="옛 주소 → /dev/tools/make_chart", response_model=ChartResponse,
          responses=UNAVAILABLE, deprecated=True)
def make_chart(req: ChartRequest, tools=Depends(get_tools),
               tracer: Tracer = Depends(telemetry.get_tracer)) -> ChartResponse:
    """chart 노드가 부르는 `make_chart` 툴. 요청한 종류로 그릴 수 없으면 원래 종류로 그리고 `note`에 이유를 적는다."""
    return ChartResponse(**_run_tool(tracer, "make_chart", tools, req.model_dump()))


@app.get("/dev/threads/{thread_id}", tags=["dev"], summary="대화 상태", response_model=ThreadState,
         responses={**UNAVAILABLE, 404: {"description": "저장된 대화 없음"}})
@app.get("/threads/{thread_id}", tags=["dev"], summary="옛 주소 → /dev/threads/{thread_id}", response_model=ThreadState,
         responses={**UNAVAILABLE, 404: {"description": "저장된 대화 없음"}}, deprecated=True)
def thread_state(thread_id: str, graph=Depends(get_graph)) -> ThreadState:
    """checkpointer에 저장된 이 대화의 메시지와 지금 보고 있는 표. 세션 검사가 없으므로 배포에서는 막는다."""
    state = _snapshot(graph, thread_id)
    messages = state.get("messages") or []
    if not messages:
        raise HTTPException(404, f"no conversation {thread_id!r}")
    return ThreadState(thread_id=thread_id, turns=sum(isinstance(m, HumanMessage) for m in messages),
                       active_table=state.get("active_table"),
                       messages=[ThreadMessage(role="user" if isinstance(m, HumanMessage) else "assistant",
                                               content=str(m.content)) for m in messages])
