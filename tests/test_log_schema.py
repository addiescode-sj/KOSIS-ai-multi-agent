"""T3-36 (KOS-169) request log draft: the rules of the table offline on SQLite, the metric SQL on a real PostgreSQL.

The rows are made up (no golden question, no real user text). The PostgreSQL tests run only when TEST_DATABASE_URL
is set; request_logs has no migration yet, so they create and drop the table themselves.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError

from kosis_store.db import session_scope
from kosis_store.log_metrics import METRICS, metric, purge_questions
from kosis_store.log_models import NO_TRACE, SPAN_KEYS, LogBase, RequestLog, clean_spans, failed_stage

offline = pytest.mark.offline
DAY = date(2026, 11, 20)  # KST
A, B, C, D, E, F = (uuid.uuid4().hex for _ in range(6))  # thread ids as the service makes them


def _log(**kw) -> RequestLog:
    """A row with a fresh trace-id-shaped key (32 hex)."""
    return RequestLog(request_id=uuid.uuid4().hex, **kw)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def _span(name, ms, status="ok", stage="search", kind="node", **more):
    return {"name": name, "kind": kind, "stage": stage, "ms": ms, "status": status, **more}


def _rows() -> list[RequestLog]:
    """Nine user questions on 11/20 KST in five conversations, one harness request, one question on 11/21 KST and one
    111 days old (past the 90-day retention). Hand counts for each metric are in test_metrics_match_hand_counts."""
    ok = dict(outcome="answered", has_numbers=True)
    return [
        # 15:30 UTC on 11/19 is 00:30 on 11/20 in KST: counted on 11/20
        _log(conversation_id=A, turn_index=0, question="2023년 출생아 수", question_len=11, created_at=_at(19, 15, 30),
                   picked_key="K1", survey_name="S1", latency_ms=1000, first_token_ms=200, llm_calls=2,
                   input_tokens=100, output_tokens=50, **ok),
        _log(conversation_id=A, turn_index=1, question="그 표에서 서울만", question_len=9, created_at=_at(20, 1),
                   picked_key="K1", survey_name="S1", latency_ms=3000, first_token_ms=400, llm_calls=2,
                   input_tokens=100, output_tokens=50, **ok),
        _log(conversation_id=B, turn_index=0, question="2031년 인구", question_len=8, created_at=_at(20, 2),
                   outcome="refused", reason_code="no_period", failed_stage="stat", latency_ms=2000, llm_calls=1,
                   input_tokens=100),
        _log(conversation_id=C, turn_index=0, question="오늘 날씨", question_len=5, created_at=_at(20, 3),
                   outcome="refused", reason_code="no_table", failed_stage="search", latency_ms=500),
        _log(conversation_id=D, turn_index=0, question="시도별 혼인 건수", question_len=9, created_at=_at(20, 4),
                   picked_key="K2", survey_name="S2", latency_ms=4000, first_token_ms=600, llm_calls=3,
                   input_tokens=300, output_tokens=150, **ok),
        _log(conversation_id=D, turn_index=1, question="그거 말고", question_len=5, created_at=_at(20, 5),
                   outcome="clarify", reason_code="ambiguous", latency_ms=800, llm_calls=1, input_tokens=100,
                   output_tokens=20),
        _log(conversation_id=D, turn_index=2, question="출생아 수로", question_len=6, created_at=_at(20, 6),
                   picked_key="K1", survey_name="S1", latency_ms=2000, first_token_ms=300, llm_calls=2,
                   input_tokens=100, output_tokens=50, **ok),
        _log(conversation_id=D, turn_index=3, question="전년 대비", question_len=5, created_at=_at(20, 7),
                   outcome="error", failed_stage="search", latency_ms=100),
        # error and aborted turns have times too, but they did not run to the end: left out of latency and first token
        _log(conversation_id=E, turn_index=0, question="인구 추이", question_len=5, created_at=_at(20, 8),
                   outcome="aborted", latency_ms=300, first_token_ms=100),
        # left out of every metric: harness traffic (no text), the next KST day, and an old row for the purge
        _log(conversation_id=F, turn_index=0, is_internal=True, created_at=_at(20, 9), picked_key="K2",
                   latency_ms=9000, **ok),
        _log(conversation_id=F, turn_index=1, question="다음 날 질문", question_len=7, created_at=_at(20, 15, 30),
                   picked_key="K2", latency_ms=7000, **ok),
        _log(conversation_id=F, turn_index=2, question="오래된 질문", question_len=6,
                   created_at=datetime(2026, 8, 1, tzinfo=timezone.utc), picked_key="K2", latency_ms=7000, **ok),
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
    dict(outcome="answered", input_method="suggestion"),  # suggested questions are out of phase 1
    dict(outcome="answered", turn_index=-1),  # the trace counts turns from 0
    dict(outcome="answered", request_id=NO_TRACE),  # tracing was off: no usable key
    dict(outcome="answered", request_id="abc"),  # not a trace id
])
def test_rows_that_break_a_rule_are_rejected(tmp_path, bad):
    engine = _sqlite(tmp_path)
    with pytest.raises(IntegrityError), session_scope(engine) as s:  # a CHECK, not some other failure
        s.add(RequestLog(**{"request_id": uuid.uuid4().hex, "conversation_id": A, "turn_index": 0, **bad}))
    with session_scope(engine) as s:
        assert s.scalar(select(func.count()).select_from(RequestLog)) == 0


@offline
def test_guest_row_needs_no_conversation_and_keeps_defaults(tmp_path):
    engine = _sqlite(tmp_path)
    with session_scope(engine) as s:  # no conversations table at all here: conversation_id is a plain value
        s.add(_log(conversation_id="c0ffee", turn_index=0, outcome="failed"))  # any client thread id, failed turn
    with session_scope(engine) as s:
        row = s.scalars(select(RequestLog)).one()
        assert len(row.request_id) == 32 and row.created_at is not None
        assert (row.is_member, row.is_internal, row.input_method, row.llm_calls, row.log_version) == (
            False, False, "direct", 0, 3)
        assert not hasattr(row, "spans")  # log version 3: steps are Prometheus metrics, not a column


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


# ---------------------------------------------------------------- a real service turn -> one row (log_rows)

def _service_turn(outcome: str = "refused", fail: bool = False, thread: str = "thread-1", retry_of: str | None = None,
                  retry_kind: str | None = None):
    """One turn traced the way the service traces it (start_turn + LiveSpans, T3-30~32), on made-up nodes:
    search node -> search_tables tool, statistics node -> one chat model call. Returns (finished spans, root)."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from langchain_core.tools import tool
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from kosis_agent.observability.tracing import start_turn

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    root, spans = start_turn(provider.get_tracer("test"), thread, 2)

    @tool
    def search_tables(question: str) -> str:
        """Find tables."""
        if fail:
            raise TimeoutError("2023년 출생아 수")  # the message carries the question: it must not reach the log
        return "K1"

    chat = GenericFakeChatModel(messages=iter([AIMessage("{}", usage_metadata={
        "input_tokens": 120, "output_tokens": 30, "total_tokens": 150})]))

    def node(name, fn, x, config):
        return RunnableLambda(fn, name=name).invoke(
            x, {**config, "metadata": {**(config.get("metadata") or {}), "langgraph_node": name}})

    def turn(x, config):
        node("search", lambda y, config: search_tables.invoke({"question": y}, config), x, config)
        node("statistics", lambda y, config: chat.invoke("해석", config), x, config)
        return {"outcome": outcome}

    try:
        RunnableLambda(turn, name="LangGraph").invoke("2023년 출생아 수", {"callbacks": [spans]})
    except TimeoutError:
        pass
    if retry_of:
        root.set_attribute("kosis.turn.retry_of", retry_of)  # as /chat/stream sets it (backend/app/main.py)
    if retry_kind:
        root.set_attribute("kosis.turn.retry_kind", retry_kind)
    root.end()
    return memory.get_finished_spans(), root


