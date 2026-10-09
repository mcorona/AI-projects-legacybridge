"""OpenTelemetry setup (ADR-007).

Tracing is **off by default**: without ``LB_TRACING`` no provider is installed, the OpenTelemetry
API is a no-op, and the agent behaves and costs exactly as before. An exporter that cannot reach
its endpoint must never slow down or break a question, so spans leave in batches, on a background
thread, with short timeouts.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

TRACER_NAME = "legacybridge"
DEFAULT_ENDPOINT = "http://localhost:4318"
EXPORT_TIMEOUT_S = 2.0
MAX_CONTENT_CHARS = 2000
_TRUE = {"1", "true", "yes", "on", "otlp"}

_lock = threading.Lock()
_provider: TracerProvider | None = None


def tracing_enabled() -> bool:
    return os.environ.get("LB_TRACING", "").strip().lower() in _TRUE


def capture_content() -> bool:
    """Prompts, SQL and rows go on spans only when asked for (ADR-007)."""
    return os.environ.get("LB_TRACE_CONTENT", "").strip().lower() in _TRUE


def truncate(value: Any, limit: int = MAX_CONTENT_CHARS) -> str:
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def get_tracer() -> trace.Tracer:
    """The agent's tracer: real once a provider is installed, a no-op before."""
    return trace.get_tracer(TRACER_NAME)


def build_provider(
    service_name: str,
    exporter: SpanExporter | None = None,
    processor: SpanProcessor | None = None,
) -> TracerProvider:
    """A provider that exports to ``exporter``, by default OTLP/HTTP to the configured endpoint."""
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if processor is None:
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            base = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/")
            exporter = OTLPSpanExporter(endpoint=f"{base}/v1/traces", timeout=EXPORT_TIMEOUT_S)
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            schedule_delay_millis=1000,
            export_timeout_millis=int(EXPORT_TIMEOUT_S * 1000),
        )
    provider.add_span_processor(processor)
    return provider


def setup_tracing(service_name: str = "legacybridge") -> TracerProvider | None:
    """Install the global provider once, when ``LB_TRACING`` is on. Returns it, or None if off."""
    global _provider
    if not tracing_enabled():
        return None
    with _lock:
        if _provider is None:
            _provider = build_provider(service_name)
            trace.set_tracer_provider(_provider)
            # the exporter logs a warning per failed batch; one line is enough for a local setup
            logging.getLogger("opentelemetry.exporter").setLevel(logging.ERROR)
        return _provider


def shutdown_tracing() -> None:
    """Flush what is queued (called when a short-lived process exits)."""
    global _provider
    with _lock:
        if _provider is not None:
            _provider.shutdown()
            _provider = None
