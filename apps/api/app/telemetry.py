"""OpenTelemetry tracing and structlog JSON logging."""

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import get_settings
from app.context import request_id_var

_configured = False


def _add_context(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    request_id = request_id_var.get()
    if request_id != "-":
        event_dict["request_id"] = request_id
    return event_dict


def setup_logging() -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_context,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def setup_telemetry(service_name: str) -> None:
    """Configure logging and (unless OTEL_ENABLED=false) the global tracer provider."""
    global _configured
    if _configured:
        return
    _configured = True
    setup_logging()
    settings = get_settings()
    if not settings.otel_enabled:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)

    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

    from app.db import instrument_engine

    instrument_engine()
    AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]


def instrument_app(app: object) -> None:
    if not get_settings().otel_enabled:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