@offline
def test_log_rows_uses_the_tracing_names():
    from kosis_agent.observability import tracing
    from kosis_store import log_rows

    assert (log_rows.ROOT_SPAN, log_rows.TOOL_PREFIX, log_rows.CHAT) == (
        tracing.ROOT_SPAN, tracing.TOOL_PREFIX, tracing.CHAT)


@offline
def test_a_service_turn_becomes_one_valid_row(tmp_path):
    from kosis_agent.observability.tracing import trace_id
    from kosis_store.log_rows import row_from_turn, step_spans

    finished, root = _service_turn("refused")
    result = {"outcome": "refused", "answer_origin": "refusal", "table_key": None, "picked": [], "chart": None}
    row = row_from_turn(finished, result, question="2023년 출생아 수", first_token_ms=40)

    assert row["request_id"] == trace_id(root) and row["conversation_id"] == "thread-1" and row["turn_index"] == 2
    # no reason on the trace (the made-up graph has no ChatResult): unknown, not a rejected row
    assert (row["outcome"], row["reason_code"], row["failed_stage"]) == ("refused", "unknown", "search")
    assert "spans" not in row  # log version 3: step timings are metrics (docs/log-schema.md 10)
    finished_root = next(s for s in finished if s.name == "invoke_agent")  # the ended copy, as row_from_turn reads it
    steps = {s["name"]: s for s in step_spans(finished, finished_root)}
    chat_name = next(n for n in steps if n.startswith("chat "))
    assert {n: (s["kind"], s["stage"]) for n, s in steps.items()} == {
        "search": ("node", "search"), "execute_tool search_tables": ("tool", "search"),
        "statistics": ("node", "stat"), chat_name: ("llm", "stat")}
    assert all(set(s) <= set(SPAN_KEYS) for s in steps.values())
    assert (row["llm_calls"], row["input_tokens"], row["output_tokens"]) == (1, 120, 30)
    assert row["latency_ms"] is not None and row["question_len"] == len("2023년 출생아 수")

    engine = _sqlite(tmp_path)
    with session_scope(engine) as s:  # the row passes every CHECK of the table as it is
        s.add(RequestLog(**row))
    with session_scope(engine) as s:
        assert s.get(RequestLog, row["request_id"]).question == "2023년 출생아 수"


