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
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from harvest_convoy.agents.contracts import TriggerContext
from harvest_convoy.agents.coordinator import run_cluster, run_cluster_with_claims
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.capacity import (
    ForecastDay,
    harvest_day_budget_acres,
    usable_harvest_days,
)
from harvest_convoy.scheduling.solver import PlotDecision, PlotOutcome, resolve_maturity_gdd, solve
from harvest_convoy.storage import Storage, get_storage
from harvest_convoy.storage.interface import HarvestConfirmation, SeasonRolloverPrompt
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

# How long to wait for a farmer's harvest-confirmation reply before this
# project stops expecting one. This is an unsourced judgment call, not a
# derived value -- there is no data behind "2," only the assumption that
# a farmer plausibly doesn't open Telegram every single day. Per explicit
# instruction (ADR-009 Part 2), silence past this window is recorded as
# UNKNOWN, never as a no-show -- it gates only how a still-unanswered
# HarvestConfirmation is *reported* (see confirmation_status() below),
# never a fairness ledger write. Change freely; nothing depends on the
# exact number.
UNCONFIRMED_HARVEST_WINDOW_DAYS = 2

# Post-harvest drying window (ADR-009 Part 4): how many days after a
# CONFIRMED harvest (not merely a scheduled one) the daily watcher keeps
# watching for rain in the near-term forecast. The number itself ("4
# days", paddy dropping from ~20-24% moisture off the combine to the
# ~14% a DPC requires) came from you, not independently re-derived here
# -- same status as UNCONFIRMED_HARVEST_WINDOW_DAYS: a stated constant,
# not a sourced one.
DRYING_WINDOW_DAYS = 4


def run_daily_watch(
    cluster_id: str | list[str],
    season_id: str,
    *,
    storage: Storage | None = None,
    today: date | None = None,
    telegram_client: TelegramClient | None = None,
    get_claim=None,
    force: bool = False,
) -> dict | list[dict]:
    """Entry point for one scheduled invocation. Returns a plain dict
    summary (never raises) -- this is what app.py's AgentCore handler and
    scripts/check_watcher_health.py both consume, and what any fallback
    entrypoint (Lambda) would call identically. No AgentCore-specific
    code in here -- see ADR-006 Decision 1's fallback note.

    cluster_id: a single cluster_id (str) reproduces the exact existing
    behavior -- one summary dict, the same shape this has always
    returned. This is what the deployed EventBridge schedule / Lambda
    shim / app.py handler send today and continue to send unchanged --
    see ADR-008 Decision 4. Pass a list[str] to check multiple clusters
    in one call: each cluster runs independently through
    _run_daily_watch_one() below (including its own top-level
    try/except), so one cluster's failure never blocks another's, and a
    list of summary dicts is returned in the same order.

    get_claim: optional injectable claim provider (see
    agents/coordinator.py:ClaimProvider). None (the default, and what
    every real invocation uses) means the real Bedrock-backed advocates
    via coordinator.run_cluster(). Tests inject a fake here for the same
    reason every other test in this codebase avoids unmarked live Bedrock
    calls -- keeps the default suite hermetic and free.

    force: skip the rain-trigger gate and run the full pipeline (real
    weather, real GDD, real coordinator negotiation) regardless of
    whether a reschedule is actually warranted today. Off by default --
    every scheduled run uses real trigger logic. For on-demand health
    checks and deployment verification (scripts/check_watcher_health.py,
    manual invokes) where the point is to exercise the negotiation path
    itself, not to wait for a rainy forecast.
    """
    storage = storage or get_storage()
    today = today or date.today()
    client = telegram_client or TelegramClient()

    if isinstance(cluster_id, list):
        return [
            _run_daily_watch_one(
                cid, season_id, storage=storage, today=today,
                telegram_client=client, get_claim=get_claim, force=force,
            )
            for cid in cluster_id
        ]
    return _run_daily_watch_one(
        cluster_id, season_id, storage=storage, today=today,
        telegram_client=client, get_claim=get_claim, force=force,
    )


