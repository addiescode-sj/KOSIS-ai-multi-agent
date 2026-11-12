"""T3-34 (KOS-169) request log draft: the rules of the table offline on SQLite, the metric SQL on a real PostgreSQL.

The rows are made up (no golden question, no real user text). The PostgreSQL tests run only when TEST_DATABASE_URL
is set; request_logs has no migration yet, so they create and drop the table themselves.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, func, select

from kosis_store.db import session_scope
from kosis_store.log_metrics import METRICS, metric, purge_questions
from kosis_store.log_models import SPAN_KEYS, LogBase, RequestLog, clean_spans, failed_stage

offline = pytest.mark.offline
DAY = date(2026, 11, 20)  # KST
A, B, C, D, E, F = (uuid.uuid4() for _ in range(6))


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def _span(name, ms, status="ok", stage="search", **more):
    return {"name": name, "kind": "node", "stage": stage, "ms": ms, "status": status, **more}


def _rows() -> list[RequestLog]:
    """Nine user questions on 11/20 KST in five conversations, one harness request, one question on 11/21 KST and one
    40 days old. Hand counts for each metric are in test_metrics_match_hand_counts."""
    ok = dict(outcome="answered", has_numbers=True, llm_model="HCX-DASH-002")
    return [
        # 15:30 UTC on 11/19 is 00:30 on 11/20 in KST: counted on 11/20
        RequestLog(conversation_id=A, turn=1, question="2023년 출생아 수", question_len=11, created_at=_at(19, 15, 30),
                   picked_key="K1", survey_name="S1", latency_ms=1000, first_token_ms=200, llm_calls=2,
                   input_tokens=100, output_tokens=50,
                   spans=[_span("retrieve", 300), _span("compute", 500, stage="stat")], **ok),
        RequestLog(conversation_id=A, turn=2, question="그 표에서 서울만", question_len=9, created_at=_at(20, 1),
                   picked_key="K1", survey_name="S1", latency_ms=3000, first_token_ms=400, llm_calls=2,
                   input_tokens=100, output_tokens=50,
                   spans=[_span("retrieve", 500), _span("compute", 700, stage="stat")], **ok),
        RequestLog(conversation_id=B, turn=1, question="2031년 인구", question_len=8, created_at=_at(20, 2),
                   outcome="refused", reason_code="no_period", failed_stage="stat", latency_ms=2000, llm_calls=1,
                   input_tokens=100,
                   spans=[_span("retrieve", 400), _span("compute", 100, status="refused", stage="stat")]),
        RequestLog(conversation_id=C, turn=1, question="오늘 날씨", question_len=5, created_at=_at(20, 3),
                   outcome="refused", reason_code="no_table", failed_stage="search", latency_ms=500,
                   spans=[_span("retrieve", 200), _span("gate", 10, status="refused")]),
        RequestLog(conversation_id=D, turn=1, question="시도별 혼인 건수", question_len=9, created_at=_at(20, 4),
                   picked_key="K2", survey_name="S2", latency_ms=4000, first_token_ms=600, llm_calls=3,
                   input_tokens=300, output_tokens=150,
                   spans=[_span("retrieve", 600), _span("compute", 900, stage="stat")], **ok),
        RequestLog(conversation_id=D, turn=2, question="그거 말고", question_len=5, created_at=_at(20, 5),
                   outcome="clarify", reason_code="ambiguous", latency_ms=800, llm_calls=1, input_tokens=100,
                   output_tokens=20, spans=[_span("retrieve", 100)]),
        RequestLog(conversation_id=D, turn=3, question="출생아 수로", question_len=6, created_at=_at(20, 6),
                   picked_key="K1", survey_name="S1", latency_ms=2000, first_token_ms=300, llm_calls=2,
                   input_tokens=100, output_tokens=50, spans=[], **ok),
        RequestLog(conversation_id=D, turn=4, question="전년 대비", question_len=5, created_at=_at(20, 7),
                   outcome="error", failed_stage="search",
                   spans=[_span("retrieve", 50, status="error", error_type="TimeoutError")]),
        RequestLog(conversation_id=E, turn=1, question="인구 추이", question_len=5, created_at=_at(20, 8),
                   outcome="aborted"),
        # left out of every metric: harness traffic (no text), the next KST day, and an old row for the purge
        RequestLog(conversation_id=F, turn=1, is_internal=True, created_at=_at(20, 9), picked_key="K2",
                   latency_ms=9000, **ok),
        RequestLog(conversation_id=F, turn=2, question="다음 날 질문", question_len=7, created_at=_at(20, 15, 30),
                   picked_key="K2", latency_ms=7000, **ok),
        RequestLog(conversation_id=F, turn=3, question="오래된 질문", question_len=6,
                   created_at=datetime(2026, 10, 1, tzinfo=timezone.utc), picked_key="K2", latency_ms=7000, **ok),
    ]


def _sqlite(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'logs.db'}")
    LogBase.metadata.create_all(engine)
    return engine


@offline
@pytest.mark.parametrize("bad", [
    dict(outcome="answered", is_internal=True, question="골든 문항 원문"),  # harness text never stored
    dict(outcome="refused"),  # a refusal says why
    dict(outcome="done"),  # not an outcome
    dict(outcome="answered", input_method="voice"),
    dict(outcome="answered", turn=0),
])
def test_rows_that_break_a_rule_are_rejected(tmp_path, bad):
    engine = _sqlite(tmp_path)
    with pytest.raises(Exception), session_scope(engine) as s:
        s.add(RequestLog(**{"conversation_id": A, "turn": 1, **bad}))
    with session_scope(engine) as s:
        assert s.scalar(select(func.count()).select_from(RequestLog)) == 0


@offline
def test_guest_row_needs_no_conversation_and_keeps_defaults(tmp_path):
    engine = _sqlite(tmp_path)
    with session_scope(engine) as s:  # no conversations table at all here: conversation_id is a plain value
        s.add(RequestLog(conversation_id=A, turn=1, outcome="aborted"))
    with session_scope(engine) as s:
        row = s.scalars(select(RequestLog)).one()
        assert isinstance(row.request_id, uuid.UUID) and row.created_at is not None
        assert (row.is_member, row.is_internal, row.input_method, row.spans, row.llm_calls) == (
            False, False, "typed", [], 0)


@offline
def test_spans_keep_allowed_keys_only():
    raw = [{"name": "retrieve", "kind": "node", "stage": "search", "ms": 12.5, "status": "ok", "top_keys": ["K1"],
            "question": "2023년 출생아 수"},
           {"name": "compute", "stage": "stat", "ms": 3, "status": "failed", "error": "ValueError: 2023년 출생아 수"},
           {"name": "llm", "kind": "llm", "ms": 900, "input_tokens": 10, "output_tokens": None}]
    spans = clean_spans(raw)
    assert all(set(s) <= set(SPAN_KEYS) for s in spans)
    assert "출생아" not in str(spans)  # free text that can carry the question is gone
    assert [s["status"] for s in spans] == ["ok", "error", "ok"]
    assert "output_tokens" not in spans[2]


@offline
def test_failed_stage_is_the_first_span_that_did_not_end_ok():
    assert failed_stage([_span("retrieve", 1), _span("compute", 1, stage="stat")]) is None
    assert failed_stage([_span("retrieve", 1), _span("compute", 1, status="refused", stage="stat"),
                         _span("chart", 1, status="error", stage="chart")]) == "stat"
    assert failed_stage([{"name": "x", "status": "error"}]) == "unknown"
    assert failed_stage([]) is None


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


@pytest.mark.postgres
def test_metrics_match_hand_counts(pg):
    with session_scope(pg) as s:
        got = {name: metric(s, name, DAY) for name in METRICS}
    assert got["question_count"] == [{"n": 9}]
    assert {r["outcome"]: (r["n"], float(r["share"])) for r in got["outcome_share"]} == {
        "aborted": (1, 0.1111), "answered": (4, 0.4444), "clarify": (1, 0.1111), "error": (1, 0.1111),
        "refused": (2, 0.2222)}
    # latency_ms: 500, 800, 1000, 2000, 2000, 3000, 4000
    assert (got["latency"][0]["p50"], got["latency"][0]["p95"]) == (2000, pytest.approx(3700))
    assert got["refusal_reasons"] == [{"reason_code": "no_period", "n": 1}, {"reason_code": "no_table", "n": 1}]
    assert got["failed_stages"] == [{"failed_stage": "search", "n": 2}, {"failed_stage": "stat", "n": 1}]
    assert got["top_tables"] == [{"picked_key": "K1", "survey_name": "S1", "n": 3},
                                 {"picked_key": "K2", "survey_name": "S2", "n": 1}]
    assert got["turns_per_conversation"] == [{"turns": "1", "n": 3}, {"turns": "2", "n": 1}, {"turns": "4+", "n": 1}]
    # retrieve ms: 50, 100, 200, 300, 400, 500, 600 · compute ms: 100, 500, 700, 900 · gate: 10
    assert {r["name"]: (float(r["p50"]), float(r["p95"])) for r in got["stage_latency"]} == {
        "compute": (600, pytest.approx(870)), "gate": (10, 10), "retrieve": (300, pytest.approx(570))}
    # first_token_ms: 200, 300, 400, 600
    assert (got["first_token"][0]["p50"], got["first_token"][0]["p95"]) == (350, pytest.approx(570))
    usage = got["llm_usage"][0]  # calls 11, input 800, output 320 over 9 questions
    assert (float(usage["calls"]), float(usage["input_tokens"]), float(usage["output_tokens"])) == (1.22, 88.89, 35.56)


@pytest.mark.postgres
def test_purge_removes_old_text_and_keeps_the_rows(pg):
    cutoff = datetime(2026, 10, 21, tzinfo=timezone.utc)  # 30 days before 11/20
    with session_scope(pg) as s:
        assert purge_questions(s, cutoff) == 1
        assert purge_questions(s, cutoff) == 0  # running it again changes nothing
    with session_scope(pg) as s:
        old = s.scalars(select(RequestLog).where(RequestLog.created_at < cutoff)).one()
        assert old.question is None and old.question_len == 6 and old.picked_key == "K2"
        assert s.scalar(select(func.count()).select_from(RequestLog)) == 12
        assert s.scalar(select(func.count()).select_from(RequestLog).where(
            RequestLog.question.is_not(None), RequestLog.created_at < cutoff)) == 0
        assert inspect_type(pg) == "JSONB"


def inspect_type(engine) -> str:
    from sqlalchemy import inspect

    return next(c for c in inspect(engine).get_columns("request_logs") if c["name"] == "spans")["type"].__class__.__name__
