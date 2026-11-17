"""Operational metrics, tracked apart from quality scores: latency, token usage, cost, tool failure rate.

Each run writes ops.json; one aggregate line per run goes to evals/reports/ops_history.jsonl (no question text,
committed) so runs can be compared over time. Token counts come from where each layer can see them:
- L1: a LangChain callback reads usage from every LLM call a retriever makes, plus the rerank2 record (stage2_*)
- l3 / e2e / agent: the chat spans of the agent turn (runners.agent.turn_record, T3-31/32), per model
- re-grade: the saved rows' token fields
Cost uses evals/pricing.json (price per 1K tokens per model) over each row's tokens_by_model, so a turn that calls
two models (follow-up reader + phase 1) is priced per model. A model without a price, or two currencies in one
run, gives cost None: the report says the price is missing instead of inventing one.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler

from evals.schemas import ROOT

PRICING = ROOT / "evals" / "pricing.json"
HISTORY = ROOT / "evals" / "reports" / "ops_history.jsonl"


def tokens_of(usage: dict | None) -> tuple[int, int]:
    """(input, output) from HCX ('promptTokens') or LangChain ('input_tokens', 'prompt_tokens') usage dicts."""
    usage = usage or {}
    pick = lambda *names: next((int(usage[n]) for n in names if usage.get(n) is not None), 0)  # noqa: E731
    return (pick("promptTokens", "input_tokens", "prompt_tokens"), pick("completionTokens", "output_tokens", "completion_tokens"))


def stage2_usage(meta: dict | None) -> tuple[int, int, int, bool]:
    """(calls, input, output, retried) of the phase 2 rerank2 call recorded on the top document's metadata.

    Same rule as the agent layer (kosis_agent.observability.tracing, T3-31): one rerank2 run is one logical LLM call;
    stage2_calls counts HTTP attempts (429/5xx retries), so it only sets `retried`. Unknown tokens (CLOVA reranker
    may give no usage) count as 0.
    """
    meta = meta or {}
    attempts = int(meta.get("stage2_calls") or 0)
    return (1 if attempts else 0, int(meta.get("stage2_input_tokens") or 0), int(meta.get("stage2_output_tokens") or 0),
            attempts > 1)


class UsageCallback(BaseCallbackHandler):
    """Counts LLM calls and tokens made inside one retriever.invoke."""

    def __init__(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.models: set[str] = set()

    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        self.calls += 1

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        self.calls += 1

    def on_llm_end(self, response, **kwargs) -> None:
        usage = (response.llm_output or {}).get("token_usage")
        if not usage:
            for gens in response.generations:
                for g in gens:
                    usage = getattr(getattr(g, "message", None), "usage_metadata", None) or usage
        i, o = tokens_of(usage)
        self.input_tokens += i
        self.output_tokens += o
        if model := (response.llm_output or {}).get("model_name"):
            self.models.add(model)


def load_pricing(path: Path = PRICING) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("models", {}) if path.exists() else {}


def cost(model: str | None, input_tokens: int, output_tokens: int, pricing: dict[str, dict]) -> float | None:
    price = pricing.get(model or "")
    if not price or price.get("input_per_1k") is None or price.get("output_per_1k") is None:
        return None
    return input_tokens / 1000 * price["input_per_1k"] + output_tokens / 1000 * price["output_per_1k"]


def tokens_by_model(row: dict) -> dict[str, dict[str, int]]:
    """{model: {"input": n, "output": n}} of one row. Rows written since T3-35 carry it (a turn calls several models:
    the follow-up reader on gpt-4o-mini and phase 1 on HCX); an older row with one model is read from its totals,
    and one with several models joined by ',' cannot be split, so it stays unpriced."""
    if row.get("tokens_by_model"):
        return row["tokens_by_model"]
    model = row.get("model")
    if not model or "," in model:
        return {}
    return {model: {"input": row.get("input_tokens", 0), "output": row.get("output_tokens", 0)}}


def row_cost(row: dict, pricing: dict[str, dict]) -> tuple[float | None, str | None]:
    """(cost, currency) of one row over its models; None when any model is unpriced or the currencies differ."""
    by_model = tokens_by_model(row)
    if not by_model and (row.get("input_tokens") or row.get("output_tokens")):
        return None, None
    total, currency = 0.0, None
    for model, t in by_model.items():
        c = cost(model, t["input"], t["output"], pricing)
        if c is None:
            return None, None
        cur = pricing[model].get("currency")
        if currency is not None and cur != currency:
            return None, None  # KRW and USD in one turn: not added up
        total, currency = total + c, cur
    return total, currency


def percentile(values: list[float], q: int) -> float:
    if not values:
        return 0.0
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1] if len(values) > 1 else values[0]


def summarize(rows: list[dict], pricing: dict[str, dict] | None = None) -> dict:
    """rows carry latency_ms, input_tokens, output_tokens, llm_calls, model, failed, retried (per tool call)."""
    pricing = load_pricing() if pricing is None else pricing
    latency = [r["latency_ms"] for r in rows]
    # one model per name: a row's "model" may join several ("HCX-DASH-002,gpt-4o-mini"), tokens_by_model splits them
    models = sorted({m for r in rows for m in (tokens_by_model(r) or {m: {} for m in (r.get("model") or "").split(",") if m})})
    tin = sum(r.get("input_tokens", 0) for r in rows)
    tout = sum(r.get("output_tokens", 0) for r in rows)
    costs = [row_cost(r, pricing) for r in rows if r.get("input_tokens") or r.get("output_tokens")]
    priced = [c for c, _ in costs if c is not None]
    currencies = {cur for _, cur in costs if cur}
    calls = len(rows)
    return {
        "tool_calls": calls,
        "latency_ms": {"p50": percentile(latency, 50), "p95": percentile(latency, 95), "mean": statistics.fmean(latency) if latency else 0.0},
        "llm_calls": sum(r.get("llm_calls", 0) for r in rows),
        "tokens": {"input": tin, "output": tout, "per_call_mean": (tin + tout) / calls if calls else 0.0},
        "models": models,
        "cost": {"total": sum(priced) if priced and len(priced) == len(costs) and len(currencies) == 1 else None,
                 "currency": next(iter(currencies)) if len(currencies) == 1 else None,
                 "unpriced_models": [m for m in models if cost(m, 1, 1, pricing) is None],
                 "by_model": {m: {"input": sum(tokens_by_model(r).get(m, {}).get("input", 0) for r in rows),
                                  "output": sum(tokens_by_model(r).get(m, {}).get("output", 0) for r in rows),
                                  "cost": cost(m, sum(tokens_by_model(r).get(m, {}).get("input", 0) for r in rows),
                                               sum(tokens_by_model(r).get(m, {}).get("output", 0) for r in rows), pricing)}
                              for m in models}},
        "tool_failure_rate": sum(1 for r in rows if r.get("failed")) / calls if calls else 0.0,
        "retry_rate": sum(1 for r in rows if r.get("retried")) / calls if calls else 0.0,
    }


def append_history(run_id: str, layer: str, stage: str, target: str, ops: dict, path: Path = HISTORY) -> None:
    line = {"run_id": run_id, "layer": layer, "stage": stage, "target": target,
            "latency_p50_ms": ops["latency_ms"]["p50"], "latency_p95_ms": ops["latency_ms"]["p95"],
            "input_tokens": ops["tokens"]["input"], "output_tokens": ops["tokens"]["output"],
            "cost": ops["cost"]["total"], "currency": ops["cost"]["currency"],
            "tool_failure_rate": ops["tool_failure_rate"], "retry_rate": ops["retry_rate"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def read_history(path: Path = HISTORY) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []
