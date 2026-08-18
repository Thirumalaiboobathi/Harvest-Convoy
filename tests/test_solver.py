"""Scheduling solver tests.

The classification tests build hand-constructed DailyTemperature series
(constant 20.0 GDD/day: t_max=40, t_min=20, T_BASE_C=10 -> mean 30 -> 20)
rather than depending on live weather or the real calendar date, so results
stay meaningful even after crop_params constants change again later -- the
number of days needed is computed from the live constant, not hardcoded.

The Kamatchipuram gate test uses the real seed_cluster.py fixture (real
plots, real transplant dates, real cluster config) but a synthetic,
constant-rate weather series derived from our own computed climatology
(crop_params.KURUVAI_MEAN_GDD_PER_DAY_REFERENCE_ESTIMATED), fixed to a specific
reference date. This keeps the gate's "identically on every run" requirement
true forever, independent of live network state or the actual calendar
date -- a solver that read real weather for "today" would give a different,
unreproducible answer depending on when the test happens to run.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from harvest_convoy.agronomy import crop_params
from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.models import Cluster, Plot
from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.scheduling.solver import PlotOutcome, assess_plot, solve

CONSTANT_T_MAX = 40.0
CONSTANT_T_MIN = 20.0
CONSTANT_DAILY_GDD = 20.0  # mean 30 - T_BASE_C(10) = 20


def _make_days(start: date, num_days: int) -> list[DailyTemperature]:
    return [
        DailyTemperature(
            date=(start + timedelta(days=i)).isoformat(),
            t_max_c=CONSTANT_T_MAX,
            t_min_c=CONSTANT_T_MIN,
        )
        for i in range(num_days)
    ]


def _days_to_maturity() -> int:
    return math.ceil(crop_params.MATURITY_GDD_ESTIMATED / CONSTANT_DAILY_GDD)


def _plot(plot_id: str, transplant_date: date, area_acres: float = 1.0) -> Plot:
    return Plot(
        plot_id=plot_id,
        farmer_id=f"farmer-{plot_id}",
        cluster_id="test-cluster",
        lat=10.0,
        lon=77.5,
        crop="paddy",
        variety="ADT45",
        transplant_date=transplant_date,
        area_acres=area_acres,
    )


def test_too_green_plot_is_excluded_not_merely_ranked_last() -> None:
    days_needed = _days_to_maturity()
    today = date(2026, 8, 16)
    transplant = today - timedelta(days=days_needed - 10)  # 10 days short
    plot = _plot("p_green", transplant)
    days = _make_days(transplant, days_needed - 10 + 1)

    decision = assess_plot(plot, days, today)

    assert decision.outcome == PlotOutcome.TOO_GREEN
    assert decision.days_past_maturity is None
    assert decision.urgency == 0.0


def test_ready_plot_gets_days_past_maturity_and_urgency() -> None:
    days_needed = _days_to_maturity()
    today = date(2026, 8, 16)
    overdue_by = 6
    # maturity lands on transplant + (days_needed - 1); today is overdue_by
    # days past that.
    transplant = today - timedelta(days=days_needed - 1 + overdue_by)
    plot = _plot("p_ready", transplant)
    days = _make_days(transplant, (today - transplant).days + 1)

    decision = assess_plot(plot, days, today)

    assert decision.outcome == PlotOutcome.FITS  # provisional pre-capacity outcome
    assert decision.days_past_maturity == overdue_by
    assert decision.urgency == min(1.0, overdue_by / crop_params.DECAY_HORIZON_DAYS_ESTIMATED)


def test_too_green_never_reenters_contention_even_with_unlimited_capacity() -> None:
    days_needed = _days_to_maturity()
    today = date(2026, 8, 16)
    transplant = today - timedelta(days=days_needed - 20)
    plot = _plot("p_green", transplant, area_acres=0.1)
    days = _make_days(transplant, days_needed - 20 + 1)

    cluster = Cluster(
        cluster_id="c",
        name="c",
        machine_capacity_acres_per_day=1000.0,  # effectively unlimited
        machine_start_lat=10.0,
        machine_start_lon=77.5,
    )
    forecast = [ForecastDay("2026-08-16", 0.0)] * 10  # no rain, huge window

    result = solve(
        [plot],
        {plot.plot_id: days},
        cluster,
        forecast,
        rain_threshold_mm=5.0,
        today=today,
    )

    assert len(result) == 1
    assert result[0].outcome == PlotOutcome.TOO_GREEN


def test_zero_plots_ready_returns_all_too_green_and_empty_route() -> None:
    days_needed = _days_to_maturity()
    today = date(2026, 8, 16)
    plots = [
        _plot("a", today - timedelta(days=5)),
        _plot("b", today - timedelta(days=10)),
    ]
    plot_days = {
        p.plot_id: _make_days(p.transplant_date, (today - p.transplant_date).days + 1)
        for p in plots
    }
    cluster = Cluster("c", "c", 3.5, 10.0, 77.5)
    forecast = [ForecastDay("2026-08-16", 0.0)] * 5

    result = solve(plots, plot_days, cluster, forecast, rain_threshold_mm=5.0, today=today)

    assert all(d.outcome == PlotOutcome.TOO_GREEN for d in result)
    assert all(d.route_position is None for d in result)


def test_every_plot_fits_comfortably_with_generous_budget() -> None:
    days_needed = _days_to_maturity()
    today = date(2026, 8, 16)
    plots = [
        _plot("a", today - timedelta(days=days_needed + 3), area_acres=1.0),
        _plot("b", today - timedelta(days=days_needed + 1), area_acres=1.0),
    ]
    plot_days = {
        p.plot_id: _make_days(p.transplant_date, (today - p.transplant_date).days + 1)
        for p in plots
    }
    cluster = Cluster("c", "c", 3.5, 10.0, 77.5)
    forecast = [ForecastDay("2026-08-16", 0.0)] * 10  # long dry window

    result = solve(plots, plot_days, cluster, forecast, rain_threshold_mm=5.0, today=today)

    assert all(d.outcome == PlotOutcome.FITS for d in result)
    assert {d.route_position for d in result} == {0, 1}


def test_no_rain_in_forecast_gives_full_window_budget() -> None:
    days_needed = _days_to_maturity()
    today = date(2026, 8, 16)
    plot = _plot("a", today - timedelta(days=days_needed + 1), area_acres=1.0)
    plot_days = {plot.plot_id: _make_days(plot.transplant_date, days_needed + 2)}
    cluster = Cluster("c", "c", 3.5, 10.0, 77.5)
    forecast = [ForecastDay(f"day{i}", 0.0) for i in range(16)]  # no rain at all

    result = solve([plot], plot_days, cluster, forecast, rain_threshold_mm=5.0, today=today)

    assert result[0].outcome == PlotOutcome.FITS


def test_contested_when_ready_acreage_exceeds_budget() -> None:
    days_needed = _days_to_maturity()
    today = date(2026, 8, 16)
    # Both ready, "urgent" more overdue than "less_urgent". 4+4=8 acres of
    # demand against a 7.0 acre (2-day) budget forces one into CONTESTED.
    urgent = _plot("urgent", today - timedelta(days=days_needed + 10), area_acres=4.0)
    less_urgent = _plot("less_urgent", today - timedelta(days=days_needed + 1), area_acres=4.0)
    plot_days = {
        p.plot_id: _make_days(p.transplant_date, (today - p.transplant_date).days + 1)
        for p in (urgent, less_urgent)
    }
    cluster = Cluster("c", "c", machine_capacity_acres_per_day=3.5, machine_start_lat=10.0, machine_start_lon=77.5)
    forecast = [ForecastDay("2026-08-16", 0.0), ForecastDay("2026-08-17", 0.0)]  # 2 usable days -> 7.0 acre budget

    result = solve(
        [urgent, less_urgent], plot_days, cluster, forecast, rain_threshold_mm=5.0, today=today
    )
    by_id = {d.plot_id: d for d in result}

    assert by_id["urgent"].outcome == PlotOutcome.FITS
    assert by_id["less_urgent"].outcome == PlotOutcome.CONTESTED
    assert by_id["less_urgent"].route_position is None


def test_cluster_maturity_gdd_override_is_used_instead_of_global_constant() -> None:
    """A cluster with its own calibrated threshold uses it -- not the
    global Theni-derived fallback. See ADR-008 Decision 2."""
    today = date(2026, 8, 16)
    # 5 days of constant 20.0 GDD/day = 100 accumulated GDD -- well below
    # the real MATURITY_GDD_ESTIMATED (~1729), but above a deliberately
    # low override, so FITS here proves the override -- not the global
    # constant -- is what solve() actually compared against.
    transplant = today - timedelta(days=4)
    plot = _plot("p_override", transplant, area_acres=1.0)
    days = _make_days(transplant, 5)

    cluster = Cluster(
        "c", "c", 3.5, 10.0, 77.5, maturity_gdd_override=90.0,
    )
    forecast = [ForecastDay("2026-08-16", 0.0)] * 5

    result = solve([plot], {plot.plot_id: days}, cluster, forecast, rain_threshold_mm=5.0, today=today)

    assert result[0].outcome == PlotOutcome.FITS


def test_uncalibrated_cluster_falls_back_to_global_constant_and_logs_loudly(caplog) -> None:
    """No maturity_gdd_override -> the global fallback applies (existing
    behavior, unchanged) and a loud warning is logged so this is never a
    silent assumption. See ADR-008 Decision 2."""
    import logging

    days_needed = _days_to_maturity()
    today = date(2026, 8, 16)
    transplant = today - timedelta(days=days_needed - 1)
    plot = _plot("p_fallback", transplant)
    days = _make_days(transplant, days_needed)

    cluster = Cluster("c", "c", 3.5, 10.0, 77.5)  # maturity_gdd_override defaults to None
    forecast = [ForecastDay("2026-08-16", 0.0)] * 5

    with caplog.at_level(logging.WARNING):
        result = solve([plot], {plot.plot_id: days}, cluster, forecast, rain_threshold_mm=5.0, today=today)

    assert result[0].outcome == PlotOutcome.FITS  # global constant behavior, unchanged
    assert any(
        "MATURITY THRESHOLD FALLBACK" in record.message for record in caplog.records
    )


def test_solve_is_deterministic_across_runs() -> None:
    days_needed = _days_to_maturity()
    today = date(2026, 8, 16)
    plots = [
        _plot("a", today - timedelta(days=days_needed + 4), area_acres=2.0),
        _plot("b", today - timedelta(days=days_needed + 2), area_acres=2.0),
        _plot("c", today - timedelta(days=days_needed - 15), area_acres=1.0),
    ]
    plot_days = {
        p.plot_id: _make_days(p.transplant_date, (today - p.transplant_date).days + 1)
        for p in plots
    }
    cluster = Cluster("c", "c", 3.5, 10.0, 77.5)
    forecast = [ForecastDay("2026-08-16", 0.0), ForecastDay("2026-08-17", 12.0)]

    result1 = solve(plots, plot_days, cluster, forecast, rain_threshold_mm=5.0, today=today)
    result2 = solve(plots, plot_days, cluster, forecast, rain_threshold_mm=5.0, today=today)

    assert result1 == result2


# --- ADR-009 Part 1.5: plot harvest lifecycle ---

def test_harvested_plot_is_excluded_not_merely_ranked_last() -> None:
    """Same shape as test_too_green_plot_is_excluded_not_merely_ranked_last
    -- a harvested plot never reaches assess_plot() or ranking at all, not
    just "ranked last". No plot_days entry needed for it either: passing
    an empty dict proves assess_plot() is genuinely never called for it."""
    today = date(2026, 8, 16)
    plot = _plot("p_harvested", today - timedelta(days=30), area_acres=1.0)
    cluster = Cluster("c", "c", 3.5, 10.0, 77.5)
    forecast = [ForecastDay("2026-08-16", 0.0)] * 5

    result = solve(
        [plot], {}, cluster, forecast, rain_threshold_mm=5.0, today=today,
        harvested_plot_ids=frozenset({"p_harvested"}),
    )

    assert len(result) == 1
    assert result[0].outcome == PlotOutcome.HARVESTED
    assert result[0].route_position is None


def test_harvested_plot_does_not_steal_capacity_from_an_urgent_unharvested_plot() -> None:
    """The actual bug Part 1's backtest found: solve() had no way to know
    a plot was already dispatched, so it kept ranking (and winning
    capacity for) a plot that no longer needed it -- at a genuinely
    urgent plot's expense. Same fixture run twice: without excluding the
    already-dispatched plot (pre-Part-1.5 behavior), it wins the only
    budget slot over the urgent plot purely because it's more overdue;
    with harvested_plot_ids (the fix), the urgent plot wins instead."""
    days_needed = _days_to_maturity()
    today = date(2026, 8, 16)
    # Maximally overdue -- would rank first by urgency if still eligible.
    already_dispatched = _plot(
        "already_dispatched", today - timedelta(days=days_needed + 30), area_acres=3.5
    )
    # Also ready and overdue, but less so -- loses the ranking race if
    # both compete for the same single-day budget.
    genuinely_urgent = _plot(
        "genuinely_urgent", today - timedelta(days=days_needed + 5), area_acres=3.5
    )
    plot_days = {
        p.plot_id: _make_days(p.transplant_date, (today - p.transplant_date).days + 1)
        for p in (already_dispatched, genuinely_urgent)
    }
    cluster = Cluster(
        "c", "c", machine_capacity_acres_per_day=3.5,
        machine_start_lat=10.0, machine_start_lon=77.5,
    )
    forecast = [ForecastDay("2026-08-16", 0.0)]  # 1 usable day -> 3.5 acre budget, room for one plot

    without_exclusion = solve(
        [already_dispatched, genuinely_urgent], plot_days, cluster, forecast,
        rain_threshold_mm=5.0, today=today,
    )
    by_id = {d.plot_id: d for d in without_exclusion}
    assert by_id["already_dispatched"].outcome == PlotOutcome.FITS
    assert by_id["genuinely_urgent"].outcome == PlotOutcome.CONTESTED  # the bug

    with_exclusion = solve(
        [already_dispatched, genuinely_urgent], plot_days, cluster, forecast,
        rain_threshold_mm=5.0, today=today,
        harvested_plot_ids=frozenset({"already_dispatched"}),
    )
    by_id = {d.plot_id: d for d in with_exclusion}
    assert by_id["already_dispatched"].outcome == PlotOutcome.HARVESTED
    assert by_id["genuinely_urgent"].outcome == PlotOutcome.FITS  # the fix


def test_solve_returns_one_decision_per_plot_including_harvested() -> None:
    today = date(2026, 8, 16)
    days_needed = _days_to_maturity()
    ready = _plot("ready", today - timedelta(days=days_needed + 2), area_acres=1.0)
    harvested = _plot("harvested", today - timedelta(days=60), area_acres=1.0)
    plot_days = {
        ready.plot_id: _make_days(ready.transplant_date, (today - ready.transplant_date).days + 1)
    }
    cluster = Cluster("c", "c", 3.5, 10.0, 77.5)
    forecast = [ForecastDay("2026-08-16", 0.0)] * 5

    result = solve(
        [ready, harvested], plot_days, cluster, forecast,
        rain_threshold_mm=5.0, today=today,
        harvested_plot_ids=frozenset({"harvested"}),
    )

    assert {d.plot_id for d in result} == {"ready", "harvested"}
    by_id = {d.plot_id: d for d in result}
    assert by_id["ready"].outcome == PlotOutcome.FITS
    assert by_id["harvested"].outcome == PlotOutcome.HARVESTED
