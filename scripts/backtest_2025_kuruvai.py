"""Historical backtest: what the system would have decided against the
real 2025 Kuruvai season, at both seeded clusters' real coordinates.

Read-only -- never calls Storage, never touches DynamoDB or the deployed
table. Replays scripts/seed_cluster.py's and
scripts/seed_cluster_naducauvery.py's exact fixture plots (same
coordinates, same area, same month/day transplant dates) one year
earlier -- 2025 instead of 2026 -- against real Open-Meteo Archive
weather, and walks the season day by day the way the real watcher does:
each simulated "today", check the rain-based trigger
(scheduling.capacity.usable_harvest_days against a real forecast-shaped
precipitation window), and on a trigger day run the exact same
deterministic scheduling.solver.solve() production uses.

See docs/adr/ADR-009-harvest-lifecycle-and-validation.md Part 1 for the
full design, and Part 1.5 for a correctness bug this backtest's first
real run found: solve() had no concept of a harvested plot, so a plot
that fit early kept re-entering capacity contention for the rest of the
season. Fixed in scheduling/solver.py (PlotOutcome.HARVESTED,
harvested_plot_ids parameter); this script models the same state
in-memory per cluster run (it never touches Storage -- see below), so
it exercises the fix the same way watcher.py does in production.

See the README's "Backtest against the real 2025 Kuruvai season"
section for both the buggy run's numbers and this fixed run's, kept
side by side rather than replaced.

WHAT THIS BACKTEST DOES NOT PROVE -- READ THIS BEFORE TRUSTING THE
NUMBERS BELOW. Open-Meteo has no archive of what a 16-day forecast said
on a given past day -- only what really happened. This script uses real
historical rainfall as the stand-in for "the forecast" the watcher's
rain trigger would have seen. That is optimistic by construction:
hindsight is perfect, a real forecast issued in real time is not. So:

  - This DOES validate that the deterministic core (GDD accumulation,
    per-cluster maturity calibration, capacity budgeting, route
    ordering) reacts sensibly to a real season's real weather, at real
    coordinates, not just synthetic 2026 fixture data.
  - This does NOT show what a real-time forecast-driven trigger would
    actually have fired on in 2025 -- a real deployment's trigger
    timing (and reliability) would likely have been somewhat different,
    and somewhat worse, than a hindsight-perfect proxy can show.
  - This does NOT prove real harvest outcomes -- there is no ground
    truth on what these (synthetic, disclosed-as-such) farmers actually
    did in 2025, because there were no real farmers in this fixture.

Usage:
    uv run python -m scripts.backtest_2025_kuruvai

Locally this prints one ConsoleSpanExporter JSON block per Open-Meteo
call, same as every other script in this project that touches
weather/openmeteo.py (see observability/otel.py) -- expected noise, not
an error; the markdown tables and narrative are the plain-text lines
around it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from harvest_convoy.agronomy.calibration import derive_cluster_maturity_gdd
from harvest_convoy.agronomy.gdd import project_maturity_date
from harvest_convoy.agronomy.crop_params import MATURITY_GDD_ESTIMATED, T_BASE_C
from harvest_convoy.models import Cluster, Plot
from harvest_convoy.scheduling.capacity import usable_harvest_days
from harvest_convoy.scheduling.solver import PlotOutcome, solve
from harvest_convoy.watcher import RAIN_THRESHOLD_MM, FORECAST_HORIZON_DAYS
from harvest_convoy.weather.openmeteo import WeatherError, get_historical_daily

from scripts import seed_cluster, seed_cluster_naducauvery

# ~4 months past transplant, per ADR-009 Decision 3 -- comfortably past
# ADT45_FIELD_DURATION_DAYS_ESTIMATED (90 days) so even the
# latest-transplanted plot in a fixture reaches maturity and is observed
# for a stretch of trigger days afterward, not cut off right at the line.
FETCH_WINDOW_DAYS = 130

# The calibration window (agronomy/calibration.py) looks back 5 complete
# years before whatever `today` it's given. Passing 2025-01-01 here (not
# the real current date) means the threshold is derived from 2020-2024
# only -- deliberately excluding the very season being backtested, so the
# threshold used to score 2025 was never trained on 2025. Also, per the
# ADR, this is a second independent calibration proof point on a
# different 5-year window than ADR-008's original derivation.
CALIBRATION_TODAY = date(2025, 1, 1)


def _shift_to_2025(plots: list[Plot]) -> list[Plot]:
    return [
        replace(p, transplant_date=date(2025, p.transplant_date.month, p.transplant_date.day))
        for p in plots
    ]


def _calibrate(cluster: Cluster) -> Cluster:
    try:
        value = derive_cluster_maturity_gdd(
            cluster.machine_start_lat, cluster.machine_start_lon, today=CALIBRATION_TODAY
        )
        print(f"  calibrated maturity threshold (2020-2024 climatology): {value:.1f} GDD")
        return replace(cluster, maturity_gdd_override=value)
    except WeatherError as exc:
        print(f"  calibration failed ({exc}) -- using the global fallback constant")
        return cluster


class ClusterBacktest:
    def __init__(self, cluster: Cluster, plots: list[Plot], farmers_by_id: dict):
        self.cluster = cluster
        self.plots = plots
        self.farmers_by_id = farmers_by_id
        # date string -> {plot_id: outcome} for every day the trigger fired
        self.trigger_days: dict[str, dict[str, PlotOutcome]] = {}
        self.projected_maturity: dict[str, str | None] = {}


def run_cluster_backtest(cluster_module) -> ClusterBacktest:
    cluster = _calibrate(cluster_module.CLUSTER)
    plots = _shift_to_2025(cluster_module.PLOTS)
    farmers_by_id = {f.farmer_id: f for f in cluster_module.FARMERS}

    print(f"\n{cluster.name} ({cluster.cluster_id}): fetching real 2025 weather...")

    season_start = min(p.transplant_date for p in plots)
    season_end = max(p.transplant_date for p in plots) + timedelta(days=FETCH_WINDOW_DAYS)

    plot_full_temps = {}
    for p in plots:
        end = p.transplant_date + timedelta(days=FETCH_WINDOW_DAYS)
        temps, _ = get_historical_daily(p.lat, p.lon, p.transplant_date, end)
        plot_full_temps[p.plot_id] = temps

    precip_end = season_end + timedelta(days=FORECAST_HORIZON_DAYS - 1)
    _, cluster_precip = get_historical_daily(
        cluster.machine_start_lat, cluster.machine_start_lon, season_start, precip_end
    )
    precip_by_date = {d.date: d for d in cluster_precip}

    # Same fallback solve()/assess_plot() apply when a cluster isn't
    # calibrated -- the global Theni-derived reference constant -- so the
    # projected-maturity column and the trigger-day solve() calls below
    # are always scored against the same threshold, never a mismatched one.
    maturity_gdd = cluster.maturity_gdd_override
    if maturity_gdd is None:
        maturity_gdd = MATURITY_GDD_ESTIMATED

    result = ClusterBacktest(cluster, plots, farmers_by_id)
    for p in plots:
        result.projected_maturity[p.plot_id] = project_maturity_date(
            plot_full_temps[p.plot_id], T_BASE_C, maturity_gdd
        )

    # In-memory only, one set per cluster/season backtest run -- never
    # persisted, discarded at the end of this function. The backtest
    # never touches Storage or the coordinator (no Bedrock, no LLM calls
    # -- see Decision 3), so it can't fetch this from a real backend the
    # way watcher.py now does; this is its own stand-in for "one season's
    # worth of harvest state." Every plot solve() decides FITS on a
    # trigger day is added here and excluded from every later day this
    # same run. See ADR-009 Part 1.5, Decision F.
    harvested_plot_ids: set[str] = set()

    simulated_today = season_start
    trigger_count = 0
    while simulated_today <= season_end:
        active_plots = [p for p in plots if p.transplant_date <= simulated_today]
        if active_plots:
            window_dates = [
                (simulated_today + timedelta(days=i)).isoformat()
                for i in range(FORECAST_HORIZON_DAYS)
            ]
            forecast = [precip_by_date[d] for d in window_dates if d in precip_by_date]

            if len(forecast) == FORECAST_HORIZON_DAYS:
                usable_days = usable_harvest_days(forecast, RAIN_THRESHOLD_MM)
                if usable_days < len(forecast):
                    plot_days = {
                        p.plot_id: [
                            t for t in plot_full_temps[p.plot_id]
                            if p.transplant_date.isoformat() <= t.date <= simulated_today.isoformat()
                        ]
                        for p in active_plots
                        if p.plot_id not in harvested_plot_ids
                    }
                    decisions = solve(
                        active_plots, plot_days, cluster, forecast,
                        rain_threshold_mm=RAIN_THRESHOLD_MM, today=simulated_today,
                        harvested_plot_ids=frozenset(harvested_plot_ids),
                    )
                    result.trigger_days[simulated_today.isoformat()] = {
                        d.plot_id: d.outcome for d in decisions
                    }
                    harvested_plot_ids.update(
                        d.plot_id for d in decisions if d.outcome == PlotOutcome.FITS
                    )
                    trigger_count += 1
        simulated_today += timedelta(days=1)

    print(f"  {trigger_count} trigger day(s) simulated across {(season_end - season_start).days + 1} days")
    return result


def _first_ready_trigger(result: ClusterBacktest, plot_id: str) -> tuple[str | None, str | None]:
    """(date, outcome) of the first trigger day this plot was ready
    (FITS or CONTESTED specifically -- not merely "not TOO_GREEN",
    since HARVESTED also isn't TOO_GREEN but was never "ready" in the
    contention sense) -- the day the system would first have offered to
    schedule or contest it."""
    for d in sorted(result.trigger_days):
        outcome = result.trigger_days[d].get(plot_id)
        if outcome in (PlotOutcome.FITS, PlotOutcome.CONTESTED):
            return d, outcome.value
    return None, None


def _final_outcome(result: ClusterBacktest, plot_id: str) -> str | None:
    """This plot's outcome on the LAST trigger day it appears in --
    its most recent state as the season progressed. Note: the current
    system (backtest included) has no "already harvested, stop
    re-assessing" mechanism, matching real production behavior today --
    a FITS plot keeps getting reassessed on every later trigger day too."""
    for d in sorted(result.trigger_days, reverse=True):
        outcome = result.trigger_days[d].get(plot_id)
        if outcome is not None:
            return outcome.value
    return None


def format_table(result: ClusterBacktest) -> str:
    lines = [
        "| Plot | Farmer | Area (ac) | Transplanted | Projected Maturity | First Ready Trigger | Final Outcome |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in sorted(result.plots, key=lambda p: p.plot_id):
        farmer = result.farmers_by_id[p.farmer_id]
        maturity = result.projected_maturity.get(p.plot_id) or "not reached in window"
        first_date, first_outcome = _first_ready_trigger(result, p.plot_id)
        final = _final_outcome(result, p.plot_id) or "never triggered ready"
        lines.append(
            f"| {p.plot_id} | {farmer.name} | {p.area_acres} | {p.transplant_date} | "
            f"{maturity} | {first_date or '-'} ({first_outcome or '-'}) | {final} |"
        )
    return "\n".join(lines)


def format_narrative(result: ClusterBacktest) -> str:
    trigger_dates = sorted(result.trigger_days)
    contested_days = [
        d for d, outcomes in result.trigger_days.items()
        if any(o == PlotOutcome.CONTESTED for o in outcomes.values())
    ]
    regressed_to_contested = [
        p.plot_id for p in result.plots
        if _first_ready_trigger(result, p.plot_id)[1] == "fits"
        and _final_outcome(result, p.plot_id) == "contested"
    ]
    lines = [
        f"- {len(trigger_dates)} trigger day(s) in the simulated window "
        f"({trigger_dates[0] if trigger_dates else 'none'} to "
        f"{trigger_dates[-1] if trigger_dates else 'none'}).",
        f"- Calibrated maturity threshold: "
        f"{result.cluster.maturity_gdd_override:.1f} GDD"
        if result.cluster.maturity_gdd_override is not None
        else "- Calibration failed; used the global fallback threshold.",
        f"- {len(contested_days)} day(s) had at least one CONTESTED plot "
        f"(ready, but the rain-shortened capacity budget didn't cover it).",
    ]
    fits_plots = [
        p.plot_id for p in result.plots
        if _first_ready_trigger(result, p.plot_id)[1] == "fits"
    ]
    if regressed_to_contested:
        lines.append(
            f"- {len(regressed_to_contested)} plot(s) first appeared as FITS "
            f"but end the simulated season as CONTESTED "
            f"({', '.join(sorted(regressed_to_contested))}): the system has no "
            f"mechanism today to remove a plot from future scheduling once "
            f"it's been marked FITS, so a plot that fit early keeps "
            f"re-competing for capacity against every later-maturing plot for "
            f"the rest of the season. This is a real property of the deployed "
            f"system, surfaced by running many trigger days in sequence, not a "
            f"backtest artifact -- see the README's discussion of this finding."
        )
    elif fits_plots:
        lines.append(
            f"- 0 of {len(fits_plots)} plot(s) that reached FITS regressed to "
            f"CONTESTED later in the season -- the plot harvest lifecycle fix "
            f"(ADR-009 Part 1.5) holds: once dispatched, a plot stays out of "
            f"contention for the rest of the season."
        )
    return "\n".join(lines)


def main() -> None:
    print("Backtest: real 2025 Kuruvai season, both seeded clusters")
    print("=" * 60)
    print(
        "\nCAVEAT, read before trusting the numbers below: this uses real "
        "2025 rainfall as a stand-in for a forecast (no archive of what a "
        "forecast said on a past day exists). This validates the "
        "deterministic scheduling math against real weather -- it does "
        "NOT show what a real-time forecast would have triggered, and "
        "does NOT prove real harvest outcomes (no ground truth on real "
        "farmers -- this fixture has none). See ADR-009 Part 1.\n"
    )

    for module in (seed_cluster, seed_cluster_naducauvery):
        result = run_cluster_backtest(module)
        print(f"\n### {result.cluster.name}\n")
        print(format_table(result))
        print()
        print(format_narrative(result))


if __name__ == "__main__":
    main()
