"""T3-54: Kakao login and member conversations (docs/service/api.md 4, 6). Kakao's servers are an httpx.MockTransport."""

from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app import auth
from backend.app import main as api
from backend.app.main import app, get_checkpointer
from kosis_store import cleanup
from kosis_store.db import session_scope
from kosis_store.models import Conversation, LoginSession, Message, User
from kosis_store.service_models import ChatRequestRow, ChatSession, Feedback
from kosis_store.service_store import BUSY_LIMIT, LOGIN_TTL, MEMBER_RETENTION, BadCursor, Busy, ServiceStore
from tests.test_service_api import Service

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)
ORIGIN = {"Origin": "http://localhost:3000"}


class FakeKakao:
    def __init__(self):
        self.id, self.nickname, self.fail, self.calls = 1001, "민수", None, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail == "down":
            raise httpx.ConnectError("no route")
        if self.fail == "garbage":
            return httpx.Response(200, json=[])
        if self.fail:
            return httpx.Response(400, json={"error_description": "SECRET-DETAIL"})
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "kakao-token"})
        assert request.headers["authorization"] == "Bearer kakao-token"
        return httpx.Response(200, json={"id": self.id, "kakao_account": {"profile": {"nickname": self.nickname}}})


class Members(Service):
    def __init__(self, store=None):
        super().__init__(store=store)
        self.kakao = FakeKakao()
        client = httpx.Client(transport=httpx.MockTransport(self.kakao))
        app.dependency_overrides[auth.get_kakao] = lambda: auth.Kakao(client)

    def browser(self) -> TestClient:
        return TestClient(app, base_url="http://localhost:3000", follow_redirects=False)

    def login(self, http=None, kakao_id=1001, back="/chat"):
        http = http or self.browser()
        self.kakao.id = kakao_id
        start = http.get("/auth/kakao/login", params={"next": back})
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        res = http.get("/auth/kakao/callback", params={"code": "CODE", "state": state})
        assert res.status_code == 302 and res.headers["location"] == back
        return http

    def ask_as(self, http, question="2030년 출생아 수", thread_id=None, headers=None):
        self.http = http
        res = http.post("/chat/stream", json={"question": question, "thread_id": thread_id}, headers=headers or ORIGIN)
        if res.status_code != 200:
            return res.status_code, res.json()
        import json
        ev = [l.removeprefix("event: ") for l in res.text.splitlines() if l.startswith("event: ")]
        data = [json.loads(l.removeprefix("data: ")) for l in res.text.splitlines() if l.startswith("data: ")]
        return 200, dict(zip(ev, data))


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("KAKAO_CLIENT_ID", "cid")
    monkeypatch.setenv("KAKAO_CLIENT_SECRET", "csecret")
    for name in auth.DEFAULTS:
        monkeypatch.delenv(name, raising=False)
    s = Members()
    yield s
    app.dependency_overrides.clear()


def _count(svc, model):
    with session_scope(svc.store.engine) as s:
        return s.scalar(select(func.count()).select_from(model))


# ---------------------------------------------------------------- login

def test_login_creates_one_member_and_logs_in_again_as_the_same(svc):
    first = svc.login()
    me = first.get("/auth/me").json()
    assert me["nickname"] == "민수"
    svc.kakao.nickname = "민수2"
    again = svc.login().get("/auth/me").json()
    assert again == {**me, "nickname": "민수2"} and _count(svc, User) == 1
    sent = parse_qs(svc.kakao.calls[0].content.decode())
    assert sent["code"] == ["CODE"] and sent["redirect_uri"] == ["http://localhost:3000/auth/kakao/callback"]


def test_the_authorize_request_asks_for_the_nickname_only(svc):
    q = parse_qs(urlsplit(svc.browser().get("/auth/kakao/login").headers["location"]).query)
    assert q["scope"] == ["profile_nickname"] and q["response_type"] == ["code"] and len(q["state"][0]) >= 32


