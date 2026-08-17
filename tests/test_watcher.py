"""watcher.py tests. Weather calls are monkeypatched (same pattern as the
Phase 1 weather-bridging tests) rather than hitting live Open-Meteo -- this
suite is about the watcher's own idempotency/trigger/failure logic, not
weather integration (already covered elsewhere).
"""

from __future__ import annotations

from datetime import date

import harvest_convoy.watcher as watcher_mod
from harvest_convoy.agents.contracts import AdvocateClaim
from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.telegram.client import SendResult
from harvest_convoy.weather.openmeteo import WeatherError

TODAY = date(2026, 8, 16)


class _FakeClient:
    def __init__(self):
        self.sent: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return SendResult(success=True)


def _cluster() -> Cluster:
    return Cluster(
        cluster_id="c1", name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454, operator_chat_id=999,
    )


def _farmer(fid: str) -> Farmer:
    return Farmer(farmer_id=fid, name=f"Farmer {fid}", cluster_id="c1", telegram_chat_id=int(fid[1:]) + 100)


def _plot(pid: str, fid: str, transplant_days_ago: int, area: float = 2.0) -> Plot:
    from datetime import timedelta

    return Plot(
        plot_id=pid, farmer_id=fid, cluster_id="c1",
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=TODAY - timedelta(days=transplant_days_ago),
        area_acres=area,
    )


def _seed(storage, plots, farmers):
    storage.put_cluster(_cluster())
    for f in farmers:
        storage.put_farmer(f)
    for p in plots:
        storage.put_plot(p)


def _synthetic_days(transplant_date, today, daily_gdd_temp):
    from datetime import timedelta

    n = (today - transplant_date).days + 1
    return [
        DailyTemperature(
            date=(transplant_date + timedelta(days=i)).isoformat(),
            t_max_c=daily_gdd_temp, t_min_c=daily_gdd_temp,
        )
        for i in range(n)
    ]


def _truthful_claim(facts, round_num, opponent_argument):
    return AdvocateClaim.from_facts(facts, argument="claim", concedes=not facts.is_ready)


def _patch_weather(monkeypatch, forecast, mean_temp=30.0):
    def fake_get_daily_temperatures(lat, lon, start, end):
        return _synthetic_days(start, end, mean_temp)

    def fake_get_precipitation_forecast(lat, lon, start, end):
        return forecast

    monkeypatch.setattr(watcher_mod, "get_daily_temperatures", fake_get_daily_temperatures)
    monkeypatch.setattr(watcher_mod, "get_precipitation_forecast", fake_get_precipitation_forecast)


def test_second_run_same_day_is_a_noop(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 5)], [_farmer("f1")])
    storage.set_watcher_last_run("c1", TODAY.isoformat())

    client = _FakeClient()
    result = watcher_mod.run_daily_watch(
        "c1", "season-1", storage=storage, today=TODAY, telegram_client=client
    )

    assert result["status"] == "already_ran"
    assert client.sent == []


def test_cluster_not_found(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    result = watcher_mod.run_daily_watch(
        "does-not-exist", "season-1", storage=storage, today=TODAY,
        telegram_client=_FakeClient(),
    )
    assert result["status"] == "error"
    assert result["reason"] == "cluster_not_found"


def test_no_plots_is_a_noop_and_marks_the_day_done(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    result = watcher_mod.run_daily_watch(
        "c1", "season-1", storage=storage, today=TODAY, telegram_client=_FakeClient()
    )
    assert result["status"] == "no_plots"
    assert storage.get_watcher_last_run("c1") == TODAY.isoformat()


def test_open_meteo_down_does_not_mark_the_day_done(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 5)], [_farmer("f1")])

    def boom(*args, **kwargs):
        raise WeatherError("simulated Open-Meteo outage")

    monkeypatch.setattr(watcher_mod, "get_precipitation_forecast", boom)

    client = _FakeClient()
    result = watcher_mod.run_daily_watch(
        "c1", "season-1", storage=storage, today=TODAY, telegram_client=client
    )

    assert result["status"] == "error"
    assert result["reason"] == "weather_unavailable"
    assert storage.get_watcher_last_run("c1") is None  # not marked -- retryable
    assert client.sent == []


def test_no_rain_in_forecast_sends_nothing_and_marks_the_day_done(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 5)], [_farmer("f1")])
    _patch_weather(monkeypatch, [ForecastDay(f"d{i}", 0.0) for i in range(16)])

    client = _FakeClient()
    result = watcher_mod.run_daily_watch(
        "c1", "season-1", storage=storage, today=TODAY, telegram_client=client
    )

    assert result["status"] == "no_trigger"
    assert client.sent == []  # silence is the product
    assert storage.get_watcher_last_run("c1") == TODAY.isoformat()


def test_triggered_run_sends_notifications_and_marks_the_day_done(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    # p1: far past maturity (fits); p2: freshly transplanted (too green)
    plots = [_plot("p1", "f1", 110), _plot("p2", "f2", 5)]
    farmers = [_farmer("f1"), _farmer("f2")]
    _seed(storage, plots, farmers)
    _patch_weather(
        monkeypatch,
        [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)],  # rain on day 1 -> trigger
    )

    client = _FakeClient()
    result = watcher_mod.run_daily_watch(
        "c1", "season-1", storage=storage, today=TODAY, telegram_client=client,
        get_claim=_truthful_claim,
    )

    assert result["status"] == "triggered"
    assert storage.get_watcher_last_run("c1") == TODAY.isoformat()
    # p1 (fits) gets harvest_scheduled, p2 (too green) gets not_ready, plus
    # an operator route summary since at least one plot fits.
    chat_ids_notified = {c for c, _, _ in client.sent}
    assert 101 in chat_ids_notified  # f1 -> chat_id 101
    assert 102 in chat_ids_notified  # f2 -> chat_id 102
    assert 999 in chat_ids_notified  # operator route summary


def test_watcher_fired_twice_same_day_does_not_double_notify(tmp_path, monkeypatch) -> None:
    """The literal failure path: two real invocations of run_daily_watch
    for the same cluster on the same day (e.g. a duplicate EventBridge
    fire), not just a pre-seeded marker."""
    storage = FileStorage(tmp_path / "s.json")
    plots = [_plot("p1", "f1", 110), _plot("p2", "f2", 5)]
    farmers = [_farmer("f1"), _farmer("f2")]
    _seed(storage, plots, farmers)
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    client = _FakeClient()
    result1 = watcher_mod.run_daily_watch(
        "c1", "season-1", storage=storage, today=TODAY, telegram_client=client,
        get_claim=_truthful_claim,
    )
    first_send_count = len(client.sent)
    assert result1["status"] == "triggered"
    assert first_send_count > 0

    result2 = watcher_mod.run_daily_watch(
        "c1", "season-1", storage=storage, today=TODAY, telegram_client=client,
        get_claim=_truthful_claim,
    )

    assert result2["status"] == "already_ran"
    assert len(client.sent) == first_send_count  # no new notifications on the duplicate fire


def test_partial_failure_mid_pipeline_does_not_mark_the_day_done(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 110)], [_farmer("f1")])
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash mid-negotiation")

    monkeypatch.setattr(watcher_mod, "run_cluster", boom)

    result = watcher_mod.run_daily_watch(
        "c1", "season-1", storage=storage, today=TODAY, telegram_client=_FakeClient()
    )

    assert result["status"] == "error"
    assert "RuntimeError" in result["reason"]
    assert storage.get_watcher_last_run("c1") is None  # not marked -- retryable
