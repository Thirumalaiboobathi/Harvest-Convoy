"""Deterministic tests for the backtest script's pure logic -- shifting
transplant dates, reading first/final outcomes out of a simulated
trigger-day history, and the narrative's "regressed to contested"
finding. No network calls: these construct a ClusterBacktest by hand
instead of running the real fetch-and-simulate pipeline, the same way
scheduling/solver.py's own tests use fixed daily-temperature fixtures
instead of live weather.
"""

from datetime import date

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.solver import PlotOutcome
from scripts.backtest_2025_kuruvai import (
    ClusterBacktest,
    _final_outcome,
    _first_ready_trigger,
    _shift_to_2025,
    format_narrative,
    format_table,
)


def _plot(plot_id: str, month: int, day: int, year: int = 2026) -> Plot:
    return Plot(
        plot_id=plot_id,
        farmer_id=f"f-{plot_id}",
        cluster_id="c1",
        lat=9.0,
        lon=77.0,
        crop="paddy",
        variety="ADT45",
        transplant_date=date(year, month, day),
        area_acres=1.0,
    )


def test_shift_to_2025_preserves_month_and_day_only() -> None:
    plots = [_plot("p01", 5, 1), _plot("p02", 7, 28)]

    shifted = _shift_to_2025(plots)

    assert [p.transplant_date for p in shifted] == [date(2025, 5, 1), date(2025, 7, 28)]
    # originals untouched -- replace() returns a new frozen instance
    assert plots[0].transplant_date.year == 2026


def _cluster_backtest() -> ClusterBacktest:
    cluster = Cluster(
        cluster_id="c1", name="Test Cluster",
        machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.0, machine_start_lon=77.0,
        maturity_gdd_override=1700.0,
    )
    plots = [_plot("p01", 5, 1, 2025)]
    farmers = {"f-p01": Farmer(farmer_id="f-p01", name="Test Farmer", cluster_id="c1")}
    result = ClusterBacktest(cluster, plots, farmers)
    result.projected_maturity["p01"] = "2025-07-28"
    return result


def test_first_ready_trigger_skips_too_green_days() -> None:
    result = _cluster_backtest()
    result.trigger_days = {
        "2025-06-01": {"p01": PlotOutcome.TOO_GREEN},
        "2025-07-28": {"p01": PlotOutcome.FITS},
        "2025-08-05": {"p01": PlotOutcome.CONTESTED},
    }

    d, outcome = _first_ready_trigger(result, "p01")

    assert d == "2025-07-28"
    assert outcome == "fits"


def test_first_ready_trigger_none_when_plot_never_matures() -> None:
    result = _cluster_backtest()
    result.trigger_days = {"2025-06-01": {"p01": PlotOutcome.TOO_GREEN}}

    d, outcome = _first_ready_trigger(result, "p01")

    assert d is None
    assert outcome is None


def test_final_outcome_is_the_chronologically_last_trigger_day() -> None:
    result = _cluster_backtest()
    result.trigger_days = {
        "2025-07-28": {"p01": PlotOutcome.FITS},
        "2025-08-05": {"p01": PlotOutcome.CONTESTED},
        "2025-08-20": {"p01": PlotOutcome.CONTESTED},
    }

    assert _final_outcome(result, "p01") == "contested"


def test_format_table_renders_dash_for_a_plot_that_never_triggered() -> None:
    result = _cluster_backtest()
    result.trigger_days = {}
    result.projected_maturity["p01"] = None

    table = format_table(result)

    assert "not reached in window" in table
    assert "never triggered ready" in table
    assert "- (-)" in table


def test_narrative_flags_plots_that_regressed_from_fits_to_contested() -> None:
    result = _cluster_backtest()
    result.trigger_days = {
        "2025-07-28": {"p01": PlotOutcome.FITS},
        "2025-08-20": {"p01": PlotOutcome.CONTESTED},
    }

    narrative = format_narrative(result)

    assert "1 plot(s) first appeared as FITS but end the simulated season as CONTESTED" in narrative
    assert "p01" in narrative


def test_narrative_omits_regression_line_when_nothing_regressed() -> None:
    result = _cluster_backtest()
    result.trigger_days = {"2025-07-28": {"p01": PlotOutcome.FITS}}

    narrative = format_narrative(result)

    assert "regressed" not in narrative.lower()
    assert "first appeared as FITS but end" not in narrative
