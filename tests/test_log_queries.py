"""T3-49 (KOS-161): the metric screen queries under the common filter, against hand counts on PostgreSQL.

Made-up rows only (no golden question). Two KST days (11/20, 11/21), members and guests, one harness turn, a pressed-
again feedback, feedback on an answer with no question row. "Today" is 11/23 for the filter check.
T3-55: feedback is read from the feedback table (POST /feedback, one row per answer); feedback screen events in
ui_events are old-client compat and never counted.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine

from kosis_store.db import session_scope
from kosis_store.log_metrics import (METRICS, TABS, MetricFilter, down_answers, purge_questions, query, tab)
from kosis_store.log_models import LogBase, RequestLog, UiEvent
from kosis_store.service_models import Feedback

D20, D21, TODAY = date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 23)
R = {n: f"{n:02d}" * 16 for n in range(1, 10)}  # request ids r1..r9 (32 hex)


def _at(day: int, hour: int) -> datetime:
    return datetime(2026, 11, day, hour, 0, tzinfo=timezone.utc)  # 01:00 UTC = 10:00 KST, same date


def _turn(n, conversation, turn, *, member=False, outcome="answered", at=None, internal=False, **kw):
    return RequestLog(request_id=R[n], conversation_id=conversation, turn_index=turn, is_member=member,
                      outcome=outcome, is_internal=internal, created_at=at or _at(20, n),
                      question=None if internal else f"질문 {n}", question_len=4, **kw)


def _event(kind, request_id, *, value=None, choice=None, reason=None, minute=0, day=20):
    props = {"value": value, "choice": choice} if kind == "feedback" else {}
    ts = datetime(2026, 11, day, 9, minute, tzinfo=timezone.utc)
    return UiEvent(event_id=uuid.uuid4(), event_type=kind, client_ts=ts, received_at=ts, ts_adjusted=False,
                   request_id=request_id, props=props, reason=reason, is_internal=False)


def _feedback(request_id, value, category, *, reason=None, day=20, pressed_again=False):
    """The feedback table's one row of an answer (POST /feedback upserts it: updated_at is the last press)."""
    ts = datetime(2026, 11, day, 9, 0, tzinfo=timezone.utc)
    return Feedback(id=uuid.uuid4(), request_id=request_id, value=value, category=category, reason=reason,
                    created_at=ts, updated_at=ts.replace(minute=2) if pressed_again else ts)


def _rows():
    return [
        _turn(1, "A", 0, member=True, picked_key="K1", survey_name="S1", has_numbers=True, latency_ms=1000,
              llm_calls=2, input_tokens=400, output_tokens=100),
        _turn(2, "A", 1, member=True, outcome="refused", reason_code="no_period", failed_stage="stat", latency_ms=2000,
              llm_calls=1, input_tokens=100, output_tokens=0),
        _turn(3, "B", 0, picked_key="K2", survey_name="S2", has_numbers=True, latency_ms=3000, llm_calls=2,
              input_tokens=200, output_tokens=50),
        _turn(4, "C", 0, outcome="aborted", latency_ms=500),
        _turn(5, "D", 0, outcome="failed", failed_stage="agent", latency_ms=4000),
        _turn(6, "E", 0, member=True, outcome="error", reason_code="RuntimeError", failed_stage="search",
              latency_ms=100, at=_at(21, 1)),
        _turn(7, "F", 0, member=True, picked_key="K1", survey_name="S1", has_numbers=True, latency_ms=1500,
              at=_at(21, 2)),
        _turn(8, "H", 0, internal=True, picked_key="K9", has_numbers=True, latency_ms=9000),  # harness
        _turn(9, "B", 1, outcome="clarify", reason_code="ambiguous", latency_ms=800),
        _feedback(R[1], "up", "accurate_number", pressed_again=True),  # down first, then up: the row holds up
        _feedback(R[3], "down", "wrong_table", reason="표가 달라요 [전화번호]"),
        _feedback(R[7], "down", "wrong_number", day=21),
        _feedback(R[8], "down", "hard"),  # on the harness turn
        _feedback("ab" * 16, "down", "hard"),  # no question row: never counted
        # old-client feedback screen events: accepted, never read by the metrics
        _event("feedback", R[3], value="up", choice="accurate", minute=5),
        _event("feedback", R[5], value="down", choice="wrong_number"),
        _event("citation_click", R[1]), _event("citation_click", R[1], minute=3), _event("citation_click", R[7], day=21),
    ]


PG_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


@pytest.fixture
def pg():
    if not PG_URL:
        pytest.skip("TEST_DATABASE_URL is not set (PostgreSQL integration test)")
    engine = create_engine(PG_URL)
    LogBase.metadata.drop_all(engine)
    LogBase.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add_all(_rows())
    yield engine
    LogBase.metadata.drop_all(engine)
    engine.dispose()


def _num(rows):  # numeric columns as float for comparison
    return [{k: (float(v) if hasattr(v, "is_integer") or type(v).__name__ == "Decimal" else v) for k, v in r.items()}
            for r in rows]


