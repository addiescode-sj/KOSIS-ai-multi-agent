"""Run the phase 2 offline build CLI (kosis_rag_build.cli) from this repo, no PYTHONPATH needed (T3-23).

    python scripts/kosis_build.py chroma --model bge-m3-ft --ckpt base --text-format n2-L60 --tables data/tables.jsonl
    python scripts/kosis_build.py chroma --list

Same commands and arguments as `python -m kosis_rag_build.cli`. Settings: arguments first, then the environment
(<repo>/.env loaded first, shell values win: CHROMA_HOST CHROMA_PORT CHROMA_COLLECTION EMBED_CKPT RERANK_CKPT
TEXT_FORMAT OPENAI_API_KEY). Give --ckpt explicitly when building: without it EMBED_CKPT from .env is used
(--ckpt base = the untrained bge-m3). The build writes its own collection (--collection, default a new
kosis_a_bgeft_<time>); CHROMA_COLLECTION is only the one --list marks as active.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "vendor" / "phase1" / "src", ROOT / "vendor" / "phase1", ROOT / "vendor" / "phase2" / "src",
           ROOT / "src", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
os.environ.setdefault("KOSIS_WORKSPACE", str(ROOT))

if __name__ == "__main__":
    from kosis_agent.envfile import load_env_file
    from kosis_rag_build.cli import main

    load_env_file()  # <repo>/.env, shell values win
    raise SystemExit(main(sys.argv[1:], env=os.environ))