def test_cookie_flags_and_the_token_is_stored_hashed(svc, monkeypatch):
    http = svc.login()
    token = http.cookies["kosis_session"]
    with session_scope(svc.store.engine) as s:
        assert s.scalar(select(LoginSession.token_hash)) != token
    res = auth.redirect("/")
    auth.set_login(res, "t")
    header = res.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=lax" in header and "max-age=1209600" in header and "secure" not in header
    monkeypatch.setenv("KAKAO_REDIRECT_URI", "https://kosis.example/auth/kakao/callback")
    res = auth.redirect("/")
    auth.set_login(res, "t")
    header = res.headers["set-cookie"]
    assert header.startswith("__Host-kosis_session=") and "Secure" in header and "Path=/" in header
    assert "domain" not in header.lower()


@pytest.mark.parametrize("params", [{"code": "C", "state": "wrong"}, {"code": "C"}, {"state": "x"},
                                    {"error": "access_denied", "error_description": "User denied access"}])
def test_forged_or_cancelled_callbacks_do_not_log_in(svc, params):
    http = svc.browser()
    http.get("/auth/kakao/login", params={"next": "/chat"})
    res = http.get("/auth/kakao/callback", params=params)
    assert res.status_code == 302 and res.headers["location"] == "/login?error=kakao&next=%2Fchat"
    assert http.get("/auth/me").status_code == 401 and not svc.kakao.calls


def test_a_cancel_with_the_right_state_does_not_log_in(svc):
    http = svc.browser()
    start = http.get("/auth/kakao/login")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    res = http.get("/auth/kakao/callback", params={"error": "access_denied", "state": state, "code": "C"})
    assert res.headers["location"] == "/login?error=kakao" and not svc.kakao.calls


def test_the_state_works_once(svc):
    http = svc.browser()
    state = parse_qs(urlsplit(http.get("/auth/kakao/login").headers["location"]).query)["state"][0]
    assert http.get("/auth/kakao/callback", params={"code": "C", "state": state}).headers["location"] == "/"
    assert "/login?error=kakao" in http.get("/auth/kakao/callback", params={"code": "C", "state": state}).headers["location"]


@pytest.mark.parametrize("bad", ["https://evil.example", "//evil.example", "/\\evil.example", "/\t/evil.example",
                                 "javascript:alert(1)", "", "/auth/logout", "/api/x", "evil", "/a\r\nSet-Cookie:x", None])
def test_return_paths_stay_on_our_pages(bad):
    assert auth.safe_return(bad) == "/"


def test_return_paths_that_are_ours_are_kept():
    assert auth.safe_return("/chat/abc?x=1") == "/chat/abc?x=1"
    assert auth.safe_return("/%2f%2fevil.example") == "/%2f%2fevil.example"  # still a path of this site
    assert auth.failed_return("/a?x=1&error=y") == "/login?error=kakao&next=%2Fa%3Fx%3D1%26error%3Dy"


@pytest.mark.parametrize("fail", ["down", "garbage", "refused"])
def test_a_kakao_failure_leaves_a_working_guest(svc, fail, caplog):
    svc.kakao.fail = fail
    http = svc.browser()
    state = parse_qs(urlsplit(http.get("/auth/kakao/login").headers["location"]).query)["state"][0]
    with caplog.at_level(logging.DEBUG):
        res = http.get("/auth/kakao/callback", params={"code": "THE-CODE", "state": state})
    assert res.headers["location"] == "/login?error=kakao"
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("kosis"))
    assert "SECRET-DETAIL" not in ours and "THE-CODE" not in ours and "csecret" not in ours and ours
    assert svc.ask_as(http)[0] == 200 and _count(svc, User) == 0


def test_login_without_keys_returns_to_the_page(svc, monkeypatch):
    monkeypatch.delenv("KAKAO_CLIENT_SECRET")
    app.dependency_overrides.pop(auth.get_kakao)
    assert svc.browser().get("/auth/kakao/login").headers["location"] == "/login?error=kakao"


