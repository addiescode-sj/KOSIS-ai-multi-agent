"""Phase 3 agent: LangGraph StateGraph over the phase 1 / phase 2 tools."""

import os
from pathlib import Path

# Phase 1 reads data/, eval/ and .env under KOSIS_WORKSPACE; point it at this repo before kosis_rag sets its own default.
# ponytail: kosis_rag still reads vendor/phase2/data and vendor/phase2/.env (fixed parents[2]); fix in phase 2 before T3-01.
os.environ.setdefault("KOSIS_WORKSPACE", str(Path(__file__).resolve().parents[2]))
