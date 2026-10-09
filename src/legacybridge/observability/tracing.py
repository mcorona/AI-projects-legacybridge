"""OpenTelemetry setup (ADR-007).

Tracing is **off by default**: without ``LB_TRACING`` no provider is installed, the OpenTelemetry
API is a no-op, and the agent behaves and costs exactly as before. An exporter that cannot reach
its endpoint must never slow down or break a question, so spans leave in batches, on a background
thread, with short timeouts.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.id_generator import IdGenerator

TRACER_NAME = "legacybridge"
DEFAULT_ENDPOINT = "http://localhost:4318"
EXPORT_TIMEOUT_S = 2.0
XRAY_EXPORT_TIMEOUT_S = 5.0  # a signed HTTPS call from a function is slower than a local one
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


def trace_backend() -> str:
    """``xray`` sends spans to the AWS X-Ray OTLP endpoint; anything else is OTLP to a Collector."""
    return "xray" if os.environ.get("LB_TRACE_BACKEND", "").strip().lower() == "xray" else "otlp"


class XRayIdGenerator(IdGenerator):
    """Trace ids that X-Ray accepts: the first four bytes are the start time in epoch seconds
    (the SDK's default is fully random, and X-Ray rejects ids whose time is not recent)."""

    def generate_span_id(self) -> int:
        return random.getrandbits(64) or 1

    def generate_trace_id(self) -> int:
        return (int(time.time()) << 96) | random.getrandbits(96)


def get_tracer() -> trace.Tracer:
    """The agent's tracer: real once a provider is installed, a no-op before."""
    return trace.get_tracer(TRACER_NAME)


def build_provider(
    service_name: str,
    exporter: SpanExporter | None = None,
    processor: SpanProcessor | None = None,
) -> TracerProvider:
    """A provider that exports to ``exporter``, by default OTLP/HTTP to the configured endpoint."""
    xray = trace_backend() == "xray"
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        id_generator=XRayIdGenerator() if xray else None,
    )
    if processor is None:
        if exporter is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            if xray:
                from legacybridge.aws.xray import signed_session, xray_endpoint

                region = os.environ.get("AWS_REGION", "").strip() or "us-east-1"
                profile = os.environ.get("AWS_PROFILE", "").strip() or None
                exporter = OTLPSpanExporter(
                    endpoint=xray_endpoint(region),
                    session=signed_session(region, profile=profile),
                    timeout=XRAY_EXPORT_TIMEOUT_S,
                )
            else:
                base = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/")
                exporter = OTLPSpanExporter(endpoint=f"{base}/v1/traces", timeout=EXPORT_TIMEOUT_S)
        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            schedule_delay_millis=1000,
            export_timeout_millis=int((XRAY_EXPORT_TIMEOUT_S if xray else EXPORT_TIMEOUT_S) * 1000),
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


def flush_tracing(timeout_ms: int = 5000) -> None:
    """Send what is queued but keep the provider (a warm Lambda environment answers again)."""
    with _lock:
        if _provider is not None:
            _provider.force_flush(timeout_ms)


def shutdown_tracing() -> None:
    """Flush what is queued (called when a short-lived process exits)."""
    global _provider
    with _lock:
        if _provider is not None:
            _provider.shutdown()
            _provider = None
