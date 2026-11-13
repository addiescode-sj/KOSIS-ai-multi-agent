"""L1 runner: call a retriever per question and keep the ranked table keys. The only LangChain touch point in evals.

Any BaseRetriever works as long as Document.metadata["key"] holds the table key; metadata["score"] (when given)
is the top-1 score the no-answer rule uses. LLM calls and tokens made inside a retriever (query rewriting,
reranking) are counted with evals.ops.UsageCallback; an exception is a tool failure, recorded and not raised.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import Runnable, RunnableLambda

from kosis_rag.legacy.retrievers import BM25TableRetriever, ChromaTableRetriever
from kosis_rag.schemas.catalog import TableRecord
from kosis_rag.shared.catalog import to_document

from kosis_agent.observability.tracing import LiveSpans

from evals.ops import UsageCallback, stage2_usage
from evals.runners.agent import progress, span_rows
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
    """BM25 over the catalog document (kosis_rag.shared.catalog) and over TableRecord.search_text (T2-01)."""
    catalog = [to_document(r.info) for r in records]
    rich = [Document(page_content=r.search_text, metadata=d.metadata, id=d.id) for r, d in zip(records, catalog)]
    return {
        "bm25-catalog": BM25TableRetriever.from_documents(catalog, k=k),
        "bm25-record": BM25TableRetriever.from_documents(rich, k=k),
    }


# Staged search (T2-06, T2-07): name -> overrides over the shell (CHROMA_COLLECTION, EMBED_CKPT, RERANK_CKPT, HCX_API_KEY).
STAGED = {
    "hybrid": {"cond_filter": False, "rerank_ckpt": "", "stage2": "none"},
    "hybrid+cond": {"cond_filter": True, "rerank_ckpt": "", "stage2": "none"},
    "staged": {"cond_filter": True, "stage2": "none"},  # + stage 1 (RERANK_CKPT)
    "clova-only": {"cond_filter": True, "rerank_ckpt": "", "stage2": "clova"},
    "staged-clova": {"cond_filter": True, "stage2": "clova"},  # stage 1 + CLOVA
    "hybrid+cond-hcx": {"cond_filter": True, "rerank_ckpt": "", "stage2": "hcx"},  # T2-09, HCX_THINKING=none | low
    "staged-hcx": {"cond_filter": True, "stage2": "hcx"},
    "hybrid+cond-gpt": {"cond_filter": True, "rerank_ckpt": "", "stage2": "gpt"},  # same prompt, gpt-4o-mini
    # stage 1 + the OpenAI-compatible stage 2: gpt-4o-mini, or STAGE2_MODEL on STAGE2_BASE_URL (e.g. vLLM Gemma, T3-33)
    "staged-gpt": {"cond_filter": True, "stage2": "gpt"},
}


STAGE2_FIELDS = ("stage2_picked_n", "stage2_reason", "stage2_error", "stage2_calls")  # None when no stage 2 ran


GRAPH = "graph:"  # graph:<STAGED name> = the same settings run through the LangGraph search (T2-10)


def _staged_config(name: str):
    """(IndexSpec, SearchOptions, stage 2 LLMConfig | None) of a STAGED preset: shell values plus the preset.

    An empty rerank_ckpt in the preset turns stage 1 off (None); no key keeps the shell's RERANK_CKPT. The stage 2
    LLM is built from the preset's stage2, not the shell's STAGE2. Node: not used by the graph.
    """
    from evals.config import search_config, stage2_llm

    overrides = {key: (value or None) if key == "rerank_ckpt" else value for key, value in STAGED[name].items()}
    spec, options = search_config(**overrides)
    return spec, options, stage2_llm(options.stage2)


def _resources(name: str):
    from kosis_rag.adapters.loader import load_resources

    spec, options, llm = _staged_config(name)
    return load_resources(spec=spec, options=options, llm=llm)


def connect_chroma(k: int = 20) -> ChromaTableRetriever:
    """The dense table collection named by the shell (CHROMA_HOST / CHROMA_PORT / CHROMA_COLLECTION, EMBED_CKPT)."""
    from evals.config import search_config

    spec, _ = search_config()
    return ChromaTableRetriever.connect(k=k, embed_ckpt=spec.embed_ckpt, chroma=spec.chroma)


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
        return connect_chroma(k)
    if name in STAGED:  # the pipeline keeps its own pool of 50; the runner cuts to k
        from kosis_rag.legacy.pipeline import build_search

        return build_search(_resources(name))
    if name.startswith(GRAPH) and name[len(GRAPH):] in STAGED:
        # The graph's whole ranked pool (state.docs, the preset's pool of 50), uncut, with the run config (callbacks)
        # passed through. The graph ends at gate (T2-41 S5), so no model is called after it. Not SearchModule's
        # candidates: those are cut to answer_k and would change Hit@10.
        from kosis_rag.graph.search import build_graph, run_search

        graph = build_graph(_resources(name[len(GRAPH):]))
        return RunnableLambda(lambda question, config: run_search(graph, question, config).docs, name=name)
    retrievers = bm25_retrievers(load_records(), k)
    if name not in retrievers:
        known = [*retrievers, *STAGED, *(GRAPH + n for n in STAGED)]
        raise ValueError(f"unknown retriever {name!r}; known: oracle, chroma, {', '.join(known)}")
    return retrievers[name]


def run(retriever: BaseRetriever, items: list[RetrievalItem], k: int, telemetry=None) -> list[dict]:
    """One row per question. telemetry (evals.runners.agent.tracing("l1")): each search is a live trace whose root is
    `execute_tool search_tables`, the span the service's search step has, with the graph nodes under it (T3-32)."""
    from opentelemetry.trace import StatusCode

    tracer = telemetry[0].get_tracer("kosis-agent") if telemetry else None
    rows = []
    for n, item in enumerate(items, 1):
        counter, nodes = UsageCallback(), NodeSpans()
        root = None
        if tracer is not None:
            telemetry[1].clear()  # memory copy unused for L1: keep it from growing over the run
            root = tracer.start_span("execute_tool search_tables", attributes={
                "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "search_tables", "kosis.eval.qid": item.qid,
                "gen_ai.tool.call.arguments": json.dumps({"question": item.question}, ensure_ascii=False)})
        callbacks = [counter, nodes] + ([LiveSpans(tracer, root)] if root is not None else [])
        started = time.perf_counter()
        error = None
        try:
            docs = retriever.invoke(item.question, config={"callbacks": callbacks})[:k]
        except Exception as exc:  # a failing question is recorded, the run goes on
            docs, error = [], f"{type(exc).__name__}: {exc}"
        ids = {}
        if root is not None:
            root.set_attribute("gen_ai.tool.call.result", json.dumps([d.metadata.get("key") for d in docs], ensure_ascii=False))
            if error:
                root.set_status(StatusCode.ERROR, error)
            root.end()
            ctx = root.get_span_context()
            mine = [s for s in telemetry[1].get_finished_spans() if s.context.trace_id == ctx.trace_id]
            ids = {"trace_id": format(ctx.trace_id, "032x"), "span_id": format(ctx.span_id, "016x"),
                   "trace_spans": span_rows(root, mine)}  # the live trace, for report.html
        meta = docs[0].metadata if docs else {}
        s2_calls, s2_in, s2_out, s2_retried = stage2_usage(meta)
        rows.append({
            "qid": item.qid, "split": item.split, "qtype": item.qtype, "answerable": item.answerable,
            "no_answer_reason": item.no_answer_reason,
            "keys": [d.metadata["key"] for d in docs],
            # a stage 2 re-ranker moves another table to the top: tau judges the stage 1 top score either way (T2-09)
            "top_score": meta.get("stage2_input_top_score", meta.get("score")),
            **{f: meta.get(f) for f in STAGE2_FIELDS},
            "latency_ms": (time.perf_counter() - started) * 1000,
            # stage 2 calls its API directly (no LangChain callback): add what it recorded on the documents,
            # one logical call per rerank2 run like the agent layer (T3-31; HTTP attempts only set `retried`)
            "llm_calls": counter.calls + s2_calls,
            "input_tokens": counter.input_tokens + s2_in,
            "output_tokens": counter.output_tokens + s2_out,
            "model": ",".join(sorted(counter.models | ({meta["stage2"]} if meta.get("stage2_calls") else set()))) or None,
            "failed": error is not None,
            "retried": s2_retried,  # the stage 2 API retried (429/5xx); the retrievers themselves do not retry
            "error": error,
            "search_spans": nodes.spans,  # graph nodes (T2-36); empty for a retriever that is not a graph
            **ids,
        })
        progress(n, len(items), item.qid, started, error)
    return rows