@pytest.mark.postgres
def test_every_tab_metric_matches_hand_counts_for_one_day(pg):
    """11/20, all users, harness left out: r1 r2 r3 r4 r5 r9."""
    with session_scope(pg) as s:
        got = {m: query(s, m, MetricFilter(D20, D20)) for tab_metrics in TABS.values() for m in tab_metrics}
    assert got["question_count"] == [{"n": 6}]
    # aborted r4 out of the denominator; failed r5 is an error: 2 answered, 1 refused, 1 clarify, 1 error of 5
    assert {r["outcome"]: (r["n"], float(r["share"])) for r in got["outcome_ratio"]} == {
        "answered": (2, 0.4), "refused": (1, 0.2), "clarify": (1, 0.2), "error": (1, 0.2)}
    assert _num(got["thumbs_up_ratio"]) == [{"up": 1, "down": 1, "ratio": 0.5}]  # r1 last up, r3 down
    # finished turns 800 1000 2000 3000 4000 (aborted r4 left out, failed r5 kept)
    assert (got["latency"][0]["p50"], got["latency"][0]["p95"]) == (2000, pytest.approx(3800))
    assert got["refusal_reasons"] == [{"reason_code": "no_period", "n": 1}]
    assert got["down_reasons"] == [{"choice": "wrong_table", "n": 1}]
    assert got["up_reasons"] == [{"choice": "accurate_number", "n": 1}]
    assert got["failed_stages"] == [{"failed_stage": "other", "n": 1}, {"failed_stage": "stat", "n": 1}]
    assert got["top_tables"] == [{"picked_key": "K1", "survey_name": "S1", "n": 1},
                                 {"picked_key": "K2", "survey_name": "S2", "n": 1}]
    assert got["turns_per_conversation"] == [{"turns": "1", "n": 2}, {"turns": "2", "n": 2}]
    assert _num(got["citation_click_rate"]) == [{"clicked": 1, "with_numbers": 2, "rate": 0.5}]  # r1 of r1, r3
    assert _num(got["abort_rate"]) == [{"aborted": 1, "questions": 6, "rate": 0.1667}]
    assert _num(got["llm_usage"]) == [{"calls": 0.83, "input_tokens": 116.67, "output_tokens": 25.0}]


@pytest.mark.postgres
def test_member_guest_and_internal_filters_add_up(pg):
    with session_scope(pg) as s:
        def q(name, **kw):
            return _num(query(s, name, MetricFilter(D20, D20, **kw)))

        assert [q("question_count", user=u)[0]["n"] for u in ("all", "member", "guest")] == [6, 2, 4]
        assert q("thumbs_up_ratio", user="member") == [{"up": 1, "down": 0, "ratio": 1.0}]
        assert q("thumbs_up_ratio", user="guest") == [{"up": 0, "down": 1, "ratio": 0.0}]
        # harness included: r8 and its thumbs-down come in (the feedback with no question row never does)
        assert q("question_count", include_internal=True) == [{"n": 7}]
        assert q("thumbs_up_ratio", include_internal=True) == [{"up": 1, "down": 2, "ratio": 0.3333}]
        assert {r["picked_key"] for r in query(s, "top_tables", MetricFilter(D20, D20, include_internal=True))} == {
            "K1", "K2", "K9"}  # the harness turn's table mixes in when internal traffic is included
        assert q("question_count", user="member", include_internal=True) == [{"n": 2}]  # r8 is not a member


@pytest.mark.postgres
def test_ranges_and_daily_series(pg):
    with session_scope(pg) as s:
        flt = MetricFilter(D20, D21)
        assert query(s, "question_count", flt) == [{"n": 8}]
        assert query(s, "question_count", flt, by_day=True) == [{"day": D20, "n": 6}, {"day": D21, "n": 2}]
        assert _num(query(s, "thumbs_up_ratio", flt, by_day=True)) == [
            {"day": D20, "up": 1, "down": 1, "ratio": 0.5}, {"day": D21, "up": 0, "down": 1, "ratio": 0.0}]
        assert _num(query(s, "abort_rate", flt, by_day=True))[1] == {"day": D21, "aborted": 0, "questions": 2,
                                                                     "rate": 0.0}
        with pytest.raises(ValueError):
            query(s, "latency", flt, by_day=True)  # percentiles are not split by day


@pytest.mark.postgres
def test_down_answers_page_and_retention(pg):
    flt = MetricFilter(D20, D21)
    with session_scope(pg) as s:
        first = down_answers(s, flt, page=1, size=1)
        second = down_answers(s, flt, page=2, size=1)
    assert first["total"] == second["total"] == 2
    assert first["rows"][0]["request_id"] == R[7]  # newest first
    row = second["rows"][0]
    assert (row["request_id"], row["question"], row["picked_key"], row["outcome"], row["choice"], row["reason"]) == (
        R[3], "질문 3", "K2", "answered", "wrong_table", "표가 달라요 [전화번호]")
    with session_scope(pg) as s:  # 90 days later the question text is gone, the row stays in the list
        purge_questions(s, datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc))
    with session_scope(pg) as s:
        rows = down_answers(s, flt, size=10)["rows"]
    assert [r["question"] for r in rows] == ["질문 7", None]


def test_the_filter_allows_only_past_days_inside_90_days():
    MetricFilter(date(2026, 8, 25), date(2026, 11, 22)).check(TODAY)  # 90 days back, yesterday: fine
    for bad in [MetricFilter(D20, TODAY),  # today is not complete
                MetricFilter(date(2026, 8, 24), D20),  # more than 90 days back
                MetricFilter(D21, D20),  # start after end
                MetricFilter(D20, D20, user="everyone")]:
        with pytest.raises(ValueError):
            bad.check(TODAY)
    with pytest.raises(ValueError):
        down_answers(None, MetricFilter(D20, D20), page=0)  # pages start at 1


def test_every_tab_metric_is_defined():
    assert all(m in METRICS for metrics in TABS.values() for m in metrics)
    assert sum(len(v) for v in TABS.values()) + 1 == 15  # + the thumbs-down list (down_answers); T3-50 rewrite_kinds
