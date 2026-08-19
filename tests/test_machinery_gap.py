"""ADR-010 Part 3: machinery_gap.py. All weather calls are monkeypatched
(same pattern as test_watcher.py) -- hermetic, no live calls in the test
suite itself, even though the real script makes live Open-Meteo calls by
design (forward-looking, not a replay).
"""

from __future__ import annotations

from datetime import date, timedelta

import scripts.machinery_gap as mg
from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.storage.file_storage import FileStorage

TODAY = date(2026, 9, 9)


def _cluster(cluster_id: str = "c1", capacity: float = 3.5, override: float | None = None) -> Cluster:
    return Cluster(
        cluster_id=cluster_id, name=f"Cluster {cluster_id}", machine_capacity_acres_per_day=capacity,
        machine_start_lat=9.865, machine_start_lon=77.454, maturity_gdd_override=override,
    )


def _plot(plot_id: str, farmer_id: str, transplant_days_ago: int, area_acres: float, cluster_id: str = "c1") -> Plot:
    return Plot(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id=cluster_id,
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=TODAY - timedelta(days=transplant_days_ago), area_acres=area_acres,
    )


def _synthetic_days(start: date, end: date, daily_gdd_temp: float) -> list[DailyTemperature]:
    n = (end - start).days + 1
    return [
        DailyTemperature(date=(start + timedelta(days=i)).isoformat(), t_max_c=daily_gdd_temp, t_min_c=daily_gdd_temp)
        for i in range(n)
    ]


def _patch_weather(monkeypatch, forecast, mean_temp=30.0):
    def fake_get_daily_temperatures(lat, lon, start, end):
        return _synthetic_days(start, end, mean_temp)

    def fake_get_precipitation_forecast(lat, lon, start, end):
        return forecast

    monkeypatch.setattr(mg, "get_daily_temperatures", fake_get_daily_temperatures)
    monkeypatch.setattr(mg, "get_precipitation_forecast", fake_get_precipitation_forecast)