def _run_daily_watch_one(
    cluster_id: str,
    season_id: str,
    *,
    storage: Storage,
    today: date,
    telegram_client: TelegramClient,
    get_claim=None,
    force: bool = False,
) -> dict:
    """One cluster's check -- the exact body run_daily_watch() had before
    ADR-008 Decision 4 added multi-cluster iteration. storage/today/client
    are already resolved by the caller, not re-resolved here."""
    client = telegram_client

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

    # Season-participation exclusion (ADR-011 Part 1): a plot whose
    # farmer declined a rollover prompt, or never replied to one, is
    # excluded from this season's scheduling pool entirely -- not merely
    # ranked last. A plot with no rollover-prompt record at all for this
    # season_id is included by default (a brand-new registration, or a
    # cluster/season run_season_rollover has never been triggered for).
    rollover_prompts = storage.get_season_rollover_prompts_for_cluster(cluster_id, season_id)
    excluded_by_rollover = {p.plot_id for p in rollover_prompts if p.replied is not True}
    if excluded_by_rollover:
        logger.info(
            "watcher: cluster=%s excluding %d plot(s) not confirmed for "
            "season %s (declined or no reply to the rollover prompt): %s",
            cluster_id, len(excluded_by_rollover), season_id, sorted(excluded_by_rollover),
        )
        plots = [p for p in plots if p.plot_id not in excluded_by_rollover]

    if not plots:
        logger.warning("watcher: cluster %s has no plots, nothing to check", cluster_id)
        storage.set_watcher_last_run(cluster_id, today.isoformat())
        return {"cluster_id": cluster_id, "date": today.isoformat(), "status": "no_plots"}

    harvested_plot_ids = storage.get_harvested_plot_ids(cluster_id, season_id)
    if harvested_plot_ids:
        logger.info(
            "watcher: cluster=%s excluding %d already-harvested plot(s) "
            "this season, no weather fetched for them: %s",
            cluster_id, len(harvested_plot_ids), sorted(harvested_plot_ids),
        )

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
            if p.plot_id not in harvested_plot_ids
        }
    except WeatherError as exc:
        logger.error(
            "watcher: weather fetch failed, cluster=%s: %s -- will retry next scheduled run",
            cluster_id, exc,
        )
        return {"cluster_id": cluster_id, "status": "error", "reason": "weather_unavailable"}

    try:
        # Drying-window alerts (ADR-009 Part 4) run every pass, whether
        # or not today's scheduling trigger fires below -- a plot in its
        # drying window doesn't need a NEW dispatch to matter, only rain
        # in the forecast this run already fetched. Same forecast, no
        # second weather call, no new data source.
        _check_drying_window_alerts(client, storage, cluster, season_id, forecast, today)

        usable_days = usable_harvest_days(forecast, RAIN_THRESHOLD_MM)
        if usable_days >= len(forecast) and not force:
            logger.info(
                "watcher no-op: cluster=%s, no rain in the %d-day forecast -- nothing sent",
                cluster_id, len(forecast),
            )
            storage.set_watcher_last_run(cluster_id, today.isoformat())
            return {"cluster_id": cluster_id, "date": today.isoformat(), "status": "no_trigger"}

        logger.info(
            "watcher triggered%s: cluster=%s, %d of %d forecast days usable before rain",
            " (forced)" if usable_days >= len(forecast) else "", cluster_id, usable_days, len(forecast),
        )
        maturity_gdd_resolved = resolve_maturity_gdd(cluster)
        trigger_context = TriggerContext(
            decision_date=today.isoformat(),
            rain_threshold_mm=RAIN_THRESHOLD_MM,
            forecast_horizon_days=len(forecast),
            usable_harvest_days=usable_days,
            maturity_gdd_used=maturity_gdd_resolved[0],
            threshold_source=maturity_gdd_resolved[1],
            machine_capacity_acres_per_day=cluster.machine_capacity_acres_per_day,
            capacity_budget_acres=harvest_day_budget_acres(
                usable_days, cluster.machine_capacity_acres_per_day
            ),
        )
        decisions = solve(
            plots, plot_days, cluster, forecast,
            rain_threshold_mm=RAIN_THRESHOLD_MM, today=today,
            harvested_plot_ids=frozenset(harvested_plot_ids),
            maturity_gdd_resolved=maturity_gdd_resolved,
        )
        if get_claim is not None:
            result = run_cluster_with_claims(
                plots, decisions, cluster_id, storage, season_id, today,
                get_claim, trigger_context,
            )
        else:
            result = run_cluster(
                plots, decisions, cluster_id, storage, season_id, today, trigger_context,
            )

        farmers_by_id = {f.farmer_id: f for f in storage.get_farmers_for_cluster(cluster_id)}
        plots_by_id = {p.plot_id: p for p in plots}
        decisions_by_id = {d.plot_id: d for d in decisions}
        _send_notifications(
            client, cluster, decisions_by_id, result, farmers_by_id, plots_by_id,
            storage, season_id, today,
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
    storage: Storage,
    season_id: str,
    today: date,
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
            # The confirmation record is created here, at dispatch time,
            # unconditionally -- even for a farmer with no chat_id (the
            # fact "this plot was scheduled and nobody could be asked"
            # stays on record either way). See ADR-009 Part 2, Decision 4.
            confirm_result = storage.put_harvest_confirmation(
                HarvestConfirmation(
                    plot_id=plot.plot_id, farmer_id=farmer.farmer_id,
                    cluster_id=cluster.cluster_id, season_id=season_id,
                    scheduled_date=today.isoformat(),
                )
            )
            if not confirm_result.success:
                logger.error(
                    "HARVEST CONFIRMATION WRITE FAILED: plot=%s cluster=%s "
                    "season=%s -- the evening prompt will have nothing to "
                    "ask about for this plot: %s",
                    plot.plot_id, cluster.cluster_id, season_id, confirm_result.error,
                )
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
            operator_language=cluster.operator_language,
        )


