"""Daily clean-up of the admin schema (kosis-infra/12 6): `python -m admin.app.cleanup`, once a day.

There are no foreign keys to the service tables, so this removes what points at deleted rows (a withdrawn member's
feedback, a deleted member) and applies the 90-day retention of notes and audit rows. Missed days are caught up by
the next run (every rule is "older than", not "on that day").
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from admin.app.config import database_url
from kosis_store.db import session_scope

RETENTION = timedelta(days=90)
STEPS = {
    "orphan_reviews": "DELETE FROM admin.feedback_reviews r WHERE NOT EXISTS "
                      "(SELECT 1 FROM feedback f WHERE f.request_id = r.request_id)",
    "gone_assignees": "UPDATE admin.feedback_reviews r SET assignee_id = NULL WHERE assignee_id IS NOT NULL "
                      "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = r.assignee_id)",
    "gone_editors": "UPDATE admin.feedback_reviews r SET updated_by = NULL WHERE updated_by IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = r.updated_by)",
    "old_notes": "UPDATE admin.feedback_reviews SET note = NULL WHERE note IS NOT NULL AND updated_at < :before",
    "old_audit": "DELETE FROM admin.audit WHERE at < :before",
}


def run(session: Session, now: datetime | None = None) -> dict[str, int]:
    """All clean-up steps; returns the rows each changed. Side effects: updates and deletes admin rows."""
    before = (now or datetime.now(timezone.utc)) - RETENTION
    return {name: session.execute(text(sql), {"before": before}).rowcount for name, sql in STEPS.items()}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    engine = create_engine(database_url())
    with session_scope(engine) as s:
        logging.info("admin cleanup: %s", run(s))
    engine.dispose()


if __name__ == "__main__":
    main()
