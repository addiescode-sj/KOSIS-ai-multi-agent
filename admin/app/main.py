"""The operator app (plans/T3-57.md, T3-67): the JSON API under /admin/api that the frontend's /admin screens read,
and the first server-rendered pages under /admin/classic (kept for a week after T3-67, then removed).

Run: PYTHONPATH=.:src uv run uvicorn admin.app.main:app --port 8100
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import Depends, FastAPI, Form, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from kosis_store.log_metrics import KST, TABS
from kosis_store.log_models import OUTCOMES

from admin.app import auth, config, queries
from admin.app.labels import LABELS, label
from admin.app.models import REVIEW_STATUSES

log = logging.getLogger("admin")
HERE = Path(__file__).parent
PREFIX = "/admin/classic"  # the server-rendered pages (T3-57); /admin itself is the frontend since T3-67
API = "/admin/api"  # JSON for the frontend's /admin screens (T3-66, T3-67); same origin, so the login cookie comes along
HEALTH = "/admin/healthz"  # the pod's readiness probe (not routed from outside)

app = FastAPI(title="KOSIS 운영", docs_url=None, redoc_url=None, openapi_url=None)
app.mount(f"{PREFIX}/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")

_engine: Engine | None = None


def get_db() -> Iterator[Session]:
    global _engine
    if _engine is None:
        _engine = create_engine(config.database_url(), pool_pre_ping=True, pool_size=3, max_overflow=2)
    session = sessionmaker(_engine, expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def current_operator(request: Request, db: Session = Depends(get_db)) -> auth.Operator:
    return auth.operator(db, request)


# ---------------------------------------------------------------- page helpers

def _kst(value: datetime | None, fmt: str = "%m-%d %H:%M") -> str:
    return value.astimezone(KST).strftime(fmt) if value else "–"


def _pct(value) -> str:
    return "–" if value is None else f"{float(value) * 100:.1f}%"


def _sec(ms) -> str:
    return "–" if ms is None else f"{float(ms) / 1000:.1f}초"


def _cut(value: str | None, n: int, empty: str = "(보관 기간 지남)") -> str:
    if not value:
        return empty
    return value if len(value) <= n else value[:n] + "…"


templates.env.filters.update(kst=_kst, pct=_pct, sec=_sec, cut=_cut, label=lambda c, k, e="–": label(k, c, e))
templates.env.globals.update(LABELS=LABELS, PREFIX=PREFIX)


def page(request: Request, name: str, op: auth.Operator | None, status_code: int = 200, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {
        "op": op, "grafana": config.grafana_url(), "read_at": datetime.now(KST).strftime("%H:%M"),
        "origin": config.public_origin(), **context}, status_code=status_code)


def _api_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _query(**params) -> str:
    return urlencode({k: v for k, v in params.items() if v not in (None, "", False)})


@app.middleware("http")
async def _headers(request: Request, call_next):
    res = await call_next(request)
    res.headers["Cache-Control"] = "no-store"
    res.headers["X-Robots-Tag"] = "noindex, nofollow"
    res.headers["X-Content-Type-Options"] = "nosniff"
    res.headers["Referrer-Policy"] = "no-referrer"
    res.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                                              "form-action 'self'; base-uri 'none'")
    return res


@app.exception_handler(auth.NotSignedIn)
async def _sign_in(request: Request, exc: auth.NotSignedIn) -> Response:
    if request.url.path.startswith(API):  # a screen's fetch: say so instead of sending it to the Kakao pages
        return _api_error(401, "login_required", "로그인이 필요합니다.")
    back = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if request.method != "GET":
        back = PREFIX
    return RedirectResponse(f"/auth/kakao/login?next={quote(back, safe='/')}", status_code=303)


@app.exception_handler(auth.NotAllowed)
async def _not_allowed(request: Request, exc: auth.NotAllowed) -> Response:
    return PlainTextResponse("Not Found", status_code=404)


@app.exception_handler(OperationalError)
@app.exception_handler(DBAPIError)
async def _db_down(request: Request, exc: Exception) -> Response:
    log.warning("admin: database error %s", type(exc).__name__)
    if request.url.path.startswith(API):
        return _api_error(503, "db_unavailable", "데이터베이스에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.")
    return page(request, "error.html", None, status_code=503, message="데이터베이스에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.")


# ---------------------------------------------------------------- pages

@app.get(HEALTH, include_in_schema=False)
def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get(PREFIX, response_class=HTMLResponse)
@app.get(f"{PREFIX}/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request, op: auth.Operator = Depends(current_operator), db: Session = Depends(get_db)):
    return page(request, "home.html", op, nav="home", db_ok=queries.db_ok(db), data=queries.home(db))


@app.get(f"{API}/metrics")
def api_metrics(start: str | None = None, end: str | None = None, user: str = "all", internal: bool = False,
                op: auth.Operator = Depends(current_operator), db: Session = Depends(get_db)) -> JSONResponse:
    """The metrics screen's numbers as JSON for the React screen (T3-66): the same filter rule and queries.metrics()
    as the page below, so both show the same numbers. Operators only (401 without a login, 404 off the list)."""
    rng = queries.date_range(start, end, include_today=False, user=user, internal=internal)
    flt = rng.flt
    data = queries.dashboard(db, flt)
    body = {"filter": {"start": flt.start_day, "end": flt.end_day, "user": flt.user, "internal": flt.include_internal},
            "notice": rng.notice, "labels": LABELS, **data}
    return JSONResponse(jsonable_encoder(body))


@app.get(f"{PREFIX}/metrics", response_class=HTMLResponse)
def metrics(request: Request, tab: str = "summary", start: str | None = None, end: str | None = None,
            user: str = "all", internal: bool = False,
            op: auth.Operator = Depends(current_operator), db: Session = Depends(get_db)):
    tab = tab if tab in TABS else "summary"
    rng = queries.date_range(start, end, include_today=False, user=user, internal=internal)
    data = queries.metrics(db, tab, rng.flt)
    return page(request, "metrics.html", op, nav="metrics", tab=tab, flt=rng.flt, notice=rng.notice, data=data,
                tabs=list(TABS), qs=lambda **kw: _query(**{"tab": tab, "start": rng.flt.start_day,
                                                           "end": rng.flt.end_day, "user": rng.flt.user,
                                                           "internal": rng.flt.include_internal, **kw}))


@app.get(f"{PREFIX}/questions", response_class=HTMLResponse)
def question_list(request: Request, start: str | None = None, end: str | None = None, outcome: str | None = None,
                  user: str = "all", internal: bool = False, page_no: int = 1,
                  op: auth.Operator = Depends(current_operator), db: Session = Depends(get_db)):
    rng = queries.date_range(start, end, include_today=True, user=user, internal=internal)
    outcome = outcome if outcome in OUTCOMES else None
    page_no = max(page_no, 1)
    data = queries.questions(db, rng.flt, outcome=outcome, page=page_no)
    return page(request, "questions.html", op, nav="questions", flt=rng.flt, notice=rng.notice, outcome=outcome,
                outcomes=OUTCOMES, data=data, page_no=page_no, pages=max(1, -(-data["total"] // queries.PAGE)),
                qs=lambda **kw: _query(**{"start": rng.flt.start_day, "end": rng.flt.end_day, "outcome": outcome,
                                          "user": rng.flt.user, "internal": rng.flt.include_internal, **kw}))


@app.get(f"{PREFIX}/questions/{{request_id}}", response_class=HTMLResponse)
def question_detail(request: Request, request_id: str, op: auth.Operator = Depends(current_operator),
                    db: Session = Depends(get_db)):
    data = queries.question(db, op, request_id)
    if data is None:
        return page(request, "error.html", op, status_code=404, message="이 질문을 찾지 못했습니다.")
    return page(request, "question.html", op, nav="questions", d=data,
                people=queries.operators(db, config.allowed_users()), statuses=REVIEW_STATUSES,
                back=f"{PREFIX}/questions/{request_id}", saved=request.query_params.get("saved"),
                error=request.query_params.get("error"))


@app.get(f"{PREFIX}/feedback", response_class=HTMLResponse)
def feedback(request: Request, status: str = "new", start: str | None = None, end: str | None = None,
             category: str | None = None, assignee: str | None = None, page_no: int = 1,
             op: auth.Operator = Depends(current_operator), db: Session = Depends(get_db)):
    status = status if status in (*REVIEW_STATUSES, "all") else "new"
    rng = queries.date_range(start, end, include_today=True, days=30)
    who, assignee = _assignee(op, assignee)
    page_no = max(page_no, 1)
    data = queries.feedback_inbox(db, rng.flt, status=status, category=category or None, assignee=who, page=page_no)
    qs = lambda **kw: _query(**{"status": status, "start": rng.flt.start_day, "end": rng.flt.end_day,  # noqa: E731
                                "category": category, "assignee": assignee, **kw})
    return page(request, "feedback.html", op, nav="feedback", flt=rng.flt, notice=rng.notice, status=status,
                category=category, assignee=assignee, statuses=REVIEW_STATUSES, data=data, page_no=page_no,
                pages=max(1, -(-data["total"] // queries.PAGE)), qs=qs,
                people=queries.operators(db, config.allowed_users()),
                back=f"{PREFIX}/feedback?{qs(page_no=page_no)}", saved=request.query_params.get("saved"),
                error=request.query_params.get("error"))


def _assignee(op: auth.Operator, assignee: str | None) -> tuple[uuid.UUID | None, str | None]:
    """The inbox's assignee filter: "me", an operator id, or none (an unreadable id is dropped)."""
    if assignee == "me":
        return op.user_id, assignee
    if assignee:
        try:
            return uuid.UUID(assignee), assignee
        except ValueError:
            pass
    return None, None


