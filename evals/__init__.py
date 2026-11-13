"""Layered evaluation (T2-03): L1 retrieval, L2 query understanding, L3 calculation, L4 end to end.

Independent of LangChain (the retrievers and pipelines are only called) and of the Phase 1 eval harness.
Named evals so it does not shadow the Phase 1 `eval` package on the import path.
Copied from phase 2 (vendor/phase2@484bd06, T3-00) and continued here; data, datasets and reports are under this repo.
"""

import sys
from pathlib import Path

# T3-23: `python -m evals.<module>` from the repo root needs no PYTHONPATH: the same paths as pytest.ini (scripts/ do
# the same). Paths already on sys.path (PYTHONPATH, pytest) are left as they are.
_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "vendor" / "phase1" / "src", _ROOT / "vendor" / "phase1", _ROOT / "vendor" / "phase2" / "src",
           _ROOT / "src", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import kosis_agent  # noqa: E402,F401  (KOSIS_WORKSPACE = this repo, before kosis_rag defaults it to vendor/phase2)
