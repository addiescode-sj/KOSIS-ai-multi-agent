"""Daily cleanup job (T3-51, docs/service/api.md 0, 4; docs/log-schema.md 13; KOS-167):

1. Guest conversations, and a member's unsaved ones, unused for SESSION_TTL: their checkpoint (LangGraph PostgresSaver) and chat_sessions /
   chat_requests rows.
1b. Member conversations (T3-54) whose last question is older than the member's limit (90 or 30 days): checkpoint, sessions, requests and saved
   turns; and logins that have expired.
2. Raw text older than RETENTION: request_logs.question, request_rewrites.text (T3-50), ui_events.reason (T3-48 purge functions) and feedback.reason.
   Rows and the other columns stay. Running it again changes nothing.

    python -m kosis_store.cleanup            # uses DATABASE_URL
    python -m kosis_store.cleanup --dry-run  # counts only
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from kosis_store.db import make_engine, session_scope
from kosis_store.log_metrics import purge_feedback_reasons, purge_questions, purge_rewrites
from kosis_store.service_models import Feedback
from kosis_store.service_store import ServiceStore

RETENTION = timedelta(days=int(os.environ.get("KOSIS_LOG_RETENTION_DAYS", "90")))  # T3-51 decision K7: 90 days


def drop_threads(store: ServiceStore, thread_ids: list[str], checkpointer=None) -> int:
    """Delete these conversations' rows and, when a checkpointer is given, their saved state."""
    for thread_id in thread_ids:
        if checkpointer is not None:
            checkpointer.delete_thread(thread_id)
    store.delete_threads(thread_ids)
    return len(thread_ids)


def purge_raw_text(store: ServiceStore, before: datetime) -> dict[str, int]:
    """Clear the masked free text older than `before`; returns cleared counts per column."""
    with session_scope(store.engine) as s:
        out = {"request_logs.question": purge_questions(s, before), "request_rewrites.text": purge_rewrites(s, before),
               "ui_events.reason": purge_feedback_reasons(s, before)}
        out["feedback.reason"] = s.execute(update(Feedback).where(Feedback.updated_at < before,
                                                                  Feedback.reason.is_not(None))
                                           .values(reason=None)).rowcount
    return out


def run(store: ServiceStore, now: datetime, checkpointer=None, dry_run: bool = False) -> dict[str, int]:
    expired, old = store.expired_threads(now), store.old_member_threads(now)
    if dry_run:
        with session_scope(store.engine) as s:
            reasons = s.scalar(select(func.count()).select_from(Feedback).where(Feedback.updated_at < now - RETENTION,
                                                                           Feedback.reason.is_not(None)))
        return {"expired_threads": len(expired), "old_member_threads": len(old), "feedback.reason": reasons or 0}
    return {"expired_threads": drop_threads(store, expired, checkpointer),
            "old_member_threads": drop_threads(store, old, checkpointer),
            "expired_logins": store.purge_logins(now), **purge_raw_text(store, now - RETENTION)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    checkpointer = None
    if not args.dry_run:
        from kosis_agent.graph.state import postgres_checkpointer

        checkpointer = postgres_checkpointer(url)
    print(run(ServiceStore(make_engine(url)), datetime.now(timezone.utc), checkpointer, args.dry_run))


if __name__ == "__main__":
    main()
