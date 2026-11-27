"""Who may use the operator app (kosis-infra/12 3): the service's Kakao login cookie + an allowlist.

The cookie's token is looked up the way the backend does it (SHA-256 in login_sessions.token_hash, not expired), so a
member signed in to the service is signed in here too. Only token_hash, user_id, expires_at of login_sessions and
id, display_name, nickname of users are read (the kosis_admin grants allow no more).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from admin.app import config

log = logging.getLogger("admin.auth")

_SESSION = text("""
    SELECT s.user_id, coalesce(u.display_name, u.nickname) AS name
    FROM login_sessions s JOIN users u ON u.id = s.user_id
    WHERE s.token_hash = :h AND s.expires_at > :now""")


class NotSignedIn(Exception):
    """No login cookie, or its session is gone or expired: send the browser to the Kakao login."""


class NotAllowed(Exception):
    """Signed in but not on ADMIN_USER_IDS: answered 404 so the screen's existence is not told."""


@dataclass(frozen=True)
class Operator:
    user_id: uuid.UUID
    name: str
    token: str  # the cookie value; only used to make the form token

    @property
    def form_token(self) -> str:
        return form_token(self.token)


def cookie_name() -> str:
    """The backend's cookie (backend/app/auth.cookie_name): __Host- only over HTTPS."""
    return "__Host-kosis_session" if config.secure() else "kosis_session"


def token_hash(token: str) -> str:
    """Same as kosis_store.service_store.token_hash (a test keeps them equal)."""
    return hashlib.sha256(token.encode()).hexdigest()


def form_token(token: str) -> str:
    """The anti-forgery value of the forms: a keyed hash of the login cookie, which another site cannot read."""
    return hmac.new(token.encode(), b"kosis-admin-form", hashlib.sha256).hexdigest()


def operator(session: Session, request: Request) -> Operator:
    """The signed-in, allowed operator of this request. Raises NotSignedIn or NotAllowed (the latter is audited and
    the member id logged, so it can be added to ADMIN_USER_IDS). Side effects: reads; on NotAllowed writes an audit
    row and commits."""
    token = request.cookies.get(cookie_name())
    if not token:
        raise NotSignedIn
    row = session.execute(_SESSION, {"h": token_hash(token), "now": datetime.now(timezone.utc)}).first()
    if row is None:
        raise NotSignedIn
    if row.user_id not in config.allowed_users():
        log.warning("admin: member %s is not on ADMIN_USER_IDS", row.user_id)
        audit(session, row.user_id, "denied", None)
        session.commit()
        raise NotAllowed
    return Operator(row.user_id, row.name, token)


def check_form(request: Request, op: Operator, sent_token: str | None) -> bool:
    """A form post from our own page: the Origin is the service address and the form token matches."""
    origin = request.headers.get("origin")
    return origin == config.public_origin() and hmac.compare_digest(sent_token or "", op.form_token)


def audit(session: Session, user_id: uuid.UUID | None, action: str, target_id: str | None) -> None:
    """One audit row (no content, no IP). Side effects: inserts (the caller commits)."""
    session.execute(text("INSERT INTO admin.audit (at, user_id, action, target_id) VALUES (:at, :u, :a, :t)"),
                    {"at": datetime.now(timezone.utc), "u": user_id, "a": action, "t": target_id})
