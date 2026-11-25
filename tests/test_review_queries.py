"""T3-52: data review queries and their indexes (plans/T3-52.md option A), on PostgreSQL built by the migrations.

- the index definitions as migration 0006 leaves them (and the old session index it replaces)
- review_turns / review_count against hand counts (one outcome, one user's flow, last feedback, filters, pages)
- the planner picks the review indexes on a bulk of made-up rows (no golden question, no provided data)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from kosis_store.db import session_scope
from kosis_store.log_metrics import MetricFilter, _review_where, review_count, review_sql, review_turns
from kosis_store.log_models import RequestLog, UiEvent
from kosis_store.service_models import Feedback

ROOT = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
D20, TODAY = date(2026, 11, 20), date(2026, 11, 23)
S1, S2 = uuid.UUID(int=1), uuid.UUID(int=2)
R = {n: f"{n:02d}" * 16 for n in range(1, 10)}


@pytest.fixture
def migrated():
    """A PostgreSQL database at alembic head (the real index definitions), emptied afterwards."""
    if not PG_URL:
        pytest.skip("TEST_DATABASE_URL is not set (PostgreSQL integration test)")
    from alembic import command
    from alembic.config import Config

    engine = create_engine(PG_URL)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", PG_URL)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield engine, cfg
    command.downgrade(cfg, "base")
    engine.dispose()


def _indexdefs(engine) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename IN "
                                 "('request_logs', 'ui_events')")).all()
    return {name: definition for name, definition in rows}


@pytest.mark.postgres
def test_migration_0006_defines_the_review_indexes_and_reverts(migrated):
    engine, cfg = migrated
    defs = _indexdefs(engine)
    assert defs["ix_request_logs_outcome_created"].endswith("USING btree (outcome, created_at)")
    assert defs["ix_request_logs_session_created"].endswith("USING btree (session_id, created_at)")
    assert "ix_request_logs_session_id" not in defs  # replaced: the composite's leading column serves session_id alone
    assert "ix_ui_events_last_feedback" not in defs  # measured and left out: it slowed the T3-49 metric queries

    from alembic import command

    command.downgrade(cfg, "0005_service_api")
    defs = _indexdefs(engine)
    assert "ix_request_logs_session_id" in defs
    assert not {"ix_request_logs_outcome_created", "ix_request_logs_session_created"} & set(defs)
    command.upgrade(cfg, "head")


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)  # 01:00 UTC = 10:00 KST, same date


def _turn(n, outcome, *, session=S1, hour=1, day=20, internal=False, member=False):
    return RequestLog(request_id=R[n], conversation_id=f"c{n}", turn_index=0, session_id=session, outcome=outcome,
                      reason_code="no_period" if outcome == "refused" else None, is_internal=internal,
                      is_member=member, created_at=_at(day, hour), question=None if internal else f"질문 {n}",
                      question_len=4)


def _feedback(n, value, minute):
    """The feedback table's one row of an answer (T3-55: POST /feedback replaces it in place)."""
    ts = _at(20, 9, minute)
    return Feedback(id=uuid.uuid4(), request_id=R[n], value=value, category="other", reason="이유", created_at=ts,
                    updated_at=ts)


def _old_feedback_event(n, value):
    """An old client's feedback screen event: still accepted, never read by the review."""
    return UiEvent(event_id=uuid.uuid4(), event_type="feedback", client_ts=_at(20, 9, 9), received_at=_at(20, 9, 9),
                   ts_adjusted=False, request_id=R[n], props={"value": value, "choice": "other"}, is_internal=False)


@pytest.mark.postgres
def test_review_turns_match_hand_counts(migrated):
    engine, _ = migrated
    with session_scope(engine) as s:
        s.add_all([
            _turn(1, "refused", hour=1), _turn(2, "answered", hour=2), _turn(3, "refused", hour=3, session=S2),
            _turn(4, "error", hour=4), _turn(5, "refused", hour=5, internal=True),  # harness: left out by default
            _turn(6, "refused", day=21, hour=1),  # another day
            _turn(7, "answered", hour=6, session=S2, member=True),
        ])
        s.flush()
        s.add_all([_feedback(1, "up", 2),  # pressed down, then up: the one row holds up
                   _feedback(3, "down", 1), _old_feedback_event(3, "up"), _old_feedback_event(2, "down"), UiEvent(event_id=uuid.uuid4(), event_type="copy_answer",
                                                    client_ts=_at(20, 9), received_at=_at(20, 9), ts_adjusted=False,
                                                    request_id=R[3], props={}, is_internal=False)])
    flt = MetricFilter(D20, D20).check(TODAY)
    with session_scope(engine) as s:
        refused = review_turns(s, flt, outcome="refused")
        flow = review_turns(s, flt, session_id=S2)
        everything = review_turns(s, flt)
        counts = (review_count(s, flt, outcome="refused"), review_count(s, flt, session_id=S2), review_count(s, flt))
        with_internal = review_count(s, MetricFilter(D20, D20, include_internal=True).check(TODAY), outcome="refused")
        members = review_turns(s, MetricFilter(D20, D20, user="member").check(TODAY))
        page2 = review_turns(s, flt, size=2, page=2)
    # one outcome, newest first, each with its last feedback
    assert [(r["request_id"], r["feedback"], r["feedback_choice"]) for r in refused] == [(R[3], "down", "other"),
                                                                                         (R[1], "up", "other")]
    assert refused[0]["question"] == "질문 3" and refused[0]["reason_code"] == "no_period"
    # one user's flow, oldest first
    assert [r["request_id"] for r in flow] == [R[3], R[7]]
    assert [r["request_id"] for r in everything] == [R[7], R[4], R[3], R[2], R[1]]
    assert counts == (2, 2, 5) and with_internal == 3
    assert [r["request_id"] for r in members] == [R[7]]
    assert [r["request_id"] for r in page2] == [R[3], R[2]]
    assert everything[0]["feedback"] is None  # no feedback: an empty value, the row is still listed
    assert everything[3]["request_id"] == R[2] and everything[3]["feedback"] is None  # an old feedback event: not read


