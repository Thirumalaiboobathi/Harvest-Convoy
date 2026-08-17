"""Scheduled daily watcher: once per cluster per day, refresh weather,
recompute GDD, decide whether the trigger condition is met, and either run
the full scheduling pipeline or log a clean no-op. See ADR-006 Decision 2.

Trigger condition: scheduling/capacity.py's usable_harvest_days() returns
less than the full forecast length -- i.e. rain breaches the threshold
somewhere within the horizon. If it returns the full length (no breach at
all), there is nothing to react to: no message, silence is the product.

Idempotency: a real, persisted marker (Storage.get/set_watcher_last_run),
not in-memory -- written only after a check *completes*, whether it found
a trigger or not. A failed check (Open-Meteo down, an unhandled exception)
does not write the marker, so it gets retried on the next scheduled run
rather than silently skipped for the rest of the day.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from harvest_convoy.agents.coordinator import run_cluster, run_cluster_with_claims
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.capacity import usable_harvest_days
from harvest_convoy.scheduling.solver import PlotDecision, PlotOutcome, solve
from harvest_convoy.storage import Storage, get_storage
from harvest_convoy.telegram import notify, webhook
from harvest_convoy.telegram.client import TelegramClient
from harvest_convoy.weather.openmeteo import (
    WeatherError,
    get_daily_temperatures,
    get_precipitation_forecast,
)

logger = logging.getLogger(__name__)

# DERIVED, tuning constant -- matches the rain threshold used throughout
# the Phase 2/3/5 demo scenarios, not independently re-derived here.
RAIN_THRESHOLD_MM = 5.0
FORECAST_HORIZON_DAYS = 16  # Open-Meteo's confirmed max forecast horizon, ADR-001


def run_daily_watch(
    cluster_id: str,
    season_id: str,
    *,
    storage: Storage | None = None,
    today: date | None = None,
    telegram_client: TelegramClient | None = None,
    get_claim=None,
) -> dict:
    """Entry point for one scheduled invocation. Returns a plain dict
    summary (never raises) -- this is what app.py's AgentCore handler and
    scripts/check_watcher_health.py both consume, and what any fallback
    entrypoint (Lambda) would call identically. No AgentCore-specific
    code in here -- see ADR-006 Decision 1's fallback note.

    get_claim: optional injectable claim provider (see
    agents/coordinator.py:ClaimProvider). None (the default, and what
    every real invocation uses) means the real Bedrock-backed advocates
    via coordinator.run_cluster(). Tests inject a fake here for the same
    reason every other test in this codebase avoids unmarked live Bedrock
    calls -- keeps the default suite hermetic and free.
    """
    storage = storage or get_storage()
    today = today or date.today()
    client = telegram_client or TelegramClient()

    last_run = storage.get_watcher_last_run(cluster_id)
    if last_run == today.isoformat():
        logger.info(
            "watcher no-op: cluster=%s already completed a check today (%s)",
            cluster_id, today,
        )
        return {"cluster_id": cluster_id, "date": today.isoformat(), "status": "already_ran"}

    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        logger.error("watcher: cluster %s not found in storage", cluster_id)
        return {"cluster_id": cluster_id, "status": "error", "reason": "cluster_not_found"}

    plots = storage.get_plots_for_cluster(cluster_id)
    if not plots:
        logger.warning("watcher: cluster %s has no plots, nothing to check", cluster_id)
        storage.set_watcher_last_run(cluster_id, today.isoformat())
        return {"cluster_id": cluster_id, "date": today.isoformat(), "status": "no_plots"}

    try:
        forecast = get_precipitation_forecast(
            cluster.machine_start_lat,
            cluster.machine_start_lon,
            today,
            today + timedelta(days=FORECAST_HORIZON_DAYS - 1),
        )
        plot_days = {
            p.plot_id: get_daily_temperatures(p.lat, p.lon, p.transplant_date, today)
            for p in plots
        }
    except WeatherError as exc:
        logger.error(
            "watcher: weather fetch failed, cluster=%s: %s -- will retry next scheduled run",
            cluster_id, exc,
        )
        return {"cluster_id": cluster_id, "status": "error", "reason": "weather_unavailable"}

    try:
        usable_days = usable_harvest_days(forecast, RAIN_THRESHOLD_MM)
        if usable_days >= len(forecast):
            logger.info(
                "watcher no-op: cluster=%s, no rain in the %d-day forecast -- nothing sent",
                cluster_id, len(forecast),
            )
            storage.set_watcher_last_run(cluster_id, today.isoformat())
            return {"cluster_id": cluster_id, "date": today.isoformat(), "status": "no_trigger"}

        logger.info(
            "watcher triggered: cluster=%s, %d of %d forecast days usable before rain",
            cluster_id, usable_days, len(forecast),
        )
        decisions = solve(
            plots, plot_days, cluster, forecast,
            rain_threshold_mm=RAIN_THRESHOLD_MM, today=today,
        )
        if get_claim is not None:
            result = run_cluster_with_claims(plots, decisions, cluster_id, storage, get_claim)
        else:
            result = run_cluster(plots, decisions, cluster_id, storage)

        farmers_by_id = {f.farmer_id: f for f in storage.get_farmers_for_cluster(cluster_id)}
        plots_by_id = {p.plot_id: p for p in plots}
        decisions_by_id = {d.plot_id: d for d in decisions}
        _send_notifications(
            client, cluster, decisions_by_id, result, farmers_by_id, plots_by_id
        )

        storage.set_watcher_last_run(cluster_id, today.isoformat())
        return {
            "cluster_id": cluster_id,
            "date": today.isoformat(),
            "status": "triggered",
            "usable_days": usable_days,
            "escalations": len(result.escalations),
        }
    except Exception as exc:  # noqa: BLE001 -- never throw from the agent loop
        logger.exception(
            "watcher: cluster=%s run failed mid-pipeline, marker not written "
            "so this will be retried: %s",
            cluster_id, exc,
        )
        return {"cluster_id": cluster_id, "status": "error", "reason": f"{type(exc).__name__}: {exc}"}


def _send_notifications(
    client: TelegramClient,
    cluster: Cluster,
    decisions_by_id: dict[str, PlotDecision],
    result,
    farmers_by_id: dict[str, Farmer],
    plots_by_id: dict[str, Plot],
) -> None:
    fits_route: list[tuple[Farmer, Plot]] = []

    for outcome in result.outcomes:
        plot = plots_by_id.get(outcome.plot_id)
        farmer = farmers_by_id.get(plot.farmer_id) if plot else None
        if plot is None or farmer is None:
            logger.error(
                "watcher: no plot/farmer found for %s, skipping notification",
                outcome.plot_id,
            )
            continue

        if outcome.outcome == PlotOutcome.TOO_GREEN:
            notify.send_not_ready(client, farmer, plot)
        elif outcome.outcome == PlotOutcome.FITS:
            decision = decisions_by_id[outcome.plot_id]
            notify.send_harvest_scheduled(client, farmer, plot, decision.route_position)
            fits_route.append((farmer, plot))
        # CONTESTED plots that resolved without escalating (result.resolved_negotiations)
        # or remain contested get no dedicated message this phase -- there is no
        # "you're contested but not escalated" message type in notify.py's four
        # shapes yet. Not fabricating a fifth one under time pressure; flagged as
        # a real, disclosed scope boundary, not silently dropped.

    if fits_route:
        notify.send_operator_route_summary(
            client, cluster.operator_chat_id, cluster, fits_route
        )

    for escalation in result.escalations:
        webhook.register_escalation(escalation)
        farmer_a = farmers_by_id.get(plots_by_id[escalation.plot_a_id].farmer_id)
        farmer_b = farmers_by_id.get(plots_by_id[escalation.plot_b_id].farmer_id)
        if farmer_a is None or farmer_b is None:
            logger.error(
                "watcher: missing farmer for escalation %s vs %s, not sent",
                escalation.plot_a_id, escalation.plot_b_id,
            )
            continue
        notify.send_escalation(
            client, cluster.operator_chat_id,
            escalation.cluster_id,
            escalation.plot_a_id, farmer_a, plots_by_id[escalation.plot_a_id], escalation.claim_a,
            escalation.plot_b_id, farmer_b, plots_by_id[escalation.plot_b_id], escalation.claim_b,
        )
