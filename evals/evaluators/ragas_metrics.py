"""Ragas judge metrics over saved e2e rows (plans/T3-19.md): samples built by code, scores by an LLM judge.

Four Ragas metrics plus two deterministic cross-checks, all on rows an e2e run already saved (no graph re-run):

    faithfulness          generation  answer text vs what it stands on: the facts pandas computed + the used table text
    answer_relevancy      generation  Ragas "Response Relevancy": answer text vs question (judge + embeddings)
    context_precision     retrieval   top-k table texts in rank order vs a reference built from the golden set
    context_recall        retrieval   same contexts and reference
    id_context_precision  retrieval   code: average precision of the gold table keys in the top k (same formula as the
                                      LLM context precision, relevance from ids instead of a judge)
    id_context_recall     retrieval   code: 1 when a gold key is in the top k (calculation questions need one table)

The reference is code, never an LLM: the gold table's survey and name plus the periods, breakdowns and items of the
golden facts (no numbers, no units: the table text has neither for sure). Rows without an answer (refused, clarify,
error) get no generation scores and rows without a gold table get no retrieval scores; the task reports how many.

ragas is an optional group (`uv sync --group embed --group ragas`); only RagasScorer imports it, so offline tests and
CI never need it.
"""

from __future__ import annotations

import asyncio
import contextvars
import math
import re
import sys
import time
import types
from pathlib import Path
from typing import Any, Callable

from evals import trace

RAGAS_METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
ID_METRICS = ("id_context_precision", "id_context_recall")
METRICS = RAGAS_METRICS + ID_METRICS
RETRIEVAL = {"context_precision", "context_recall", "id_context_precision", "id_context_recall"}
CONTEXT_FORMAT = "n2-L60:bm25_text:2000"  # table text format and per-context cap: part of the quality gate key
MAX_CONTEXT_CHARS = 2000  # ponytail: a few tables have 16k chars of labels; the head holds name, survey, period, items
# {model: {"input": n, "output": n}} of the judge job running in this context (T3-50): an httpx hook on the judge's
# OpenAI client adds each response's usage, so a disk-cache hit costs 0 tokens, as it does on the bill
_JOB_USAGE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("judge_usage", default=None)


async def record_usage(response) -> None:
    """httpx response hook: chat (prompt/completion_tokens) and embeddings (prompt_tokens) usage, per model."""
    usage = _JOB_USAGE.get()
    if usage is None or response.status_code != 200:
        return
    await response.aread()
    try:
        body = response.json()
    except ValueError:
        return
    u = body.get("usage") or {}
    t = usage.setdefault(body.get("model") or "unknown", {"input": 0, "output": 0})
    t["input"] += int(u.get("prompt_tokens") or 0)
    t["output"] += int(u.get("completion_tokens") or 0)


RETRIES = 2  # judge call retries after an error (2026-11-17: 3 APITimeoutError in one 90-question run)
LOW = 0.5  # a score below this is a "low" row the report attributes to a graph node
TEXT_NODES = ("fallback_chatbot", "compose_llm", "template", "answer_final")  # phase 1 spans that write the answer text
TOTALS = {"계", "총계", "합계", "전체", "소계"}
UNIT_TAIL = re.compile(r"\s*\([^()]*\)\s*$")  # "출생건수 (명)" -> "출생건수"
SOURCE_LINE = re.compile(r"\n+\s*출처:.*$", re.S)


# --- samples (pure, offline) ---------------------------------------------------------------------------------------

def number(value: int | float | None, digits: int | None = None) -> str:
    if value is None:
        return "값 없음"
    if digits is not None:
        return f"{value:,.{digits}f}"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.4f}".rstrip("0").rstrip(".")


def fact_lines(answer: Any) -> list[str]:
    """One line per fact the answer stands on: `f1: 2025 · 시도별=경기도 = 76,309 명 (f2 / f1 - 1) * 100`."""
    lines = []
    for f in answer.result.facts:
        where = " · ".join([*([f.period] if f.period else []), *(f"{k}={v}" for k, v in (f.dims or {}).items())])
        value = number(f.value, f.round) if f.value is not None else (f.raw or "값 없음")
        lines.append(f"{f.id}: {where} = {value} {f.unit or ''}".rstrip()
                     + (f" (계산식 {f.derivation})" if f.derivation else "") + (f" ({f.note})" if f.note else ""))
    return lines


def source_line(answer: Any) -> str | None:
    s = answer.source
    return f"출처: KOSIS / {s.survey_nm} / {s.tbl_nm}" if s and s.survey_nm != "-" else None


