"""Phase 2 gate: given the 8 seeded Kamatchipuram plots and a rain window,
the solver returns which plots fit, which are contested, and which are
excluded as too green -- identically on every run.

Uses the real seed_cluster.py fixture (real plots, transplant dates, areas,
cluster config) with a synthetic, constant-rate weather series derived from
our own computed climatology (crop_params.KURUVAI_MEAN_GDD_PER_DAY_THENI_ESTIMATED)
rather than live Open-Meteo data, fixed to the 2026-08-16 reference date the
seed script's transplant dates were chosen against. This is what makes the
gate's "identically on every run" requirement actually true forever: a
solver wired to live weather and the real calendar date would give a
different answer depending on when the test happens to execute.
"""

from __future__ import annotations

from datetime import date, timedelta

from harvest_convoy.agronomy import crop_params
from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.scheduling.solver import PlotOutcome, solve
from scripts import seed_cluster

REFERENCE_TODAY = date(2026, 8, 16)


def _synthetic_days(transplant_date: date, today: date) -> list[DailyTemperature]:
    rate = crop_params.KURUVAI_MEAN_GDD_PER_DAY_THENI_ESTIMATED
    mean_temp = crop_params.T_BASE_C + rate
    n = (today - transplant_date).days + 1
    return [
        DailyTemperature(
            date=(transplant_date + timedelta(days=i)).isoformat(),
            t_max_c=mean_temp,
            t_min_c=mean_temp,
        )
        for i in range(n)
    ]


def _run_gate_scenario():
    plot_days = {
        p.plot_id: _synthetic_days(p.transplant_date, REFERENCE_TODAY)
        for p in seed_cluster.PLOTS
    }
    # A tight rain window: only 1 usable day before rain closes it ->
    # 3.5 acre budget, forcing contestation among the ready plots.
    forecast = [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)]
    return solve(
        seed_cluster.PLOTS,
        plot_days,
        seed_cluster.CLUSTER,
        forecast,
        rain_threshold_mm=5.0,
        today=REFERENCE_TODAY,
    )


def test_kamatchipuram_gate_produces_all_three_outcomes() -> None:
    result = _run_gate_scenario()
    by_id = {d.plot_id: d for d in result}

    assert len(result) == 8

    too_green = {pid for pid, d in by_id.items() if d.outcome == PlotOutcome.TOO_GREEN}
    fits = {pid for pid, d in by_id.items() if d.outcome == PlotOutcome.FITS}
    contested = {pid for pid, d in by_id.items() if d.outcome == PlotOutcome.CONTESTED}

    assert too_green == {"p05", "p06", "p07", "p08"}
    assert fits == {"p01", "p02"}
    assert contested == {"p03", "p04"}

    # The most recently transplanted plots must be too green -- not a
    # coincidence, the seed script designed them that way.
    assert "p07" in too_green and "p08" in too_green


def test_too_green_plots_have_no_route_position_or_urgency() -> None:
    result = _run_gate_scenario()
    for d in result:
        if d.outcome == PlotOutcome.TOO_GREEN:
            assert d.route_position is None
            assert d.urgency == 0.0
            assert d.days_past_maturity is None


def test_fits_plots_get_a_route_position_and_contested_plots_do_not() -> None:
    result = _run_gate_scenario()
    for d in result:
        if d.outcome == PlotOutcome.FITS:
            assert d.route_position is not None
        else:
            assert d.route_position is None


def test_gate_scenario_is_identical_across_repeated_runs() -> None:
    result1 = _run_gate_scenario()
    result2 = _run_gate_scenario()
    result3 = _run_gate_scenario()
    assert result1 == result2 == result3