def test_kakao_addresses_and_limits_come_from_the_environment(svc, monkeypatch):
    monkeypatch.setenv("KAKAO_AUTHORIZE_URL", "https://fake.example/authorize")
    monkeypatch.setenv("KAKAO_TOKEN_URL", "https://fake.example/oauth/token")
    monkeypatch.setenv("KAKAO_ME_URL", "https://fake.example/me")
    monkeypatch.setenv("KAKAO_STATE_TTL_SECONDS", "60")
    monkeypatch.setenv("KAKAO_TIMEOUT_SECONDS", " ")  # empty = the default
    start = svc.browser().get("/auth/kakao/login")
    assert start.headers["location"].startswith("https://fake.example/authorize?")
    assert "max-age=60;" in start.headers["set-cookie"].lower()
    svc.login()
    assert [str(c.url) for c in svc.kakao.calls] == ["https://fake.example/oauth/token", "https://fake.example/me"]
    assert auth.seconds("KAKAO_TIMEOUT_SECONDS") == 5


@pytest.mark.parametrize("name", ["KAKAO_TIMEOUT_SECONDS", "KAKAO_STATE_TTL_SECONDS"])
@pytest.mark.parametrize("bad", ["abc", "0", "-1", "1.5"])
def test_a_wrong_limit_stops_the_startup_and_is_named(monkeypatch, name, bad):
    monkeypatch.setenv(name, bad)
    with pytest.raises(ValueError, match=name), TestClient(app):
        pass


def test_login_ends_after_14_days(svc):
    token = svc.store.login("1", "a", NOW)
    assert svc.store.member(token, NOW + LOGIN_TTL - timedelta(seconds=1)).nickname == "a"
    assert svc.store.member(token, NOW + LOGIN_TTL) is None and svc.store.member("nope", NOW) is None


def test_a_new_login_replaces_this_browsers_old_one(svc):
    http = svc.login()
    old = http.cookies["kosis_session"]
    svc.login(http)
    assert http.cookies["kosis_session"] != old and svc.store.member(old, datetime.now(timezone.utc)) is None


def test_logout_ends_this_device_only(svc):
    a, b = svc.login(), svc.login()
    assert a.post("/auth/logout", headers=ORIGIN).status_code == 204
    assert a.get("/auth/me").status_code == 401 and b.get("/auth/me").status_code == 200
    assert a.post("/auth/logout", headers=ORIGIN).status_code == 204  # already out


def test_logout_clears_the_cookie_even_when_the_store_fails(svc, monkeypatch):
    http = svc.login()
    monkeypatch.setattr(svc.store, "logout", lambda t: 1 / 0)
    res = http.post("/auth/logout", headers=ORIGIN)
    assert res.status_code == 503 and "kosis_session" not in http.cookies


def test_other_sites_cannot_send_member_writes(svc):
    http, evil = svc.login(), {"Origin": "https://evil.example"}
    thread = svc.ask_as(http)[1]["meta"]["thread_id"]
    assert http.post("/auth/logout", headers=evil).status_code == 403
    assert http.delete("/auth/me", headers=evil).status_code == 403
    assert http.delete(f"/chat/{thread}", headers=evil).status_code == 403
    assert svc.ask_as(http, headers=evil)[0] == 403
    assert http.get(f"/chat/{thread}").status_code == 200


def test_the_access_log_has_no_code():
    record = logging.LogRecord("uvicorn.access", 20, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("1.1.1.1", "GET", "/auth/kakao/callback?code=SECRET&state=S", "1.1", 302), None)
    assert auth.QueryFilter().filter(record) and "SECRET" not in record.getMessage()
    assert "/auth/kakao/callback" in record.getMessage()


# ---------------------------------------------------------------- member conversations

def test_turns_are_saved_listed_restored_and_continued(svc):
    http = svc.login()
    thread = svc.ask_as(http)[1]["meta"]["thread_id"]
    assert str(uuid.UUID(thread)) == thread
    status, ev = svc.ask_as(http, "2031년은?", thread.replace("-", "").upper())  # any UUID spelling is the same one
    assert status == 200 and ev["meta"]["thread_id"] == thread
    items = http.get("/chat").json()["items"]
    assert [i["id"] for i in items] == [thread] and items[0]["title"] == "2030년 출생아 수" and items[0]["questions"] == 2
    detail = svc.login().get(f"/chat/{thread}").json()  # another device
    assert [(t["turn"], t["question"]) for t in detail["turns"]] == [(1, "2030년 출생아 수"), (2, "2031년은?")]
    assert detail["turns"][1]["result"] == ev["result"]


