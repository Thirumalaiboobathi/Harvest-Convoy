"""Combines agronomy assessment, capacity budgeting, and route ordering into
the per-plot outcome the coordinator (Phase 3) and the demo need: which
plots are too green to touch, which fit this window, and which are ready
but lost out to capacity. DETERMINISTIC -- no LLM involvement.

Not listed by name in the original repo layout (which named capacity.py and
route.py as examples under scheduling/), but the Phase 2 gate needs a single
combined classification, and that orchestration doesn't belong inside either
capacity.py's pure budget math or route.py's pure distance math -- so it
gets its own module rather than being bolted onto one of them.

Every function here takes already-fetched data (daily temperatures,
forecast) as input rather than reaching out to the network itself. That
keeps the solver's core logic testable with fixed inputs, independent of
the current calendar date -- necessary for the "identically on every run"
gate, since actual weather changes day to day and a solver that reads
date.today() internally would make yesterday's test outcome unreproducible
today.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum

from harvest_convoy.agronomy import crop_params
from harvest_convoy.agronomy.decay import decay_fraction
from harvest_convoy.agronomy.gdd import (
    DailyTemperature,
    accumulate_gdd,
    project_maturity_date,
)
from harvest_convoy.models import Cluster, Plot
from harvest_convoy.scheduling.capacity import (
    ForecastDay,
    harvest_day_budget_acres,
    usable_harvest_days,
)
from harvest_convoy.scheduling.route import RoutePoint, order_route

logger = logging.getLogger(__name__)


class PlotOutcome(str, Enum):
    TOO_GREEN = "too_green"  # below maturity GDD -- excluded from contention entirely
    FITS = "fits"  # ready, and the capacity budget covers it
    CONTESTED = "contested"  # ready, but the budget ran out before it
    HARVESTED = "harvested"  # already dispatched this season -- excluded, not ranked. See ADR-009 Part 1.5.


@dataclass(frozen=True)
class PlotDecision:
    plot_id: str
    outcome: PlotOutcome
    accumulated_gdd: float
    days_past_maturity: int | None  # None for TOO_GREEN
    urgency: float  # decay_fraction; 0.0 for TOO_GREEN
    route_position: int | None  # only set for FITS


def assess_plot(
    plot: Plot,
    days: list[DailyTemperature],
    today: date,
    *,
    maturity_gdd: float = crop_params.MATURITY_GDD_ESTIMATED,
) -> PlotDecision:
    """Classify a single plot as TOO_GREEN or ready (returned as FITS here
    provisionally -- capacity allocation in solve() may downgrade a ready
    plot to CONTESTED). `days` must span [plot.transplant_date, today].

    This is the load-bearing classification: a plot below the maturity
    threshold is excluded from contention outright, before any capacity or
    ranking math runs -- it can never be pulled back in just because
    capacity happens to be available.

    `maturity_gdd` defaults to the global, Theni-derived reference
    constant for direct callers (demo scripts, tests) that don't have a
    Cluster in hand. solve() below always passes the cluster's own
    resolved threshold explicitly -- see ADR-008 Decision 2.
    """
    total_gdd = accumulate_gdd(days, crop_params.T_BASE_C)

    if total_gdd < maturity_gdd:
        return PlotDecision(
            plot_id=plot.plot_id,
            outcome=PlotOutcome.TOO_GREEN,
            accumulated_gdd=total_gdd,
            days_past_maturity=None,
            urgency=0.0,
            route_position=None,
        )

    maturity_date_str = project_maturity_date(days, crop_params.T_BASE_C, maturity_gdd)
    assert maturity_date_str is not None  # total_gdd already crossed the threshold
    maturity_date = date.fromisoformat(maturity_date_str)
    days_past_maturity = max(0, (today - maturity_date).days)

    return PlotDecision(
        plot_id=plot.plot_id,
        outcome=PlotOutcome.FITS,  # provisional; solve() may downgrade to CONTESTED
        accumulated_gdd=total_gdd,
        days_past_maturity=days_past_maturity,
        urgency=decay_fraction(days_past_maturity),
        route_position=None,
    )


def resolve_maturity_gdd(cluster: Cluster) -> tuple[float, str]:
    """Returns (maturity_gdd, "calibrated" | "fallback"). Extracted out of
    solve() (ADR-010 Part 0.5) so watcher.py can resolve and log the exact
    same threshold, the exact same way, without duplicating the
    None-means-fallback rule or its warning wording in a second place --
    watcher.py needs this value up front to build a TriggerContext before
    solve() runs, not just after.
    """
    maturity_gdd = cluster.maturity_gdd_override
    if maturity_gdd is None:
        maturity_gdd = crop_params.MATURITY_GDD_ESTIMATED
        logger.warning(
            "MATURITY THRESHOLD FALLBACK: cluster=%s has no derived "
            "maturity_gdd_override -- using the global Theni-reference "
            "constant (%.1f GDD, see crop_params.MATURITY_GDD_ESTIMATED) "
            "instead of this cluster's own climatology. Run the seed "
            "script with --calibrate to derive one for this cluster.",
            cluster.cluster_id, maturity_gdd,
        )
        return maturity_gdd, "fallback"
    return maturity_gdd, "calibrated"


def solve(
    plots: list[Plot],
    plot_days: dict[str, list[DailyTemperature]],
    cluster: Cluster,
    forecast: list[ForecastDay],
    rain_threshold_mm: float,
    today: date,
    *,
    harvested_plot_ids: frozenset[str] = frozenset(),
    maturity_gdd_resolved: tuple[float, str] | None = None,
) -> list[PlotDecision]:
    """Full scheduling pass over a cluster's plots for one weather trigger.

    0. Plots in `harvested_plot_ids` (already dispatched earlier this
       season -- ADR-009 Part 1.5) are excluded from everything below,
       the same way TOO_GREEN plots are excluded: a distinct outcome
       decided before ranking runs, not a plot ranked last. No
       `assess_plot()` call and no `plot_days` lookup happens for them.
    1. Assess every remaining plot: TOO_GREEN plots are excluded from
       everything below too.
    2. Rank ready plots by urgency (most decayed first; ties broken by
       days_past_maturity, then plot_id, for determinism).
    3. Greedily allocate acreage against the capacity budget computed from
       `forecast`/`rain_threshold_mm`/`cluster.machine_capacity_acres_per_day`
       -- plots that fit within budget stay FITS, the rest become CONTESTED.
    4. Order the FITS plots into a route by straight-line distance from the
       cluster's machine start position, and record each one's position.

    Returns one PlotDecision per input plot (including harvested ones),
    sorted by plot_id so the result shape doesn't depend on dict/set
    iteration order.

    Maturity threshold resolution (ADR-008 Decision 2): uses
    `cluster.maturity_gdd_override` if this cluster has been calibrated
    against its own climatology (agronomy/calibration.py); otherwise
    falls back to the global, Theni-derived
    `crop_params.MATURITY_GDD_ESTIMATED` and logs a loud warning so an
    uncalibrated cluster is never a silent assumption. `maturity_gdd_resolved`
    lets a caller that already resolved this (watcher.py, building a
    TriggerContext before calling solve() -- ADR-010 Part 0.5) pass the
    same `(value, source)` pair through instead of solve() re-resolving
    and re-logging the fallback warning a second time for one trigger;
    every other caller (tests, backtest, trigger_scenario.py) omits it and
    solve() resolves it here exactly as before.
    """
    plots_by_id = {p.plot_id: p for p in plots}

    if maturity_gdd_resolved is not None:
        maturity_gdd, _source = maturity_gdd_resolved
    else:
        maturity_gdd, _source = resolve_maturity_gdd(cluster)

    harvested = [
        PlotDecision(
            plot_id=p.plot_id,
            outcome=PlotOutcome.HARVESTED,
            accumulated_gdd=0.0,
            days_past_maturity=None,
            urgency=0.0,
            route_position=None,
        )
        for p in plots
        if p.plot_id in harvested_plot_ids
    ]
    schedulable = [p for p in plots if p.plot_id not in harvested_plot_ids]

    assessed = [
        assess_plot(p, plot_days[p.plot_id], today, maturity_gdd=maturity_gdd)
        for p in schedulable
    ]

    ready = [d for d in assessed if d.outcome == PlotOutcome.FITS]
    too_green = [d for d in assessed if d.outcome == PlotOutcome.TOO_GREEN]

    ready_ranked = sorted(
        ready,
        key=lambda d: (-d.urgency, -(d.days_past_maturity or 0), d.plot_id),
    )

    usable_days = usable_harvest_days(forecast, rain_threshold_mm)
    budget_acres = harvest_day_budget_acres(
        usable_days, cluster.machine_capacity_acres_per_day
    )

    fits: list[PlotDecision] = []
    contested: list[PlotDecision] = []
    acres_committed = 0.0
    for decision in ready_ranked:
        plot = plots_by_id[decision.plot_id]
        if acres_committed + plot.area_acres <= budget_acres:
            fits.append(decision)
            acres_committed += plot.area_acres
        else:
            contested.append(
                PlotDecision(
                    plot_id=decision.plot_id,
                    outcome=PlotOutcome.CONTESTED,
                    accumulated_gdd=decision.accumulated_gdd,
                    days_past_maturity=decision.days_past_maturity,
                    urgency=decision.urgency,
                    route_position=None,
                )
            )

    route_points = [
        RoutePoint(plot_id=d.plot_id, lat=plots_by_id[d.plot_id].lat, lon=plots_by_id[d.plot_id].lon)
        for d in fits
    ]
    route_order = order_route(
        route_points, cluster.machine_start_lat, cluster.machine_start_lon
    )
    position_by_plot_id = {pid: i for i, pid in enumerate(route_order)}

    fits_routed = [
        PlotDecision(
            plot_id=d.plot_id,
            outcome=PlotOutcome.FITS,
            accumulated_gdd=d.accumulated_gdd,
            days_past_maturity=d.days_past_maturity,
            urgency=d.urgency,
            route_position=position_by_plot_id[d.plot_id],
        )
        for d in fits
    ]

    result = too_green + fits_routed + contested + harvested
    return sorted(result, key=lambda d: d.plot_id)