def test_shortfall_computed_against_hand_math(tmp_path, monkeypatch) -> None:
    """1 usable day x 3.5 acres/day = 3.5 acres capacity. One 5.0-acre
    ready plot -> shortfall = 5.0 - 3.5 = 1.5 acres exactly, machine-days
    = 1.5 / 3.5 = 0.42857142857142855 exactly."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(capacity=3.5))
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c1"))
    storage.put_plot(_plot("p1", "f1", transplant_days_ago=110, area_acres=5.0))
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    result = mg.build_result(
        storage, cluster_ids=["c1"], today=TODAY, window_start=None, window_end=None,
    )
    c = result.clusters[0]

    assert c.usable_days == 1
    assert c.capacity_available_acres == 3.5
    assert c.acres_needing_harvest == 5.0
    assert c.shortfall_acres == 1.5
    assert c.surplus_acres == 0.0
    assert abs(c.machine_days_needed - (1.5 / 3.5)) < 1e-9
    assert c.machines_needed == 1  # ceil(0.4286) == 1
    assert c.farmers_affected == ["f1"]
    assert c.farmers_affected_acres == 5.0


def test_surplus_reported_not_a_negative_shortfall(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(capacity=3.5))
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c1"))
    storage.put_plot(_plot("p1", "f1", transplant_days_ago=110, area_acres=1.0))
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 0.0), ForecastDay("d2", 20.0)])

    result = mg.build_result(
        storage, cluster_ids=["c1"], today=TODAY, window_start=None, window_end=None,
    )
    c = result.clusters[0]

    assert c.usable_days == 2
    assert c.capacity_available_acres == 7.0
    assert c.shortfall_acres == 0.0
    assert c.surplus_acres == 6.0
    assert "surplus" in c.headline.lower()


def test_no_rain_in_forecast_reports_zero_gap_with_a_reason(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c1"))
    storage.put_plot(_plot("p1", "f1", transplant_days_ago=110, area_acres=999.0))  # huge demand
    _patch_weather(monkeypatch, [ForecastDay(f"d{i}", 0.0) for i in range(16)])  # no breach at all

    result = mg.build_result(
        storage, cluster_ids=["c1"], today=TODAY, window_start=None, window_end=None,
    )
    c = result.clusters[0]

    assert c.shortfall_acres == 0.0
    assert "no rain detected" in c.gap_reason
    assert c.window_end is None


def test_no_ready_plots_reports_zero_gap_with_a_different_reason(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c1"))
    storage.put_plot(_plot("p1", "f1", transplant_days_ago=5, area_acres=2.0))  # freshly transplanted
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    result = mg.build_result(
        storage, cluster_ids=["c1"], today=TODAY, window_start=None, window_end=None,
    )
    c = result.clusters[0]

    assert c.acres_needing_harvest == 0.0
    assert c.shortfall_acres == 0.0
    assert c.surplus_acres == c.capacity_available_acres
    assert "no plots at or past maturity" in c.gap_reason


def test_explicit_window_bypasses_the_live_forecast_call_entirely(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c1"))
    storage.put_plot(_plot("p1", "f1", transplant_days_ago=110, area_acres=2.0))

    def fake_get_daily_temperatures(lat, lon, start, end):
        return _synthetic_days(start, end, 30.0)

    def boom(*args, **kwargs):
        raise AssertionError("get_precipitation_forecast must not be called with an explicit window")

    monkeypatch.setattr(mg, "get_daily_temperatures", fake_get_daily_temperatures)
    monkeypatch.setattr(mg, "get_precipitation_forecast", boom)

    result = mg.build_result(
        storage, cluster_ids=["c1"], today=TODAY,
        window_start=TODAY, window_end=TODAY + timedelta(days=4),
    )
    c = result.clusters[0]

    assert c.window_source == "explicit --window-start/--window-end"
    assert c.usable_days == 4
    assert c.error is None


def test_fallback_threshold_surfaces_in_the_result(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(override=None))  # uncalibrated -> fallback
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c1"))
    storage.put_plot(_plot("p1", "f1", transplant_days_ago=110, area_acres=1.0))
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    result = mg.build_result(
        storage, cluster_ids=["c1"], today=TODAY, window_start=None, window_end=None,
    )
    c = result.clusters[0]

    assert c.threshold_source == "fallback"

    text = mg.render_text(result)
    assert "fallback" in text


def test_calibrated_threshold_surfaces_in_the_result(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(override=90.0))
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c1"))
    storage.put_plot(_plot("p1", "f1", transplant_days_ago=5, area_acres=1.0))
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    result = mg.build_result(
        storage, cluster_ids=["c1"], today=TODAY, window_start=None, window_end=None,
    )
    assert result.clusters[0].threshold_source == "calibrated"


def test_cluster_not_found_reports_an_error_not_a_crash(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")

    result = mg.build_result(
        storage, cluster_ids=["ghost"], today=TODAY, window_start=None, window_end=None,
    )

    assert result.clusters[0].error == "cluster not found"
    assert result.aggregate_shortfall_acres == 0.0
    assert any("ghost" in gap for gap in result.data_gaps)


def test_aggregate_sums_across_multiple_clusters(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster("c1", capacity=3.5))
    storage.put_cluster(_cluster("c2", capacity=3.5))
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c1"))
    storage.put_farmer(Farmer(farmer_id="f2", name="F2", cluster_id="c2"))
    storage.put_plot(_plot("p1", "f1", transplant_days_ago=110, area_acres=5.0, cluster_id="c1"))
    storage.put_plot(_plot("p2", "f2", transplant_days_ago=110, area_acres=4.0, cluster_id="c2"))
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    result = mg.build_result(
        storage, cluster_ids=["c1", "c2"], today=TODAY, window_start=None, window_end=None,
    )

    assert result.aggregate_shortfall_acres == (5.0 - 3.5) + (4.0 - 3.5)
    assert result.aggregate_farmers_affected == 2


def test_main_defaults_to_every_cluster_when_none_given(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "s.json"
    storage = FileStorage(storage_path)
    storage.put_cluster(_cluster("c1"))
    storage.put_cluster(_cluster("c2"))
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])
    monkeypatch.setattr("harvest_convoy.storage.file_storage.DEFAULT_FILE_PATH", storage_path)

    exit_code = mg.main([])
    assert exit_code == 0


def test_no_clusters_in_storage_exits_1(tmp_path, monkeypatch, capsys) -> None:
    storage_path = tmp_path / "s.json"
    FileStorage(storage_path)  # empty
    monkeypatch.setattr("harvest_convoy.storage.file_storage.DEFAULT_FILE_PATH", storage_path)

    exit_code = mg.main([])
    assert exit_code == 1
    assert "no clusters found" in capsys.readouterr().err


def test_window_start_without_window_end_is_a_usage_error(capsys) -> None:
    exit_code = mg.main(["--window-start", "2026-09-09"])
    assert exit_code == 2
    assert "must be given together" in capsys.readouterr().err


def test_no_bedrock_calls_possible_in_this_script(tmp_path, monkeypatch) -> None:
    storage_path = tmp_path / "s.json"
    storage = FileStorage(storage_path)
    storage.put_cluster(_cluster())
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])
    monkeypatch.setattr("harvest_convoy.storage.file_storage.DEFAULT_FILE_PATH", storage_path)

    def _boom(*args, **kwargs):
        raise AssertionError("no Bedrock calls expected in a reporting script")

    monkeypatch.setattr("harvest_convoy.agents.advocate.get_advocate_claim", _boom)

    exit_code = mg.main(["--cluster", "c1"])
    assert exit_code == 0


def test_text_and_json_built_from_one_result_object(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c1"))
    storage.put_plot(_plot("p1", "f1", transplant_days_ago=110, area_acres=5.0))
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    result = mg.build_result(
        storage, cluster_ids=["c1"], today=TODAY, window_start=None, window_end=None,
    )
    text = mg.render_text(result)
    json_dict = mg.to_json_dict(result)

    assert json_dict["clusters"][0]["shortfall_acres"] == 1.5
    assert "1.5" in text
    import json as _json
    assert _json.dumps(json_dict, default=str)
