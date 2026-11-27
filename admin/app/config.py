"""Settings of the operator app (plans/T3-57.md), read from the environment on each call so tests can set them."""

from __future__ import annotations

import os
import uuid

from kosis_store.db import database_url as service_database_url


def database_url() -> str:
    """ADMIN_DATABASE_URL: the kosis_admin account (admin/sql/grants.sql). Falls back to DATABASE_URL for local runs."""
    return os.environ.get("ADMIN_DATABASE_URL", "").strip() or service_database_url()


def public_origin() -> str:
    """The service address the browser sees, e.g. https://3-37-96-249.sslip.io. Form posts must come from it."""
    return os.environ.get("ADMIN_PUBLIC_ORIGIN", "http://localhost:3000").strip().rstrip("/")


def secure() -> bool:
    return public_origin().startswith("https://")


def allowed_users() -> frozenset[uuid.UUID]:
    """ADMIN_USER_IDS: comma-separated member ids (users.id). Empty: nobody gets in. A bad entry is an error, so a typo
    does not quietly lock someone out."""
    raw = os.environ.get("ADMIN_USER_IDS", "")
    return frozenset(uuid.UUID(part.strip()) for part in raw.split(",") if part.strip())


def grafana_url() -> str | None:
    """Where the Grafana links go (e.g. https://…/grafana). None: links are not shown."""
    return os.environ.get("ADMIN_GRAFANA_URL", "").strip().rstrip("/") or None
