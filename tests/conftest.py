"""Tests never read a developer's .env (src/kosis_agent/envfile.py): settings come only from what each test sets."""

import os

os.environ["KOSIS_DOTENV"] = "off"
