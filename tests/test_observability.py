from opentelemetry import trace

from harvest_convoy.observability.otel import configure_tracing, get_tracer


def test_get_tracer_returns_usable_tracer() -> None:
    tracer = get_tracer("test")
    with tracer.start_as_current_span("test-span") as span:
        assert span.is_recording()


def test_configure_tracing_is_idempotent() -> None:
    provider_a = configure_tracing()
    provider_b = configure_tracing()
    assert provider_a is provider_b
    assert trace.get_tracer_provider() is provider_a
