"""T3-57: the operator app (admin/). Offline rules, then PostgreSQL tests through the restricted kosis_admin account.

Made-up rows only. The service schema comes from the service migrations, the admin schema from admin/migrations, and
the account's grants from admin/sql/grants.sql as it is.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from admin.app import auth, queries
from admin.app.labels import label
from kosis_store.log_metrics import KST, MetricFilter

ROOT = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
ORIGIN = "https://ops.test"
ME, OTHER, OUTSIDER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


# ---------------------------------------------------------------- offline

def test_token_hash_is_the_backends():
    from kosis_store.service_store import token_hash

    assert auth.token_hash("abc") == token_hash("abc")


def test_cookie_name_follows_the_public_origin(monkeypatch):
    monkeypatch.setenv("ADMIN_PUBLIC_ORIGIN", "https://x.test")
    assert auth.cookie_name() == "__Host-kosis_session"
    monkeypatch.setenv("ADMIN_PUBLIC_ORIGIN", "http://localhost:3000")
    assert auth.cookie_name() == "kosis_session"


def test_clean_review_rules():
    allowed = {ME, OTHER}
    with pytest.raises(ValueError, match="Linear"):
        queries.clean_review("fix_requested", "", "", "", ME, allowed)
    with pytest.raises(ValueError, match="KOS-201"):
        queries.clean_review("checking", "", "", "ABC-1", ME, allowed)
    with pytest.raises(ValueError, match="메모"):
        queries.clean_review("no_issue", "", "  ", "", ME, allowed)
    with pytest.raises(ValueError, match="200"):
        queries.clean_review("checking", "", "가" * 201, "", ME, allowed)
    with pytest.raises(ValueError, match="운영자"):
        queries.clean_review("checking", str(OUTSIDER), "", "", ME, allowed)
    with pytest.raises(ValueError):
        queries.clean_review("done", "", "", "", ME, allowed)
    v = queries.clean_review("checking", "", "010-1234-5678 확인", " kos-12 ", ME, allowed)
    assert (v.assignee_id, v.issue_key, v.note) == (ME, "KOS-12", "[전화번호] 확인")  # checking: me by default, masked
    assert queries.clean_review("new", "", "", "", ME, allowed).assignee_id is None
    assert queries.clean_review("no_issue", str(OTHER), "맞는 답", "", ME, allowed).assignee_id == OTHER


def test_date_range_is_moved_into_the_allowed_range(monkeypatch):
    monkeypatch.setattr(queries, "today", lambda: date(2026, 11, 26))
    r = queries.date_range(None, None, include_today=False)
    assert (r.flt.start_day, r.flt.end_day, r.notice) == (date(2026, 11, 19), date(2026, 11, 25), None)
    r = queries.date_range("2026-11-20", "2026-11-26", include_today=False)  # metrics stop at yesterday
    assert r.flt.end_day == date(2026, 11, 25) and r.notice
    r = queries.date_range("2026-01-01", "2026-11-26", include_today=True)  # review screens include today, 90 days
    assert (r.flt.start_day, r.flt.end_day) == (date(2026, 8, 28), date(2026, 11, 26)) and r.notice
    r = queries.date_range("bad", None, include_today=True, user="x")
    assert r.flt.end_day == date(2026, 11, 26) and r.flt.user == "all" and "형식" in r.notice
    r.flt.__class__(r.flt.start_day, r.flt.end_day)  # still a MetricFilter


def test_unknown_code_is_shown_as_is():
    assert label("outcome", "answered") == "답함"
    assert label("outcome", "partial") == "partial"
    assert label("down", None, "선택 안 함") == "선택 안 함"


# ---------------------------------------------------------------- PostgreSQL

RID = {n: f"{n:032x}" for n in range(1, 10)}


def _now_kst(hour_offset: int = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hour_offset)


def _seed(conn) -> dict[str, str]:
    """Three members (two operators, one outsider), their login tokens, questions over today and the last days,
    thumbs-down feedback. Returns the cookie tokens."""
    now = datetime.now(timezone.utc)
    for uid, name in ((ME, "병석"), (OTHER, "선주"), (OUTSIDER, "손님")):
        conn.execute(text("INSERT INTO users (id, kakao_id, nickname, created_at, last_login_at) "
                          "VALUES (:id, :k, :n, :t, :t)"), {"id": uid, "k": str(uid.int)[:12], "n": name, "t": now})
    tokens = {"me": "tok-me", "other": "tok-other", "outsider": "tok-out", "expired": "tok-old"}
    for key, uid, exp in (("me", ME, now + timedelta(days=1)), ("other", OTHER, now + timedelta(days=1)),
                          ("outsider", OUTSIDER, now + timedelta(days=1)), ("expired", ME, now - timedelta(minutes=1))):
        conn.execute(text("INSERT INTO login_sessions (token_hash, user_id, created_at, expires_at) "
                          "VALUES (:h, :u, :t, :e)"), {"h": auth.token_hash(tokens[key]), "u": uid, "t": now, "e": exp})
    yesterday = datetime.combine(datetime.now(KST).date() - timedelta(days=1), datetime.min.time(), tzinfo=KST)
    rows = [  # n, conversation, turn, outcome, at, extra
        (1, "A", 0, "answered", yesterday + timedelta(hours=10), {"picked_key": "K1", "survey_name": "S1"}),
        (2, "A", 1, "refused", yesterday + timedelta(hours=11), {"reason_code": "no_period"}),
        (3, "B", 0, "answered", yesterday + timedelta(hours=12), {"picked_key": "K2", "survey_name": "S2"}),
        (4, "C", 0, "error", _now_kst(0) - timedelta(minutes=5), {"failed_stage": "search"}),
        (5, "D", 0, "aborted", _now_kst(0) - timedelta(minutes=3), {}),
    ]
    for n, c, turn, outcome, at, extra in rows:
        conn.execute(text(f"""INSERT INTO request_logs (request_id, conversation_id, turn_index, is_member, is_internal,
                input_method, question, question_len, outcome, has_numbers, has_chart, llm_calls, input_tokens,
                output_tokens, log_version, created_at, latency_ms {''.join(', ' + k for k in extra)})
            VALUES (:id, :c, :turn, false, false, 'direct', :q, 4, :o, true, false, 1, 10, 5, 1, :at, 1000
                    {''.join(', :' + k for k in extra)})"""),
                     {"id": RID[n], "c": c, "turn": turn, "q": f"질문 {n}", "o": outcome, "at": at, **extra})
    for n, value, cat in ((1, "down", "wrong_number"), (3, "down", None), (2, "up", "easy")):
        conn.execute(text("INSERT INTO feedback (id, request_id, value, category, created_at, updated_at) "
                          "VALUES (:id, :r, :v, :c, :t, :t)"), {"id": uuid.uuid4(), "r": RID[n], "v": value, "c": cat,
                                                                "t": now})
    return tokens


@pytest.fixture
def app_db(monkeypatch):
    """Service schema at head, admin schema by kosis_admin, seed rows; the app pointed at kosis_admin."""
    if not PG_URL:
        pytest.skip("TEST_DATABASE_URL is not set (PostgreSQL integration test)")
    from alembic import command
    from alembic.config import Config

    owner = create_engine(PG_URL)
    with owner.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS admin CASCADE"))
        conn.execute(text("DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kosis_admin') THEN "
                          "EXECUTE 'DROP OWNED BY kosis_admin'; DROP ROLE kosis_admin; END IF; END $$"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", PG_URL)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    with owner.begin() as conn:
        conn.execute(text("CREATE ROLE kosis_admin LOGIN PASSWORD 'test-only'"))
        conn.execute(text((ROOT / "admin" / "sql" / "grants.sql").read_text(encoding="utf-8")))
        tokens = _seed(conn)
    admin_url = make_url(PG_URL).set(username="kosis_admin", password="test-only").render_as_string(False)
    acfg = Config(str(ROOT / "admin" / "alembic.ini"))
    acfg.set_main_option("script_location", str(ROOT / "admin" / "migrations"))
    acfg.set_main_option("sqlalchemy.url", admin_url)
    command.upgrade(acfg, "head")

    monkeypatch.setenv("ADMIN_DATABASE_URL", admin_url)
    monkeypatch.setenv("ADMIN_PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("ADMIN_USER_IDS", f"{ME},{OTHER}")
    from admin.app import main

    main._engine = None
    queries._cache.clear()
    yield owner, tokens, admin_url
    if main._engine is not None:
        main._engine.dispose()
        main._engine = None
    command.downgrade(acfg, "base")
    with owner.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS admin CASCADE"))
        conn.execute(text("DROP OWNED BY kosis_admin"))
        conn.execute(text("DROP ROLE kosis_admin"))
    command.downgrade(cfg, "base")
    owner.dispose()


def _client():
    from fastapi.testclient import TestClient

    from admin.app.main import app

    return TestClient(app, base_url=ORIGIN, follow_redirects=False)


def _as(token: str) -> dict:
    return {"cookie": f"__Host-kosis_session={token}"}


@pytest.mark.postgres
def test_sign_in_cases(app_db):
    owner, tokens, _ = app_db
    c = _client()
    r = c.get("/admin/questions?outcome=error")
    assert r.status_code == 303 and r.headers["location"] == "/auth/kakao/login?next=/admin/questions%3Foutcome%3Derror"
    assert c.get("/admin", headers=_as(tokens["expired"])).status_code == 303
    assert c.get("/admin", headers=_as("not-a-session")).status_code == 303
    r = c.get("/admin", headers=_as(tokens["outsider"]))
    assert r.status_code == 404 and "KOSIS" not in r.text
    with owner.connect() as conn:
        assert conn.execute(text("SELECT user_id FROM admin.audit WHERE action = 'denied'")).scalar() == OUTSIDER
    r = c.get("/admin", headers=_as(tokens["me"]))
    assert r.status_code == 200 and "병석" in r.text
    assert r.headers["cache-control"] == "no-store" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert c.get("/admin/healthz").text == "ok"


@pytest.mark.postgres
def test_kosis_admin_reads_only_what_it_was_granted(app_db):
    _, _, admin_url = app_db
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM request_logs")).scalar() == 5
            assert conn.execute(text("SELECT count(nickname) FROM users")).scalar() == 3
        for sql in ("SELECT kakao_id FROM users", "SELECT count(*) FROM conversations", "SELECT count(*) FROM messages",
                    "SELECT created_at FROM login_sessions", "DELETE FROM request_logs",
                    "UPDATE feedback SET value = 'up'"):
            with engine.connect() as conn, pytest.raises(Exception, match="permission denied"):
                conn.execute(text(sql))
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_home_counts_today(app_db):
    _, tokens, _ = app_db
    r = _client().get("/admin", headers=_as(tokens["me"]))
    assert r.status_code == 200
    from admin.app import main

    with main.sessionmaker(main._engine)() as s:
        data = queries.home(s)
    assert (data["questions"], data["errors"], data["aborted"], data["new_down"]) == (2, 1, 1, 2)
    assert [t["request_id"] for t in data["problems"]] == [RID[5], RID[4]]


@pytest.mark.postgres
def test_metric_tabs_show_log_metrics_numbers(app_db):
    _, tokens, _ = app_db
    from kosis_store import log_metrics as lm

    from admin.app import main

    c = _client()
    day = datetime.now(KST).date() - timedelta(days=1)
    flt = MetricFilter(day, day)
    for tab in lm.TABS:
        r = c.get(f"/admin/metrics?tab={tab}&start={day}&end={day}", headers=_as(tokens["me"]))
        assert r.status_code == 200, tab
    with main.sessionmaker(main._engine)() as s:
        assert queries.metrics(s, "summary", flt)["rows"] == lm.tab(s, "summary", flt)
        assert lm.tab(s, "summary", flt)["question_count"] == [{"n": 3}]
    r = c.get(f"/admin/metrics?tab=summary&start={day}&end={day}", headers=_as(tokens["me"]))
    assert "질문 수 <b>3</b>" in r.text


@pytest.mark.postgres
def test_question_detail_is_audited(app_db):
    owner, tokens, _ = app_db
    c = _client()
    r = c.get(f"/admin/questions/{RID[1]}", headers=_as(tokens["me"]))
    assert r.status_code == 200 and "질문 1" in r.text and "질문 2" in r.text  # same conversation
    assert c.get("/admin/questions/nothex", headers=_as(tokens["me"])).status_code == 404
    with owner.connect() as conn:
        assert conn.execute(text("SELECT target_id FROM admin.audit WHERE action = 'view_question'")).scalar() == RID[1]
    r = c.get("/admin/questions?outcome=error", headers=_as(tokens["me"]))
    assert r.status_code == 200 and RID[4] in r.text and RID[1] not in r.text


@pytest.mark.postgres
def test_review_save_conflict_and_inbox(app_db):
    owner, tokens, _ = app_db
    c = _client()
    page = c.get("/admin/feedback", headers=_as(tokens["me"]))
    assert page.status_code == 200 and RID[1] in page.text and RID[3] in page.text
    form = {"status": "checking", "assignee": "", "note": "", "issue_key": "", "seen": "",
            "back": "/admin/feedback", "form_token": auth.form_token(tokens["me"])}
    url = f"/admin/feedback/{RID[1]}"
    assert c.post(url, data=form, headers=_as(tokens["me"])).status_code == 403  # no Origin
    bad = {**form, "form_token": "x"}
    assert c.post(url, data=bad, headers={**_as(tokens["me"]), "origin": ORIGIN}).status_code == 403
    r = c.post(url, data=form, headers={**_as(tokens["me"]), "origin": ORIGIN})
    assert r.status_code == 303 and "saved=1" in r.headers["location"]
    with owner.connect() as conn:
        row = conn.execute(text("SELECT status, assignee_id, updated_at FROM admin.feedback_reviews")).one()
    assert (row.status, row.assignee_id) == ("checking", ME)
    # another operator still holding the page read before the save
    other = {**form, "status": "no_issue", "note": "맞는 답", "form_token": auth.form_token(tokens["other"])}
    r = c.post(url, data=other, headers={**_as(tokens["other"]), "origin": ORIGIN})
    assert r.status_code == 303 and "error=" in r.headers["location"]
    # with the current value it goes through; a missing Linear number is refused
    r = c.post(url, data={**other, "seen": row.updated_at.isoformat()}, headers={**_as(tokens["other"]), "origin": ORIGIN})
    assert "saved=1" in r.headers["location"]
    with owner.connect() as conn:
        assert conn.execute(text("SELECT status FROM admin.feedback_reviews")).scalar() == "no_issue"
        assert conn.execute(text("SELECT count(*) FROM admin.audit WHERE action = 'update_review'")).scalar() == 2
    r = c.post(f"/admin/feedback/{RID[3]}", data={**form, "status": "fix_requested"},
               headers={**_as(tokens["me"]), "origin": ORIGIN})
    assert "error=" in r.headers["location"]
    new = c.get("/admin/feedback?status=new", headers=_as(tokens["me"])).text
    assert RID[3] in new and RID[1] not in new
    assert RID[1] in c.get("/admin/feedback?status=no_issue", headers=_as(tokens["me"])).text
    assert c.post(f"/admin/feedback/{RID[5]}", data=form, headers={**_as(tokens["me"]), "origin": ORIGIN}
                  ).status_code == 404  # no feedback on that answer


@pytest.mark.postgres
def test_cleanup_drops_orphans_and_old_notes(app_db):
    owner, _, admin_url = app_db
    from admin.app import cleanup

    now = datetime.now(timezone.utc)
    with owner.begin() as conn:
        conn.execute(text("""INSERT INTO admin.feedback_reviews VALUES
            (:a, 'no_issue', :gone, '오래된 메모', NULL, :me, :old, :old),
            ('ffffffffffffffffffffffffffffffff', 'checking', NULL, NULL, NULL, NULL, :now, :now)"""),
                     {"a": RID[1], "gone": uuid.uuid4(), "me": ME, "old": now - timedelta(days=91), "now": now})
        conn.execute(text("INSERT INTO admin.audit (at, action) VALUES (:old, 'denied')"),
                     {"old": now - timedelta(days=91)})
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            from sqlalchemy.orm import Session

            counts = cleanup.run(Session(bind=conn))
    finally:
        engine.dispose()
    assert counts == {"orphan_reviews": 1, "gone_assignees": 1, "gone_editors": 0, "old_notes": 1, "old_audit": 1}
    with owner.connect() as conn:
        assert conn.execute(text("SELECT assignee_id, note FROM admin.feedback_reviews")).one() == (None, None)


def test_database_down_is_503(monkeypatch):
    monkeypatch.setenv("ADMIN_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/none?connect_timeout=1")
    monkeypatch.setenv("ADMIN_PUBLIC_ORIGIN", ORIGIN)
    from admin.app import main

    main._engine = None
    try:
        r = _client().get("/admin", headers=_as("tok"))
    finally:
        main._engine = None
    assert r.status_code == 503 and "데이터베이스" in r.text
