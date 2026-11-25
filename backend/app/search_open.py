"""검색 모듈 열기: 임베딩·1차 리랭커를 프로세스 안에서 로드하거나(기본), 따로 띄운 추론 서버(inference/)에 HTTP로 묻는다(배포).

    EMBED_URL   질의 임베딩 서버(inference 이미지 INFER_MODEL=embed, 예 http://<서버 주소>:8001). 비우면 EMBED_CKPT로 로컬 로드
    RERANK_URL  1차 리랭커 서버(INFER_MODEL=rerank, 예 http://<서버 주소>:8002). 비우면 RERANK_CKPT로 로컬 로드(둘 다 비면 1차 끔)

값은 backend/app/config.py agent_config()가 .env(셸 값 우선)에서 읽어 cfg.embed_url·cfg.rerank_url로 넘긴다.
원격일 때도 로컬과 같은 검사를 한다: 컬렉션 메타의 embed_model·embed_ckpt·doc_format이 임베딩 서버 /health 값과 같아야 하고,
리랭커 서버의 doc_format도 검색 텍스트 형식과 같아야 한다(다르면 ValueError: 다른 가중치로 조용히 검색하지 않음).
phase 2는 고치지 않는다: load_resources의 dense·stage1_score 주입 + SearchModule.from_resources만 쓴다.
이 파일은 torch·sentence_transformers를 import하지 않는다(backend 이미지를 WITH_EMBED=0으로 빌드할 수 있게).
"""

from __future__ import annotations

import importlib.util
from typing import Any

from kosis_rag.api import SearchModule


def open_search(cfg: Any) -> SearchModule:
    """cfg = backend.app.config.agent_config(). embed_url·rerank_url이 둘 다 없으면 지금과 똑같이 SearchModule(spec, options, llm)."""
    embed_url, rerank_url = getattr(cfg, "embed_url", None), getattr(cfg, "rerank_url", None)
    _check_local_models(cfg, embed_url, rerank_url)
    if not embed_url and not rerank_url:
        return SearchModule(cfg.spec, cfg.options, cfg.stage2_llm)

    from kosis_rag.adapters import files
    from kosis_rag.adapters.loader import load_resources
    from kosis_rag.rerank.stage1 import passage_scorer
    from kosis_rag.shared import tabletext as text

    spec, options = cfg.spec, cfg.options
    records = files.read_records(spec.tables or files.TABLES)
    dense = remote_dense(spec, options, embed_url) if embed_url else None
    stage1 = None
    if rerank_url:
        from inference.client import RemoteScorer

        scorer = RemoteScorer(rerank_url)
        fmt = text.text_format(spec, dense) if dense is not None else spec.text_format
        trained = scorer.doc_format
        if fmt and trained and trained != fmt:
            raise ValueError(f"reranker server {rerank_url} was trained on {trained!r} text, the search uses {fmt!r}")
        fmt = fmt or trained or text.DOC_FORMAT
        text_of = {r.info.key: text.build_doc(r, fmt)["embed_text"] for r in records}
        stage1 = passage_scorer(scorer, text_of, text.norm_query)
    resources = load_resources(spec=spec, options=options, llm=cfg.stage2_llm, records=records, dense=dense,
                               stage1_score=stage1)
    return SearchModule.from_resources(resources)


def remote_dense(spec: Any, options: Any, url: str) -> Any:
    """DenseTableRetriever.connect와 같은 검사, 질의 임베딩만 서버에 묻는다 (fine-tuned bge-m3 컬렉션만)."""
    from inference.client import RemoteEmbedder
    from kosis_rag.adapters import models as model
    from kosis_rag.adapters.vectorstore import vector_client
    from kosis_rag.legacy.retrievers import DenseTableRetriever
    from kosis_rag.schemas.resources import VectorStoreConfig
    from kosis_rag.shared import tabletext as text

    store = spec.vector_store or VectorStoreConfig()
    name = store.collection
    if not name:
        raise ValueError("no collection: set VECTOR_COLLECTION")
    collection = vector_client(store).get_collection(name, embedding_function=None)
    meta = collection.metadata or {}
    if meta.get("embed_model") != model.EMBED_BASE:
        raise ValueError(f"collection {name} was embedded with {meta.get('embed_model')!r}; "
                         f"EMBED_URL serves {model.EMBED_BASE} only (leave EMBED_URL empty for a KURE collection)")
    fmt = meta.get("doc_format")
    if fmt not in text.FORMATS:
        raise ValueError(f"collection {name} holds {fmt!r} text; this code knows {', '.join(text.FORMATS)}")
    embedder = RemoteEmbedder(url)
    if embedder.ckpt != meta.get("embed_ckpt"):
        raise ValueError(f"collection {name} was embedded with {meta.get('embed_ckpt')!r}, "
                         f"the embed server {url} serves {embedder.ckpt!r}")
    if embedder.doc_format and embedder.doc_format != fmt:
        raise ValueError(f"embed server {url} was trained on {embedder.doc_format!r} text, collection {name} holds {fmt!r}")
    return DenseTableRetriever(collection=collection, embed_query=lambda q: embedder.embed_query(text.norm_query(q)),
                               k=options.pool, doc_format=fmt)


def _check_local_models(cfg: Any, embed_url: str | None, rerank_url: str | None) -> None:
    """로컬 로드가 필요한데 이미지에 sentence-transformers가 없으면(WITH_EMBED=0) 무엇을 넣어야 하는지 바로 알린다."""
    need = []
    if not embed_url:
        need.append("EMBED_URL")
    if not rerank_url and cfg.spec.rerank_ckpt:
        need.append("RERANK_URL")
    if need and importlib.util.find_spec("sentence_transformers") is None:
        raise RuntimeError(f"no sentence-transformers in this image: set {' and '.join(need)} in .env "
                           "(inference servers) or build the backend with WITH_EMBED=1")
