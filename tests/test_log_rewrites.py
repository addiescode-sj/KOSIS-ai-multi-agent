"""T3-50: the questions the agent rewrote, logged next to the question as typed (plans/T3-50.md option B).

How a turn was read comes from its finished ChatResult; the rewritten text goes to request_rewrites, masked, one row
per rewritten question. Made-up questions only (no golden question).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import pytest
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy import create_engine, select

from kosis_agent.graph.state import ChatResult
from kosis_store.db import session_scope
from kosis_store.log_collector import TurnLogCollector, db_writer
from kosis_store.log_metrics import MetricFilter, down_answers, purge_rewrites, purge_texts, query
from kosis_store.log_models import LogBase, RequestLog, RequestRewrite, UiEvent
from kosis_store.log_rows import rewrite_kind, rewrite_rows

RID = "ab" * 16


def _result(question="부산은?", **kw) -> dict:
    """A ChatResult as the collector gets it (model_dump), so a renamed field fails here."""
    base = dict(question=question, route="found", no_answer=False, answer="…", answer_origin="stat",
                outcome="answered", resolved_question=question, interpreted_by="rule")
    return ChatResult(**{**base, **kw}).model_dump(mode="json")


CARRY = _result(resolved_question="2023년 부산광역시 출생아 수", reused_table=True)
# the earlier table refused, the rewritten question found another table: the turn ends with reused_table False
CARRY_FALLBACK = _result(resolved_question="2023년 부산광역시 출생아 수", reused_table=False)
REFERENCE = _result("그 표에서 2022년은?", reused_table=True)
# resolved_question of a chart-only turn is a copy of the earlier question, not a rewrite
CHART = _result("막대로 그려줘", answer_origin="chart", reused_table=True, resolved_question="2023년 시도별 출생아 수")
MULTI = _result("서울이랑 부산 출생아 수", answer_origin="multi", resolved_question="2023년 부산 출생아 수",
                sub_results=[{"question": "2023년 서울 출생아 수", "outcome": "answered"},
                             {"question": "2023년 부산 출생아 수", "outcome": "answered"}])
NEW = _result("2023년 서울 출생아 수", interpreted_by="llm")


@pytest.mark.parametrize("result, kind", [
    (CARRY, "carry_over"), (CARRY_FALLBACK, "carry_over"), (REFERENCE, "reference"), (CHART, "chart_only"),
    (MULTI, "multi"), (NEW, "new"), (None, None),
])
def test_rewrite_kind_reads_the_finished_result(result, kind):
    assert rewrite_kind(result) == kind


def test_only_rewritten_turns_get_rows_and_the_text_is_masked():
    leaky = _result(resolved_question="010-1234-5678 말고 2023년 부산광역시 출생아 수", reused_table=True)
    assert rewrite_rows(RID, leaky) == [{"request_id": RID, "seq": 0, "kind": "resolved",
                                         "text": "[전화번호] 말고 2023년 부산광역시 출생아 수", "text_len": 27}]
    assert [(r["seq"], r["kind"], r["text"]) for r in rewrite_rows(RID, MULTI)] == [
        (0, "sub_question", "2023년 서울 출생아 수"), (1, "sub_question", "2023년 부산 출생아 수")]
    for kept_as_typed in (REFERENCE, CHART, NEW, None):
        assert rewrite_rows(RID, kept_as_typed) == []
    assert rewrite_rows(RID, CARRY, is_internal=True) == []  # harness: no text, so no row


# ---------------------------------------------------------------- through the collector (SQLite)

@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'logs.db'}")
    LogBase.metadata.create_all(engine)
    return engine


def _finish(collector, provider, conversation, result, *, internal=False):
    root = provider.get_tracer("test").start_span("invoke_agent", attributes={
        "gen_ai.conversation.id": conversation, "kosis.turn.index": 0})
    root.end()
    collector.finish(root, result, question="질문", is_internal=internal)
    return format(root.get_span_context().trace_id, "032x")


def test_collector_writes_the_turn_row_then_its_rewrites(store):
    calls: list[str] = []
    write = db_writer(store)

    def recording(table, rows):
        calls.append(table)
        write(table, rows)

    provider = TracerProvider()
    collector = TurnLogCollector(recording)
    provider.add_span_processor(collector)
    multi = _finish(collector, provider, "m", MULTI)
    carry = _finish(collector, provider, "c", CARRY)
    new = _finish(collector, provider, "n", NEW)
    harness = _finish(collector, provider, "h", CARRY, internal=True)
    collector.force_flush()
    # the rewrites follow their own turn row (foreign key); turns kept as typed and harness turns add none
    assert calls == ["request_logs", "request_rewrites", "request_logs", "request_rewrites", "request_logs",
                     "request_logs"]
    with session_scope(store) as s:
        logs = {r.request_id: (r.rewrite_kind, r.interpreted_by) for r in s.scalars(select(RequestLog))}
        texts = [(r.request_id, r.seq, r.text) for r in s.scalars(select(RequestRewrite).order_by(
            RequestRewrite.request_id, RequestRewrite.seq))]
    assert logs == {multi: ("multi", "rule"), carry: ("carry_over", "rule"), new: ("new", "llm"),
                    harness: ("carry_over", "rule")}
    assert sorted(texts) == sorted([(multi, 0, "2023년 서울 출생아 수"), (multi, 1, "2023년 부산 출생아 수"),
                                    (carry, 0, "2023년 부산광역시 출생아 수")])
    assert collector.counts["written_request_rewrites"] == 3


# ---------------------------------------------------------------- metrics and retention (PostgreSQL)

PG_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
D20, TODAY = date(2026, 11, 20), date(2026, 11, 23)
R = {n: f"{n:02d}" * 16 for n in range(1, 7)}


def _at(hour: int) -> datetime:
    return datetime(2026, 11, 20, hour, 0, tzinfo=timezone.utc)


def _turn(n, kind, outcome="answered", internal=False):
    return RequestLog(request_id=R[n], conversation_id=f"c{n}", turn_index=0, outcome=outcome, is_internal=internal,
                      question=None if internal else f"질문 {n}", rewrite_kind=kind, interpreted_by="rule",
                      created_at=_at(n), reason_code="no_period" if outcome == "refused" else None)


def _feedback(n, value):
    import uuid

    ts = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
    return UiEvent(event_id=uuid.uuid4(), event_type="feedback", client_ts=ts, received_at=ts, ts_adjusted=False,
                   request_id=R[n], props={"value": value, "choice": "other"}, is_internal=False)


@pytest.fixture
def pg():
    if not PG_URL:
        pytest.skip("TEST_DATABASE_URL is not set (PostgreSQL integration test)")
    engine = create_engine(PG_URL)
    LogBase.metadata.drop_all(engine)
    LogBase.metadata.create_all(engine)
    with session_scope(engine) as s:
        s.add_all([_turn(1, "carry_over"), _turn(2, "carry_over", "refused"), _turn(3, "new"), _turn(4, "multi"),
                   _turn(5, None, "error"), _turn(6, "carry_over", internal=True)])
        s.flush()
        s.add_all([RequestRewrite(request_id=R[1], seq=0, kind="resolved", text="2023년 부산 출생아 수", text_len=14,
                                  created_at=_at(1)),
                   RequestRewrite(request_id=R[2], seq=0, kind="resolved", text="2022년 부산 출생아 수", text_len=14,
                                  created_at=_at(2)),
                   RequestRewrite(request_id=R[4], seq=0, kind="sub_question", text="서울 출생아 수", text_len=8,
                                  created_at=_at(4)),
                   RequestRewrite(request_id=R[4], seq=1, kind="sub_question", text="부산 출생아 수", text_len=8,
                                  created_at=_at(4)),
                   _feedback(1, "up"), _feedback(2, "down"), _feedback(4, "down")])
    yield engine
    LogBase.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.postgres
def test_rewrite_kinds_match_hand_counts(pg):
    """11/20, harness left out: carry_over r1 (up) r2 (refused, down), new r3, multi r4 (down), error r5 (no result)."""
    with session_scope(pg) as s:
        got = query(s, "rewrite_kinds", MetricFilter(D20, D20).check(TODAY))
    as_float = [{k: float(v) if type(v).__name__ == "Decimal" else v for k, v in r.items()} for r in got]
    assert as_float == [
        {"rewrite_kind": "carry_over", "n": 2, "answered": 1, "answered_share": 0.5, "up": 1, "down": 1, "up_ratio": 0.5},
        {"rewrite_kind": "multi", "n": 1, "answered": 1, "answered_share": 1.0, "up": 0, "down": 1, "up_ratio": 0.0},
        {"rewrite_kind": "new", "n": 1, "answered": 1, "answered_share": 1.0, "up": 0, "down": 0, "up_ratio": None},
        {"rewrite_kind": "unknown", "n": 1, "answered": 0, "answered_share": 0.0, "up": 0, "down": 0,
         "up_ratio": None},
    ]


@pytest.mark.postgres
def test_down_answers_show_the_rewritten_question_until_retention(pg):
    flt = MetricFilter(D20, D20).check(TODAY)
    with session_scope(pg) as s:
        rows = {r["request_id"]: (r["rewrite_kind"], r["rewritten"]) for r in down_answers(s, flt)["rows"]}
    assert rows == {R[2]: ("carry_over", "2022년 부산 출생아 수"), R[4]: ("multi", "서울 출생아 수 / 부산 출생아 수")}
    with session_scope(pg) as s:
        assert purge_rewrites(s, _at(3)) == 2  # r1, r2 are past the cutoff; r4 is not
        assert purge_rewrites(s, _at(3)) == 0  # running it again changes nothing
    with session_scope(pg) as s:
        rows = {r["request_id"]: (r["rewrite_kind"], r["rewritten"]) for r in down_answers(s, flt)["rows"]}
        kept = s.scalars(select(RequestRewrite).where(RequestRewrite.request_id == R[2])).one()
    assert rows == {R[2]: ("carry_over", None), R[4]: ("multi", "서울 출생아 수 / 부산 출생아 수")}
    assert (kept.kind, kept.text, kept.text_len) == ("resolved", None, 14)  # the row and its length stay


@pytest.mark.postgres
def test_purge_texts_reaches_every_free_text_column_and_rows_go_with_their_turn(pg):
    with session_scope(pg) as s:
        assert purge_texts(s, _at(23)) == {"question": 5, "rewrite": 4, "feedback_reason": 0}
    with session_scope(pg) as s:
        s.delete(s.get(RequestLog, R[4]))  # deleting a turn takes its rewrites with it (ON DELETE CASCADE)
    with session_scope(pg) as s:
        assert s.scalars(select(RequestRewrite).where(RequestRewrite.request_id == R[4])).all() == []