@offline
def test_an_error_turn_keeps_the_error_type_and_drops_its_message():
    from kosis_store.log_rows import row_from_turn

    finished, _ = _service_turn(fail=True)
    row = row_from_turn(finished, None, question="2023년 출생아 수")
    assert (row["outcome"], row["reason_code"], row["failed_stage"]) == ("error", "TimeoutError", "search")
    assert "출생아" not in str({k: v for k, v in row.items() if k != "question"})  # the exception message is not kept


@offline
def test_aborted_and_harness_turns():
    from kosis_store.log_rows import row_from_turn

    finished, _ = _service_turn("answered")
    picked = [{"key": "K0", "survey_name": "S0"}, {"key": "K1", "survey_name": "인구동향조사"}]
    answered = {"outcome": "answered", "answer_origin": "stat", "table_key": "K1", "picked": picked,
                "stat_answer": {"outcome": "answered"}, "chart": {"mark": "bar"}}
    row = row_from_turn(finished, answered, question="2023년 출생아 수", is_internal=True)
    assert (row["outcome"], row["picked_key"], row["survey_name"], row["has_numbers"], row["has_chart"]) == (
        "answered", "K1", "인구동향조사", True, True)
    assert row["question"] is None and row["is_internal"]  # harness text is never kept
    assert row_from_turn(finished, answered, aborted=True)["outcome"] == "aborted"
    # the picked list does not hold the answer's table: no survey name rather than another table's
    other = {**answered, "picked": [{"key": "K0", "survey_name": "S0"}]}
    assert row_from_turn(finished, other)["survey_name"] is None


