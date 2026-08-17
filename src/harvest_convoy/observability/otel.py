"""OpenTelemetry tracing setup.

Every agent turn and tool call is expected to open a span under the tracer
returned by get_tracer(). Locally, this defaults to a synchronous console
exporter. Deployed on AgentCore Runtime (AGENT_OBSERVABILITY_ENABLED=true,
set as an environment variable on the Runtime resource, not in this repo),
it delegates to AWS's own OTel distro configurator instead -- see
docs/adr/ADR-006-deploy.md Decision 5 and the deployment postmortem in the
phase report: the documented `opentelemetry-instrument` CLI launcher
approach failed in practice (`CREATE_FAILED`,
"OpenTelemetry instrumentation executable not found") because pip/uv
generates that console-script's executable in the HOST platform's format
(a Windows .exe on the machine that built the deployment zip), not the
target arm64 Linux runtime's -- a real, verified finding, not a
theoretical concern. Calling AwsOpenTelemetryConfigurator().configure()
directly in Python does the identical setup without depending on a
platform-specific launcher shim.
"""

from __future__ import annotations

import logging
import os
import socket

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
_diag_logger = logging.getLogger(__name__)


def _log_local_collector_reachability() -> None:
    """One-time diagnostic: is anything listening on the OTLP default port?
    Deployed AgentCore Runtime invocations were observed exporting spans to
    localhost:4318 and getting Connection refused -- this pins down whether
    that's because no local collector is present at all (a platform/mode
    limitation worth disclosing) versus some other export misconfiguration.
    """
    for port in (4318, 4317):
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                _diag_logger.warning("otel diagnostic: localhost:%d is reachable", port)
        except OSError as exc:
            _diag_logger.warning("otel diagnostic: localhost:%d unreachable (%s)", port, exc)


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

    if os.environ.get("AGENT_OBSERVABILITY_ENABLED", "").lower() == "true":
        _log_local_collector_reachability()
        from amazon.opentelemetry.distro.aws_opentelemetry_configurator import (
            AwsOpenTelemetryConfigurator,
        )

        AwsOpenTelemetryConfigurator().configure()
        _configured = True
        return trace.get_tracer_provider()  # type: ignore[return-value]

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
