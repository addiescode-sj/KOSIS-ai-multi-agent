"""Phase 3 agent: LangGraph StateGraph over the phase 1 / phase 2 modules."""

import os
from pathlib import Path

# Phase 1 summarize_stat finds a table key in <KOSIS_WORKSPACE>/data/tables.csv; default it to this repo's root.
# Phase 2 (kosis_rag @56b4252) reads no environment: backend/app/config.py passes its data paths in IndexSpec.
os.environ.setdefault("KOSIS_WORKSPACE", str(Path(__file__).resolve().parents[2]))