def test_review_rejects_an_unknown_outcome_and_bad_pages():
    flt = MetricFilter(D20, D20)
    with pytest.raises(ValueError):
        review_sql(flt, outcome="failed_or_error")  # one stored value at a time: failed and error are apart
    with pytest.raises(ValueError):
        review_sql(flt, page=0)
    sql, params = review_sql(flt, outcome="refused", session_id=S1)
    assert "outcome = :outcome" in sql and "session_id = :session_id" in sql and params["outcome"] == "refused"
    assert "IS NULL OR" not in sql  # an optional condition is left out, not written as "x IS NULL OR ..."


# ---------------------------------------------------------------- the planner picks the review indexes

BULK = """
INSERT INTO request_logs (request_id, conversation_id, turn_index, session_id, is_member, is_internal, input_method,
                          outcome, reason_code, has_numbers, has_chart, llm_calls, input_tokens, output_tokens,
                          log_version, created_at)
SELECT md5('r' || i), 'c' || i, 0,
       ('00000000-0000-0000-0000-' || lpad(to_hex(i % :sessions), 12, '0'))::uuid,
       i % 3 = 0, i % 50 = 0, 'direct',
       CASE WHEN i % 100 < 80 THEN 'answered' WHEN i % 100 < 90 THEN 'refused' WHEN i % 100 < 94 THEN 'clarify'
            WHEN i % 100 < 96 THEN 'failed' WHEN i % 100 < 97 THEN 'error' ELSE 'aborted' END,
       CASE WHEN i % 100 BETWEEN 80 AND 89 THEN 'no_period' END,
       true, false, 1, 100, 20, 3,
       timestamptz '2026-08-26 00:00+09' + (i * interval '90 days') / :turns
FROM generate_series(1, :turns) AS i;
INSERT INTO ui_events (event_id, event_type, client_ts, received_at, ts_adjusted, request_id, props, is_internal,
                       log_version)
SELECT gen_random_uuid(), CASE WHEN j % 10 < 6 THEN 'citation_click' ELSE 'copy_answer' END,
       now() - j * interval '1 second', now() - j * interval '1 second', false,
       md5('r' || (1 + j % :turns)), '{}'::jsonb, false, 3
FROM generate_series(1, :events) AS j;
INSERT INTO feedback (id, request_id, value, category, created_at, updated_at)
SELECT gen_random_uuid(), md5('r' || i), CASE WHEN i % 20 = 0 THEN 'down' ELSE 'up' END, 'other', now(), now()
FROM generate_series(1, :turns) AS i WHERE i % 10 = 0;
"""


def bulk_load(conn, *, turns: int, events: int, sessions: int) -> None:
    """Made-up log rows over 90 days ending 11/23: outcome mix 80/10/4/2/1/3 %, a feedback on every 10th question."""
    for statement in BULK.split(";"):
        if statement.strip():
            conn.execute(text(statement), {"turns": turns, "events": events, "sessions": sessions})
    conn.execute(text("ANALYZE request_logs; ANALYZE ui_events; ANALYZE feedback"))


def plan_of(conn, sql: str, params: dict) -> str:
    return json.dumps(conn.execute(text("EXPLAIN (FORMAT JSON) " + sql), params).scalar())


@pytest.mark.postgres
def test_the_planner_uses_the_review_indexes(migrated):
    engine, _ = migrated
    with engine.begin() as conn:
        bulk_load(conn, turns=30_000, events=60_000, sessions=500)
    # 90 days. A common outcome with a page limit (refused, 10 %) is as cheap through the created_at index walked newest
    # first, and the planner rightly keeps it; a rare outcome (error, 1 %) and the counts take the outcome index.
    quarter = MetricFilter(date(2026, 8, 26), date(2026, 11, 22))
    with engine.connect() as conn:
        by_outcome = plan_of(conn, *review_sql(quarter, outcome="error"))
        where, extra = _review_where("refused", None)
        count = plan_of(conn, f"SELECT count(*) FROM request_logs WHERE {where}", {**quarter.params(), **extra})
        by_user = plan_of(conn, *review_sql(quarter, session_id=uuid.UUID(int=7)))
    assert "ix_request_logs_outcome_created" in by_outcome and "ix_request_logs_outcome_created" in count
    assert "ix_request_logs_session_created" in by_user
    # T3-55: feedback comes from the feedback table (one row per answer), never from ui_events. A rare outcome looks
    # each listed question up on the unique feedback.request_id index; for one user's few questions the planner may
    # instead hash the small feedback table once (measured: 3,000 rows), which is as cheap.
    assert "feedback_request_id_key" in by_outcome
    assert '"Relation Name": "feedback"' in by_user
    assert "ui_events" not in by_outcome and "ui_events" not in by_user