def reference(gold_keys: list[str], facts: list, texts: dict[str, dict]) -> str:
    """What retrieval must bring back, from the golden set by code: the gold table(s) and the periods, breakdowns and
    items its facts need, without numbers or units."""
    tables = [f"{texts[k]['survey']}의 「{texts[k]['name']}」" for k in gold_keys if k in texts] or gold_keys
    head = (f"이 질문에 답하려면 {tables[0]} 통계표가 필요하다." if len(tables) == 1
            else f"이 질문에 답하려면 다음 통계표 중 하나가 필요하다: {', '.join(tables)}.")
    periods = sorted({f.period for f in facts if f.period})
    # values only: the breakdown names belong to the representative gold table ("행정구역별"), an alternate gold table
    # names the same thing differently ("시도별"), and totals ("계") say nothing about which table it is
    values = list(dict.fromkeys(v for f in facts for v in map(lambda x: UNIT_TAIL.sub("", x), (f.dims or {}).values())
                                if v and v not in TOTALS))
    parts = [head]
    if periods:
        parts.append(f"그 표는 {', '.join(periods)} 기간을 수록해야 한다.")
    if values:
        parts.append(f"그 표에는 {', '.join(values)}에 해당하는 분류나 항목이 있어야 한다.")
    return " ".join(parts)


def table_texts(keys: set[str], tables: Path) -> dict[str, dict]:
    """key -> {"text", "name", "survey"} for the keys asked, from tables.jsonl through the phase 2 table text
    (the full bm25_text, capped: embed_text drops units and region tails a reference may name)."""
    from kosis_rag.adapters.files import read_records
    from kosis_rag.shared.tabletext import build_doc

    out = {}
    for r in read_records(tables):
        if r.info.key in keys:
            out[r.info.key] = {"text": build_doc(r, "n2-L60")["bm25_text"][:MAX_CONTEXT_CHARS],
                               "name": r.info.table_name, "survey": r.info.survey_name}
    return out


def answered(row: dict) -> bool:
    a = row.get("answer")
    return a is not None and a.outcome == "answered" and bool(a.text.strip())


def no_contexts(row: dict, facts: list) -> bool:
    """A retrieval target whose run brought back no table at all (a run error, a gate refusal before ranking): the
    context metrics are 0 by definition, without a judge (Ragas refuses empty contexts), as the id metrics are."""
    return bool(row.get("gold_keys")) and bool(facts) and not row["keys"]


def samples(row: dict, facts: list, texts: dict[str, dict], k: int) -> dict[str, dict | None]:
    """metric -> the keyword arguments of its Ragas ascore, or None when the row is not a target of that metric.
    row: an e2e row as evals.runners.stat.read_rows gives it plus gold_keys (answer is a StatAnswer)."""
    out: dict[str, dict | None] = dict.fromkeys(RAGAS_METRICS)
    question, gold = row["question"], list(row.get("gold_keys") or [])
    if answered(row):
        a = row["answer"]
        grounds = fact_lines(a) + [x for x in (source_line(a),) if x]
        used = texts.get(row.get("used_table") or "")
        out["faithfulness"] = {"user_input": question, "response": a.text,
                               "retrieved_contexts": ["\n".join(grounds), *([used["text"]] if used else [])]}
        out["answer_relevancy"] = {"user_input": question, "response": SOURCE_LINE.sub("", a.text).strip() or a.text}
    keys = row["keys"][:k]
    if gold and keys and facts:
        contexts = [texts[key]["text"] for key in keys if key in texts]
        ref = reference(gold, facts, texts)
        out["context_precision"] = {"user_input": question, "reference": ref, "retrieved_contexts": contexts}
        out["context_recall"] = {"user_input": question, "retrieved_contexts": contexts, "reference": ref}
    return out


def id_scores(keys: list[str], gold: set[str], k: int) -> dict[str, float | None]:
    """Deterministic retrieval scores; None without a gold table."""
    if not gold:
        return dict.fromkeys(ID_METRICS)
    top = keys[:k]
    hits = [i for i, key in enumerate(top, 1) if key in gold]
    ap = sum(sum(1 for key in top[:i] if key in gold) / i for i in hits) / len(hits) if hits else 0.0
    return {"id_context_precision": ap, "id_context_recall": 1.0 if hits else 0.0}


# --- graph node attribution ----------------------------------------------------------------------------------------

def node_for(metric: str, row: dict, k: int) -> str:
    """The LangGraph node (graph node / inner span) a low score of `metric` points at, from the row's spans.

    retrieval metrics -> search, inside it the search node that left the gold table out of the top (trace.lost_at);
    faithfulness -> statistics, inside it the phase 1 span that wrote the text; answer_relevancy -> search when the
    answer came from a non-gold table, else the same text span."""
    gold = set(row.get("gold_keys") or [])
    search_inner = lambda kk: (trace.lost_at(row.get("search_spans") or [], gold, kk) or ("search_tables",))[0]  # noqa: E731
    if metric in RETRIEVAL:
        return f"search/{search_inner(k if metric.endswith('recall') else 1)}"
    if metric == "answer_relevancy" and gold and row.get("used_table") not in gold:
        return f"search/{search_inner(1)}"
    names = {s["name"] for s in row.get("trace_spans") or []} | {s["name"] for s in row.get("spans") or []}
    return f"statistics/{next((n for n in TEXT_NODES if n in names), 'summarize_stat')}"