def _back(target: str, **flash) -> str:
    """Where to go after a save: our own pages only."""
    if not target.startswith(f"{PREFIX}/") or target.startswith("//"):
        target = f"{PREFIX}/feedback"
    return target + ("&" if "?" in target else "?") + urlencode(flash)


@app.post(f"{PREFIX}/feedback/{{request_id}}")
def save_feedback_review(request: Request, request_id: str, status: str = Form(...), assignee: str = Form(""),
                         note: str = Form(""), issue_key: str = Form(""), seen: str = Form(""),
                         back: str = Form(""), form_token: str = Form(""),
                         op: auth.Operator = Depends(current_operator), db: Session = Depends(get_db)):
    if not auth.check_form(request, op, form_token):
        return PlainTextResponse("Forbidden", status_code=403)
    try:
        value = queries.clean_review(status, assignee, note, issue_key, op.user_id, config.allowed_users())
        queries.save_review(db, op, request_id, value, seen)
    except ValueError as e:
        return RedirectResponse(_back(back, error=str(e)), status_code=303)
    except queries.Conflict:
        db.rollback()
        return RedirectResponse(_back(back, error="다른 사람이 먼저 바꿨습니다. 새로 읽은 내용을 확인해 주세요."),
                                status_code=303)
    except LookupError:
        return PlainTextResponse("Not Found", status_code=404)
    return RedirectResponse(_back(back, saved="1"), status_code=303)