def test_guests_get_401_and_others_get_404(svc):
    mine, other, guest = svc.login(), svc.login(kakao_id=2002), svc.browser()
    thread = svc.ask_as(mine)[1]["meta"]["thread_id"]
    assert guest.get("/chat").status_code == guest.get(f"/chat/{thread}").status_code == 401
    assert guest.get("/chat").json()["error"]["code"] == "login_required"
    assert other.get("/chat").json()["items"] == [] and other.get(f"/chat/{thread}").status_code == 404
    assert other.delete(f"/chat/{thread}", headers=ORIGIN).status_code == 404
    assert guest.delete(f"/chat/{thread}").status_code == 404
    for bad in ("x", "a%00b", "9" * 200):
        assert mine.get(f"/chat/{bad}").status_code == 404
    assert mine.get(f"/chat/{thread}").status_code == 200


def test_a_member_conversation_cannot_be_taken_over(svc):
    thread = svc.ask_as(svc.login())[1]["meta"]["thread_id"]
    for who in (svc.browser(), svc.login(kakao_id=2002)):
        assert svc.ask_as(who, "그 표 다시", thread)[1]["meta"]["thread_id"] != thread


def test_a_member_does_not_take_a_guest_or_unknown_conversation(svc):
    guest = svc.ask_as(svc.browser())[1]["meta"]["thread_id"]
    member = svc.login()
    for sent in (guest, str(uuid.uuid4()), "c0ffee"):
        assert svc.ask_as(member, "q", sent)[1]["meta"]["thread_id"] != sent


def test_a_store_failure_never_continues_the_thread_that_was_sent(svc, monkeypatch):
    http = svc.login()
    thread = svc.ask_as(http)[1]["meta"]["thread_id"]
    for name in ("member", "open_thread", "open_member_thread"):
        monkeypatch.setattr(svc.store, name, lambda *a, **k: 1 / 0)
    status, ev = svc.ask_as(http, "q", thread)
    assert status == 200 and ev["meta"]["thread_id"] != thread and "result" in ev
    assert http.get("/chat").status_code == 503 and http.get("/auth/me").status_code == 503
    assert http.delete(f"/chat/{thread}", headers=ORIGIN).status_code == 503


def test_an_answer_goes_out_when_saving_fails(svc, monkeypatch):
    http = svc.login()
    monkeypatch.setattr(svc.store, "save_turn", lambda *a, **k: 1 / 0)
    assert "result" in svc.ask_as(http)[1]


def test_a_second_question_while_one_runs_is_refused(svc):
    http = svc.login()
    thread = svc.ask_as(http)[1]["meta"]["thread_id"]
    with session_scope(svc.store.engine) as s:
        row = s.get(ChatSession, thread)
        assert row.busy_until is None  # the finished turn let go
        row.busy_until = datetime.now(timezone.utc) + BUSY_LIMIT
    status, body = svc.ask_as(http, "2031년은?", thread)
    assert status == 409 and body["error"]["code"] == "conversation_busy"
    with session_scope(svc.store.engine) as s:
        s.get(ChatSession, thread).busy_until = datetime.now(timezone.utc) - timedelta(seconds=1)  # a crashed turn
    assert svc.ask_as(http, "2031년은?", thread)[0] == 200


def test_a_stopped_stream_saves_nothing_and_frees_the_conversation(svc):
    user = svc.store.member(svc.store.login("1", "a", NOW), NOW)
    opened = svc.store.open_member_thread(None, user.id, NOW)
    graph = app.dependency_overrides[api.get_graph]()
    tracer = app.dependency_overrides[api.telemetry.get_tracer]()
    stream = api.chat_stream(api.ChatRequest(question="q"), graph, tracer, None, svc.store,
                             api.Turn(opened, None, user))
    assert next(stream).event == "meta"
    stream.close()
    assert _count(svc, Message) == 0 and svc.store.open_member_thread(opened.thread_id, user.id, NOW).new is False


