"""T3-51: the service API against its contract (docs/service/api.md, KOS-155), scenarios S1-S9 of plans/T3-51.md.

Offline: the fake search module and phase 1 call of test_agent_graph, the in-memory service store (SQLite) and an
in-memory checkpointer that the tests rebuild to stand for a restart. The PostgreSQL checkpointer test runs only
when TEST_DATABASE_URL is set.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from sqlalchemy import select

from backend.app import main as api
from backend.app import telemetry
from backend.app.main import app, get_checkpointer, get_graph, get_store, get_tools
from kosis_agent.graph.main_graph import build_graph
from kosis_agent.graph.state import new_checkpointer
from kosis_store import cleanup
from kosis_store.db import session_scope
from kosis_store.service_models import ChatSession, Feedback
from kosis_store.service_store import SESSION_TTL, ServiceStore
from tests.test_agent_graph import STAT_LLM, FakeSearch
from tests.test_api import summarize

ME, OTHER = str(uuid.uuid4()), str(uuid.uuid4())


class Service:
    """The app with a fake graph over a checkpointer that outlives a 'restart' (a new graph on the same saver)."""

    def __init__(self, checkpointer=None, store=None):
        self.store = store or ServiceStore.in_memory()
        self.checkpointer = checkpointer or new_checkpointer()
        self.restart()
        self.http = TestClient(app)

    def restart(self):
        graph = build_graph(FakeSearch("found"), STAT_LLM, summarize=summarize, checkpointer=self.checkpointer)
        app.dependency_overrides.update({get_graph: lambda: graph, get_tools: lambda: {},
                                         get_store: lambda: self.store, get_checkpointer: lambda: self.checkpointer,
                                         telemetry.get_tracer: lambda: TracerProvider().get_tracer("test")})

    def ask(self, question="2030년 출생아 수", thread_id=None, session=ME, **body):
        headers = {"X-Session-Id": session} if session else {}
        res = self.http.post("/chat/stream", json={"question": question, "thread_id": thread_id, **body},
                             headers=headers)
        events = [(e.removeprefix("event: "), json.loads(d.removeprefix("data: ")))
                  for e, d in zip([l for l in res.text.splitlines() if l.startswith("event: ")],
                                  [l for l in res.text.splitlines() if l.startswith("data: ")])]
        return res.status_code, dict(events) if res.status_code == 200 else res.json()


@pytest.fixture
def svc():
    api._hits.clear()
    s = Service()
    yield s
    app.dependency_overrides.clear()


def _turns(svc: Service, thread_id: str) -> int:
    return svc.http.get(f"/dev/threads/{thread_id}").json().get("turns", 0)


def test_s1_question_length_is_limited_to_200(svc):
    assert svc.ask("가" * 201)[0] == 422
    assert svc.ask("가" * 200)[0] == 200


def test_s2_meta_and_result_carry_the_request_id(svc):
    status, ev = svc.ask()
    assert status == 200 and set(ev["meta"]) == {"thread_id", "trace_id", "expired"}
    assert ev["meta"]["expired"] is False and len(ev["meta"]["trace_id"]) == 32
    assert ev["result"]["request_id"] == ev["meta"]["trace_id"]


def test_s3_dev_routes_and_the_deployment_switch(svc, monkeypatch):
    body = svc.http.post("/dev/chat", json={"question": "2030년 출생아 수"}, headers={"X-Session-Id": ME}).json()
    assert body["result"]["request_id"] == body["trace_id"] and body["expired"] is False
    assert svc.http.get(f"/dev/threads/{body['thread_id']}").status_code == 200
    monkeypatch.setenv("KOSIS_DISABLE_DEV_ROUTES", "1")
    for method, path in [("post", "/dev/chat"), ("post", "/chat"), ("get", f"/dev/threads/{body['thread_id']}"),
                         ("get", "/threads/x"), ("post", "/tools/search_tables")]:
        res = svc.http.post(path, json={"question": "q"}) if method == "post" else svc.http.get(path)
        assert res.status_code == 404
    assert svc.ask()[0] == 200  # the user endpoints stay


def test_s4_someone_elses_thread_starts_a_new_conversation(svc):
    mine = svc.ask()[1]["meta"]["thread_id"]
    meta = svc.ask("2031년은?", thread_id=mine, session=OTHER)[1]["meta"]
    assert meta["thread_id"] != mine and meta["expired"] is False
    assert _turns(svc, mine) == 1  # nothing was added to mine
    assert svc.ask("2031년은?", thread_id=mine)[1]["meta"]["thread_id"] == mine  # the owner continues it


def test_s5_a_restart_keeps_the_conversation(svc):
    thread = svc.ask()[1]["meta"]["thread_id"]
    svc.restart()  # a new graph object over the same saved state (PostgreSQL in the service)
    meta = svc.ask("2031년은?", thread_id=thread)[1]["meta"]
    assert meta["thread_id"] == thread and _turns(svc, thread) == 2


def test_s6_an_hour_unused_expires_into_a_new_conversation(svc):
    thread = svc.ask()[1]["meta"]["thread_id"]
    with session_scope(svc.store.engine) as s:
        s.get(ChatSession, thread).last_used_at = datetime.now(timezone.utc) - SESSION_TTL - timedelta(minutes=1)
    meta = svc.ask("2031년은?", thread_id=thread)[1]["meta"]
    assert meta["expired"] is True and meta["thread_id"] != thread
    assert svc.ask("2032년은?", thread_id=meta["thread_id"])[1]["meta"]["expired"] is False  # said once


def test_s7_delete_only_my_conversation(svc):
    thread = svc.ask()[1]["meta"]["thread_id"]
    assert svc.http.delete(f"/chat/{thread}", headers={"X-Session-Id": OTHER}).status_code == 404
    assert svc.http.delete("/chat/nope", headers={"X-Session-Id": ME}).json()["error"]["code"] == "not_found"
    assert svc.http.delete(f"/chat/{thread}", headers={"X-Session-Id": ME}).status_code == 204
    assert svc.checkpointer.get_tuple({"configurable": {"thread_id": thread}}) is None  # the saved turns are gone
    meta = svc.ask("2031년은?", thread_id=thread)[1]["meta"]  # the id is free again: nothing to continue
    assert meta["thread_id"] == thread and _turns(svc, thread) == 1


def test_s8_feedback_rules(svc):
    rid = svc.ask()[1]["meta"]["trace_id"]
    post = lambda body, session=ME: svc.http.post("/feedback", json={"request_id": rid, **body},  # noqa: E731
                                                   headers={"X-Session-Id": session})
    first = post({"value": "down", "category": "wrong_number", "reason": "010-1234-5678로 연락 주세요"})
    assert first.status_code == 200 and first.json()["updated"] is False
    again = post({"value": "up", "category": "accurate_number"})
    assert again.json() == {"feedback_id": first.json()["feedback_id"], "updated": True}
    with session_scope(svc.store.engine) as s:
        row = s.scalar(select(Feedback))
        assert (row.value, row.category, row.reason) == ("up", "accurate_number", None)
    assert post({"value": "up", "category": "wrong_number"}).status_code == 422  # a down choice on up
    assert post({"value": "down", "category": "other"}).json()["error"]["code"] == "invalid_feedback"  # needs a reason
    assert post({"value": "down"}).status_code == 200  # closed without a choice: the value only
    assert post({"value": "down"}, session=OTHER).status_code == 404
    assert svc.http.post("/feedback", json={"request_id": "f" * 32, "value": "up"},
                         headers={"X-Session-Id": ME}).status_code == 404
    assert post({"value": "down", "category": "other", "reason": "전화 010-1234-5678 주세요"}).status_code == 200
    with session_scope(svc.store.engine) as s:  # the reason was masked before it was stored
        stored = s.scalar(select(Feedback.reason))
    assert stored and "1234" not in stored and "주세요" in stored


def test_s8_feedback_window_and_rate_limit(svc):
    rid = svc.ask()[1]["meta"]["trace_id"]
    svc.store.record_request(rid, "t-old", ME, datetime.now(timezone.utc) - timedelta(hours=25))
    assert svc.http.post("/feedback", json={"request_id": rid, "value": "up"},
                         headers={"X-Session-Id": ME}).status_code == 404  # older than 24 hours
    api._hits.clear()
    codes = [svc.http.post("/feedback", json={"request_id": "x", "value": "up"}).status_code for _ in range(11)]
    assert codes[:10] == [404] * 10 and codes[10] == 429


def test_s9_feedback_right_after_the_answer(svc):
    """The owner is stored before `meta`, so a press as soon as `result` arrives already finds it."""
    rid = svc.ask()[1]["meta"]["trace_id"]
    assert svc.http.post("/feedback", json={"request_id": rid, "value": "up", "category": "easy"},
                         headers={"X-Session-Id": ME}).status_code == 200


def test_old_body_session_and_no_session_keep_working(svc):
    """The frontend has not moved to X-Session-Id yet: the body field and no session at all still continue."""
    thread = svc.ask(session=None)[1]["meta"]["thread_id"]
    assert svc.ask("2031년은?", thread_id=thread, session=None)[1]["meta"]["thread_id"] == thread
    t2 = svc.ask(session=None, session_id=ME)[1]["meta"]["thread_id"]
    assert svc.ask("2031년은?", thread_id=t2)[1]["meta"]["thread_id"] == t2  # body ME == header ME


def test_cleanup_drops_expired_threads_and_old_text(tmp_path):
    """cleanup.run on a store that also holds the log tables (SQLite here, PostgreSQL in the service)."""
    import kosis_store.log_models  # noqa: F401  (request_logs, ui_events on the same Base)
    from sqlalchemy import create_engine

    from kosis_store.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'svc.db'}")
    Base.metadata.create_all(engine)
    svc = Service(store=ServiceStore(engine))
    try:
        old_thread = svc.ask()[1]["meta"]["thread_id"]
        live_thread = svc.ask()[1]["meta"]["thread_id"]
        now = datetime.now(timezone.utc)
        stale = now - cleanup.RETENTION - timedelta(days=1)
        with session_scope(engine) as s:
            s.get(ChatSession, old_thread).last_used_at = now - SESSION_TTL - timedelta(minutes=1)
            s.add(Feedback(id=uuid.uuid4(), request_id="r" * 32, value="down", category="other", reason="가린 이유",
                           created_at=stale, updated_at=stale))
            s.add(Feedback(id=uuid.uuid4(), request_id="n" * 32, value="up", category="other", reason="새 이유",
                           created_at=now, updated_at=now))
        assert cleanup.run(svc.store, now, dry_run=True) == {"expired_threads": 1, "old_member_threads": 0,
                                                                "feedback.reason": 1}
        counts = cleanup.run(svc.store, now, svc.checkpointer)
        assert counts["expired_threads"] == 1 and counts["feedback.reason"] == 1
        assert svc.checkpointer.get_tuple({"configurable": {"thread_id": old_thread}}) is None
        assert svc.checkpointer.get_tuple({"configurable": {"thread_id": live_thread}}) is not None
        with session_scope(engine) as s:
            assert s.get(ChatSession, old_thread) is None and s.get(ChatSession, live_thread) is not None
            assert sorted(r or "" for r in s.scalars(select(Feedback.reason))) == ["", "새 이유"]
        assert cleanup.run(svc.store, now, svc.checkpointer)["feedback.reason"] == 0  # running it again: no change
    finally:
        app.dependency_overrides.clear()


def test_store_failure_does_not_stop_the_answer(svc, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("database down")

    monkeypatch.setattr(svc.store, "open_thread", broken)
    monkeypatch.setattr(svc.store, "record_request", broken)
    status, ev = svc.ask()
    assert status == 200 and ev["result"]["answer"]


def test_error_event_says_retryable_and_the_request_id(svc):
    class Boom:
        def get_state(self, config):
            raise ValueError("no checkpointer")

        def stream(self, *a, **kw):
            raise TimeoutError("phase 1 timed out")

    app.dependency_overrides[get_graph] = lambda: Boom()
    _, ev = svc.ask()
    assert ev["error"] == {"type": "TimeoutError", "detail": "phase 1 timed out", "retryable": True,
                           "request_id": ev["meta"]["trace_id"]}


PG_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


@pytest.mark.postgres
@pytest.mark.skipif(not PG_URL, reason="TEST_DATABASE_URL is not set (PostgreSQL integration test)")
def test_postgres_checkpointer_survives_a_restart_without_serde_warnings(recwarn):
    from kosis_agent.graph.state import postgres_checkpointer

    first = postgres_checkpointer(PG_URL)
    svc = Service(checkpointer=first)
    try:
        thread = svc.ask()[1]["meta"]["thread_id"]
        first.conn.close()
        svc.checkpointer = postgres_checkpointer(PG_URL)  # a new process: a new pool on the same tables
        svc.restart()
        assert svc.ask("2031년은?", thread_id=thread)[1]["meta"]["thread_id"] == thread
        assert _turns(svc, thread) == 2
        assert not [w for w in recwarn if "msgpack" in str(w.message).lower()]  # CHECKPOINT_TYPES allow-list
    finally:
        app.dependency_overrides.clear()
        for saver in {first, svc.checkpointer}:
            saver.conn.close()
        # the throwaway database is shared with tests/test_store.py's migration check, which must not see these
        from sqlalchemy import create_engine, text

        engine = create_engine(PG_URL)
        with engine.begin() as conn:
            for name in ("checkpoint_writes", "checkpoint_blobs", "checkpoints", "checkpoint_migrations"):
                conn.execute(text(f"DROP TABLE IF EXISTS {name} CASCADE"))
        engine.dispose()
