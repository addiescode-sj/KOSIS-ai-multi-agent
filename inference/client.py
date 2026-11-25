"""HTTP clients for inference/server.py with the same surface as kosis_rag.adapters.models.

    RemoteEmbedder  .name .ckpt .doc_format .embed_query(text) .embed_documents(texts)   (like models.Embedder)
    RemoteScorer    .name .ckpt .doc_format .score(question, passages)                   (like models.CrossEncoderScorer)

So they drop into what the search already takes: DenseTableRetriever(embed_query=...) and
rerank.stage1.passage_scorer(RemoteScorer(...), text_of, norm_query). Only httpx is imported (no torch), so the
backend image can leave torch out. Nothing here reads the environment: the caller passes the URL.
"""

from __future__ import annotations

from typing import Any

import httpx

MAX_ITEMS = 256  # server limit per request (inference/server.py)


class InferenceError(RuntimeError):
    """The model server could not answer (message names the URL)."""


class _Remote:
    kind = ""

    def __init__(self, url: str, *, timeout: float = 60.0, client: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self._http = client or httpx.Client(timeout=timeout)
        info = self.health()
        if info.get("kind") != self.kind:
            raise ValueError(f"{self.url} serves {info.get('kind')!r}, expected {self.kind!r}")
        self.name: str = info["model"]
        self.ckpt: str = info["ckpt"]  # adapters.models.ckpt_id of the loaded weights: "<file>@<sha256[:12]>"
        self.doc_format: str | None = info.get("doc_format")

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """One request; connection failures, timeouts and error statuses raise InferenceError naming the URL."""
        url = f"{self.url}{path}"
        try:
            r = self._http.request(method, url, json=body)
        except httpx.TimeoutException as e:
            raise InferenceError(f"{self.kind} server timed out: {method} {url} ({e!r})") from e
        except httpx.TransportError as e:  # refused, unreachable, DNS (e.g. the server's IP changed)
            raise InferenceError(f"{self.kind} server unreachable: {method} {url} ({e!r})") from e
        if r.status_code >= 400:
            raise InferenceError(f"{self.kind} server {r.status_code}: {method} {url}: {r.text[:300]}")
        return r.json()

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/health")

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        out = self._call("POST", path, body)
        if out.get("ckpt") != self.ckpt:  # the server restarted with other weights since we connected
            raise InferenceError(f"{self.url} now serves {out.get('ckpt')!r}, connected to {self.ckpt!r}")
        return out

    def close(self) -> None:
        self._http.close()


class RemoteEmbedder(_Remote):
    """Unit-length vectors from the embed server (same weights and token limits as models.Embedder)."""

    kind = "embed"

    def _embed(self, texts: list[str], kind: str) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), MAX_ITEMS):
            out += self._post("/embed", {"texts": texts[i:i + MAX_ITEMS], "kind": kind})["vectors"]
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "query")[0]

    def embed_documents(self, texts: list[str], batch: int = 16) -> list[list[float]]:
        return self._embed(texts, "document") if texts else []


class RemoteScorer(_Remote):
    """One relevance per passage from the rerank server (same values as models.CrossEncoderScorer.score)."""

    kind = "rerank"

    def score(self, question: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        out: list[float] = []
        for i in range(0, len(passages), MAX_ITEMS):
            out += self._post("/rerank", {"question": question, "passages": passages[i:i + MAX_ITEMS]})["scores"]
        return out
