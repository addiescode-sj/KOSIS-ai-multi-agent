"""Phase 3 reads its .env (phase 1 / phase 2 modules never do: they get settings objects from backend/app/config.py).

Entry points (backend/app/main.py, `python -m evals.run_eval`, `python -m evals.suite`, scripts/) call
load_env_file() once before reading settings. Values already in the shell win (override=False), so `set X=...`
still changes one run. KOSIS_DOTENV=off skips the file (tests/conftest.py sets it, so tests never see a local .env).
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
OFF = {"off", "0", "false", "no"}


def load_env_file(path: Path | None = None) -> Path | None:
    """Load path (default <repo>/.env) into os.environ without overriding values already set.

    Out: the file loaded, or None (KOSIS_DOTENV=off, or no such file: e.g. in the container, where compose
    passes the values through env_file). Side effects: sets os.environ entries that were unset.
    """
    if (os.environ.get("KOSIS_DOTENV") or "").strip().lower() in OFF:
        return None
    path = path or ENV_FILE
    if not path.is_file():
        return None
    from dotenv import load_dotenv

    load_dotenv(path, override=False)
    return path
