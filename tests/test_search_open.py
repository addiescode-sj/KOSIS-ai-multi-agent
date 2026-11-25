"""backend/app/search_open.py: EMBED_URL·RERANK_URL 유무에 따라 로컬 로드 / 원격 주입을 고르는지 (서버·DB·모델 없이, 가짜로)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import inference.client as client
from backend.app import search_open
from kosis_rag.adapters import files, loader


def _cfg(embed_url=None, rerank_url=None, rerank_ckpt="data/models/rr.pt", text_format="n2-L60"):
    spec = SimpleNamespace(tables="tables.jsonl", rerank_ckpt=rerank_ckpt, text_format=text_format, vector_store=None)
    return SimpleNamespace(spec=spec, options=SimpleNamespace(pool=50), stage2_llm=None,
                           embed_url=embed_url, rerank_url=rerank_url)


class _Module:
    """SearchModule 대역: 생성자 인자 / from_resources 인자를 기록."""

    def __init__(self, *args):
        self.args = args

    @classmethod
    def from_resources(cls, resources):
        return SimpleNamespace(resources=resources)


@pytest.fixture
def fakes(monkeypatch):
    seen = {}
    monkeypatch.setattr(search_open, "SearchModule", _Module)
    monkeypatch.setattr(search_open.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(files, "read_records", lambda path: [])

    def fake_load(**kw):
        seen.update(kw)
        return "resources"

    monkeypatch.setattr(loader, "load_resources", fake_load)
    return seen


def test_no_urls_loads_locally_as_before(fakes):
    cfg = _cfg()
    module = search_open.open_search(cfg)
    assert isinstance(module, _Module) and module.args == (cfg.spec, cfg.options, None)
    assert fakes == {}  # load_resources not called directly


def test_urls_inject_remote_dense_and_stage1(fakes, monkeypatch):
    dense = SimpleNamespace(doc_format="n2-L60")
    monkeypatch.setattr(search_open, "remote_dense", lambda spec, options, url: dense)

    class Scorer:
        def __init__(self, url):
            self.url, self.doc_format = url, "n2-L60"

        def score(self, question, passages):
            return [0.5] * len(passages)

    monkeypatch.setattr(client, "RemoteScorer", Scorer)
    module = search_open.open_search(_cfg("http://10.0.0.5:8001", "http://10.0.0.5:8002"))
    assert module.resources == "resources"
    assert fakes["dense"] is dense and callable(fakes["stage1_score"])


def test_reranker_text_format_mismatch_refused(fakes, monkeypatch):
    monkeypatch.setattr(search_open, "remote_dense", lambda *a: SimpleNamespace(doc_format="n2-L60"))

    class Scorer:
        def __init__(self, url):
            self.doc_format = "n3"

    monkeypatch.setattr(client, "RemoteScorer", Scorer)
    with pytest.raises(ValueError, match="trained on 'n3'"):
        search_open.open_search(_cfg("http://e:8001", "http://r:8002"))


def test_missing_sentence_transformers_names_the_variables(monkeypatch):
    monkeypatch.setattr(search_open.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(RuntimeError, match="EMBED_URL and RERANK_URL"):
        search_open.open_search(_cfg())


def test_rerank_url_only_formats_passages_like_the_local_collection(fakes, monkeypatch):
    """RERANK_URL without EMBED_URL: the local dense list is opened first and its collection format is the one checked."""
    from kosis_rag.legacy import retrievers

    monkeypatch.setattr(retrievers.DenseTableRetriever, "connect",
                        classmethod(lambda cls, *a, **kw: SimpleNamespace(doc_format="n2-L60")))

    class Scorer:
        def __init__(self, url):
            self.doc_format = "n3"

    monkeypatch.setattr(client, "RemoteScorer", Scorer)
    cfg = _cfg(rerank_url="http://r:8002", text_format=None)
    cfg.spec.vector_store = SimpleNamespace(collection="c")
    cfg.spec.device = None
    cfg.spec.embed_ckpt = None
    with pytest.raises(ValueError, match="the search uses 'n2-L60'"):
        search_open.open_search(cfg)
