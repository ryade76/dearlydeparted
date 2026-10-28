"""OpenTelemetry tracing setup for v2 agent."""

from __future__ import annotations

import logging
import os

log = logging.getLogger("v2.tracing")

_tracer = None
_initialized = False
_langfuse_client = None


def init_tracing(service_name: str = "dearly-departed-v2") -> None:
    """Initialize OpenTelemetry tracing. Safe to call if OTel packages not installed."""
    global _tracer, _initialized, _langfuse_client

    if _initialized:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        # Try OTLP exporter (for Langfuse)
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                provider.add_span_processor(SimpleSpanProcessor(exporter))
                log.info("OTLP exporter configured: %s", otlp_endpoint)
            except Exception as exc:
                log.warning("OTLP exporter setup failed: %s", exc)

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)

        # FastAPI auto-instrumentation
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument()
            log.info("FastAPI auto-instrumentation enabled")
        except Exception as exc:
            log.info("FastAPI auto-instrumentation not available: %s", exc)

        # Langfuse client init
        try:
            from services.v2 import config
            if config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY:
                from langfuse import Langfuse
                _langfuse_client = Langfuse(
                    public_key=config.LANGFUSE_PUBLIC_KEY,
                    secret_key=config.LANGFUSE_SECRET_KEY,
                    host=config.LANGFUSE_BASE_URL,
                )
                log.info("Langfuse client initialized at %s", config.LANGFUSE_BASE_URL)
        except Exception as exc:
            log.info("Langfuse init not available: %s", exc)

        _initialized = True
        log.info("OpenTelemetry tracing initialized for %s", service_name)

    except ImportError:
        log.info("OpenTelemetry packages not installed — tracing disabled")
        _initialized = True


def get_tracer():
    """Get the OTel tracer, or None if not initialized."""
    return _tracer


def get_langfuse():
    """Get the Langfuse client, or None if not configured."""
    return _langfuse_client


def langfuse_observe(name: str):
    """Wrap async handler with Langfuse @observe if configured."""
    try:
        from langfuse.decorators import observe
        return observe(name=name)
    except (ImportError, Exception):
        def noop(fn):
            return fn
        return noop
