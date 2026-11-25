"""Kakao login (T3-54, docs/service/api.md 6): the OAuth calls, the cookies, and who a request comes from.

- Kakao's member number and nickname are read once and the Kakao token is dropped. Nothing of Kakao's answers, the
  authorization code, our token or the keys is logged (QueryFilter also cuts ?code=&state= off the access log).
- The login cookie holds a random token; the database holds its hash (kosis_store.service_store).
- `state` sits in a short-lived cookie with the page to return to, and is checked and cleared by the callback.
- KAKAO_REDIRECT_URI is the address the browser sees (the frontend, which passes /auth/* on). It is fixed by settings and
  never made from request headers; its origin is the only one that may send cookie-authenticated writes.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import Request
from fastapi.responses import RedirectResponse, Response

from kosis_store.service_store import LOGIN_TTL

# Settings (.env.example) and their defaults, read when used: .env is loaded after this module is imported.
DEFAULTS = {
    "KAKAO_AUTHORIZE_URL": "https://kauth.kakao.com/oauth/authorize",
    "KAKAO_TOKEN_URL": "https://kauth.kakao.com/oauth/token",
    "KAKAO_ME_URL": "https://kapi.kakao.com/v2/user/me",
    "KAKAO_REDIRECT_URI": "http://localhost:3000/auth/kakao/callback",
    "KAKAO_TIMEOUT_SECONDS": "5",  # each call to Kakao
    "KAKAO_STATE_TTL_SECONDS": "600",  # to finish Kakao's screens
}
STATE_COOKIE = "kosis_login_state"
NICKNAME_MAX = 50  # users.nickname holds 100


def setting(name: str) -> str:
    return os.environ.get(name, "").strip() or DEFAULTS[name]


def seconds(name: str) -> int:
    """A setting that must be a whole number of seconds, 1 or more."""
    try:
        value = int(setting(name))
    except ValueError:
        value = 0
    if value < 1:
        raise ValueError(f"{name} must be a whole number of seconds, 1 or more: {setting(name)!r}")
    return value


def check_settings() -> None:
    """Called at startup, so a wrong value stops the server instead of every login."""
    seconds("KAKAO_TIMEOUT_SECONDS")
    seconds("KAKAO_STATE_TTL_SECONDS")


class KakaoError(Exception):
    """Login could not be completed at Kakao. The message never holds Kakao's response."""


@dataclass(frozen=True)
class KakaoUser:
    kakao_id: str
    nickname: str


def redirect_uri() -> str:
    return setting("KAKAO_REDIRECT_URI")


def secure() -> bool:
    return urlsplit(redirect_uri()).scheme == "https"


def cookie_name() -> str:
    return "__Host-kosis_session" if secure() else "kosis_session"


def allowed_origin() -> str:
    u = urlsplit(redirect_uri())
    return f"{u.scheme}://{u.netloc}"