def _check_drying_window_alerts(
    client: TelegramClient,
    storage: Storage,
    cluster: Cluster,
    season_id: str,
    forecast: list[ForecastDay],
    today: date,
) -> None:
    """ADR-009 Part 4: for every CONFIRMED harvest (Part 2) still inside
    its DRYING_WINDOW_DAYS, send one cover-your-grain alert if rain has
    entered the near-term forecast -- at most once per drying window,
    not per day and not per rain event (HarvestConfirmation.drying_alert_sent).
    Chained off confirmation, not off Part 1.5's harvest marker: a
    merely-scheduled-but-unconfirmed plot never triggers this.
    """
    near_term = forecast[:DRYING_WINDOW_DAYS]
    if not any(day.precipitation_mm > RAIN_THRESHOLD_MM for day in near_term):
        return  # nothing to check further -- no rain in the near-term window at all

    confirmations = storage.get_confirmations_for_cluster(cluster.cluster_id, season_id)
    for confirmation in confirmations:
        if not confirmation.confirmed or confirmation.confirmed_at is None:
            continue  # only a CONFIRMED "yes" starts a drying window
        if confirmation.drying_alert_sent:
            continue  # already alerted for this window
        confirmed_date = date.fromisoformat(confirmation.confirmed_at[:10])
        if (today - confirmed_date).days >= DRYING_WINDOW_DAYS:
            continue  # window has closed

        farmer = storage.get_farmer(confirmation.farmer_id)
        if farmer is None or farmer.telegram_chat_id is None:
            logger.info(
                "drying-window alert: no reachable farmer for plot=%s, skipping",
                confirmation.plot_id,
            )
            continue

        send_result = notify.send_drying_window_alert(client, farmer)
        if send_result.success:
            update_result = storage.put_harvest_confirmation(
                replace(confirmation, drying_alert_sent=True)
            )
            if not update_result.success:
                logger.error(
                    "DRYING ALERT FLAG WRITE FAILED: plot=%s cluster=%s "
                    "season=%s -- this farmer may be re-alerted on a "
                    "later trigger day within the same window: %s",
                    confirmation.plot_id, cluster.cluster_id, season_id,
                    update_result.error,
                )
        else:
            logger.error(
                "drying-window alert send failed for plot=%s: %s",
                confirmation.plot_id, send_result.error,
            )


