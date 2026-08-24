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
from harvest_convoy.agronomy.calibration import project_maturity_from_days
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.capacity import (
    ForecastDay,
    harvest_day_budget_acres,
    usable_harvest_days,
)
from harvest_convoy.scheduling.rain_event import classify_rain_event, rain_urgency_boost
from harvest_convoy.scheduling.solver import PlotDecision, PlotOutcome, resolve_maturity_gdd, solve
from harvest_convoy.storage import Storage, get_storage
from harvest_convoy.storage.interface import (
    AdvanceNoticeRecord,
    BreakdownDisplacement,
    HarvestConfirmation,
    MachineStatus,
    RouteOverride,
    SeasonRolloverPrompt,
)
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

# Advance harvest notice (ADR-011 Part 4): how many days before projected
# maturity the one-time "arrange transport, drying space" message goes
# out. Unsourced judgment call, same status as UNCONFIRMED_HARVEST_
# WINDOW_DAYS/DRYING_WINDOW_DAYS -- "roughly a week" as stated in the
# brief, not independently derived. Change freely; nothing depends on the
# exact number.
ADVANCE_NOTICE_DAYS_BEFORE_MATURITY = 7


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


def _apply_rollover_exclusion(storage: Storage, cluster_id: str, season_id: str) -> list[Plot]:
    """This season's plot roster, minus (a) any plot whose farmer
    declined a rollover prompt or never replied to one (ADR-011 Part 1)
    and (b) any plot retired by ADR-013 Part 2's /linkfarmer (Decision
    18) -- both excluded from the scheduling pool entirely, not merely
    ranked last. A plot with no rollover-prompt record at all for this
    season_id is included by default (a brand-new registration, a
    cluster/season run_season_rollover has never been triggered for, or
    -- ADR-013 Part 2 Decision 16 -- a permanently unreachable farmer,
    for whom run_season_rollover now deliberately writes no prompt at
    all rather than one that would otherwise exclude him). Shared by the
    normal daily trigger and the machine-breakdown recompute (ADR-011
    Part 2), so a plot excluded this morning doesn't reappear in an
    afternoon recompute.
    """
    plots = storage.get_plots_for_cluster(cluster_id)
    rollover_prompts = storage.get_season_rollover_prompts_for_cluster(cluster_id, season_id)
    excluded_by_rollover = {p.plot_id for p in rollover_prompts if p.replied is not True}
    if excluded_by_rollover:
        logger.info(
            "watcher: cluster=%s excluding %d plot(s) not confirmed for "
            "season %s (declined or no reply to the rollover prompt): %s",
            cluster_id, len(excluded_by_rollover), season_id, sorted(excluded_by_rollover),
        )
        plots = [p for p in plots if p.plot_id not in excluded_by_rollover]

    excluded_by_link = [p.plot_id for p in plots if p.retired_reason is not None]
    if excluded_by_link:
        logger.info(
            "watcher: cluster=%s excluding %d retired plot(s) (linked to a "
            "canonical farmer via /linkfarmer): %s",
            cluster_id, len(excluded_by_link), sorted(excluded_by_link),
        )
        plots = [p for p in plots if p.retired_reason is None]
    return plots


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

    plots = _apply_rollover_exclusion(storage, cluster_id, season_id)

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

        # Advance harvest notice (ADR-011 Part 4): same "runs every pass,
        # unconditionally" placement as the drying-window check above --
        # a plot entering its notice window doesn't need today's rain
        # trigger to matter, only the plot_days already fetched.
        _check_advance_harvest_notices(
            client, storage, cluster, season_id, plots, plot_days, harvested_plot_ids, today,
        )

        # Machine down indefinitely (ADR-011 Part 2): set by an
        # operator's breakdown follow-up tap, cleared only by a
        # symmetric "machine is back" tap. Gates dispatch only --
        # drying-window alerts above are unrelated to whether today's
        # machine run happens, so they still run either way.
        machine_status = storage.get_machine_status(cluster_id)
        if machine_status is not None and machine_status.status == "down":
            logger.info(
                "watcher no-op: cluster=%s machine reported down indefinitely "
                "since %s, not scheduling",
                cluster_id, machine_status.reported_at,
            )
            storage.set_watcher_last_run(cluster_id, today.isoformat())
            return {"cluster_id": cluster_id, "date": today.isoformat(), "status": "machine_down"}

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
        # ADR-011 Part 3: classified once here, off the same forecast
        # usable_harvest_days() already read -- passed through to both
        # solve() (so the urgency boost and the classification agree with
        # what gets persisted) and TriggerContext (so it's visible in the
        # audit trail), never computed twice.
        event_class = classify_rain_event(forecast, RAIN_THRESHOLD_MM)
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
            rain_event_classification=event_class.value,
            rain_urgency_boost=rain_urgency_boost(event_class),
        )
        decisions = solve(
            plots, plot_days, cluster, forecast,
            rain_threshold_mm=RAIN_THRESHOLD_MM, today=today,
            harvested_plot_ids=frozenset(harvested_plot_ids),
            maturity_gdd_resolved=maturity_gdd_resolved,
            rain_event_classification=event_class,
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
            rain_event_classification=event_class.value,
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
    *,
    rain_event_classification: str = "none",
) -> None:
    # (route_position, Farmer, Plot) -- sorted by route_position before
    # use, below. Appending in result.outcomes's incidental order (a
    # plot-id sort, agents/coordinator.py's RunResult.outcomes) and
    # relying on that order for the operator's route summary was a real,
    # live bug: it could silently number the operator's stops in a
    # different sequence than each farmer was individually told via
    # decision.route_position. See ADR-013's Prerequisite section.
    fits_route: list[tuple[int, Farmer, Plot]] = []

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
            # ADR-011 Part 3: reflected in the message only for a
            # SUSTAINED event -- BRIEF/NONE render byte-identical to the
            # existing wording, so the common case doesn't grow at all.
            notify.send_not_ready(
                client, farmer, plot,
                season_id=season_id, decision_date=today.isoformat(),
                rain_event_classification=rain_event_classification,
            )
        elif outcome.outcome == PlotOutcome.FITS:
            decision = decisions_by_id[outcome.plot_id]
            notify.send_harvest_scheduled(client, farmer, plot, decision.route_position)
            fits_route.append((decision.route_position, farmer, plot))
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
        fits_route.sort(key=lambda item: item[0])
        ordered_route = [(farmer, plot) for _, farmer, plot in fits_route]
        # The proposal record (ADR-013): created every time a non-empty
        # route is dispatched, before it's sent, so the Accept/Modify
        # buttons on the message that follows always have something real
        # to act on. proposed_route/current_route start identical --
        # "no_response" (silence) is the correct initial reading, per
        # route_override_status() below.
        override_result = storage.put_route_override(RouteOverride(
            cluster_id=cluster.cluster_id, season_id=season_id,
            decision_date=today.isoformat(),
            proposed_route=[p.plot_id for _, p in ordered_route],
            current_route=[p.plot_id for _, p in ordered_route],
            proposed_at=datetime.now(timezone.utc).isoformat(),
        ))
        if not override_result.success:
            logger.error(
                "ROUTE OVERRIDE RECORD WRITE FAILED: cluster=%s season=%s "
                "date=%s -- the operator's Accept/Modify taps will have "
                "nothing to act on today: %s",
                cluster.cluster_id, season_id, today.isoformat(), override_result.error,
            )
        notify.send_operator_route_summary(
            client, cluster.operator_chat_id, cluster, ordered_route,
            season_id=season_id, report_date=today.isoformat(),
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


def _check_advance_harvest_notices(
    client: TelegramClient,
    storage: Storage,
    cluster: Cluster,
    season_id: str,
    plots: list[Plot],
    plot_days: dict[str, list],
    harvested_plot_ids: set[str],
    today: date,
) -> None:
    """ADR-011 Part 4: for every plot not yet harvested this season and
    not yet sent this notice, send one "arrange transport, drying space"
    message once it's projected to mature within
    ADVANCE_NOTICE_DAYS_BEFORE_MATURITY days. Runs every trigger pass,
    unconditionally, same as _check_drying_window_alerts -- and reuses
    `plot_days` the caller already fetched for the capacity/
    classification pipeline (project_maturity_from_days takes
    already-fetched data), so this adds zero new network calls.

    Sent exactly once ever, per plot per season: an AdvanceNoticeRecord's
    mere existence is the entire suppression check, regardless of what a
    later, more accurate projection would say (Decision 17). A plot
    whose projection is already at or past maturity the first time it's
    checked (days_until <= 0) is excluded by the same `0 < days_until`
    test below and never gets a record written -- but this is not a
    special case needing its own bookkeeping: once real accumulated GDD
    has crossed the maturity threshold, project_maturity_from_days pins
    the projection at exactly `today` every subsequent call (remaining
    GDD floors at 0), so days_until stays exactly 0 forever and this
    plot is excluded on every future check too, with no extra state.
    """
    for plot in plots:
        if plot.plot_id in harvested_plot_ids:
            continue
        if storage.get_advance_notice_record(plot.plot_id, season_id) is not None:
            continue  # already sent this season -- never resent

        days = plot_days.get(plot.plot_id)
        if days is None:
            continue  # not in this trigger's schedulable set

        projected = date.fromisoformat(
            project_maturity_from_days(days, plot.transplant_date, cluster, today=today)
        )
        days_until = (projected - today).days
        if not (0 < days_until <= ADVANCE_NOTICE_DAYS_BEFORE_MATURITY):
            continue

        farmer = storage.get_farmer(plot.farmer_id)
        if farmer is None or farmer.telegram_chat_id is None:
            logger.info(
                "advance harvest notice: no reachable farmer for plot=%s, skipping",
                plot.plot_id,
            )
            continue

        send_result = notify.send_advance_harvest_notice(client, farmer, plot, projected)
        if send_result.success:
            write_result = storage.put_advance_notice_record(AdvanceNoticeRecord(
                plot_id=plot.plot_id, farmer_id=plot.farmer_id, cluster_id=cluster.cluster_id,
                season_id=season_id, sent_at=datetime.now(timezone.utc).isoformat(),
                projected_maturity_date=projected.isoformat(),
            ))
            if not write_result.success:
                logger.error(
                    "ADVANCE NOTICE RECORD WRITE FAILED: plot=%s cluster=%s "
                    "season=%s -- this farmer may be re-notified on a later "
                    "trigger day: %s",
                    plot.plot_id, cluster.cluster_id, season_id, write_result.error,
                )
        else:
            logger.error(
                "advance harvest notice send failed for plot=%s: %s",
                plot.plot_id, send_result.error,
            )


def confirmation_status(
    confirmation: HarvestConfirmation, today: date
) -> str:
    """One of "cancelled" (invalidated by a machine breakdown -- ADR-011
    Part 2), "confirmed_yes", "confirmed_no", "pending" (asked, still
    within the window, plausibly on its way), or "unknown" (never asked,
    or asked and the window has closed with no reply). This is a purely
    computed classification -- nothing about "unknown" is written to
    storage; `HarvestConfirmation.confirmed` simply stays None forever
    for a plot nobody ever answers about, exactly as it should for a
    signal we genuinely don't have. See ADR-009 Part 2.

    "cancelled" is checked first and is final -- a farmer's late reply
    against a cancelled confirmation still updates `confirmed`/
    `confirmed_at` (the truth stays on record), but it must never be
    reported as an ordinary confirmed_yes/no: "cancelled" means we know
    what happened and why (a breakdown, not the farmer's or another
    farmer's doing), which is a different fact from either a real
    confirmation or real silence.
    """
    if confirmation.cancelled:
        return "cancelled"
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
    # A cancelled confirmation (ADR-011 Part 2 -- its dispatch was
    # invalidated by a machine breakdown) is never asked about: it's
    # already known the machine didn't come, and why, which is a
    # different fact from silence.
    to_ask = [c for c in confirmations if c.asked_at is None and not c.cancelled]

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


def route_override_status(override: RouteOverride) -> str:
    """One of "modified" (current_route differs from proposed_route,
    regardless of whether an Accept tap ever happened), "accepted"
    (unmodified, and an explicit Accept tap was recorded), or
    "no_response" (unmodified, silence) -- derived at read time, the
    same pattern as confirmation_status()/rollover_status(). There is no
    sweep and no timeout that finalizes this: a route nobody touches
    simply reads "no_response" for as long as nobody touches it, which
    is the entire implementation of "silence is not a veto" (ADR-013).
    """
    if override.current_route != override.proposed_route:
        return "modified"
    if override.accepted_at is not None:
        return "accepted"
    return "no_response"


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
            # No prompt record written at all -- this plot stays included
            # by default next trigger, via ADR-011 Part 1 Decision 2's
            # own no-record-means-include rule.
            #
            # Corrected, ADR-013 Part 2 Decision 16: this branch used to
            # write a prompt record anyway (asked_at set, replied left
            # None) specifically so the plot would be *excluded* --
            # treating "couldn't ask" the same as "asked and got no
            # answer". That was defensible before proxy registration
            # existed, when telegram_chat_id=None was always an anomaly
            # (a seed script or a hand-edited record), never a real,
            # by-design, permanent state. ADR-013 Part 2 makes
            # "registered, permanently unreachable" an intended
            # population for the first time, and the old default would
            # have silently and permanently excluded exactly the farmers
            # that feature exists to include, starting the very next
            # season. Writing nothing here lets the existing default do
            # the correct thing instead.
            logger.info(
                "run_season_rollover: farmer %s has no chat_id -- no prompt "
                "recorded, plot=%s stays included by default next trigger "
                "(ADR-011 Part 1 Decision 2)",
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


def handle_machine_breakdown(
    cluster_id: str,
    season_id: str,
    report_date: date,
    *,
    storage: Storage | None = None,
    telegram_client: TelegramClient | None = None,
    get_claim=None,
) -> dict:
    """ADR-011 Part 2: an operator's "machine down today" tap. Marks
    today's dispatched plots as not-actually-harvested (the
    clear_plot_harvest reversal hook ADR-009 Part 1.5 built and left
    unused for exactly this), recomputes the schedule against the
    remaining harvest days, re-notifies every affected farmer with
    either a revised slot or an honest "not ready" -- never a fabricated
    promise -- and records a BreakdownDisplacement for each affected
    plot. Never writes a LedgerEntry: a breakdown displacement is not a
    farmer losing to another farmer, and crediting it to the fairness
    ledger would make that ledger measure equipment reliability instead
    of the thing it exists to measure (Decision 8).

    Idempotent per (cluster_id, season_id, report_date): a second tap
    for a day already reported is a no-op, not a second recompute and a
    second round of notifications.

    Known simplification, disclosed rather than hidden: the recomputed
    capacity budget starts from tomorrow (today's forecast day is
    dropped, since today's machine-hours are lost to the breakdown
    regardless of rain), but a plot that fits within that budget still
    gets the existing harvest_scheduled wording ("the machine is coming
    to your plot today"). Building a distinctly-worded "your revised
    slot" message was out of scope for this pass; flagged here rather
    than silently reused as if it were exactly accurate.
    """
    storage = storage or get_storage()
    client = telegram_client or TelegramClient()
    report_date_iso = report_date.isoformat()

    already = storage.get_breakdown_displacements_for_date(cluster_id, season_id, report_date_iso)
    if already:
        logger.info(
            "machine breakdown: cluster=%s season=%s date=%s already reported "
            "(%d plot(s)) -- no-op",
            cluster_id, season_id, report_date_iso, len(already),
        )
        return {
            "cluster_id": cluster_id, "season_id": season_id, "date": report_date_iso,
            "status": "already_reported", "displaced": 0,
        }

    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        logger.error("machine breakdown: cluster %s not found in storage", cluster_id)
        return {"cluster_id": cluster_id, "status": "error", "reason": "cluster_not_found"}

    confirmations = storage.get_confirmations_for_cluster(cluster_id, season_id)
    todays = [
        c for c in confirmations
        if c.scheduled_date == report_date_iso and not c.cancelled
    ]
    if not todays:
        logger.info(
            "machine breakdown: cluster=%s date=%s -- no route that day, "
            "nothing to recompute",
            cluster_id, report_date_iso,
        )
        return {
            "cluster_id": cluster_id, "season_id": season_id, "date": report_date_iso,
            "status": "no_route", "displaced": 0,
        }

    now_iso = datetime.now(timezone.utc).isoformat()
    for confirmation in todays:
        storage.clear_plot_harvest(confirmation.plot_id, cluster_id, season_id)
        storage.put_breakdown_displacement(BreakdownDisplacement(
            plot_id=confirmation.plot_id, farmer_id=confirmation.farmer_id,
            cluster_id=cluster_id, season_id=season_id,
            original_scheduled_date=report_date_iso, reported_at=now_iso,
        ))
        storage.put_harvest_confirmation(replace(confirmation, cancelled=True))

    try:
        forecast = get_precipitation_forecast(
            cluster.machine_start_lat, cluster.machine_start_lon,
            report_date, report_date + timedelta(days=FORECAST_HORIZON_DAYS - 1),
        )
    except WeatherError as exc:
        logger.error(
            "machine breakdown: weather fetch failed, cluster=%s: %s -- "
            "plots un-harvested, but the recompute could not run; will be "
            "picked up by the next scheduled trigger",
            cluster_id, exc,
        )
        return {
            "cluster_id": cluster_id, "season_id": season_id, "date": report_date_iso,
            "status": "weather_unavailable", "displaced": len(todays),
        }

    # Today's machine-hours are lost regardless of rain -- the recomputed
    # budget starts from tomorrow, not from a fresh reading of today's
    # forecast day.
    remaining_forecast = forecast[1:]

    plots = _apply_rollover_exclusion(storage, cluster_id, season_id)
    harvested_plot_ids = storage.get_harvested_plot_ids(cluster_id, season_id)
    try:
        plot_days = {
            p.plot_id: get_daily_temperatures(p.lat, p.lon, p.transplant_date, report_date)
            for p in plots
            if p.plot_id not in harvested_plot_ids
        }
    except WeatherError as exc:
        logger.error(
            "machine breakdown: per-plot weather fetch failed, cluster=%s: %s",
            cluster_id, exc,
        )
        return {
            "cluster_id": cluster_id, "season_id": season_id, "date": report_date_iso,
            "status": "weather_unavailable", "displaced": len(todays),
        }

    maturity_gdd_resolved = resolve_maturity_gdd(cluster)
    remaining_usable_days = usable_harvest_days(remaining_forecast, RAIN_THRESHOLD_MM)
    event_class = classify_rain_event(remaining_forecast, RAIN_THRESHOLD_MM)
    trigger_context = TriggerContext(
        decision_date=report_date_iso,
        rain_threshold_mm=RAIN_THRESHOLD_MM,
        forecast_horizon_days=len(remaining_forecast),
        usable_harvest_days=remaining_usable_days,
        maturity_gdd_used=maturity_gdd_resolved[0],
        threshold_source=maturity_gdd_resolved[1],
        machine_capacity_acres_per_day=cluster.machine_capacity_acres_per_day,
        capacity_budget_acres=harvest_day_budget_acres(
            remaining_usable_days, cluster.machine_capacity_acres_per_day
        ),
        trigger_reason="breakdown_recompute",
        rain_event_classification=event_class.value,
        rain_urgency_boost=rain_urgency_boost(event_class),
    )
    decisions = solve(
        plots, plot_days, cluster, remaining_forecast,
        rain_threshold_mm=RAIN_THRESHOLD_MM, today=report_date,
        harvested_plot_ids=frozenset(harvested_plot_ids),
        maturity_gdd_resolved=maturity_gdd_resolved,
        rain_event_classification=event_class,
    )
    if get_claim is not None:
        result = run_cluster_with_claims(
            plots, decisions, cluster_id, storage, season_id, report_date,
            get_claim, trigger_context,
        )
    else:
        result = run_cluster(
            plots, decisions, cluster_id, storage, season_id, report_date, trigger_context,
        )

    farmers_by_id = {f.farmer_id: f for f in storage.get_farmers_for_cluster(cluster_id)}
    plots_by_id = {p.plot_id: p for p in plots}
    decisions_by_id = {d.plot_id: d for d in decisions}
    _send_notifications(
        client, cluster, decisions_by_id, result, farmers_by_id, plots_by_id,
        storage, season_id, report_date,
        rain_event_classification=event_class.value,
    )

    return {
        "cluster_id": cluster_id,
        "season_id": season_id,
        "date": report_date_iso,
        "status": "recomputed",
        "displaced": len(todays),
        "escalations": len(result.escalations),
    }


def set_machine_down(cluster_id: str, *, storage: Storage | None = None, today: date | None = None) -> dict:
    """The "down indefinitely" follow-up (ADR-011 Part 2) -- suppresses
    dispatch on every subsequent trigger day until clear_machine_down()
    is called. Deliberately narrow: this alone does not touch today's
    already-handled breakdown recompute, only future trigger days."""
    storage = storage or get_storage()
    today = today or date.today()
    result = storage.put_machine_status(MachineStatus(
        cluster_id=cluster_id, status="down",
        reported_at=datetime.now(timezone.utc).isoformat(),
    ))
    return {"cluster_id": cluster_id, "status": "down" if result.success else "error"}


def clear_machine_down(cluster_id: str, *, storage: Storage | None = None) -> dict:
    """The symmetric "machine is back" action -- safe to call on a
    cluster that was never marked down (a no-op success)."""
    storage = storage or get_storage()
    result = storage.clear_machine_status(cluster_id)
    return {"cluster_id": cluster_id, "status": "operational" if result.success else "error"}
