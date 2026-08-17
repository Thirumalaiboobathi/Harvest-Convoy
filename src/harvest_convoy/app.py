"""AgentCore Runtime entrypoint. See docs/adr/ADR-006-deploy.md Decision 1.

Invoked with a payload like {"cluster_id": "kamatchipuram", "season_id":
"2026-kuruvai"}. EventBridge Scheduler cannot call InvokeAgentRuntime
directly (its universal target can't marshal that API's raw-blob request
body -- see Decision 7); a small Lambda shim (harvest-convoy-watcher-invoker)
sits in between and calls this API via boto3 with the same payload shape.
Delegates immediately to watcher.run_daily_watch() -- there is no
AgentCore-specific logic beyond this thin wrapper.
"""

from __future__ import annotations

import logging

from bedrock_agentcore import BedrockAgentCoreApp

from harvest_convoy.observability.otel import get_tracer
from harvest_convoy.watcher import run_daily_watch

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)

app = BedrockAgentCoreApp()


@app.entrypoint
def handler(payload: dict) -> dict:
    cluster_id = payload.get("cluster_id")
    season_id = payload.get("season_id")
    if not cluster_id or not season_id:
        logger.error("invalid payload, missing cluster_id/season_id: %s", payload)
        return {
            "status": "error",
            "reason": "payload must include cluster_id and season_id",
        }

    force = bool(payload.get("force", False))
    with tracer.start_as_current_span(
        "app.daily_watch",
        attributes={"cluster_id": cluster_id, "season_id": season_id, "force": force},
    ):
        return run_daily_watch(cluster_id, season_id, force=force)


if __name__ == "__main__":
    app.run()
