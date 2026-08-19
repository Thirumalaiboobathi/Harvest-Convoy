"""Machinery gap report: where is shared-harvester capacity short, and by
how much, before the next rain window closes? See ADR-010 Part 3.

Read-only. Makes no Bedrock calls, writes nothing to Storage, never
touches the deployed AgentCore artifact. Forward-looking, not a replay --
it makes one kind of live call (Open-Meteo weather), the same kind
watcher.py itself makes every trigger day, for the same reason: "what
does capacity look like right now" is inherently about current state, not
stored history.

All arithmetic is the same deterministic ranking/allocation rule
scheduling/solver.py:solve() uses (urgency descending, ties broken by
days_past_maturity then plot_id) -- reimplemented here rather than called
through solve() directly, because this report needs to control the
usable-day window itself (an explicit --window-start/--window-end must
bypass the live forecast entirely, which solve() has no way to do).

Usage:
    uv run python -m scripts.machinery_gap
    uv run python -m scripts.machinery_gap --cluster kamatchipuram
    uv run python -m scripts.machinery_gap --window-start 2026-09-09 \\
        --window-end 2026-09-12 --out gap.json

Assumption stated once, here, since it applies to every cluster in every
run: travel time between clusters is not modelled -- each cluster's
machine is treated entirely independently. This report also does not
exclude a plot already dispatched earlier in the season (it accepts no
season_id, so there is nothing to look a harvest marker up against) --
a plot still showing as ready in Storage after being harvested earlier
this season would be double-counted here; disclosed in the assumptions
block of every cluster's output, not silently assumed away.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from harvest_convoy.models import Cluster, Plot
from harvest_convoy.reporting.provenance import Provenance, build_provenance, render_provenance_text
from harvest_convoy.scheduling.capacity import ForecastDay, harvest_day_budget_acres, usable_harvest_days
from harvest_convoy.scheduling.solver import PlotOutcome, assess_plot, resolve_maturity_gdd
from harvest_convoy.storage import get_storage
from harvest_convoy.storage.interface import Storage
from harvest_convoy.watcher import FORECAST_HORIZON_DAYS, RAIN_THRESHOLD_MM
from harvest_convoy.weather.openmeteo import WeatherError, get_daily_temperatures, get_precipitation_forecast


@dataclass(frozen=True)
class ClusterGapResult:
    cluster_id: str
    cluster_name: str
    error: str | None

    window_start: str | None
    window_end: str | None
    window_source: str  # "live forecast" | "explicit --window-start/--window-end"
    usable_days: int

    rain_threshold_mm: float
    machine_capacity_acres_per_day: float
    maturity_gdd_used: float
    threshold_source: str

    plots_needing_harvest: list[str]
    acres_needing_harvest: float
    capacity_available_acres: float
    shortfall_acres: float
    surplus_acres: float
    machine_days_needed: float
    machines_needed: int
    farmers_affected: list[str]
    farmers_affected_acres: float

    gap_reason: str
    headline: str


@dataclass(frozen=True)
class MachineryGapReport:
    provenance: Provenance
    clusters: list[ClusterGapResult]
    aggregate_shortfall_acres: float
    aggregate_machine_days: float
    aggregate_farmers_affected: int
    data_gaps: list[str]


def _error_result(cluster_id: str, cluster_name: str, error: str) -> ClusterGapResult:
    return ClusterGapResult(
        cluster_id=cluster_id, cluster_name=cluster_name, error=error,
        window_start=None, window_end=None, window_source="", usable_days=0,
        rain_threshold_mm=RAIN_THRESHOLD_MM, machine_capacity_acres_per_day=0.0,
        maturity_gdd_used=0.0, threshold_source="", plots_needing_harvest=[],
        acres_needing_harvest=0.0, capacity_available_acres=0.0, shortfall_acres=0.0,
        surplus_acres=0.0, machine_days_needed=0.0, machines_needed=0,
        farmers_affected=[], farmers_affected_acres=0.0,
        gap_reason=error, headline=f"{cluster_name}: {error}",
    )


def _rank_and_allocate(
    ready: list[tuple[Plot, float, int]], budget_acres: float,
) -> tuple[list[Plot], list[Plot]]:
    """ready: (plot, urgency, days_past_maturity). Same deterministic
    tie-break rule as scheduling/solver.py:solve()'s ready_ranked --
    urgency descending, then days_past_maturity descending, then plot_id
    -- reimplemented here (not imported) because solve() derives its own
    usable-day window from a live forecast internally, and this report
    needs to override that with an explicit window when one is given.
    """
    ranked = sorted(ready, key=lambda t: (-t[1], -t[2], t[0].plot_id))
    fits: list[Plot] = []
    shortfall: list[Plot] = []
    committed = 0.0
    for plot, _urgency, _days in ranked:
        if committed + plot.area_acres <= budget_acres:
            fits.append(plot)
            committed += plot.area_acres
        else:
            shortfall.append(plot)
    return fits, shortfall


def _resolve_window(
    cluster: Cluster, today: date, window_start: date | None, window_end: date | None,
) -> tuple[date, date | None, int, str, bool]:
    """Returns (window_start, window_end, usable_days, window_source, no_rain_detected)."""
    if window_start is not None and window_end is not None:
        usable_days = max(0, (window_end - window_start).days)
        return window_start, window_end, usable_days, "explicit --window-start/--window-end", False

    forecast = get_precipitation_forecast(
        cluster.machine_start_lat, cluster.machine_start_lon,
        today, today + timedelta(days=FORECAST_HORIZON_DAYS - 1),
    )
    usable_days = usable_harvest_days(forecast, RAIN_THRESHOLD_MM)
    no_rain_detected = usable_days >= len(forecast)
    resolved_end = None if no_rain_detected else today + timedelta(days=usable_days)
    return today, resolved_end, usable_days, "live forecast", no_rain_detected


def build_cluster_result(storage: Storage, cluster_id: str, today: date, *, window_start: date | None, window_end: date | None) -> ClusterGapResult:
    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        return _error_result(cluster_id, cluster_id, "cluster not found")

    try:
        w_start, w_end, usable_days, window_source, no_rain_detected = _resolve_window(
            cluster, today, window_start, window_end,
        )
    except WeatherError as exc:
        return _error_result(cluster_id, cluster.name, f"weather unavailable: {exc}")

    maturity_gdd, threshold_source = resolve_maturity_gdd(cluster)
    capacity_available = harvest_day_budget_acres(usable_days, cluster.machine_capacity_acres_per_day)

    plots = storage.get_plots_for_cluster(cluster_id)
    ready: list[tuple[Plot, float, int]] = []
    for plot in plots:
        try:
            days = get_daily_temperatures(plot.lat, plot.lon, plot.transplant_date, today)
        except WeatherError as exc:
            return _error_result(cluster_id, cluster.name, f"weather unavailable for plot {plot.plot_id}: {exc}")
        decision = assess_plot(plot, days, today, maturity_gdd=maturity_gdd)
        if decision.outcome != PlotOutcome.TOO_GREEN:
            ready.append((plot, decision.urgency, decision.days_past_maturity or 0))

    acres_needing = sum(p.area_acres for p, _, _ in ready)
    plots_needing = sorted(p.plot_id for p, _, _ in ready)

    if no_rain_detected:
        gap_reason = (
            f"no rain detected in the {FORECAST_HORIZON_DAYS}-day forecast; nothing forces "
            "a harvest deadline within this window"
        )
        shortfall = 0.0
        surplus = 0.0
        farmers_affected: list[str] = []
        farmers_acres = 0.0
    elif not ready:
        gap_reason = "no plots at or past maturity in this cluster within the window"
        shortfall = 0.0
        surplus = capacity_available
        farmers_affected = []
        farmers_acres = 0.0
    else:
        fits, shortfall_plots = _rank_and_allocate(ready, capacity_available)
        shortfall = max(0.0, acres_needing - capacity_available)
        surplus = max(0.0, capacity_available - acres_needing)
        farmers_affected = sorted({p.farmer_id for p in shortfall_plots})
        farmers_acres = sum(p.area_acres for p in shortfall_plots)
        if shortfall > 0:
            gap_reason = f"{shortfall:g} acres short before {w_end.isoformat() if w_end else '(no deadline)'}"
        else:
            gap_reason = f"capacity comfortably covers demand before {w_end.isoformat() if w_end else '(no deadline)'}"

    machine_days_needed = shortfall / cluster.machine_capacity_acres_per_day if cluster.machine_capacity_acres_per_day else 0.0
    machines_needed = math.ceil(machine_days_needed) if shortfall > 0 else 0

    if shortfall > 0:
        headline = (
            f"{cluster.name} is short {shortfall:g} acres of capacity before "
            f"{w_end.isoformat() if w_end else '(no deadline)'} -- approximately "
            f"{machine_days_needed:g} machine-days."
        )
    elif surplus > 0 and not no_rain_detected and ready:
        headline = (
            f"{cluster.name} has {surplus:g} acres of surplus capacity before "
            f"{w_end.isoformat() if w_end else '(no deadline)'} -- no shortfall."
        )
    else:
        headline = f"{cluster.name}: {gap_reason}."

    return ClusterGapResult(
        cluster_id=cluster_id, cluster_name=cluster.name, error=None,
        window_start=w_start.isoformat(), window_end=w_end.isoformat() if w_end else None,
        window_source=window_source, usable_days=usable_days,
        rain_threshold_mm=RAIN_THRESHOLD_MM,
        machine_capacity_acres_per_day=cluster.machine_capacity_acres_per_day,
        maturity_gdd_used=maturity_gdd, threshold_source=threshold_source,
        plots_needing_harvest=plots_needing, acres_needing_harvest=acres_needing,
        capacity_available_acres=capacity_available, shortfall_acres=shortfall,
        surplus_acres=surplus, machine_days_needed=machine_days_needed,
        machines_needed=machines_needed, farmers_affected=farmers_affected,
        farmers_affected_acres=farmers_acres, gap_reason=gap_reason, headline=headline,
    )


def build_result(
    storage: Storage, *, cluster_ids: list[str], today: date,
    window_start: date | None, window_end: date | None,
) -> MachineryGapReport:
    clusters = [
        build_cluster_result(storage, cid, today, window_start=window_start, window_end=window_end)
        for cid in cluster_ids
    ]
    ok = [c for c in clusters if c.error is None]
    data_gaps = [f"{c.cluster_id}: {c.error}" for c in clusters if c.error is not None]

    return MachineryGapReport(
        provenance=build_provenance(storage, cluster_ids=cluster_ids, season_ids=[]),
        clusters=clusters,
        aggregate_shortfall_acres=sum(c.shortfall_acres for c in ok),
        aggregate_machine_days=sum(c.machine_days_needed for c in ok),
        aggregate_farmers_affected=len({f for c in ok for f in c.farmers_affected}),
        data_gaps=data_gaps,
    )


def render_text(result: MachineryGapReport) -> str:
    lines = [render_provenance_text(result.provenance), ""]
    lines.append("MACHINERY GAP REPORT")
    lines.append(
        "Assumption, applies to every cluster below: travel time between clusters "
        "is not modelled -- each cluster's machine is treated independently. This "
        "report also does not exclude a plot already dispatched earlier in the "
        "season (no season_id is accepted here)."
    )
    lines.append("")

    for c in result.clusters:
        lines.append(f"--- {c.cluster_id} ({c.cluster_name}) ---")
        if c.error is not None:
            lines.append(f"  ERROR: {c.error}")
            lines.append("")
            continue
        lines.append(
            f"  Window: {c.window_start} to {c.window_end or '(no deadline detected)'} "
            f"({c.window_source}), usable days: {c.usable_days}"
        )
        lines.append(
            f"  Assumptions used: capacity {c.machine_capacity_acres_per_day:g} acres/day, "
            f"rain threshold {c.rain_threshold_mm:g}mm, maturity threshold "
            f"{c.maturity_gdd_used:.1f} ({c.threshold_source})"
        )
        lines.append(
            f"  Acres needing harvest: {c.acres_needing_harvest:g} across "
            f"{len(c.plots_needing_harvest)} plot(s) {c.plots_needing_harvest}"
        )
        lines.append(f"  Capacity available: {c.capacity_available_acres:g} acres")
        lines.append(f"  Shortfall: {c.shortfall_acres:g} acres  |  Surplus: {c.surplus_acres:g} acres")
        lines.append(
            f"  Additional machine-days needed: {c.machine_days_needed:g} "
            f"(~{c.machines_needed} machine(s))"
        )
        lines.append(
            f"  Farmers affected by the shortfall: {len(c.farmers_affected)} "
            f"({c.farmers_affected_acres:g} acres) {c.farmers_affected}"
        )
        lines.append(f"  {c.headline}")
        lines.append("")

    lines.append("-" * 72)
    lines.append("AGGREGATE ACROSS ALL CLUSTERS ABOVE")
    lines.append("-" * 72)
    lines.append(f"  Total shortfall: {result.aggregate_shortfall_acres:g} acres")
    lines.append(f"  Total machine-days needed: {result.aggregate_machine_days:g}")
    lines.append(f"  Total distinct farmers affected: {result.aggregate_farmers_affected}")

    if result.data_gaps:
        lines.append("")
        lines.append("-" * 72)
        lines.append("NOT RECORDED, AND WHY")
        lines.append("-" * 72)
        for gap in result.data_gaps:
            lines.append(f"  - {gap}")

    return "\n".join(lines)


def to_json_dict(result: MachineryGapReport) -> dict:
    return asdict(result)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--cluster", default=None, dest="cluster_id", help="Narrow to one cluster")
    parser.add_argument("--window-start", default=None, type=_parse_date, help="YYYY-MM-DD")
    parser.add_argument("--window-end", default=None, type=_parse_date, help="YYYY-MM-DD")
    parser.add_argument("--out", default=None, help="Path to write the JSON report to")
    args = parser.parse_args(argv)

    if (args.window_start is None) != (args.window_end is None):
        print("error: --window-start and --window-end must be given together", file=sys.stderr)
        return 2

    storage = get_storage()

    if args.cluster_id is not None:
        if storage.get_cluster(args.cluster_id) is None:
            print(f"error: no cluster found with cluster_id={args.cluster_id!r}", file=sys.stderr)
            return 1
        cluster_ids = [args.cluster_id]
    else:
        cluster_ids = storage.list_cluster_ids()
        if not cluster_ids:
            print("error: no clusters found in storage", file=sys.stderr)
            return 1

    result = build_result(
        storage, cluster_ids=cluster_ids, today=date.today(),
        window_start=args.window_start, window_end=args.window_end,
    )

    print(render_text(result))

    if args.out is not None:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(to_json_dict(result), f, indent=2, default=str)

    return 0


if __name__ == "__main__":
    sys.exit(main())
