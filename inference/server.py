"""Model server for the stage 1 search models: the query embedder (E2s-e2) or the stage 1 re-ranker (RR-E2s-e2).

One image, one model per container (INFER_MODEL=embed | rerank), so each can go on its own GPU or machine.
Loading is vendor/phase2 kosis_rag.adapters.models (load_embedder / load_cross_encoder) imported as is, so the
server gives the same numbers as the in-process search: same base model, same checkpoint put back, fp16 on a GPU.

Environment (read here only, the server is an entry point):
    INFER_MODEL   embed | rerank (required)
    EMBED_CKPT    embed: checkpoint .pt (container path, e.g. /models/embedder/E2s-e2.pt), or "base"
    RERANK_CKPT   rerank: checkpoint .pt (e.g. /models/reranker/RR-E2s-e2.pt), or "base"
    TEXT_FORMAT   optional: refuse weights trained on another table text format (adapters.models.check_format)
    INFER_DEVICE  optional: cuda | cpu (default: cuda when available)
    INFER_BATCH   optional: encode / predict batch size (default 16)

API (contract shared with backend, see inference/README.md):
    GET  /health  -> {status, kind, model, ckpt, doc_format, device}
    POST /embed   {texts, kind?: query | document} -> {vectors, model, ckpt}
    POST /rerank  {question, passages} -> {scores, model, ckpt}
The question normalization (shared.tabletext.norm_query) stays with the caller, as in the in-process search.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

log = logging.getLogger("inference")

MAX_ITEMS = 256  # texts per /embed, passages per /rerank (stage 1 scores the top 50)
KINDS = ("embed", "rerank")


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_ITEMS)
    kind: Literal["query", "document"] = "query"  # query: 512 tokens, document: 1024 tokens (adapters.models)


class EmbedResponse(BaseModel):
    vectors: list[list[float]]
    model: str
    ckpt: str


class RerankRequest(BaseModel):
    question: str = Field(min_length=1)
    passages: list[str] = Field(max_length=MAX_ITEMS)


class RerankResponse(BaseModel):
    scores: list[float]
    model: str
    ckpt: str


class Health(BaseModel):
    status: str
    kind: str
    model: str
    ckpt: str
    doc_format: str | None
    device: str


@dataclass
class Loaded:
    """The one model this process serves."""

    kind: str  # embed | rerank
    model: Any  # adapters.models.Embedder or CrossEncoderScorer (or a fake in tests)
    doc_format: str | None
    device: str


def load_from_env(env: dict[str, str] | None = None) -> Loaded:
    """Read the environment and load the model it names; raises ValueError / FileNotFoundError early."""
    from kosis_rag.adapters import models

    env = dict(os.environ) if env is None else env
    kind = (env.get("INFER_MODEL") or "").strip().lower()
    if kind not in KINDS:
        raise ValueError(f"INFER_MODEL must be one of {KINDS}, got {kind!r}")
    ckpt = (env.get("EMBED_CKPT") if kind == "embed" else env.get("RERANK_CKPT")) or None
    if not ckpt:  # an empty value would load the untrained base model silently
        raise ValueError(f"set {'EMBED_CKPT' if kind == 'embed' else 'RERANK_CKPT'} (a .pt path, or 'base')")
    fmt = env.get("TEXT_FORMAT") or None
    device = env.get("INFER_DEVICE") or None
    batch = int(env.get("INFER_BATCH") or 16)
    if kind == "embed":
        model = models.load_embedder(ckpt, device=device, doc_format=fmt)
        model_device = str(model.model.device)
    else:
        model = models.load_cross_encoder(ckpt, device=device, doc_format=fmt, batch=batch)
        model_device = str(getattr(model.model, "device", device or "cpu"))
    trained = models.trained_format(models.resolve_ckpt(ckpt))
    return Loaded(kind=kind, model=model, doc_format=trained, device=model_device)


def create_app(loader=load_from_env) -> FastAPI:
    """The app; `loader` returns a Loaded (tests pass a fake so no torch is needed)."""
    state: dict[str, Any] = {}
    lock = asyncio.Lock()  # one forward pass at a time: keeps GPU memory flat, torch runs the batch anyway

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        loaded = loader()
        state["m"] = loaded
        log.info("loaded %s %s %s on %s (text %s)", loaded.kind, loaded.model.name, loaded.model.ckpt,
                 loaded.device, loaded.doc_format)
        yield
        state.clear()

    app = FastAPI(title="KOSIS inference", lifespan=lifespan)

    def need(kind: str) -> Loaded:
        loaded: Loaded = state["m"]
        if loaded.kind != kind:
            raise HTTPException(404, f"this server serves {loaded.kind!r}, not {kind!r}")
        return loaded

    @app.get("/health", response_model=Health)
    async def health() -> Health:
        m: Loaded = state["m"]
        return Health(status="ok", kind=m.kind, model=m.model.name, ckpt=m.model.ckpt, doc_format=m.doc_format,
                      device=m.device)

    @app.post("/embed", response_model=EmbedResponse)
    async def embed(req: EmbedRequest) -> EmbedResponse:
        m = need("embed")
        async with lock:
            if req.kind == "document":
                vectors = await run_in_threadpool(m.model.embed_documents, req.texts)
            else:  # queries one by one, as the in-process search embeds them (embed_query: batch 1, 512 tokens)
                vectors = await run_in_threadpool(lambda: [m.model.embed_query(t) for t in req.texts])
        return EmbedResponse(vectors=vectors, model=m.model.name, ckpt=m.model.ckpt)

    @app.post("/rerank", response_model=RerankResponse)
    async def rerank(req: RerankRequest) -> RerankResponse:
        m = need("rerank")
        async with lock:
            scores = await run_in_threadpool(m.model.score, req.question, req.passages)
        return RerankResponse(scores=scores, model=m.model.name, ckpt=m.model.ckpt)

    return app


def main() -> None:
    import argparse

    import uvicorn

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8001)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(), host=args.host, port=args.port, workers=1)  # one model copy per container


if __name__ == "__main__":
    main()