# ---------------------------------------------------------------- JSON API (T3-67)
# What the frontend's /admin screens read. The same queries as the pages above, so both show the same numbers.
# Operators only: 401 JSON without a login, 404 off the list, 503 JSON when the database is down.

# The stored fields of a question the detail screen shows (request_logs has no answer text, 11/26)
QUESTION_FIELDS = ("request_id", "created_at", "question", "is_internal", "is_member", "rewrite_kind", "interpreted_by",
                   "outcome", "reason_code", "failed_stage", "survey_name", "picked_key", "has_numbers", "has_chart",
                   "latency_ms", "first_token_ms", "llm_calls", "input_tokens", "output_tokens")


def _json(body: dict) -> JSONResponse:
    return JSONResponse(jsonable_encoder(body))


def _no_session(rows: list[dict]) -> list[dict]:
    """Question rows without the browser session id (not shown, not needed by a screen)."""
    return [{k: v for k, v in r.items() if k != "session_id"} for r in rows]


def _filter(rng: queries.Range) -> dict:
    flt = rng.flt
    return {"start": flt.start_day, "end": flt.end_day, "user": flt.user, "internal": flt.include_internal}


@app.get(f"{API}/me")
def api_me(op: auth.Operator = Depends(current_operator)) -> JSONResponse:
    """The signed-in operator. The frontend proxy asks this before drawing any /admin screen; the browser reads the
    form token here for saves (X-Admin-Token) and the code labels once."""
    return _json({"user_id": op.user_id, "name": op.name, "form_token": op.form_token, "grafana": config.grafana_url(),
                  "labels": LABELS, "statuses": list(REVIEW_STATUSES), "outcomes": list(OUTCOMES)})


@app.get(f"{API}/home")
def api_home(op: auth.Operator = Depends(current_operator), db: Session = Depends(get_db)) -> JSONResponse:
    data = queries.home(db)
    data["problems"] = _no_session(data["problems"])
    return _json({"db_ok": queries.db_ok(db), **data, "read_at": datetime.now(timezone.utc)})


@app.get(f"{API}/questions")
def api_questions(start: str | None = None, end: str | None = None, outcome: str | None = None, user: str = "all",
                  internal: bool = False, page_no: int = 1,
                  op: auth.Operator = Depends(current_operator), db: Session = Depends(get_db)) -> JSONResponse:
    rng = queries.date_range(start, end, include_today=True, user=user, internal=internal)
    outcome = outcome if outcome in OUTCOMES else None
    page_no = max(page_no, 1)
    data = queries.questions(db, rng.flt, outcome=outcome, page=page_no)
    data["rows"] = _no_session(data["rows"])
    return _json({"filter": {**_filter(rng), "outcome": outcome}, "notice": rng.notice, "page_no": page_no,
                  "page_size": queries.PAGE, **data})


@app.get(f"{API}/questions/{{request_id}}")
def api_question(request_id: str, op: auth.Operator = Depends(current_operator),
                 db: Session = Depends(get_db)) -> JSONResponse:
    """One question. Reading it is audited (one row per read: the screen reads it once)."""
    data = queries.question(db, op, request_id)
    if data is None:
        return _api_error(404, "not_found", "이 질문을 찾지 못했습니다.")
    data["row"] = {k: data["row"].get(k) for k in QUESTION_FIELDS}
    return _json({**data, "people": queries.operators(db, config.allowed_users())})


@app.get(f"{API}/feedback")
def api_feedback(status: str = "new", start: str | None = None, end: str | None = None, category: str | None = None,
                 assignee: str | None = None, page_no: int = 1,
                 op: auth.Operator = Depends(current_operator), db: Session = Depends(get_db)) -> JSONResponse:
    status = status if status in (*REVIEW_STATUSES, "all") else "new"
    rng = queries.date_range(start, end, include_today=True, days=30)
    who, assignee = _assignee(op, assignee)
    page_no = max(page_no, 1)
    data = queries.feedback_inbox(db, rng.flt, status=status, category=category or None, assignee=who, page=page_no)
    return _json({"filter": {"start": rng.flt.start_day, "end": rng.flt.end_day, "status": status,
                             "category": category or None, "assignee": assignee},
                  "notice": rng.notice, "page_no": page_no, "page_size": queries.PAGE,
                  "people": queries.operators(db, config.allowed_users()), **data})


class ReviewBody(BaseModel):
    status: str
    assignee: str = ""
    note: str = ""
    issue_key: str = ""
    seen: str = ""  # the review's updated_at the screen showed, "" when it had none


@app.post(f"{API}/feedback/{{request_id}}")
def api_save_review(request: Request, request_id: str, body: ReviewBody,
                    op: auth.Operator = Depends(current_operator), db: Session = Depends(get_db)) -> JSONResponse:
    """Save one thumbs-down answer's review: 200 with the stored review, 409 when someone saved first, 422 for a
    value to fix, 404 without feedback, 403 when not from our screen (Origin, JSON, X-Admin-Token)."""
    if not auth.check_api_post(request, op):
        return _api_error(403, "forbidden", "운영자 화면에서 다시 시도해 주세요.")
    try:
        value = queries.clean_review(body.status, body.assignee, body.note, body.issue_key, op.user_id,
                                     config.allowed_users())
        queries.save_review(db, op, request_id, value, body.seen)
    except ValueError as e:
        return _api_error(422, "invalid_review", str(e))
    except queries.Conflict:
        db.rollback()
        return _api_error(409, "conflict", "다른 사람이 먼저 바꿨습니다. 새로 읽은 내용을 확인해 주세요.")
    except LookupError:
        return _api_error(404, "not_found", "이 답에는 피드백이 없습니다.")
    return _json({"review": queries.review(db, request_id)})
