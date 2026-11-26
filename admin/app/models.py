"""The operator app's own tables in the `admin` schema (plans/T3-57.md). No foreign keys to service tables, so the
service migrations never depend on these; admin.app.cleanup removes what points at deleted rows."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "admin"
REVIEW_STATUSES = ("new", "checking", "fix_requested", "no_issue")
AUDIT_ACTIONS = ("view_question", "update_review", "denied")
NOTE_MAX = 200


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AdminBase(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class FeedbackReview(AdminBase):
    """What the operators decided about one thumbs-down answer. No row = new (nobody looked)."""

    __tablename__ = "feedback_reviews"
    __table_args__ = (
        CheckConstraint(_in("status", REVIEW_STATUSES), name="ck_feedback_reviews_status"),
        CheckConstraint("issue_key IS NULL OR issue_key ~ '^KOS-[0-9]+$'", name="ck_feedback_reviews_issue_key"),
        CheckConstraint(f"note IS NULL OR char_length(note) <= {NOTE_MAX}", name="ck_feedback_reviews_note"),
    )

    request_id: Mapped[str] = mapped_column(String(32), primary_key=True)  # feedback.request_id
    status: Mapped[str] = mapped_column(String(16))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column()  # users.id
    note: Mapped[str | None] = mapped_column(Text)  # masked; NULL after 90 days
    issue_key: Mapped[str | None] = mapped_column(String(16))  # Linear issue, e.g. KOS-201
    updated_by: Mapped[uuid.UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Audit(AdminBase):
    """Who opened or changed what. No content and no IP."""

    __tablename__ = "audit"
    __table_args__ = (CheckConstraint(_in("action", AUDIT_ACTIONS), name="ck_audit_action"),
                      Index("ix_admin_audit_at", "at"))

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[uuid.UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64))