def test_a_conversation_deleted_while_answering_is_not_saved(svc):
    user = svc.store.member(svc.store.login("1", "a", NOW), NOW)
    opened = svc.store.open_member_thread(None, user.id, NOW)
    graph = app.dependency_overrides[api.get_graph]()
    tracer = app.dependency_overrides[api.telemetry.get_tracer]()
    stream = api.chat_stream(api.ChatRequest(question="q"), graph, tracer, None, svc.store,
                             api.Turn(opened, None, user))
    next(stream)
    svc.store.delete_threads([opened.thread_id])
    assert [e.event for e in stream][-1] == "result"
    assert _count(svc, Conversation) == 0
    assert graph.get_state({"configurable": {"thread_id": opened.thread_id}}).values == {}


def test_delete_removes_turns_and_saved_state(svc):
    http = svc.login()
    thread = svc.ask_as(http)[1]["meta"]["thread_id"]
    assert http.delete(f"/chat/{thread}", headers=ORIGIN).status_code == 204
    assert http.get(f"/chat/{thread}").status_code == 404 and http.get("/chat").json()["items"] == []
    assert _count(svc, Message) == _count(svc, ChatSession) == _count(svc, ChatRequestRow) == 0
    assert svc.checkpointer.get_tuple({"configurable": {"thread_id": thread}}) is None


class BrokenSaver:
    def delete_thread(self, thread_id):
        raise RuntimeError("db down")


def test_a_failed_delete_keeps_the_rows_so_it_can_be_tried_again(svc):
    http, guest = svc.login(), svc.browser()
    thread = svc.ask_as(http)[1]["meta"]["thread_id"]
    sid = {"X-Session-Id": str(uuid.uuid4())}
    gthread = svc.ask_as(guest, headers=sid)[1]["meta"]["thread_id"]
    app.dependency_overrides[get_checkpointer] = lambda: BrokenSaver()
    assert http.delete(f"/chat/{thread}", headers=ORIGIN).status_code == 503
    assert guest.delete(f"/chat/{gthread}", headers=sid).status_code == 503
    app.dependency_overrides[get_checkpointer] = lambda: svc.checkpointer
    assert http.delete(f"/chat/{thread}", headers=ORIGIN).status_code == 204
    assert guest.delete(f"/chat/{gthread}", headers=sid).status_code == 204


def test_withdrawal_removes_everything_on_every_device(svc):
    a, b = svc.login(), svc.login()
    thread = svc.ask_as(a)[1]["meta"]["thread_id"]
    keep = svc.login(kakao_id=2002)
    svc.ask_as(keep)
    assert a.delete("/auth/me", headers=ORIGIN).status_code == 204
    assert b.get("/auth/me").status_code == 401
    assert _count(svc, User) == _count(svc, Conversation) == _count(svc, ChatSession) == _count(svc, LoginSession) == 1
    assert svc.checkpointer.get_tuple({"configurable": {"thread_id": thread}}) is None
    assert len(keep.get("/chat").json()["items"]) == 1
    assert svc.login().get("/chat").json()["items"] == []  # joining again starts empty


def test_feedback_belongs_to_the_member(svc):
    http = svc.login()
    sid = {"X-Session-Id": str(uuid.uuid4()), **ORIGIN}
    rid = svc.ask_as(http, headers=sid)[1]["meta"]["trace_id"]
    body = {"request_id": rid, "value": "up"}
    assert svc.browser().post("/feedback", json=body, headers=sid).status_code == 404  # the same session, signed out
    assert svc.login(kakao_id=2002).post("/feedback", json=body, headers=sid).status_code == 404
    assert svc.login().post("/feedback", json=body, headers=ORIGIN).status_code == 200  # another device


