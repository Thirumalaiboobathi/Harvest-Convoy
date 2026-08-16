"""AgentCore Runtime entrypoint.

Placeholder for Phase 0. The scheduled daily watcher, coordinator/advocate
agent wiring, and AgentCore Runtime handler are built in Phases 3 and 6.
This module currently only proves the observability skeleton is wired.
"""

from __future__ import annotations

from harvest_convoy.observability.otel import get_tracer

tracer = get_tracer(__name__)


def main() -> None:
    with tracer.start_as_current_span("harvest_convoy.app.main"):
        pass


if __name__ == "__main__":
    main()
