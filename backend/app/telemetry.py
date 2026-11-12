"""The service's OTel provider (T3-30, KOS-114): every API request leaves one trace under service `kosis-agent`.

Kept apart from evaluation on purpose: evals/runners/agent.py makes its own provider (`kosis-agent-evals`) and never
runs this app's lifespan, and this module never sets the global provider. The only shared piece is the callback
(kosis_agent.observability.tracing). OTLP export only when OTEL_EXPORTER_OTLP_ENDPOINT is set; BatchSpanProcessor
sends from its own thread, so export time is not added to the request.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

SERVICE = "kosis-agent"

_provider: TracerProvider | None = None


def start() -> TracerProvider:
    """Create the service provider (FastAPI lifespan start)."""
    global _provider
    _provider = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    return _provider


def stop() -> None:
    """Flush and close (FastAPI lifespan end)."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


def get_tracer() -> Tracer:
    """FastAPI dependency. Before start() (e.g. TestClient used without `with`) spans are not recorded."""
    # ponytail: no-op tracer outside the lifespan; tests override this dependency with an in-memory provider
    return _provider.get_tracer("kosis-agent") if _provider is not None else trace.NoOpTracer()
