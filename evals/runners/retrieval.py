"""L1 runner: call a retriever per question and keep the ranked table keys. The only LangChain touch point in evals.

Any BaseRetriever works as long as Document.metadata["key"] holds the table key; metadata["score"] (when given)
is the top-1 score the no-answer rule uses. LLM calls and tokens made inside a retriever (query rewriting,
reranking) are counted with evals.ops.UsageCallback; an exception is a tool failure, recorded and not raised.
"""

from __future__ import annotations

import time
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnableLambda

from kosis_rag.loader import to_document
from kosis_rag.retriever import BM25TableRetriever, ChromaTableRetriever
from kosis_rag.schemas import TableRecord

from evals.ops import UsageCallback
from evals.schemas import TABLES, RetrievalItem
from evals.trace import NodeSpans


class OracleRetriever(BaseRetriever):
    """Returns the gold keys first: a self-check of the evaluator (Hit@1 must be 100% on answerable items)."""

    gold: dict[str, list[str]]

    def _get_relevant_documents(self, query: str, *, run_manager) -> list[Document]:
        return [Document(page_content="", metadata={"key": k, "score": 1.0}) for k in self.gold.get(query, [])]


def load_records(path: Path = TABLES) -> list[TableRecord]:
    with path.open(encoding="utf-8") as f:
        return [TableRecord.model_validate_json(line) for line in f if line.strip()]


def bm25_retrievers(records: list[TableRecord], k: int) -> dict[str, BM25TableRetriever]:
    """BM25 over the catalog document (kosis_rag.loader) and over TableRecord.search_text (T2-01)."""
    catalog = [to_document(r.info) for r in records]
    rich = [Document(page_content=r.search_text, metadata=d.metadata, id=d.id) for r, d in zip(records, catalog)]
    return {
        "bm25-catalog": BM25TableRetriever.from_documents(catalog, k=k),
        "bm25-record": BM25TableRetriever.from_documents(rich, k=k),
    }


# Staged search (T2-06, T2-07): name -> settings over .env (CHROMA_COLLECTION, EMBED_CKPT, RERANK_CKPT, HCX_API_KEY).
STAGED = {
    "hybrid": {"cond_filter": False, "rerank_ckpt": "", "stage2": "none"},
    "hybrid+cond": {"cond_filter": True, "rerank_ckpt": "", "stage2": "none"},
    "staged": {"cond_filter": True, "stage2": "none"},  # + stage 1 (RERANK_CKPT)
    "clova-only": {"cond_filter": True, "rerank_ckpt": "", "stage2": "clova"},
    "staged-clova": {"cond_filter": True, "stage2": "clova"},  # stage 1 + CLOVA
    "hybrid+cond-hcx": {"cond_filter": True, "rerank_ckpt": "", "stage2": "hcx"},  # T2-09, HCX_THINKING=none | low
    "staged-hcx": {"cond_filter": True, "stage2": "hcx"},
    "hybrid+cond-gpt": {"cond_filter": True, "rerank_ckpt": "", "stage2": "gpt"},  # same prompt, gpt-4o-mini
}


STAGE2_FIELDS = ("stage2_picked_n", "stage2_reason", "stage2_error", "stage2_calls")  # None when no stage 2 ran


GRAPH = "graph:"  # graph:<STAGED name> = the same settings run through the LangGraph search (T2-10)


def _staged_settings(name: str):
    """SearchSettings for a STAGED preset: .env plus the preset's overrides (empty rerank_ckpt = stage 1 off).

    In: name = a STAGED key. Out: SearchSettings. Side effects: loads .env into the environment.
    Node: not used by the graph (the runner builds the search with it).
    """
    from kosis_rag.pipeline import SearchSettings

    overrides = {key: (value or None) if key == "rerank_ckpt" else value for key, value in STAGED[name].items()}
    return SearchSettings.from_env(**overrides)


def build(name: str, items: list[RetrievalItem], k: int) -> Runnable:
    """The search the L1 runner calls for a retriever name.

    In: name = oracle | chroma | bm25-catalog | bm25-record | a STAGED preset | graph:<STAGED preset>;
        items = the golden items (oracle only); k = hits for the simple retrievers.
    Out: something with .invoke(question, config) -> ranked Documents (metadata["key"] set). graph:<preset> returns
        the LangGraph search's whole ranked pool, the same documents the preset's nested retrievers return.
    Side effects: loads tables.jsonl, connects to Chroma and loads models as the preset needs; ValueError for an
        unknown name. Node: not used by the graph.
    """
    if name == "oracle":
        return OracleRetriever(gold={i.question: [s[0] for s in i.required] for i in items})
    if name == "chroma":  # needs the shared server and CHROMA_COLLECTION (T2-26)
        return ChromaTableRetriever.connect(k=k)
    if name in STAGED:  # the pipeline keeps its own pool of 50; the runner cuts to k
        from kosis_rag.pipeline import build_search

        return build_search(_staged_settings(name))
    if name.startswith(GRAPH) and name[len(GRAPH):] in STAGED:
        # The graph's ranked pool (state.docs), uncut, with the run config (callbacks) passed through. The guide node
        # gets Phase 1 MockClient: no model call, so L1 counts the same calls and tokens as the retriever above.
        from kosis_chat.llm.model import MockClient
        from kosis_rag.graph import build_graph, run_search
        from kosis_rag.pipeline import load_resources

        graph = build_graph(load_resources(_staged_settings(name[len(GRAPH):])), MockClient())
        return RunnableLambda(lambda question, config: run_search(graph, question, config).docs, name=name)
    retrievers = bm25_retrievers(load_records(), k)
    if name not in retrievers:
        known = [*retrievers, *STAGED, *(GRAPH + n for n in STAGED)]
        raise ValueError(f"unknown retriever {name!r}; known: oracle, chroma, {', '.join(known)}")
    return retrievers[name]


def run(retriever: BaseRetriever, items: list[RetrievalItem], k: int) -> list[dict]:
    rows = []
    for item in items:
        counter, nodes = UsageCallback(), NodeSpans()
        started = time.perf_counter()
        error = None
        try:
            docs = retriever.invoke(item.question, config={"callbacks": [counter, nodes]})[:k]
        except Exception as exc:  # a failing question is recorded, the run goes on
            docs, error = [], f"{type(exc).__name__}: {exc}"
        meta = docs[0].metadata if docs else {}
        rows.append({
            "qid": item.qid, "split": item.split, "qtype": item.qtype, "answerable": item.answerable,
            "no_answer_reason": item.no_answer_reason,
            "keys": [d.metadata["key"] for d in docs],
            # a stage 2 re-ranker moves another table to the top: tau judges the stage 1 top score either way (T2-09)
            "top_score": meta.get("stage2_input_top_score", meta.get("score")),
            **{f: meta.get(f) for f in STAGE2_FIELDS},
            "latency_ms": (time.perf_counter() - started) * 1000,
            # stage 2 calls its API directly (no LangChain callback): add what it recorded on the documents
            "llm_calls": counter.calls + (meta.get("stage2_calls") or 0),
            "input_tokens": counter.input_tokens + (meta.get("stage2_input_tokens") or 0),
            "output_tokens": counter.output_tokens + (meta.get("stage2_output_tokens") or 0),
            "model": ",".join(sorted(counter.models | ({meta["stage2"]} if meta.get("stage2_calls") else set()))) or None,
            "failed": error is not None,
            "retried": False,  # retrievers here do not retry; a retrying one reports it through its own metadata
            "error": error,
            "search_spans": nodes.spans,  # graph nodes (T2-36); empty for a retriever that is not a graph
        })
    return rows