@offline
def test_a_retried_turn_keeps_what_it_retried():
    """T3-68: the retry_of the screen sent reaches request_logs, cut to the column; a plain turn has none."""
    from kosis_store.log_rows import row_from_turn

    def got(**kw):
        row = row_from_turn(_service_turn("answered", **kw)[0], {"outcome": "answered"})
        return row["retry_of"], row["retry_kind"]

    assert got(retry_of="ab" * 16 + "extra", retry_kind="auto") == ("ab" * 16, "auto")
    assert got(retry_of="ab" * 16) == ("ab" * 16, None)  # an older client sends no kind
    assert got(retry_of="ab" * 16, retry_kind="manual") == ("ab" * 16, None)  # unknown kind: not past the column CHECK
    assert got(retry_kind="user") == (None, None)  # a kind without retry_of means nothing
    assert got() == (None, None)


@offline
def test_an_aborted_turn_has_no_failed_stage():
    from kosis_store.log_rows import row_from_turn

    finished, _ = _service_turn(fail=True)  # a span ended as an error, as closing the stream can do
    row = row_from_turn(finished, None, aborted=True)
    assert (row["outcome"], row["failed_stage"]) == ("aborted", None)


@offline
def test_conversation_id_fits_the_column_and_must_exist(tmp_path):
    from kosis_store.log_rows import row_from_turn

    long_id = "x" * 300  # the client may send any thread_id
    finished, _ = _service_turn("answered", thread=long_id)
    row = row_from_turn(finished, {"outcome": "answered"})
    assert len(row["conversation_id"]) == 64 and row["conversation_id"] != long_id[:64]
    assert row_from_turn(finished, {"outcome": "answered"})["conversation_id"] == row["conversation_id"]  # stable
    engine = _sqlite(tmp_path)
    with session_scope(engine) as s:
        s.add(RequestLog(**row))

    for attributes in ({"kosis.turn.index": 0}, {"gen_ai.conversation.id": "c0ffee"}):
        root = SimpleNamespace(name="invoke_agent", attributes=attributes)
        with pytest.raises(ValueError):
            row_from_turn([root], None)


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
    # latency_ms of turns that ran to the end: 500, 800, 1000, 2000, 2000, 3000, 4000 (error, aborted left out)
    assert (got["latency"][0]["p50"], got["latency"][0]["p95"]) == (2000, pytest.approx(3700))
    assert got["refusal_reasons"] == [{"reason_code": "no_period", "n": 1}, {"reason_code": "no_table", "n": 1}]
    assert got["failed_stages"] == [{"failed_stage": "search", "n": 2}, {"failed_stage": "stat", "n": 1}]
    assert got["top_tables"] == [{"picked_key": "K1", "survey_name": "S1", "n": 3},
                                 {"picked_key": "K2", "survey_name": "S2", "n": 1}]
    assert got["turns_per_conversation"] == [{"turns": "1", "n": 3}, {"turns": "2", "n": 1}, {"turns": "4+", "n": 1}]
    # first_token_ms: 200, 300, 400, 600 (aborted 100 left out)
    assert (got["first_token"][0]["p50"], got["first_token"][0]["p95"]) == (350, pytest.approx(570))
    usage = got["llm_usage"][0]  # calls 11, input 800, output 320 over 9 questions
    assert (float(usage["calls"]), float(usage["input_tokens"]), float(usage["output_tokens"])) == (1.22, 88.89, 35.56)


@pytest.mark.postgres
def test_purge_removes_old_text_and_keeps_the_rows(pg):
    cutoff = datetime(2026, 8, 22, tzinfo=timezone.utc)  # 90 days before 11/20
    with session_scope(pg) as s:
        assert purge_questions(s, cutoff) == 1
        assert purge_questions(s, cutoff) == 0  # running it again changes nothing
    with session_scope(pg) as s:
        old = s.scalars(select(RequestLog).where(RequestLog.created_at < cutoff)).one()
        assert old.question is None and old.question_len == 6 and old.picked_key == "K2"
        assert s.scalar(select(func.count()).select_from(RequestLog)) == 12
        assert s.scalar(select(func.count()).select_from(RequestLog).where(
            RequestLog.question.is_not(None), RequestLog.created_at < cutoff)) == 0