def confirmation_status(
    confirmation: HarvestConfirmation, today: date
) -> str:
    """One of "confirmed_yes", "confirmed_no", "pending" (asked, still
    within the window, plausibly on its way), or "unknown" (never asked,
    or asked and the window has closed with no reply). This is a purely
    computed classification -- nothing about "unknown" is written to
    storage; `HarvestConfirmation.confirmed` simply stays None forever
    for a plot nobody ever answers about, exactly as it should for a
    signal we genuinely don't have. See ADR-009 Part 2."""
    if confirmation.confirmed is True:
        return "confirmed_yes"
    if confirmation.confirmed is False:
        return "confirmed_no"
    if confirmation.asked_at is None:
        return "unknown"
    asked_date = date.fromisoformat(confirmation.asked_at[:10])
    if (today - asked_date).days >= UNCONFIRMED_HARVEST_WINDOW_DAYS:
        return "unknown"
    return "pending"


def run_evening_confirmations(
    cluster_id: str,
    season_id: str,
    *,
    storage: Storage | None = None,
    today: date | None = None,
    telegram_client: TelegramClient | None = None,
) -> dict:
    """Code-only entrypoint -- not wired to any schedule yet. Sends the
    evening "did the machine come?" prompt (one message, two taps) for
    every HarvestConfirmation in this cluster/season that hasn't been
    asked yet, regardless of how old (a missed evening run is caught up
    on the next one, not silently skipped forever). Never a sweep that
    credits the fairness ledger on silence -- see confirmation_status()
    and webhook.handle_confirmation_callback for where an actual "no"
    reply is handled. This needs a second EventBridge Schedule to run on
    a real evening; deploying that is a separate, explicit decision --
    see ADR-009 Part 2, Decision 5.
    """
    storage = storage or get_storage()
    today = today or date.today()
    client = telegram_client or TelegramClient()

    confirmations = storage.get_confirmations_for_cluster(cluster_id, season_id)
    to_ask = [c for c in confirmations if c.asked_at is None]

    asked = 0
    skipped_no_chat_id = 0
    for confirmation in to_ask:
        farmer = storage.get_farmer(confirmation.farmer_id)
        plot = storage.get_plot(confirmation.plot_id)
        if farmer is None or plot is None:
            logger.error(
                "run_evening_confirmations: no farmer/plot found for plot=%s, "
                "cannot send confirmation prompt",
                confirmation.plot_id,
            )
            continue
        if farmer.telegram_chat_id is None:
            # Never gets an asked_at -- the record stays "unknown" on
            # record (nobody could be asked), not a special exemption
            # from anything. See ADR-009 Part 2, Decision 6.
            logger.info(
                "run_evening_confirmations: farmer %s has no chat_id, "
                "cannot ask about plot=%s",
                farmer.farmer_id, confirmation.plot_id,
            )
            skipped_no_chat_id += 1
            continue

        send_result = notify.send_harvest_confirmation_prompt(client, farmer, plot, season_id)
        if send_result.success:
            updated = replace(
                confirmation, asked_at=datetime.now(timezone.utc).isoformat()
            )
            storage.put_harvest_confirmation(updated)
            asked += 1
        else:
            logger.error(
                "run_evening_confirmations: send failed for plot=%s: %s -- "
                "asked_at not set, will retry next invocation",
                confirmation.plot_id, send_result.error,
            )

    return {
        "cluster_id": cluster_id,
        "season_id": season_id,
        "date": today.isoformat(),
        "asked": asked,
        "skipped_no_chat_id": skipped_no_chat_id,
        "already_asked": len(confirmations) - len(to_ask),
    }


def rollover_status(prompt: SeasonRolloverPrompt) -> str:
    """One of "confirmed" (replied=True), "declined" (replied=False), or
    "unknown" (replied=None -- never answered). Deliberately three
    states, not four: unlike confirmation_status(), there is no
    time-windowed "pending" distinction here -- the brief's instruction
    was to record silence as unknown, not to model a reply-still-plausibly-
    on-its-way state. "Declined" and "unknown" are different facts about
    a person and must never be reported as a single collapsed "excluded"
    value -- both exclude a plot from scheduling identically (see
    _run_daily_watch_one's rollover exclusion filter), but this function
    is what keeps them distinguishable in every report built on top of
    it. See ADR-011 Part 1.
    """
    if prompt.replied is True:
        return "confirmed"
    if prompt.replied is False:
        return "declined"
    return "unknown"


