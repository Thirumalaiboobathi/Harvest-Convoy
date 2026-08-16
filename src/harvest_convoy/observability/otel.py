"""OpenTelemetry tracing setup.

Every agent turn and tool call is expected to open a span under the tracer
returned by get_tracer(). Exporter wiring (OTLP endpoint, AgentCore's
observability sink, etc.) is decided in Phase 6; until then this defaults to
a console exporter so traces are visible locally during Phases 1-5.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

_configured = False


def configure_tracing(
    service_name: str = "harvest-convoy",
    exporter: SpanExporter | None = None,
) -> TracerProvider:
    """Configure the global TracerProvider. Safe to call more than once;
    only the first call takes effect."""
    global _configured
    provider = trace.get_tracer_provider()
    if _configured:
        return provider  # type: ignore[return-value]

    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    if exporter is None:
        # SimpleSpanProcessor: synchronous, no background export thread.
        # Fine for local/console output; a real OTLP exporter (Phase 6)
        # should be passed in explicitly and gets BatchSpanProcessor.
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _configured = True
    return provider


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer for the given module/component name. Calls
    configure_tracing() with defaults if tracing hasn't been configured yet,
    so tests and scripts can call this without separate setup."""
    if not _configured:
        configure_tracing()
    return trace.get_tracer(name)
