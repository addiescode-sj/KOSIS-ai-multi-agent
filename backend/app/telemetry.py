"""The service's OTel provider (T3-30, KOS-114): every API request leaves one trace under service `kosis-agent`.

Kept apart from evaluation on purpose: evals/runners/agent.py makes its own provider (`kosis-agent-evals`) and never
runs this app's lifespan, and this module never sets the global provider. The only shared piece is the callback
(kosis_agent.observability.tracing). OTLP export only when OTEL_EXPORTER_OTLP_ENDPOINT is set; BatchSpanProcessor
sends from its own thread, so export time is not added to the request.

Metrics (T3-31, KOS-115): gen_ai.client.token.usage / gen_ai.client.operation.duration from the same callback. Jaeger
does not take metrics, so OTLP metric export only when OTEL_EXPORTER_OTLP_METRICS_ENDPOINT is set (a collector or
Prometheus OTLP receiver); otherwise the meter is a no-op.
"""

from __future__ import annotations

import os

from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

SERVICE = "kosis-agent"

_provider: TracerProvider | None = None
_meters: MeterProvider | None = None


def start() -> TracerProvider:
    """Create the service provider (FastAPI lifespan start)."""
    global _provider, _meters
    resource = Resource.create({"service.name": SERVICE})
    _provider = TracerProvider(resource=resource)
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if os.environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"):
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter

        _meters = MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
    return _provider


def stop() -> None:
    """Flush and close (FastAPI lifespan end)."""
    global _provider, _meters
    if _provider is not None:
        _provider.shutdown()
        _provider = None
    if _meters is not None:
        _meters.shutdown()
        _meters = None


def get_tracer() -> Tracer:
    """FastAPI dependency. Before start() (e.g. TestClient used without `with`) spans are not recorded."""
    # ponytail: no-op tracer outside the lifespan; tests override this dependency with an in-memory provider
    return _provider.get_tracer("kosis-agent") if _provider is not None else trace.NoOpTracer()


def get_meter() -> Meter:
    """The service meter (no-op unless OTEL_EXPORTER_OTLP_METRICS_ENDPOINT was set at start())."""
    return _meters.get_meter("kosis-agent") if _meters is not None else metrics.NoOpMeter("kosis-agent")
