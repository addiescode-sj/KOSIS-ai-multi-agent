"""Engine and session from DATABASE_URL, and loading eval run aggregates (T3-18).

Tables are created by Alembic (`alembic upgrade head`, migrations/), not by this module.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from kosis_store.models import EvalRun

ENV_VAR = "DATABASE_URL"
# compose publishes postgres on the host's loopback only (docker-compose.yml)
LOCAL_URL = "postgresql+psycopg://kosis:kosis@localhost:5432/kosis"


def database_url() -> str:
    return os.environ.get(ENV_VAR, "").strip() or LOCAL_URL


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """A session that commits on success and rolls back on any error."""
    session = sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_eval_runs(session: Session, path: Path) -> int:
    """Add the lines of an ops_history.jsonl file whose run_id is not stored yet; returns how many were added."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    known = set(session.scalars(select(EvalRun.run_id)))
    added = 0
    for row in rows:
        if row["run_id"] in known:
            continue
        session.add(EvalRun(**{k: row.get(k) for k in EvalRun.FIELDS}))
        known.add(row["run_id"])
        added += 1
    return added