def node_ms(row: dict, node: str) -> float | None:
    """Duration of a graph node (the part before '/') in the row's trace."""
    top = node.split("/")[0]
    return next((s["duration_ms"] for s in row.get("trace_spans") or [] if s["name"] == top and s.get("depth") == 1), None)


# --- judge -------------------------------------------------------------------------------------------------------

def load_ragas():
    """Import ragas 0.4.3. Its ragas.llms.base imports langchain_community.chat_models.vertexai, which
    langchain-community 0.4 removed (and 0.3 needs langchain-core < 1, LangGraph needs >= 1.7)."""
    name = "langchain_community.chat_models.vertexai"
    # ponytail: stub module for an import ragas only uses in isinstance checks; delete once ragas drops the import
    if name not in sys.modules:
        try:
            __import__(name)
        except ImportError:
            stub = types.ModuleType(name)
            stub.ChatVertexAI = type("ChatVertexAI", (), {})
            sys.modules[name] = stub
    try:
        import ragas
    except ImportError as e:
        raise SystemExit("ragas layer: run `uv sync --group embed --group ragas` first") from e
    return ragas


class RagasScorer:
    """The four Ragas metrics on one judge (OpenAI chat model, not a pipeline model) with a disk cache."""

    def __init__(self, model: str, embed_model: str, cache_dir: Path | None, concurrency: int = 16):
        ragas = load_ragas()
        from openai import AsyncOpenAI
        from ragas.cache import DiskCacheBackend
        from ragas.embeddings import OpenAIEmbeddings
        from ragas.llms import llm_factory
        from ragas.metrics.collections import AnswerRelevancy, ContextPrecisionWithReference, ContextRecall, Faithfulness

        self.version = ragas.__version__
        cache = DiskCacheBackend(str(cache_dir)) if cache_dir else None
        from openai import DefaultAsyncHttpxClient

        # the SDK's own defaults (timeout, limits) plus the usage hook
        client = AsyncOpenAI(http_client=DefaultAsyncHttpxClient(event_hooks={"response": [record_usage]}))
        # max_tokens: faithfulness lists every claim of a long answer; the default cut one off (IncompleteOutputException)
        llm = llm_factory(model, client=client, cache=cache, temperature=0, max_tokens=8192)
        embeddings = OpenAIEmbeddings(client=client, model=embed_model, cache=cache)
        self.metrics = {"faithfulness": Faithfulness(llm=llm), "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
                        "context_precision": ContextPrecisionWithReference(llm=llm), "context_recall": ContextRecall(llm=llm)}
        self.concurrency = concurrency

    async def _one(self, sem: asyncio.Semaphore, metric: str, kwargs: dict) -> tuple[float | None, str | None, float, dict]:
        async with sem:
            usage: dict = {}
            _JOB_USAGE.set(usage)  # this gather task's own context: concurrent jobs do not mix
            t0 = time.perf_counter()
            error = None
            for attempt in range(RETRIES + 1):  # transient API errors (timeouts) must not move the gated means
                try:
                    value = (await self.metrics[metric].ascore(**kwargs)).value
                    value = None if value is None or (isinstance(value, float) and math.isnan(value)) else float(value)
                    return value, None, (time.perf_counter() - t0) * 1000, usage
                except Exception as e:  # one failed judgement is a None score, not a failed run
                    error = f"{type(e).__name__}: {e}"[:300]
                    if attempt < RETRIES:
                        await asyncio.sleep(2 ** attempt)
            return None, error, (time.perf_counter() - t0) * 1000, usage

    async def ascore_all(self, jobs: list[tuple[int, str, dict]]) -> list[tuple[float | None, str | None, float, dict]]:
        sem = asyncio.Semaphore(self.concurrency)
        return await asyncio.gather(*(self._one(sem, metric, kwargs) for _, metric, kwargs in jobs))

    def score(self, jobs: list[tuple[int, str, dict]]) -> list[tuple[float | None, str | None, float]]:
        """(row index, metric, ascore kwargs) -> (score or None, error or None, ms, judge tokens by model), in order."""
        return asyncio.run(self.ascore_all(jobs))


# a scorer may leave out the 4th item (judge tokens by model); tests' fake judges do
Scorer = Callable[[list[tuple[int, str, dict]]], list[tuple]]
