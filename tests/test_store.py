"""T3-18 relational store: ORM rules offline on SQLite, and the Alembic migration on a real PostgreSQL.

The PostgreSQL tests run only when TEST_DATABASE_URL is set (a throwaway database, never the backend's
DATABASE_URL). They build the schema with `alembic upgrade head` and drop it with `downgrade base`.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, delete, event, func, inspect, select

from kosis_store.db import load_eval_runs, session_scope
from kosis_store.models import Base, Conversation, EvalRun, Message

ROOT = Path(__file__).resolve().parents[1]
RESULT = {"answer": "2023년 출생아 수는 …", "citations": [{"key": "101_DT_1B8000F", "rank": 1}]}


def _sqlite(tmp_path):
    """SQLite with foreign keys on, so ON DELETE CASCADE behaves as on PostgreSQL."""
    engine = create_engine(f"sqlite:///{tmp_path / 'store.db'}")
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    return engine


def _ops_lines(tmp_path, n=3):
    path = tmp_path / "ops_history.jsonl"
    rows = [{"run_id": f"run-{i}", "layer": "l1", "stage": "T3-18-test", "target": "bm25-record",
             "latency_p50_ms": 1.5 + i, "latency_p95_ms": 3.0, "input_tokens": 0, "output_tokens": 0,
             "cost": None, "currency": None, "tool_failure_rate": 0.0, "retry_rate": 0.0} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _roundtrip(engine, tmp_path):
    """Write one two-turn conversation and the eval runs, read them back, delete the conversation."""
    with session_scope(engine) as s:
        conv = Conversation()
        conv.messages = [Message(turn=1, role="user", content="2023년 출생아 수"),
                         Message(turn=1, role="assistant", content="…", result=RESULT),
                         Message(turn=2, role="user", content="그 표에서 서울만")]
        s.add(conv)
        assert load_eval_runs(s, _ops_lines(tmp_path)) == 3
    with session_scope(engine) as s:
        conv = s.scalars(select(Conversation)).one()
        assert isinstance(conv.id, uuid.UUID) and conv.created_at is not None
        assert [(m.turn, m.role) for m in conv.messages] == [(1, "user"), (1, "assistant"), (2, "user")]
        assert conv.messages[1].result == RESULT
        assert load_eval_runs(s, _ops_lines(tmp_path)) == 0  # same run_ids are not added twice
        assert s.get(EvalRun, "run-2").latency_p50_ms == 3.5
        cid = conv.id
    return cid


@pytest.mark.offline
def test_orm_roundtrip_on_sqlite(tmp_path):
    engine = _sqlite(tmp_path)
    _roundtrip(engine, tmp_path)
    with session_scope(engine) as s:  # deleting a conversation deletes its messages
        s.delete(s.scalars(select(Conversation)).one())
    with session_scope(engine) as s:
        assert s.scalar(select(func.count()).select_from(Message)) == 0


@pytest.mark.offline
def test_one_message_per_turn_and_role(tmp_path):
    engine = _sqlite(tmp_path)
    with pytest.raises(Exception), session_scope(engine) as s:
        s.add(Conversation(messages=[Message(turn=1, role="user", content="a"),
                                     Message(turn=1, role="user", content="b")]))
    with session_scope(engine) as s:  # the failed write was rolled back
        assert s.scalar(select(func.count()).select_from(Conversation)) == 0


@pytest.mark.offline
def test_eval_runs_hold_aggregates_only():
    columns = {c.name for c in EvalRun.__table__.columns}
    assert not columns & {"question", "answer", "item_id", "gold", "expected"}
    assert set(EvalRun.FIELDS) <= columns


PG_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


@pytest.fixture
def pg_engine():
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


@pytest.mark.postgres
def test_migration_matches_models(pg_engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    engine, _ = pg_engine
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
    assert inspect(engine).get_columns("messages")[5]["type"].__class__.__name__ == "JSONB"


@pytest.mark.postgres
def test_roundtrip_and_cascade_on_postgres(pg_engine, tmp_path):
    engine, _ = pg_engine
    cid = _roundtrip(engine, tmp_path)
    with session_scope(engine) as s:  # database-level ON DELETE CASCADE, without loading the messages
        s.execute(delete(Conversation).where(Conversation.id == cid))
    with session_scope(engine) as s:
        assert s.scalar(select(func.count()).select_from(Message)) == 0
        assert s.scalar(select(func.count()).select_from(EvalRun)) == 3