def run_season_rollover(
    cluster_id: str,
    old_season_id: str,
    new_season_id: str,
    *,
    storage: Storage | None = None,
    today: date | None = None,
    telegram_client: TelegramClient | None = None,
) -> dict:
    """Code-only entrypoint, operator-triggered -- ADR-011 Part 1. Not
    wired to any schedule, and deliberately so: there is no verified
    Tamil-Nadu-wide season calendar this project could encode without
    inventing one, so "when does a season roll over" stays a decision
    for whoever runs the cluster, exactly the same way cluster_id/
    season_id are already human-configured everywhere else in this
    codebase (ADR-006's EventBridge payload, every script's CLI args).

    Idempotent per plot: a plot that already has a SeasonRolloverPrompt
    for new_season_id is skipped entirely -- re-running this after a
    partial failure or an interrupted previous attempt resumes rather
    than re-prompting a farmer who already has a record. Every write
    (prompt sent -> record created) happens together; a send failure
    leaves no record, so it's retried on the next invocation, the same
    discipline run_evening_confirmations already uses.
    """
    storage = storage or get_storage()
    today = today or date.today()
    client = telegram_client or TelegramClient()

    plots = storage.get_plots_for_cluster(cluster_id)
    asked = 0
    skipped_already_prompted = 0
    skipped_no_chat_id = 0
    skipped_no_farmer = 0

    for plot in plots:
        if storage.get_season_rollover_prompt(plot.plot_id, new_season_id) is not None:
            skipped_already_prompted += 1
            continue

        farmer = storage.get_farmer(plot.farmer_id)
        if farmer is None:
            logger.error(
                "run_season_rollover: no farmer found for plot=%s, cannot "
                "send rollover prompt",
                plot.plot_id,
            )
            skipped_no_farmer += 1
            continue

        now_iso = datetime.now(timezone.utc).isoformat()

        if farmer.telegram_chat_id is None:
            # Never gets a real send attempt -- the record still lands on
            # storage (asked_at set) so this plot is correctly excluded
            # via replied staying None, not silently defaulted to
            # "included" for lack of any record at all. See ADR-011
            # Part 1, Decision 2's no-record-means-include rule: that
            # rule is for a plot nobody has ever asked about, not one we
            # tried and couldn't reach.
            storage.put_season_rollover_prompt(SeasonRolloverPrompt(
                plot_id=plot.plot_id, farmer_id=farmer.farmer_id, cluster_id=cluster_id,
                old_season_id=old_season_id, new_season_id=new_season_id, asked_at=now_iso,
            ))
            logger.info(
                "run_season_rollover: farmer %s has no chat_id, cannot ask "
                "about plot=%s -- recorded as unknown",
                farmer.farmer_id, plot.plot_id,
            )
            skipped_no_chat_id += 1
            continue

        send_result = notify.send_season_rollover_prompt(client, farmer, plot, new_season_id)
        if send_result.success:
            storage.put_season_rollover_prompt(SeasonRolloverPrompt(
                plot_id=plot.plot_id, farmer_id=farmer.farmer_id, cluster_id=cluster_id,
                old_season_id=old_season_id, new_season_id=new_season_id, asked_at=now_iso,
            ))
            asked += 1
        else:
            logger.error(
                "run_season_rollover: send failed for plot=%s: %s -- no "
                "record written, will retry next invocation",
                plot.plot_id, send_result.error,
            )

    return {
        "cluster_id": cluster_id,
        "old_season_id": old_season_id,
        "new_season_id": new_season_id,
        "date": today.isoformat(),
        "asked": asked,
        "skipped_no_chat_id": skipped_no_chat_id,
        "skipped_no_farmer": skipped_no_farmer,
        "skipped_already_prompted": skipped_already_prompted,
    }
