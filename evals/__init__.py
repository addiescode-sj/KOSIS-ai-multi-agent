"""Layered evaluation (T2-03): L1 retrieval, L2 query understanding, L3 calculation, L4 end to end.

Independent of LangChain (the retrievers and pipelines are only called) and of the Phase 1 eval harness.
Named evals so it does not shadow the Phase 1 `eval` package on the import path.
Copied from phase 2 (vendor/phase2@484bd06, T3-00) and continued here; data, datasets and reports are under this repo.
"""

import kosis_agent  # noqa: F401  (KOSIS_WORKSPACE = this repo, before kosis_rag defaults it to vendor/phase2)