class Kakao:
    """The two Kakao calls after the browser comes back with a code."""

    def __init__(self, client: httpx.Client | None = None):
        self.client_id = os.environ.get("KAKAO_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("KAKAO_CLIENT_SECRET", "").strip()
        self._client = client

    @property
    def ready(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authorize_url(self, state: str) -> str:
        return setting("KAKAO_AUTHORIZE_URL") + "?" + urlencode({
            "response_type": "code", "client_id": self.client_id, "redirect_uri": redirect_uri(),
            "scope": "profile_nickname", "state": state})

    def user(self, code: str) -> KakaoUser:
        try:
            client = self._client or httpx.Client(timeout=seconds("KAKAO_TIMEOUT_SECONDS"))
            try:
                res = client.post(setting("KAKAO_TOKEN_URL"), data={
                    "grant_type": "authorization_code", "client_id": self.client_id,
                    "client_secret": self.client_secret, "redirect_uri": redirect_uri(), "code": code})
                res.raise_for_status()
                me = client.get(setting("KAKAO_ME_URL"), headers={"Authorization": f"Bearer {res.json()['access_token']}"})
                me.raise_for_status()
                body = me.json()
            finally:
                if self._client is None:
                    client.close()
            kakao_id = str(int(body["id"]))
            profile = (body.get("kakao_account") or {}).get("profile") or {}
            nickname = str(profile.get("nickname") or "").replace("\x00", "").strip()[:NICKNAME_MAX]
        except httpx.HTTPStatusError as e:
            raise KakaoError(f"kakao answered {e.response.status_code}") from None
        except httpx.HTTPError as e:
            raise KakaoError(f"kakao call failed: {type(e).__name__}") from None
        except (KeyError, TypeError, ValueError, AttributeError):
            raise KakaoError("kakao sent an unexpected answer") from None
        return KakaoUser(kakao_id, nickname or "회원")


def get_kakao() -> Kakao:
    """FastAPI dependency (tests override it)."""
    return Kakao()


# ---------------------------------------------------------------- cookies and redirects

_BAD_PATH = re.compile(r"[\\\x00-\x20\x7f]")


def safe_return(path: str | None) -> str:
    """A path of our own pages to go back to; "/" for anything that could leave the site."""
    if not path or len(path) > 500 or not path.startswith("/") or path.startswith("//") or _BAD_PATH.search(path):
        return "/"
    u = urlsplit(path)
    if u.scheme or u.netloc or u.path.startswith("//") or re.match(r"^/(auth|api)(/|$)", u.path):
        return "/"
    return u.path + (f"?{u.query}" if u.query else "")


def failed_return(path: str) -> str:
    """The login screen with its failure notice; `next` keeps the page for another try."""
    return "/login?" + urlencode({"error": "kakao", **({"next": path} if path != "/" else {})})


def redirect(location: str) -> RedirectResponse:
    return RedirectResponse(location, status_code=302, headers={"Cache-Control": "no-store",
                                                                "Referrer-Policy": "no-referrer"})


def set_state(res: Response, state: str, back: str) -> None:
    value = base64.urlsafe_b64encode(json.dumps([state, back]).encode()).decode()
    res.set_cookie(STATE_COOKIE, value, max_age=seconds("KAKAO_STATE_TTL_SECONDS"), path="/auth", httponly=True,
                   samesite="lax", secure=secure())


def clear_state(res: Response) -> None:
    res.delete_cookie(STATE_COOKIE, path="/auth", httponly=True, samesite="lax", secure=secure())


def read_state(request: Request) -> tuple[str, str]:
    """(state, page to return to) of the login this browser started; ("", "/") when there is none."""
    try:
        state, back = json.loads(base64.urlsafe_b64decode(request.cookies.get(STATE_COOKIE, "").encode()))
        return (state, safe_return(back)) if isinstance(state, str) and isinstance(back, str) else ("", "/")
    except (ValueError, TypeError):
        return "", "/"


def same_state(expected: str, got: str | None) -> bool:
    return bool(expected and got) and hmac.compare_digest(expected.encode(), got.encode())


def new_state() -> str:
    return secrets.token_urlsafe(24)


def set_login(res: Response, token: str) -> None:
    res.set_cookie(cookie_name(), token, max_age=int(LOGIN_TTL.total_seconds()), path="/", httponly=True,
                   samesite="lax", secure=secure())


def clear_login(res: Response) -> None:
    res.delete_cookie(cookie_name(), path="/", httponly=True, samesite="lax", secure=secure())


def login_token(request: Request) -> str | None:
    return request.cookies.get(cookie_name()) or None


def foreign_origin(request: Request) -> bool:
    """A browser request from another site (a missing Origin is not a cross-site browser write; SameSite=Lax is the
    other guard)."""
    origin = request.headers.get("origin")
    return origin is not None and origin != allowed_origin()


class QueryFilter(logging.Filter):
    """Cuts the query off /auth/* lines of uvicorn's access log: it holds the authorization code and state."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and args[2].startswith("/auth/"):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def install_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, QueryFilter) for f in access.filters):
        access.addFilter(QueryFilter())
