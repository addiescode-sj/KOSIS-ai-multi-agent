"""Which table collections in the vector store fit the embedder weights (embed_ckpt and doc_format), and is
VECTOR_COLLECTION one of them.

    uv run python inference/check_collection.py                                  # weights = data/models/embedder/E2s-e2.pt
    uv run python inference/check_collection.py --embed-url http://<GPU IP>:8001  # weights = what the server loaded

The search opens a collection only when its metadata embed_ckpt ("<file>@<sha256[:12]>") equals the query embedder's
(backend/app/search_open.py, as legacy.retrievers does locally). Reads DATABASE_URL and VECTOR_COLLECTION from .env
(shell values win), like the backend. No torch needed: the local fingerprint is a file hash.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src"), str(ROOT / "vendor/phase2/src"), str(ROOT / "vendor/phase1"),
                str(ROOT / "vendor/phase1/src")]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--embed-ckpt", default="data/models/embedder/E2s-e2.pt", help="local weights (ignored with --embed-url)")
    p.add_argument("--embed-url", default=None, help="embed server; its /health ckpt is the fingerprint to match")
    args = p.parse_args()

    from kosis_agent.envfile import load_env_file

    load_env_file()
    from backend.app.config import vector_store
    from kosis_rag.adapters import models
    from kosis_rag.adapters.vectorstore import vector_client

    if args.embed_url:
        from inference.client import RemoteEmbedder

        e = RemoteEmbedder(args.embed_url)
        want, fmt, src = e.ckpt, e.doc_format, args.embed_url
    else:
        path = models.resolve_ckpt(args.embed_ckpt)
        want, fmt, src = models.ckpt_id(path), models.trained_format(path), args.embed_ckpt
    print(f"embedder {want} ({fmt}) from {src}")

    store = vector_store()
    active = store.collection
    fits = []
    for c in sorted(vector_client(store).list_collections(), key=lambda c: c.name):
        m = c.metadata or {}
        if "doc_format" not in m:
            continue  # not a table build
        ok = m.get("embed_model") == models.EMBED_BASE and m.get("embed_ckpt") == want and m.get("doc_format") == fmt
        fits += [c.name] if ok else []
        mark = "*" if c.name == active else " "
        print(f"{mark} {'OK ' if ok else '   '} {c.name}  {m.get('embed_ckpt', '-')}  {m.get('doc_format')}  docs {c.count()}")
    print(f"VECTOR_COLLECTION={active}")
    if active in fits:
        print("PASS: the search will open VECTOR_COLLECTION with these weights")
        return 0
    if fits:
        print(f"FAIL: set VECTOR_COLLECTION to one of: {', '.join(fits)}")
    else:
        print("FAIL: no collection was built with these weights; build one: "
              f"python scripts/kosis_build.py vectors --model bge-m3-ft --ckpt {args.embed_ckpt} --text-format {fmt}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
