"""T3-48 (KOS-170): the user log collector. Masking, screen event checks, and service turns written as request_logs rows
through the real chat endpoints (fake search and phase 1 of test_agent_graph, SQLite store). Made-up questions only."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy import create_engine, func, select

from backend.app import telemetry
from backend.app.main import ChatRequest, app, chat_stream, get_graph, get_tools
from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.state import new_checkpointer
from kosis_agent.retrieval.search import make_search_tables_tool
from kosis_store.db import session_scope
from kosis_store.log_collector import CLOCK_SKEW, MAX_EVENTS_PER_BATCH, TurnLogCollector, check_events, db_writer
from kosis_store.daily_usage import build_day
from kosis_store.log_metrics import purge_feedback_reasons
from kosis_store.log_models import (LOG_VERSION, DailySessionUsage, DailyUsage, IngestReject, LogBase, RequestLog,
                                    UiEvent)
from kosis_store.masking import mask
from tests.test_agent_graph import STAT_LLM, FakeSearch
from tests.test_api import summarize

NOW = datetime(2026, 11, 23, 3, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- masking

@pytest.mark.parametrize("text, expected", [
    ("연락은 hong.gil@example.co.kr 로", "연락은 [이메일] 로"),
    ("010-1234-5678로 알려줘", "[전화번호]로 알려줘"),
    ("01012345678", "[전화번호]"),
    ("02-123-4567 번", "[전화번호] 번"),
    ("031.1234.5678", "[전화번호]"),
    ("주민번호 900101-1234567", "주민번호 [주민번호]"),
    ("카드 1234-5678-9012-3456", "카드 [카드번호]"),
])
def test_mask_hides_personal_data(text, expected):
    assert mask(text) == expected


@pytest.mark.parametrize("text", [
    "2023년 출생아 수", "2015~2023년 서울 인구", "1,234,567명", "2024년 1월~12월", "101_DT_1B04005N 표",
    "10년 전보다 30% 줄었어?", "202407~202609 월별",
])
def test_mask_keeps_numbers_a_statistics_question_needs(text):
    assert mask(text) == text


def test_mask_keeps_none():
    assert mask(None) is None


# ---------------------------------------------------------------- screen events

def _event(**kw) -> dict:
    base = {"event_id": str(uuid.uuid4()), "event_type": "citation_click", "ts": NOW.isoformat(),
            "log_version": LOG_VERSION, "session_id": str(uuid.uuid4()), "request_id": "ab" * 16,
            "screen_id": "S-03", "props": {"table_key": "101_DT_1B8000H", "rank": 1}}
    return {**base, **kw}


def test_events_that_pass_become_rows():
    rows, rejects = check_events([_event(), _event(event_type="copy_answer", props={})], NOW)
    assert len(rows) == 2 and not rejects
    assert rows[0]["props"] == {"table_key": "101_DT_1B8000H", "rank": 1} and rows[0]["ts_adjusted"] is False


@pytest.mark.parametrize("bad, reason", [
    (dict(event_type="question_submit"), "unknown_event_type"),  # the server logs questions, not the browser
    (dict(event_type="page_view", props={}), "unknown_event_type"),  # dropped 11/23
    (dict(event_type="chart_view", props={"chart_type": "bar"}), "unknown_event_type"),  # dropped 11/23
    (dict(reason="자유 글자"), "bad_field"),  # free text only on feedback
    (dict(log_version=99), "unknown_version"),
    (dict(event_id="not-a-uuid"), "bad_field"),
    (dict(ts="2026-11-23T03:00:00"), "bad_field"),  # no zone
    (dict(request_id="abc"), "bad_field"),
    (dict(screen_id="화면 3번"), "bad_field"),
    (dict(props={"table_key": "K", "rank": 1, "note": "질문 원문"}), "bad_props"),  # no free text key
    (dict(props={"table_key": "K", "rank": True}), "bad_props"),  # bool is not a rank
    (dict(props={"table_key": "K" * 65, "rank": 1}), "bad_props"),
])
def test_events_that_fail_are_counted_by_reason_and_not_kept(bad, reason):
    rows, rejects = check_events([_event(**bad)], NOW)
    assert rows == [] and sum(rejects.values()) == 1 and next(iter(rejects))[0] == reason


def test_missing_field_and_odd_shapes():
    event = _event()
    del event["ts"]
    rows, rejects = check_events([event, "text", None], NOW)
    assert rows == [] and dict(rejects) == {("missing_field", "citation_click"): 1, ("not_an_object", None): 2}


def test_a_skewed_browser_clock_is_replaced_and_flagged():
    late = (NOW - CLOCK_SKEW - timedelta(seconds=1)).isoformat()
    ok = (NOW - CLOCK_SKEW + timedelta(seconds=1)).isoformat()
    rows, _ = check_events([_event(ts=late), _event(ts=ok)], NOW)
    assert (rows[0]["client_ts"], rows[0]["ts_adjusted"]) == (NOW, True)
    assert rows[1]["ts_adjusted"] is False


def test_a_repeated_event_id_is_kept_once_and_a_big_batch_is_cut():
    same = _event()
    rows, rejects = check_events([same, dict(same)], NOW)
    assert len(rows) == 1 and not rejects
    rows, rejects = check_events([_event() for _ in range(MAX_EVENTS_PER_BATCH + 3)], NOW)
    assert len(rows) == MAX_EVENTS_PER_BATCH and rejects[("batch_too_large", None)] == 3


def _feedback(value="down", choice="wrong_number", reason=None, request_id="ab" * 16, **kw) -> dict:
    return _event(event_type="feedback", props={"value": value, "choice": choice}, request_id=request_id,
                  **({"reason": reason} if reason is not None else {}), **kw)


def test_feedback_is_checked_and_its_reason_masked():
    rows, rejects = check_events([_feedback(reason="숫자가 달라요 010-1234-5678"), _feedback("up", "accurate")], NOW)
    assert not rejects and rows[0]["reason"] == "숫자가 달라요 [전화번호]" and rows[0]["props"]["value"] == "down"
    assert rows[1]["reason"] is None


@pytest.mark.parametrize("bad", [
    _feedback(choice="accurate"),  # an up choice on a down
    _feedback(value="meh"),
    _feedback(choice="other"),  # "other" needs a reason
    _feedback(reason="가" * 201),
    _feedback(request_id=None),  # feedback is about one answer
])
def test_bad_feedback_is_rejected(bad):
    rows, rejects = check_events([bad], NOW)
    assert rows == [] and next(iter(rejects))[0] in {"bad_feedback", "bad_field"}


def test_harness_feedback_keeps_no_reason():
    rows, _ = check_events([_feedback(choice="other", reason="이유")], NOW, is_internal=True)
    assert rows[0]["reason"] is None and rows[0]["is_internal"]


# ---------------------------------------------------------------- service turns -> rows

@pytest.fixture
def store(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'logs.db'}")
    LogBase.metadata.create_all(engine)
    return engine


def _service(store, graph, *, write=None):
    """The app with a fake graph, an in-memory provider and a collector writing to `store` (or to `write`)."""
    provider = TracerProvider()
    collector = TurnLogCollector(write or db_writer(store))
    provider.add_span_processor(collector)
    tools = {t.name: t for t in (make_search_tables_tool(FakeSearch("found")),)}
    app.dependency_overrides.update({get_graph: lambda: graph, get_tools: lambda: tools,
                                     telemetry.get_tracer: lambda: provider.get_tracer("test"),
                                     telemetry.get_collector: lambda: collector})
    return TestClient(app), provider, collector


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _rows(store) -> list[RequestLog]:
    with session_scope(store) as s:
        return list(s.scalars(select(RequestLog).order_by(RequestLog.created_at)))


def _graph(route="found", summarize_fn=summarize):
    return build_graph(FakeSearch(route), STAT_LLM, summarize=summarize_fn, checkpointer=new_checkpointer())


def test_every_turn_becomes_one_row(store):
    """Stream and plain chat, a phase 1 refusal, a search refusal and an error: one row each, keyed by the trace id."""
    def failing(question, table_id, **kw):
        raise RuntimeError("HCX call failed for 010-1234-5678")

    turns = 0
    http, provider, collector = _service(store, _graph())
    sid = str(uuid.uuid4())
    meta = http.post("/chat/stream", json={"question": "2030년 출생아 수 010-1234-5678", "thread_id": "t1",
                                           "session_id": sid}).text
    http.post("/chat", json={"question": "2031년은?", "thread_id": "t1", "input_method": "clarify_option"})
    turns += 2
    http, provider, collector = _service(store, _graph("refuse"))
    http.post("/chat/stream", json={"question": "오늘 날씨", "thread_id": "t2"})
    turns += 1
    http, provider, collector = _service(store, _graph(summarize_fn=failing))
    http.post("/chat/stream", json={"question": "전년 대비", "thread_id": "t3"})
    turns += 1
    collector.force_flush()

    rows = _rows(store)
    assert len(rows) == turns and len({r.request_id for r in rows}) == turns  # one row per turn, no duplicate
    first = next(r for r in rows if r.conversation_id == "t1" and r.turn_index == 0)
    assert first.request_id in meta  # the key the browser got in the meta event
    assert first.question == "2030년 출생아 수 [전화번호]" and str(first.session_id) == sid  # stored masked
    assert (first.outcome, first.reason_code, first.input_method) == ("refused", "out_of_scope", "direct")
    assert first.llm_calls == 1 and first.input_tokens == 120  # from the phase 1 step spans (same source as traces)
    assert first.first_token_ms is not None and first.latency_ms is not None
    second = next(r for r in rows if r.conversation_id == "t1" and r.turn_index == 1)
    assert second.input_method == "clarify_option" and second.first_token_ms is None  # /chat has no tokens
    assert next(r for r in rows if r.conversation_id == "t2").failed_stage == "search"
    error = next(r for r in rows if r.conversation_id == "t3")
    assert (error.outcome, error.reason_code) == ("error", "RuntimeError")
    assert all("010-1234" not in (r.question or "") for r in rows)


def test_an_aborted_stream_is_one_aborted_row(store):
    provider = TracerProvider()
    collector = TurnLogCollector(db_writer(store))
    provider.add_span_processor(collector)
    stream = chat_stream(ChatRequest(question="q", thread_id="t9"), _graph(), provider.get_tracer("test"), collector,
                         None)
    assert next(stream).event == "meta"
    stream.close()  # what Starlette does on disconnect (GeneratorExit)
    collector.force_flush()
    (row,) = _rows(store)
    assert (row.outcome, row.failed_stage, row.conversation_id) == ("aborted", None, "t9")


def test_harness_traffic_keeps_no_text_and_is_marked_on_the_span(store):
    http, provider, collector = _service(store, _graph())
    http.post("/chat", json={"question": "골든 문항이라고 가정", "thread_id": "h1"}, headers={"X-Kosis-Internal": "1"})
    collector.force_flush()
    (row,) = _rows(store)
    assert row.is_internal and row.question is None and row.question_len is not None


def test_a_failed_write_never_reaches_the_answer(store):
    def broken(table, rows):
        raise ConnectionError("database is down")

    http, provider, collector = _service(store, _graph(), write=broken)
    res = http.post("/chat", json={"question": "2030년 출생아 수"})
    stream = http.post("/chat/stream", json={"question": "2030년 출생아 수"})
    collector.force_flush()
    assert res.status_code == 200 and res.json()["result"]["outcome"] == "refused"
    assert stream.status_code == 200 and "event: result" in stream.text
    assert collector.counts["failed_request_logs"] == 2 and collector.counts["written_request_logs"] == 0


def test_tool_calls_and_untraced_turns_write_nothing(store):
    http, provider, collector = _service(store, _graph())
    http.post("/tools/search_tables", json={"question": "출생아 수"})
    assert collector._spans == {}  # the tool trace was dropped when its root ended, nothing left behind
    from opentelemetry.trace import NoOpTracer

    app.dependency_overrides[telemetry.get_tracer] = lambda: NoOpTracer()  # tracing off: no key for a row
    http.post("/chat", json={"question": "2030년 출생아 수"})
    collector.force_flush()
    assert _rows(store) == [] and collector.counts["skipped_no_trace"] == 1


def test_events_endpoint_stores_passing_events_once_and_counts_the_rest(store):
    http, provider, collector = _service(store, _graph())
    event = _event(ts=datetime.now(timezone.utc).isoformat())
    body = http.post("/events", json=[event, event, _event(event_type="question_submit")]).json()
    again = http.post("/events", json=[event]).json()  # a resent batch
    collector.force_flush()
    assert (body, again) == ({"accepted": 1, "rejected": 1}, {"accepted": 1, "rejected": 0})
    with session_scope(store) as s:
        assert s.scalar(select(func.count()).select_from(UiEvent)) == 1  # the resent event_id was ignored
        reject = s.scalars(select(IngestReject)).one()
        assert (reject.reason, reject.event_type, reject.n) == ("unknown_event_type", "question_submit", 1)
    assert http.post("/events", json=[_event()] * (MAX_EVENTS_PER_BATCH + 1)).status_code == 422


def test_log_counts_endpoint(store):
    http, provider, collector = _service(store, _graph())
    http.post("/chat", json={"question": "2030년 출생아 수"})
    collector.force_flush()
    body = http.get("/ops/log-counts").json()
    assert body["enabled"] and body["counts"]["written_request_logs"] == 1


# ---------------------------------------------------------------- daily usage (PostgreSQL: KST day bounds)

PG_URL = __import__("os").environ.get("TEST_DATABASE_URL", "").strip()


@pytest.fixture
def pg():
    if not PG_URL:
        pytest.skip("TEST_DATABASE_URL is not set (PostgreSQL integration test)")
    engine = create_engine(PG_URL)
    LogBase.metadata.drop_all(engine)
    LogBase.metadata.create_all(engine)
    yield engine
    LogBase.metadata.drop_all(engine)
    engine.dispose()


def _turn(conversation, turn, session=None, outcome="answered", internal=False, member=False,
          at=datetime(2026, 11, 23, 1, 0, tzinfo=timezone.utc)):
    return RequestLog(request_id=uuid.uuid4().hex, conversation_id=conversation, turn_index=turn, session_id=session,
                      outcome=outcome, is_internal=internal, is_member=member, created_at=at,
                      question=None if internal else "q")


def _ui(kind, request_id=None, value=None, at=datetime(2026, 11, 23, 1, 0, tzinfo=timezone.utc), internal=False,
        session=None):
    props = {"value": value, "choice": "other"} if kind == "feedback" else {}
    return UiEvent(event_id=uuid.uuid4(), event_type=kind, client_ts=at, received_at=at, ts_adjusted=False,
                   request_id=request_id, props=props, is_internal=internal, session_id=session)


@pytest.mark.postgres
def test_daily_usage_total_and_per_user_match_hand_counts_and_rebuild_the_same(pg):
    s1, s2, s3 = sorted((uuid.uuid4() for _ in range(3)), key=str)
    r1 = "cd" * 16
    rows = [
        _turn("A", 0, s1, member=True), _turn("A", 1, s1, member=True), _turn("A", 2, s1, member=True),  # 3 turns
        _turn("B", 0, s2, outcome="aborted"),
        _turn("C", 0, None),  # no session id sent: in the total only
        _turn("H", 0, None, internal=True),  # harness: left out
        _turn("D", 0, s2, at=datetime(2026, 11, 23, 15, 30, tzinfo=timezone.utc)),  # 00:30 on 11/24 KST
        _ui("citation_click", session=s1), _ui("citation_click", session=s1), _ui("copy_answer", session=s2),
        _ui("feedback", r1, "down", session=s1, at=datetime(2026, 11, 23, 1, 0, tzinfo=timezone.utc)),
        _ui("feedback", r1, "up", session=s1, at=datetime(2026, 11, 23, 1, 5, tzinfo=timezone.utc)),  # last: up
        _ui("feedback", "ef" * 16, "down", session=s2),
        _ui("copy_answer", session=s3),  # a user who only copied an answer that day
        _ui("citation_click", internal=True, session=s1),
    ]
    with session_scope(pg) as s:
        s.add_all(rows)
    day = datetime(2026, 11, 23).date()
    with session_scope(pg) as s:
        first = build_day(s, day, now=NOW)
    with session_scope(pg) as s:
        again = build_day(s, day, now=NOW)
        assert s.scalar(select(func.count()).select_from(DailyUsage)) == 1
        assert s.scalar(select(func.count()).select_from(DailySessionUsage)) == 3
    total, users = first
    expected = {"questions": 5, "sessions": 2, "conversations": 3, "avg_turns": 1.67, "multi_turn_conversations": 1,
                "aborted": 1, "citation_clicks": 2, "copy_answers": 2, "feedback_up": 1, "feedback_down": 1}
    assert {k: total[k] for k in expected} == expected and first == again
    keys = ("is_member", "questions", "conversations", "avg_turns", "multi_turn_conversations", "aborted",
            "citation_clicks", "copy_answers", "feedback_up", "feedback_down")
    assert {u["session_id"]: tuple(u[k] for k in keys) for u in users} == {
        s1: (True, 3, 1, 3.0, 1, 0, 2, 0, 1, 0),
        s2: (False, 1, 1, 1.0, 0, 1, 0, 1, 0, 1),
        s3: (False, 0, 0, 0.0, 0, 0, 0, 1, 0, 0),
    }


@pytest.mark.postgres
def test_feedback_reasons_are_purged_after_retention(pg):
    old = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with session_scope(pg) as s:
        e = _ui("feedback", "ab" * 16, "down", at=old)
        e.reason = "이유"
        s.add(e)
    with session_scope(pg) as s:
        assert purge_feedback_reasons(s, datetime(2026, 8, 25, tzinfo=timezone.utc)) == 1
        assert purge_feedback_reasons(s, datetime(2026, 8, 25, tzinfo=timezone.utc)) == 0
        assert s.scalars(select(UiEvent)).one().reason is None
