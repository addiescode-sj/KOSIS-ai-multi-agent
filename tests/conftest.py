"""Tests never read a developer's .env (src/kosis_agent/envfile.py): settings come only from what each test sets."""

import os
import sys

import pytest

os.environ["KOSIS_DOTENV"] = "off"


@pytest.fixture(autouse=True)
def _fresh_service_state():
    """T3-51: the API keeps a process-wide service store, checkpointer and feedback rate counter; each test starts
    without the conversations of the one before (only when the API module is loaded at all)."""
    api = sys.modules.get("backend.app.main")
    if api is not None:
        api._store, api._checkpointer = None, None
        api._hits.clear()
    yield
