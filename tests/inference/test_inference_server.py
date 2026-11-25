"""inference/server.py and inference/client.py with fake models (no torch, no weights)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from inference.client import RemoteEmbedder, RemoteScorer
from inference.server import MAX_ITEMS, Loaded, create_app


class FakeEmbedder:
    name, ckpt = "BAAI/bge-m3", "E2s-e2.pt@000000000001"

    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(("query", [text]))
        return [float(len(text)), 1.0]

    def embed_documents(self, texts: list[str], batch: int = 16) -> list[list[float]]:
        self.calls.append(("document", texts))
        return [[float(len(t)), 0.0] for t in texts]


class FakeScorer:
    name, ckpt = "BAAI/bge-reranker-v2-m3", "RR-E2s-e2.pt@000000000002"

    def score(self, question: str, passages: list[str]) -> list[float]:
        return [1.0 / (1 + i) for i, _ in enumerate(passages)]


def client(kind: str, model) -> TestClient:
    app = create_app(lambda: Loaded(kind=kind, model=model, doc_format="n2-L60", device="cpu"))
    return TestClient(app)


def test_health_reports_weights_and_format():
    with client("embed", FakeEmbedder()) as c:
        h = c.get("/health").json()
    assert h == {"status": "ok", "kind": "embed", "model": "BAAI/bge-m3", "ckpt": "E2s-e2.pt@000000000001",
                 "doc_format": "n2-L60", "device": "cpu"}


def test_embed_query_one_by_one_and_documents_in_batch():
    fake = FakeEmbedder()
    with client("embed", fake) as c:
        q = c.post("/embed", json={"texts": ["ab", "abc"]}).json()
        d = c.post("/embed", json={"texts": ["x"], "kind": "document"}).json()
    assert q["vectors"] == [[2.0, 1.0], [3.0, 1.0]] and q["ckpt"] == fake.ckpt
    assert d["vectors"] == [[1.0, 0.0]]
    assert fake.calls == [("query", ["ab"]), ("query", ["abc"]), ("document", ["x"])]


def test_limits_and_wrong_kind():
    with client("embed", FakeEmbedder()) as c:
        assert c.post("/embed", json={"texts": []}).status_code == 422
        assert c.post("/embed", json={"texts": ["a"] * (MAX_ITEMS + 1)}).status_code == 422
        assert c.post("/rerank", json={"question": "q", "passages": ["p"]}).status_code == 404


def test_rerank_scores():
    with client("rerank", FakeScorer()) as c:
        r = c.post("/rerank", json={"question": "출생아수", "passages": ["a", "b"]}).json()
        assert c.post("/rerank", json={"question": "q", "passages": []}).json()["scores"] == []
    assert r == {"scores": [1.0, 0.5], "model": FakeScorer.name, "ckpt": FakeScorer.ckpt}


def test_clients_match_model_surface():
    with client("embed", FakeEmbedder()) as c:
        e = RemoteEmbedder("http://testserver", client=c)
        assert (e.name, e.ckpt, e.doc_format) == ("BAAI/bge-m3", "E2s-e2.pt@000000000001", "n2-L60")
        assert e.embed_query("abc") == [3.0, 1.0]
        assert len(e.embed_documents(["a"] * (MAX_ITEMS + 3))) == MAX_ITEMS + 3  # split into requests
        with pytest.raises(ValueError):
            RemoteScorer("http://testserver", client=c)  # an embed server is not a re-ranker
    with client("rerank", FakeScorer()) as c:
        s = RemoteScorer("http://testserver", client=c)
        assert s.score("q", ["a", "b", "c"]) == [1.0, 0.5, 1 / 3] and s.score("q", []) == []


def test_unreachable_server_names_the_url():
    import httpx

    from inference.client import InferenceError

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(InferenceError, match=r"http://10\.0\.0\.9:8001/health"):
        RemoteEmbedder("http://10.0.0.9:8001", client=httpx.Client(transport=httpx.MockTransport(refuse)))