def test_the_list_pages_without_gaps_when_times_are_equal(svc):
    user = svc.store.member(svc.store.login("1", "a", NOW), NOW)
    ids = []
    for _ in range(7):
        t = svc.store.open_member_thread(None, user.id, NOW).thread_id
        svc.store.save_turn(t, user.id, "q", "a", {}, NOW)
        ids.append(t)
    seen, cursor = [], None
    while True:
        rows, cursor = svc.store.conversations(user.id, cursor, limit=3)
        seen += [str(c.id) for c in rows]
        if cursor is None:
            break
    assert sorted(seen) == sorted(ids) and len(seen) == 7
    for bad in ("x", "%%%", "bm90fGE=", "é"):
        with pytest.raises(BadCursor):
            svc.store.conversations(user.id, bad)
    assert svc.login().get("/chat", params={"cursor": "x"}).status_code == 422


def test_cleanup_keeps_members_past_the_hour_and_drops_them_after_90_days(svc):
    user = svc.store.member(svc.store.login("1", "a", NOW), NOW)
    thread = svc.store.open_member_thread(None, user.id, NOW).thread_id
    svc.store.save_turn(thread, user.id, "q", "a", {}, NOW)
    guest = svc.store.open_thread(None, "s", NOW).thread_id
    dropped = []
    saver = type("S", (), {"delete_thread": lambda self, t: dropped.append(t)})()
    # the raw-text purges need the log tables: only the conversation part is under test here
    later = NOW + timedelta(hours=2)
    assert svc.store.expired_threads(later) == [guest] and svc.store.old_member_threads(later) == []
    cleanup.drop_threads(svc.store, svc.store.expired_threads(later), saver)
    assert _count(svc, Conversation) == 1
    later = NOW + MEMBER_RETENTION + timedelta(seconds=1)
    cleanup.drop_threads(svc.store, svc.store.old_member_threads(later), saver)
    assert dropped == [guest, thread] and _count(svc, Conversation) == _count(svc, Message) == 0
    assert svc.store.purge_logins(later) == 1 and _count(svc, User) == 1


# ---------------------------------------------------------------- settings (Change 1)

def test_settings_are_kept_on_the_account(svc):
    http = svc.login()
    assert http.get("/auth/me").json() == {**http.get("/auth/me").json(), "display_name": None, "color": "blue",
                                           "save_history": True, "retention_days": 90}
    res = http.patch("/auth/me", json={"display_name": " 통계왕 ", "color": "rose"}, headers=ORIGIN)
    assert res.status_code == 200 and (res.json()["display_name"], res.json()["color"]) == ("통계왕", "rose")
    other = svc.login()  # another device; logging in again keeps them
    assert other.get("/auth/me").json() == res.json()
    assert other.patch("/auth/me", json={"display_name": None}, headers=ORIGIN).json()["display_name"] is None
    assert other.get("/auth/me").json()["color"] == "rose"


@pytest.mark.parametrize("bad", [{"display_name": " "}, {"display_name": "가" * 21}, {"display_name": "a\x00b"},
                                 {"color": "purple"}, {"color": None}, {"save_history": None}, {"retention_days": 365},
                                 {"nickname": "x"}, {"kakao_id": "1"}])
def test_bad_settings_are_refused(svc, bad):
    http = svc.login()
    before = http.get("/auth/me").json()
    assert http.patch("/auth/me", json=bad, headers=ORIGIN).status_code == 422 and http.get("/auth/me").json() == before


def test_settings_need_a_login_from_our_pages(svc):
    assert svc.browser().patch("/auth/me", json={"color": "rose"}).status_code == 401
    assert svc.login().patch("/auth/me", json={"color": "rose"}, headers={"Origin": "https://evil"}).status_code == 403


def test_history_off_keeps_new_conversations_unsaved_and_saved_ones_going(svc):
    http = svc.login()
    old = svc.ask_as(http)[1]["meta"]["thread_id"]
    http.patch("/auth/me", json={"save_history": False}, headers=ORIGIN)
    new = svc.ask_as(http, "2031년 출생아 수")[1]["meta"]["thread_id"]
    assert svc.ask_as(http, "그 전해는?", new)[1]["meta"]["thread_id"] == new  # follow-ups still work
    svc.ask_as(http, "2032년은?", old)
    assert [(i["id"], i["questions"]) for i in http.get("/chat").json()["items"]] == [(old, 2)]
    assert http.get(f"/chat/{new}").status_code == 404


