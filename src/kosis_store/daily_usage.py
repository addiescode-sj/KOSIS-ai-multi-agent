"""Daily usage counts from the user log (plans/T3-48.md 범위 변경 1·2, docs/log-schema.md 13.5).

Two tables per KST day, built together:
- daily_usage: all users, one row (questions, sessions, conversations, turns, clicks, feedback, turns left early).
- daily_session_usage: one row per user. The user is the anonymous browser session_id (the log keeps no account id,
  POL-006); questions without a session id count only in the total.

Harness traffic (is_internal) is left out of both. The day is rebuilt whole (delete then insert in one transaction), so
running it again gives the same rows.

    python -m kosis_store.daily_usage --date 2026-11-23      # DATABASE_URL
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from kosis_store.log_metrics import day_range
from kosis_store.log_models import DailySessionUsage, DailyUsage, RequestLog, UiEvent


def _counts(turns: list, events: list, votes_of: dict[str, str]) -> dict:
    """Usage counts of some turns, the click events that go with them, and the last feedback value of each answer.

    Feedback follows kosis_store.log_metrics (T3-49): the last feedback of an answer over all time, counted on the
    question's day and for the question's user, so the total equals the sum over users. Side effects: none.
    """
    per_conversation = Counter(t.conversation_id for t in turns)
    clicks = Counter(e.event_type for e in events)
    votes = Counter(votes_of[t.request_id] for t in turns if t.request_id in votes_of)
    return {
        "questions": len(turns),
        "conversations": len(per_conversation),
        "avg_turns": round(len(turns) / len(per_conversation), 2) if per_conversation else 0.0,
        "multi_turn_conversations": sum(1 for n in per_conversation.values() if n >= 2),
        "aborted": sum(1 for t in turns if t.outcome == "aborted"),
        "citation_clicks": clicks["citation_click"],
        "copy_answers": clicks["copy_answer"],
        "feedback_up": votes["up"],
        "feedback_down": votes["down"],
    }


def usage_of_day(session: Session, day: date) -> tuple[dict, list[dict]]:
    """(the daily_usage values, the daily_session_usage values per user) of a KST day.

    Counted in Python over that day's rows, so the result is the same on any database. Side effects: reads the database.
    """
    bounds = day_range(day)
    turns = session.execute(
        select(RequestLog.request_id, RequestLog.conversation_id, RequestLog.session_id, RequestLog.outcome,
               RequestLog.is_member)
        .where(~RequestLog.is_internal, RequestLog.created_at >= bounds["start"], RequestLog.created_at < bounds["end"])
    ).all()
    events = session.execute(  # clicks: counted on the day they came, for the session that sent them
        select(UiEvent.event_type, UiEvent.session_id)
        .where(~UiEvent.is_internal, UiEvent.received_at >= bounds["start"], UiEvent.received_at < bounds["end"])
        .where(UiEvent.event_type != "feedback")
    ).all()
    # the last feedback of each of the day's answers, whenever it came (same order as log_metrics: client_ts,
    # received_at, event_id)
    votes_of: dict[str, str] = {}
    ids = [t.request_id for t in turns]
    for i in range(0, len(ids), 1000):
        for e in session.execute(
            select(UiEvent.request_id, UiEvent.props)
            .where(UiEvent.event_type == "feedback", UiEvent.request_id.in_(ids[i:i + 1000]))
            .order_by(UiEvent.client_ts, UiEvent.received_at, UiEvent.event_id)
        ).all():
            votes_of[e.request_id] = (e.props or {}).get("value")

    total = {"day": day, **_counts(turns, events, votes_of),
             "sessions": len({t.session_id for t in turns if t.session_id is not None})}

    turns_of: dict = defaultdict(list)
    events_of: dict = defaultdict(list)
    for t in turns:
        if t.session_id is not None:
            turns_of[t.session_id].append(t)
    for e in events:
        if e.session_id is not None:
            events_of[e.session_id].append(e)
    users = [{"day": day, "session_id": sid, "is_member": any(t.is_member for t in turns_of[sid]),
              **_counts(turns_of[sid], events_of[sid], votes_of)}
             for sid in sorted(set(turns_of) | set(events_of), key=str)]
    return total, users


def build_day(session: Session, day: date, now: datetime | None = None) -> tuple[dict, list[dict]]:
    """Replace the day's rows of daily_usage and daily_session_usage with fresh counts; returns them.

    Side effects: deletes and inserts that day's rows of both tables.
    """
    built = now or datetime.now(timezone.utc)
    total, users = usage_of_day(session, day)
    session.execute(delete(DailyUsage).where(DailyUsage.day == day))
    session.execute(delete(DailySessionUsage).where(DailySessionUsage.day == day))
    session.add(DailyUsage(**total, built_at=built))
    session.add_all(DailySessionUsage(**u, built_at=built) for u in users)
    return total, users


def main(argv: list[str] | None = None) -> int:
    from kosis_store.db import make_engine, session_scope

    parser = argparse.ArgumentParser(description="daily_usage and daily_session_usage for one KST date (safe to rerun)")
    parser.add_argument("--date", type=date.fromisoformat, required=True, help="KST date, YYYY-MM-DD")
    args = parser.parse_args(argv)
    with session_scope(make_engine()) as session:
        total, users = build_day(session, args.date)
    print({"total": total, "users": len(users)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
