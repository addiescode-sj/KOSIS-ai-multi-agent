"""Operational metrics, tracked apart from quality scores: latency, token usage, cost, tool failure rate.

Each run writes ops.json; one aggregate line per run goes to evals/reports/ops_history.jsonl (no question text,
committed) so runs can be compared over time. Token counts come from where each layer can see them:
- L1: a LangChain callback reads usage from every LLM call a retriever makes (query rewrite, rerank)
- L3 live: UsageClient wraps the Phase 1 LLM client and reads LLMResult.usage
- L3 re-grade: the run file's `usage` and the model name in its spans
Cost uses evals/pricing.json (price per 1K tokens per model). A model without a price gets cost None: the report
says the price is missing instead of inventing one.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from evals.schemas import ROOT

PRICING = ROOT / "evals" / "pricing.json"
HISTORY = ROOT / "evals" / "reports" / "ops_history.jsonl"


def tokens_of(usage: dict | None) -> tuple[int, int]:
    """(input, output) from HCX ('promptTokens') or LangChain ('input_tokens', 'prompt_tokens') usage dicts."""
    usage = usage or {}
    pick = lambda *names: next((int(usage[n]) for n in names if usage.get(n) is not None), 0)  # noqa: E731
    return (pick("promptTokens", "input_tokens", "prompt_tokens"), pick("completionTokens", "output_tokens", "completion_tokens"))


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


def usage_client(settings=None):
    """The Phase 1 client from settings, wrapped so each question's tokens and calls can be read and reset."""
    from kosis_chat.config import load_settings
    from kosis_chat.llm.model import LLMClient, build_client

    settings = settings or load_settings()
    inner = build_client(settings)

    class UsageClient(LLMClient):
        name = inner.name
        model = settings.model_name

        def __init__(self) -> None:
            self.reset()

        def reset(self) -> None:
            self.calls, self.input_tokens, self.output_tokens, self.failures = 0, 0, 0, 0

        def record_event(self, event: dict[str, Any]) -> None:
            inner.record_event(event)

        def generate(self, system: str, user: str):
            result = inner.generate(system, user)
            i, o = tokens_of(result.usage)
            self.calls += 1
            self.input_tokens += i
            self.output_tokens += o
            self.failures += result.status != "ok"
            return result

    return UsageClient()


def load_pricing(path: Path = PRICING) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("models", {}) if path.exists() else {}


def cost(model: str | None, input_tokens: int, output_tokens: int, pricing: dict[str, dict]) -> float | None:
    price = pricing.get(model or "")
    if not price or price.get("input_per_1k") is None or price.get("output_per_1k") is None:
        return None
    return input_tokens / 1000 * price["input_per_1k"] + output_tokens / 1000 * price["output_per_1k"]


def percentile(values: list[float], q: int) -> float:
    if not values:
        return 0.0
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1] if len(values) > 1 else values[0]


def summarize(rows: list[dict], pricing: dict[str, dict] | None = None) -> dict:
    """rows carry latency_ms, input_tokens, output_tokens, llm_calls, model, failed, retried (per tool call)."""
    pricing = load_pricing() if pricing is None else pricing
    latency = [r["latency_ms"] for r in rows]
    models = sorted({r["model"] for r in rows if r.get("model")})
    tin = sum(r.get("input_tokens", 0) for r in rows)
    tout = sum(r.get("output_tokens", 0) for r in rows)
    costs = [cost(r.get("model"), r.get("input_tokens", 0), r.get("output_tokens", 0), pricing) for r in rows if r.get("input_tokens") or r.get("output_tokens")]
    priced = [c for c in costs if c is not None]
    calls = len(rows)
    return {
        "tool_calls": calls,
        "latency_ms": {"p50": percentile(latency, 50), "p95": percentile(latency, 95), "mean": statistics.fmean(latency) if latency else 0.0},
        "llm_calls": sum(r.get("llm_calls", 0) for r in rows),
        "tokens": {"input": tin, "output": tout, "per_call_mean": (tin + tout) / calls if calls else 0.0},
        "models": models,
        "cost": {"total": sum(priced) if priced and len(priced) == len(costs) else None,
                 "currency": next((pricing[m].get("currency") for m in models if m in pricing), None),
                 "unpriced_models": [m for m in models if cost(m, 1, 1, pricing) is None]},
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