def test_an_unsaved_conversation_ends_after_an_hour(svc):
    user = svc.store.member(svc.store.login("1", "a", NOW), NOW)
    svc.store.account(user.id, {"save_history": False})
    thread = svc.store.open_member_thread(None, user.id, NOW).thread_id
    assert not svc.store.save_turn(thread, user.id, "q", "a", {}, NOW)
    svc.store.end_turn(thread)
    later = NOW + timedelta(hours=2)
    again = svc.store.open_member_thread(thread, user.id, later)
    assert again.expired and again.thread_id != thread and svc.store.expired_threads(later) == [thread]


def test_thirty_days_is_the_members_own_limit(svc):
    threads = {}
    for kakao_id, days in (("1", 30), ("2", 90)):
        user = svc.store.member(svc.store.login(kakao_id, "a", NOW), NOW)
        svc.store.account(user.id, {"retention_days": days})
        threads[days] = svc.store.open_member_thread(None, user.id, NOW).thread_id
        svc.store.save_turn(threads[days], user.id, "q", "a", {}, NOW)
    assert svc.store.old_member_threads(NOW + timedelta(days=31)) == [threads[30]]


def test_withdrawal_removes_feedback_of_deleted_conversations_too(svc):
    http = svc.login()
    meta = svc.ask_as(http)[1]["meta"]
    assert http.post("/feedback", json={"request_id": meta["trace_id"], "value": "up"}, headers=ORIGIN).status_code == 200
    http.delete(f"/chat/{meta['thread_id']}", headers=ORIGIN)
    assert _count(svc, Feedback) == 1
    http.delete("/auth/me", headers=ORIGIN)
    assert _count(svc, Feedback) == 0


# ---------------------------------------------------------------- PostgreSQL

@pytest.mark.postgres
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL", "").strip(), reason="TEST_DATABASE_URL not set")
def test_postgres_one_question_at_a_time_and_cascade():
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from kosis_store.db import make_engine

    url, root = os.environ["TEST_DATABASE_URL"].strip(), Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    store = ServiceStore(make_engine(url))
    try:
        tokens: list[str] = []
        both = [threading.Thread(target=lambda: tokens.append(store.login("7", "동시", NOW))) for _ in range(4)]
        [t.start() for t in both], [t.join() for t in both]
        members = {store.member(t, NOW).id for t in tokens}
        assert len(tokens) == 4 and len(members) == 1  # first logins at the same time make one member
        uid = members.pop()
        thread = store.open_member_thread(None, uid, NOW).thread_id
        store.end_turn(thread)
        got: list[str] = []

        def race():
            try:
                store.open_member_thread(thread, uid, NOW)
                got.append("ok")
            except Busy:
                got.append("busy")

        racers = [threading.Thread(target=race) for _ in range(5)]
        [t.start() for t in racers], [t.join() for t in racers]
        assert sorted(got) == ["busy"] * 4 + ["ok"]
        savers = [threading.Thread(target=lambda: store.save_turn(thread, uid, "q", "a", {}, NOW)) for _ in range(4)]
        [t.start() for t in savers], [t.join() for t in savers]
        conv, messages = store.conversation(thread, uid)
        assert sorted(m.turn for m in messages if m.role == "user") == [1, 2, 3, 4]
        store.record_request("r" * 32, thread, None, NOW, uid)
        store.delete_member(uid)
        with session_scope(store.engine) as s:
            for model in (User, LoginSession, Conversation, Message, ChatSession, ChatRequestRow):
                assert s.scalar(select(func.count()).select_from(model)) == 0
        command.downgrade(cfg, "0006_review_indexes")
        command.upgrade(cfg, "head")
    finally:
        store.close()
        command.downgrade(cfg, "base")
