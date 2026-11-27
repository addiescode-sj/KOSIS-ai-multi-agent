"""What the operator screens read and write (kosis-infra/12 5·6).

Metric numbers come from kosis_store.log_metrics as they are; nothing is computed again here. The thumbs-down inbox
reuses log_metrics' common filter (`turns` + `last_feedback`) and only joins the review state on top.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kosis_store import log_metrics as lm
from kosis_store.log_metrics import KST, MAX_DAYS, MetricFilter
from kosis_store.masking import mask

from admin.app.auth import Operator, audit
from admin.app.models import NOTE_MAX, REVIEW_STATUSES

PAGE = 20
REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
ISSUE_KEY = re.compile(r"^KOS-[0-9]+$")
DAILY = {"question_count", "thumbs_up_ratio", "abort_rate"}  # metrics with a per-day line


def today() -> date:
    return datetime.now(KST).date()


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Range:
    flt: MetricFilter
    notice: str | None  # set when the asked range was moved into the allowed one


def date_range(start: str | None, end: str | None, *, include_today: bool, user: str = "all",
               internal: bool = False, days: int = 7) -> Range:
    """The filter of a screen from the address. Metrics stop at yesterday (a day's numbers are fixed once it ends);
    review screens include today. Both look back MAX_DAYS at most. A range outside is moved in and told."""
    now = today()
    last = now if include_today else now - timedelta(days=1)
    first = now - timedelta(days=MAX_DAYS)
    s, e = _day(start), _day(end)
    notice = None
    if (start and s is None) or (end and e is None):
        notice = "날짜 형식이 맞지 않아 기본 기간으로 보여 줍니다."
        s = e = None
    e = e or last
    s = s or e - timedelta(days=days - 1)
    if e > last or s < first or s > e:
        notice = notice or f"볼 수 있는 기간({first} ~ {last})으로 고쳤습니다."
        e = min(max(e, first), last)
        s = min(max(s, first), e)
    if user not in ("all", "member", "guest"):
        user = "all"
    return Range(MetricFilter(s, e, user, internal), notice)


# ---------------------------------------------------------------- metrics (5 minutes cache)

_cache: dict[tuple, tuple[float, dict]] = {}
CACHE_SECONDS = 300


def metrics(session: Session, tab: str, flt: MetricFilter) -> dict:
    """One tab's metrics under the filter plus the per-day lines of its daily metrics. The filter must be a past
    range (MetricFilter.check). Cached 5 minutes per tab and filter. Side effects: reads."""
    if tab not in lm.TABS:
        raise ValueError(tab)
    flt.check(today())
    key = (tab, flt)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    result = {"rows": lm.tab(session, tab, flt),
              "daily": {m: _every_day(lm.query(session, m, flt, by_day=True), flt) for m in lm.TABS[tab] if m in DAILY},
              "read_at": datetime.now(timezone.utc)}  # when these numbers were read (the cache keeps them 5 minutes)
    if len(_cache) > 200:  # filters come from the address; keep the cache small
        _cache.clear()
    _cache[key] = (time.monotonic(), result)
    return result


def _cached(key: tuple, read):
    """The 5-minute cache of metrics() for one more read (the dashboard's TTFT)."""
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = read()
    _cache[key] = (time.monotonic(), value)
    return value


def _every_day(rows: list[dict], flt: MetricFilter) -> list[dict]:
    """One entry per day of the range; a day without questions has only `day` (the chart shows a count of 0 and
    leaves a ratio out)."""
    by_day = {r["day"]: r for r in rows}
    days = (flt.end_day - flt.start_day).days + 1
    return [by_day.get(d, {"day": d}) for d in (flt.start_day + timedelta(days=i) for i in range(days))]


def previous(flt: MetricFilter) -> MetricFilter | None:
    """The range of the same length just before `flt` (for the change shown next to a number); None when it would
    start more than MAX_DAYS ago. Side effects: none."""
    days = (flt.end_day - flt.start_day).days + 1
    end = flt.start_day - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    if start < today() - timedelta(days=MAX_DAYS):
        return None
    return MetricFilter(start, end, flt.user, flt.include_internal)


def dashboard(session: Session, flt: MetricFilter) -> dict:
    """Every tab's metrics for the React metrics screen (T3-66), the summary of the range before for the changes, and
    when the numbers were read (the oldest tab). The same metrics() the operator pages use: nothing is computed again.
    Side effects: reads."""
    tabs = {tab: metrics(session, tab, flt) for tab in lm.TABS}
    before = previous(flt)
    return {
        "tabs": {tab: {"rows": t["rows"], "daily": t["daily"]} for tab, t in tabs.items()},
        "first_token": _cached(("first_token", flt), lambda: lm.query(session, "first_token", flt)),  # on no tab
        "previous": metrics(session, "summary", before)["rows"] if before else None,
        "read_at": min(t["read_at"] for t in tabs.values()),
    }


# ---------------------------------------------------------------- home

def home(session: Session) -> dict:
    """Today (KST 00:00 until now): questions, errors, aborts, p95, new thumbs-down; the last problem questions and
    new thumbs-down of the past 7 days. Side effects: reads."""
    day = today()
    flt = MetricFilter(day, day)  # today is not complete: not checked, only for this screen
    share = {r["outcome"]: r["n"] for r in lm.query(session, "outcome_share", flt)}
    latency = lm.query(session, "latency", flt)
    week = MetricFilter(day - timedelta(days=6), day)
    problems = [t for o in ("failed", "error", "aborted") for t in lm.review_turns(session, week, outcome=o, size=5)]
    problems.sort(key=lambda t: t["created_at"], reverse=True)
    inbox = feedback_inbox(session, MetricFilter(day - timedelta(days=MAX_DAYS), day), status="new", size=5)
    return {
        "questions": lm.query(session, "question_count", flt)[0]["n"],
        "errors": share.get("failed", 0) + share.get("error", 0),
        "aborted": share.get("aborted", 0),
        "p95": latency[0]["p95"] if latency else None,
        "new_down": inbox["total"],
        "problems": problems[:5],
        "inbox": inbox["rows"],
    }


def db_ok(session: Session) -> bool:
    try:
        session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - the card only says up or down
        session.rollback()
        return False


# ---------------------------------------------------------------- question review

def questions(session: Session, flt: MetricFilter, *, outcome: str | None, page: int) -> dict:
    return {"total": lm.review_count(session, flt, outcome=outcome),
            "rows": lm.review_turns(session, flt, outcome=outcome, page=page, size=PAGE)}


def question(session: Session, op: Operator, request_id: str) -> dict | None:
    """Everything stored about one question, or None. Opening it is audited. Side effects: reads, inserts audit."""
    if not REQUEST_ID.match(request_id):
        return None
    row = session.execute(text("SELECT * FROM request_logs WHERE request_id = :id"), {"id": request_id}).mappings().first()
    if row is None:
        return None
    rewrites = session.execute(text(
        "SELECT seq, kind, text FROM request_rewrites WHERE request_id = :id ORDER BY seq"), {"id": request_id}).mappings()
    fb = session.execute(text(
        "SELECT value, category, reason, updated_at FROM feedback WHERE request_id = :id"), {"id": request_id}).mappings()
    # the same conversation, within a week of this question (keeps to the created_at index)
    around = session.execute(text("""
        SELECT request_id, turn_index, created_at, question, outcome, is_internal FROM request_logs
        WHERE conversation_id = :c AND created_at BETWEEN :a AND :b ORDER BY turn_index, created_at LIMIT 50"""),
        {"c": row["conversation_id"], "a": row["created_at"] - timedelta(days=7),
         "b": row["created_at"] + timedelta(days=7)}).mappings()
    audit(session, op.user_id, "view_question", request_id)
    return {"row": dict(row), "rewrites": [dict(r) for r in rewrites], "feedback": next(iter(fb), None),
            "review": review(session, request_id), "conversation": [dict(r) for r in around]}


# ---------------------------------------------------------------- thumbs-down inbox

def feedback_inbox(session: Session, flt: MetricFilter, *, status: str = "new", category: str | None = None,
                   assignee: uuid.UUID | None = None, page: int = 1, size: int = PAGE) -> dict:
    """Thumbs-down answers of questions asked in the range (log_metrics' `last_feedback`), newest feedback first, with
    their review state. status: one of REVIEW_STATUSES or "all"; category: a choice code or "none" (no choice).
    Returns {"total", "rows"}. Side effects: reads."""
    where, params = ["lf.value = 'down'"], {**flt.params(), "limit": size, "offset": (page - 1) * size}
    if status == "new":
        where.append("(r.request_id IS NULL OR r.status = 'new')")
    elif status in REVIEW_STATUSES:
        where.append("r.status = :status")
        params["status"] = status
    if category == "none":
        where.append("lf.choice IS NULL")
    elif category:
        where.append("lf.choice = :category")
        params["category"] = category
    if assignee is not None:
        where.append("r.assignee_id = :assignee")
        params["assignee"] = assignee
    body = f"""
        SELECT lf.request_id, lf.created_at AS asked_at, f.updated_at AS received_at, lf.question, lf.outcome,
               lf.reason_code, lf.picked_key, lf.survey_name, lf.choice, lf.feedback_reason,
               coalesce(r.status, 'new') AS status, r.assignee_id, coalesce(u.display_name, u.nickname) AS assignee,
               r.note, r.issue_key, r.updated_at AS review_updated_at, count(*) OVER () AS total
        FROM last_feedback lf
        JOIN feedback f ON f.request_id = lf.request_id
        LEFT JOIN admin.feedback_reviews r ON r.request_id = lf.request_id
        LEFT JOIN users u ON u.id = r.assignee_id
        WHERE {' AND '.join(where)}
        ORDER BY f.updated_at DESC, lf.request_id LIMIT :limit OFFSET :offset"""
    rows = [dict(r) for r in session.execute(text(lm._sql(body)), params).mappings()]
    total = rows[0]["total"] if rows else 0
    for r in rows:
        del r["total"]
    return {"total": total, "rows": rows}


def review(session: Session, request_id: str) -> dict | None:
    row = session.execute(text("""
        SELECT r.status, r.assignee_id, coalesce(u.display_name, u.nickname) AS assignee, r.note, r.issue_key,
               r.updated_at, coalesce(b.display_name, b.nickname) AS updated_by
        FROM admin.feedback_reviews r LEFT JOIN users u ON u.id = r.assignee_id LEFT JOIN users b ON b.id = r.updated_by
        WHERE r.request_id = :id"""), {"id": request_id}).mappings().first()
    return dict(row) if row else None


def operators(session: Session, ids) -> list[dict]:
    """Names of the allowed operators, for the assignee choice."""
    if not ids:
        return []
    rows = session.execute(text("SELECT id, coalesce(display_name, nickname) AS name FROM users WHERE id = ANY(:ids)"),
                           {"ids": list(ids)}).mappings()
    return sorted((dict(r) for r in rows), key=lambda r: r["name"])


@dataclass(frozen=True)
class ReviewInput:
    status: str
    assignee_id: uuid.UUID | None
    note: str | None
    issue_key: str | None


def clean_review(status: str, assignee: str | None, note: str | None, issue_key: str | None,
                 me: uuid.UUID, allowed) -> ReviewInput:
    """The form values checked and cleaned (note masked). Raises ValueError with the message to show."""
    if status not in REVIEW_STATUSES:
        raise ValueError("상태를 다시 골라 주세요.")
    note = (note or "").strip()
    if len(note) > NOTE_MAX:
        raise ValueError(f"메모는 {NOTE_MAX}자까지 쓸 수 있습니다.")
    note = (mask(note) or "")[:NOTE_MAX] or None
    issue_key = (issue_key or "").strip().upper() or None
    if issue_key is not None and not ISSUE_KEY.match(issue_key):
        raise ValueError("Linear 번호는 KOS-201 같은 형식으로 써 주세요.")
    if status == "fix_requested" and issue_key is None:
        raise ValueError("수정 요청에는 Linear 번호가 필요합니다.")
    if status == "no_issue" and note is None:
        raise ValueError("문제 없음에는 메모가 필요합니다.")
    assignee = (assignee or "").strip()
    if assignee == "me" or (assignee == "" and status == "checking"):
        assignee_id = me
    elif assignee == "":
        assignee_id = None
    else:
        try:
            assignee_id = uuid.UUID(assignee)
        except ValueError:
            raise ValueError("담당을 다시 골라 주세요.") from None
        if assignee_id not in allowed:
            raise ValueError("담당은 운영자 중에서 골라 주세요.")
    return ReviewInput(status, assignee_id, note, issue_key)


class Conflict(Exception):
    """Someone else saved this answer's review after the page was read."""


def save_review(session: Session, op: Operator, request_id: str, value: ReviewInput, seen: str | None) -> None:
    """Store the review of one thumbs-down answer. `seen` is the review's updated_at the page showed ("" or None when
    there was none); a different current value raises Conflict. LookupError when the answer has no feedback.
    Side effects: inserts or updates admin.feedback_reviews, inserts audit (the caller commits)."""
    if not REQUEST_ID.match(request_id) or session.execute(
            text("SELECT 1 FROM feedback WHERE request_id = :id"), {"id": request_id}).first() is None:
        raise LookupError(request_id)
    current = session.execute(text(
        "SELECT updated_at FROM admin.feedback_reviews WHERE request_id = :id FOR UPDATE"), {"id": request_id}).first()
    seen = seen or None
    if (current is None) != (seen is None) or (current is not None and current.updated_at.isoformat() != seen):
        raise Conflict
    now = datetime.now(timezone.utc)
    params = {"id": request_id, "status": value.status, "assignee": value.assignee_id, "note": value.note,
              "issue": value.issue_key, "by": op.user_id, "now": now}
    if current is None:
        try:
            with session.begin_nested():
                session.execute(text("""
                    INSERT INTO admin.feedback_reviews
                        (request_id, status, assignee_id, note, issue_key, updated_by, created_at, updated_at)
                    VALUES (:id, :status, :assignee, :note, :issue, :by, :now, :now)"""), params)
        except IntegrityError:  # another operator made the first row meanwhile
            raise Conflict from None
    else:
        session.execute(text("""
            UPDATE admin.feedback_reviews SET status = :status, assignee_id = :assignee, note = :note,
                   issue_key = :issue, updated_by = :by, updated_at = :now
            WHERE request_id = :id"""), params)
    audit(session, op.user_id, "update_review", request_id)
