"""watcher.py tests. Weather calls are monkeypatched (same pattern as the
Phase 1 weather-bridging tests) rather than hitting live Open-Meteo -- this
suite is about the watcher's own idempotency/trigger/failure logic, not
weather integration (already covered elsewhere).
"""

from __future__ import annotations

from dataclasses import replace as replace_fn
from datetime import date

import harvest_convoy.watcher as watcher_mod
from harvest_convoy.agents.contracts import AdvocateClaim
from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.telegram.client import SendResult
from harvest_convoy.weather.openmeteo import WeatherError
from scripts import seed_cluster, seed_cluster_naducauvery

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


def test_operator_route_summary_order_matches_farmer_route_positions(tmp_path, monkeypatch) -> None:
    """ADR-013 Prerequisite: the operator's route summary must number
    stops in the same order each farmer was individually told via
    decision.route_position -- previously it numbered them in
    coordinator.py's RunResult.outcomes order (a plot-id sort), which
    could silently disagree with route_position whenever nearest-neighbor
    ordering (scheduling/route.py) didn't happen to match alphabetical
    plot_id order. Three plots, placed so nearest-neighbor order (from
    the machine's start point) is p3, p1, p2 -- deliberately not
    alphabetical -- so a plot-id-sorted rendering would be provably wrong.
    """
    storage = FileStorage(tmp_path / "s.json")
    start_lat, start_lon = 9.865, 77.454

    def _plot_at(pid: str, fid: str, lat: float, lon: float) -> Plot:
        from datetime import timedelta

        return Plot(
            plot_id=pid, farmer_id=fid, cluster_id="c1",
            lat=lat, lon=lon, crop="paddy", variety="ADT45",
            transplant_date=TODAY - timedelta(days=110), area_acres=1.0,
        )

    plots = [
        _plot_at("p1", "f1", 9.900, 77.500),  # 2nd nearest to the start point
        _plot_at("p2", "f2", 9.950, 77.550),  # farthest from the start point
        _plot_at("p3", "f3", 9.866, 77.455),  # nearest to the start point
    ]
    farmers = [
        Farmer(farmer_id="f1", name="Farmer One", cluster_id="c1", telegram_chat_id=101, language="en"),
        Farmer(farmer_id="f2", name="Farmer Two", cluster_id="c1", telegram_chat_id=102, language="en"),
        Farmer(farmer_id="f3", name="Farmer Three", cluster_id="c1", telegram_chat_id=103, language="en"),
    ]
    storage.put_cluster(Cluster(
        cluster_id="c1", name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=start_lat, machine_start_lon=start_lon,
        operator_chat_id=999, operator_language="en",
    ))
    for f in farmers:
        storage.put_farmer(f)
    for p in plots:
        storage.put_plot(p)
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    client = _FakeClient()
    result = watcher_mod.run_daily_watch(
        "c1", "season-1", storage=storage, today=TODAY, telegram_client=client,
        get_claim=_truthful_claim,
    )
    assert result["status"] == "triggered"

    import re

    announced_position = {}
    for chat_id, text, _ in client.sent:
        if chat_id == 999:
            continue
        match = re.search(r"stop #(\d+)", text)
        assert match, f"expected a route-position message for chat {chat_id}, got: {text!r}"
        farmer_name = next(f.name for f in farmers if f.telegram_chat_id == chat_id)
        announced_position[farmer_name] = int(match.group(1))

    operator_text = next(text for chat_id, text, _ in client.sent if chat_id == 999)
    lines_in_order = [
        line for line in operator_text.splitlines()
        if re.match(r"^\d+\. ", line)
    ]
    assert len(lines_in_order) == 3
    for line in lines_in_order:
        position_str, rest = line.split(". ", 1)
        farmer_name = next(f.name for f in farmers if rest.startswith(f.name))
        assert int(position_str) == announced_position[farmer_name], (
            f"operator summary listed {farmer_name} at position {position_str}, "
            f"but they were told {announced_position[farmer_name]}"
        )


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


def test_cluster_id_list_returns_one_summary_per_cluster_in_order(tmp_path, monkeypatch) -> None:
    """ADR-008 Decision 4: passing a list iterates independently -- proven
    here with two clusters in genuinely different states (one already ran
    today, one has no plots), each getting its own correct summary."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(Cluster(
        cluster_id="c1", name="Cluster One", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454, operator_chat_id=999,
    ))
    storage.set_watcher_last_run("c1", TODAY.isoformat())
    storage.put_cluster(Cluster(
        cluster_id="c2", name="Cluster Two", machine_capacity_acres_per_day=3.5,
        machine_start_lat=10.861, machine_start_lon=79.046, operator_chat_id=998,
    ))

    client = _FakeClient()
    results = watcher_mod.run_daily_watch(
        ["c1", "c2"], "season-1", storage=storage, today=TODAY, telegram_client=client,
    )

    assert isinstance(results, list)
    assert len(results) == 2
    assert results[0]["cluster_id"] == "c1"
    assert results[0]["status"] == "already_ran"
    assert results[1]["cluster_id"] == "c2"
    assert results[1]["status"] == "no_plots"  # c2 has no plots seeded
    # c2's check still completed and marked its own day done, independent of c1.
    assert storage.get_watcher_last_run("c2") == TODAY.isoformat()


def test_cluster_id_list_one_clusters_error_does_not_block_the_next(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    # "missing" is never seeded -> cluster_not_found for the first entry.
    storage.put_cluster(Cluster(
        cluster_id="c2", name="Cluster Two", machine_capacity_acres_per_day=3.5,
        machine_start_lat=10.861, machine_start_lon=79.046, operator_chat_id=998,
    ))
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c2", telegram_chat_id=101))
    storage.put_plot(Plot(
        plot_id="p1", farmer_id="f1", cluster_id="c2",
        lat=10.861, lon=79.046, crop="paddy", variety="ADT45",
        transplant_date=TODAY, area_acres=1.0,
    ))
    _patch_weather(monkeypatch, [ForecastDay(f"d{i}", 0.0) for i in range(16)])

    results = watcher_mod.run_daily_watch(
        ["missing", "c2"], "season-1", storage=storage, today=TODAY,
        telegram_client=_FakeClient(),
    )

    assert results[0]["status"] == "error"
    assert results[0]["reason"] == "cluster_not_found"
    # c2 still ran and completed cleanly despite "missing" erroring first.
    assert results[1]["status"] == "no_trigger"
    assert storage.get_watcher_last_run("c2") == TODAY.isoformat()


def test_both_real_seeded_clusters_run_independently_through_the_watcher(
    tmp_path, monkeypatch
) -> None:
    """ADR-008's actual multi-district claim, end to end: both real
    fixture clusters (Kamatchipuram/Theni, Naducauvery/Thanjavur) seeded
    into one storage backend and checked in a single run_daily_watch()
    call, each producing its own independent, correct outcome."""
    storage = FileStorage(tmp_path / "s.json")
    seed_cluster.seed_into_storage(storage)
    seed_cluster_naducauvery.seed_into_storage(storage)
    _patch_weather(
        monkeypatch,
        [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)],  # rain on day 1 -> trigger
    )

    results = watcher_mod.run_daily_watch(
        ["kamatchipuram", "naducauvery"], "season-1", storage=storage, today=TODAY,
        telegram_client=_FakeClient(), get_claim=_truthful_claim,
    )

    assert [r["cluster_id"] for r in results] == ["kamatchipuram", "naducauvery"]
    assert all(r["status"] == "triggered" for r in results)
    assert storage.get_watcher_last_run("kamatchipuram") == TODAY.isoformat()
    assert storage.get_watcher_last_run("naducauvery") == TODAY.isoformat()


def test_triggered_run_creates_a_harvest_confirmation_record_for_each_fits_plot(
    tmp_path, monkeypatch
) -> None:
    """ADR-009 Part 2, Decision 4: the confirmation record is created at
    dispatch time, unconditionally -- before any farmer reply exists."""
    storage = FileStorage(tmp_path / "s.json")
    plots = [_plot("p1", "f1", 110), _plot("p2", "f2", 5)]  # p1 fits, p2 too green
    farmers = [_farmer("f1"), _farmer("f2")]
    _seed(storage, plots, farmers)
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    watcher_mod.run_daily_watch(
        "c1", "2026-kuruvai", storage=storage, today=TODAY, telegram_client=_FakeClient(),
        get_claim=_truthful_claim,
    )

    confirmation = storage.get_harvest_confirmation("p1", "2026-kuruvai")
    assert confirmation is not None
    assert confirmation.farmer_id == "f1"
    assert confirmation.cluster_id == "c1"
    assert confirmation.scheduled_date == TODAY.isoformat()
    assert confirmation.asked_at is None
    assert confirmation.confirmed is None
    # p2 was too green -- never dispatched, no confirmation record at all.
    assert storage.get_harvest_confirmation("p2", "2026-kuruvai") is None


def test_triggered_run_persists_a_decision_record_for_every_plot(tmp_path, monkeypatch) -> None:
    """ADR-010 Part 0.5: the whole point -- a real end-to-end
    run_daily_watch() call must leave a DecisionRecord behind for every
    plot it decided, carrying the real weather/capacity context this
    trigger actually used, not just an in-memory result discarded after
    notifications are sent."""
    storage = FileStorage(tmp_path / "s.json")
    plots = [_plot("p1", "f1", 110), _plot("p2", "f2", 5)]  # p1 fits, p2 too green
    farmers = [_farmer("f1"), _farmer("f2")]
    _seed(storage, plots, farmers)
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    watcher_mod.run_daily_watch(
        "c1", "2026-kuruvai", storage=storage, today=TODAY, telegram_client=_FakeClient(),
        get_claim=_truthful_claim,
    )

    fits_record = storage.get_decision_record("p1", "2026-kuruvai", TODAY.isoformat())
    assert fits_record is not None
    assert fits_record.outcome == "fits"
    assert fits_record.threshold_source == "fallback"  # this fixture cluster has no override
    assert fits_record.rain_threshold_mm == watcher_mod.RAIN_THRESHOLD_MM
    assert fits_record.usable_harvest_days == 1  # one dry day (d0) before rain on d1
    assert fits_record.machine_capacity_acres_per_day == 3.5
    assert fits_record.capacity_budget_acres == 3.5  # 1 usable day x 3.5 acres/day

    too_green_record = storage.get_decision_record("p2", "2026-kuruvai", TODAY.isoformat())
    assert too_green_record is not None
    assert too_green_record.outcome == "too_green"


def test_run_evening_confirmations_sends_prompt_and_sets_asked_at(tmp_path) -> None:
    from harvest_convoy.storage.interface import HarvestConfirmation

    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 110)], [_farmer("f1")])
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p1", farmer_id="f1", cluster_id="c1", season_id="2026-kuruvai",
        scheduled_date=TODAY.isoformat(),
    ))

    client = _FakeClient()
    result = watcher_mod.run_evening_confirmations(
        "c1", "2026-kuruvai", storage=storage, today=TODAY, telegram_client=client
    )

    assert result["asked"] == 1
    assert result["skipped_no_chat_id"] == 0
    chat_ids_sent = {c for c, _, _ in client.sent}
    assert 101 in chat_ids_sent  # f1's chat_id
    confirmation = storage.get_harvest_confirmation("p1", "2026-kuruvai")
    assert confirmation.asked_at is not None


def test_run_evening_confirmations_skips_farmer_with_no_chat_id(tmp_path) -> None:
    from harvest_convoy.storage.interface import HarvestConfirmation

    storage = FileStorage(tmp_path / "s.json")
    farmer_no_chat = Farmer(farmer_id="f1", name="F1", cluster_id="c1")  # no chat_id
    _seed(storage, [_plot("p1", "f1", 110)], [farmer_no_chat])
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p1", farmer_id="f1", cluster_id="c1", season_id="2026-kuruvai",
        scheduled_date=TODAY.isoformat(),
    ))

    client = _FakeClient()
    result = watcher_mod.run_evening_confirmations(
        "c1", "2026-kuruvai", storage=storage, today=TODAY, telegram_client=client
    )

    assert result["asked"] == 0
    assert result["skipped_no_chat_id"] == 1
    assert client.sent == []
    confirmation = storage.get_harvest_confirmation("p1", "2026-kuruvai")
    assert confirmation.asked_at is None  # never gets an asked_at at all


def test_run_evening_confirmations_does_not_reask_already_asked(tmp_path) -> None:
    from harvest_convoy.storage.interface import HarvestConfirmation

    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 110)], [_farmer("f1")])
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p1", farmer_id="f1", cluster_id="c1", season_id="2026-kuruvai",
        scheduled_date=TODAY.isoformat(), asked_at="2026-08-15T18:00:00+00:00",
    ))

    client = _FakeClient()
    result = watcher_mod.run_evening_confirmations(
        "c1", "2026-kuruvai", storage=storage, today=TODAY, telegram_client=client
    )

    assert result["asked"] == 0
    assert result["already_asked"] == 1
    assert client.sent == []


def test_confirmation_status_transitions() -> None:
    from harvest_convoy.storage.interface import HarvestConfirmation

    base = HarvestConfirmation(
        plot_id="p1", farmer_id="f1", cluster_id="c1", season_id="2026-kuruvai",
        scheduled_date=TODAY.isoformat(),
    )

    never_asked = base
    assert watcher_mod.confirmation_status(never_asked, TODAY) == "unknown"

    just_asked = replace_fn(base, asked_at=TODAY.isoformat() + "T18:00:00+00:00")
    assert watcher_mod.confirmation_status(just_asked, TODAY) == "pending"

    from datetime import timedelta
    stale = replace_fn(
        base, asked_at=(TODAY - timedelta(days=3)).isoformat() + "T18:00:00+00:00"
    )
    assert watcher_mod.confirmation_status(stale, TODAY) == "unknown"

    answered_yes = replace_fn(base, confirmed=True, confirmed_at="x")
    assert watcher_mod.confirmation_status(answered_yes, TODAY) == "confirmed_yes"

    answered_no = replace_fn(base, confirmed=False, confirmed_at="x")
    assert watcher_mod.confirmation_status(answered_no, TODAY) == "confirmed_no"


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


# ---------------------------------------------------------------------
# Post-harvest drying-window alerts (ADR-009 Part 4)
# ---------------------------------------------------------------------

from harvest_convoy.storage.interface import HarvestConfirmation  # noqa: E402

SEASON = "2026-kuruvai"


def _confirmed(
    plot_id="p1", farmer_id="f1", confirmed_at=None, drying_alert_sent=False,
) -> HarvestConfirmation:
    return HarvestConfirmation(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id="c1", season_id=SEASON,
        scheduled_date=TODAY.isoformat(), asked_at=f"{TODAY.isoformat()}T18:00:00+00:00",
        confirmed=True, confirmed_at=confirmed_at or f"{TODAY.isoformat()}T19:00:00+00:00",
        drying_alert_sent=drying_alert_sent,
    )


def test_drying_alert_sends_when_rain_in_near_term_forecast(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 110)], [_farmer("f1")])
    storage.put_harvest_confirmation(_confirmed())
    forecast = [ForecastDay("d0", 0.0), ForecastDay("d1", 12.0)] + [ForecastDay(f"d{i}", 0.0) for i in range(2, 16)]
    client = _FakeClient()

    watcher_mod._check_drying_window_alerts(client, storage, _cluster(), SEASON, forecast, TODAY)

    assert len(client.sent) == 1
    assert client.sent[0][0] == 101  # f1's chat_id
    updated = storage.get_harvest_confirmation("p1", SEASON)
    assert updated.drying_alert_sent is True


def test_drying_alert_sends_nothing_when_no_rain_in_near_term_forecast(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 110)], [_farmer("f1")])
    storage.put_harvest_confirmation(_confirmed())
    forecast = [ForecastDay(f"d{i}", 0.0) for i in range(16)]
    client = _FakeClient()

    watcher_mod._check_drying_window_alerts(client, storage, _cluster(), SEASON, forecast, TODAY)

    assert client.sent == []
    assert storage.get_harvest_confirmation("p1", SEASON).drying_alert_sent is False


def test_drying_alert_ignores_rain_outside_the_near_term_window(tmp_path) -> None:
    """Rain on day 10 of a 16-day forecast is not "the next few days" --
    only the first DRYING_WINDOW_DAYS days count."""
    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 110)], [_farmer("f1")])
    storage.put_harvest_confirmation(_confirmed())
    forecast = [ForecastDay(f"d{i}", 0.0) for i in range(9)] + [ForecastDay("d9", 20.0)] + [ForecastDay(f"d{i}", 0.0) for i in range(10, 16)]

    watcher_mod._check_drying_window_alerts(_FakeClient(), storage, _cluster(), SEASON, forecast, TODAY)

    assert storage.get_harvest_confirmation("p1", SEASON).drying_alert_sent is False


def test_drying_alert_does_not_resend_within_the_same_window(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 110)], [_farmer("f1")])
    storage.put_harvest_confirmation(_confirmed(drying_alert_sent=True))
    forecast = [ForecastDay("d0", 12.0)] + [ForecastDay(f"d{i}", 0.0) for i in range(1, 16)]
    client = _FakeClient()

    watcher_mod._check_drying_window_alerts(client, storage, _cluster(), SEASON, forecast, TODAY)

    assert client.sent == []


def test_drying_alert_skips_unconfirmed_harvest(tmp_path) -> None:
    """Scheduled but never confirmed -- Part 1.5's harvest marker alone
    never starts a drying window, only Part 2's confirmed=True does."""
    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 110)], [_farmer("f1")])
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p1", farmer_id="f1", cluster_id="c1", season_id=SEASON,
        scheduled_date=TODAY.isoformat(),  # confirmed stays None
    ))
    forecast = [ForecastDay("d0", 12.0)] + [ForecastDay(f"d{i}", 0.0) for i in range(1, 16)]
    client = _FakeClient()

    watcher_mod._check_drying_window_alerts(client, storage, _cluster(), SEASON, forecast, TODAY)

    assert client.sent == []


def test_drying_alert_skips_after_the_window_closes(tmp_path) -> None:
    from datetime import timedelta

    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 110)], [_farmer("f1")])
    old_confirm = (TODAY - timedelta(days=5)).isoformat() + "T19:00:00+00:00"
    storage.put_harvest_confirmation(_confirmed(confirmed_at=old_confirm))
    forecast = [ForecastDay("d0", 12.0)] + [ForecastDay(f"d{i}", 0.0) for i in range(1, 16)]
    client = _FakeClient()

    watcher_mod._check_drying_window_alerts(client, storage, _cluster(), SEASON, forecast, TODAY)

    assert client.sent == []


def test_drying_alert_skips_farmer_with_no_chat_id(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(Farmer(farmer_id="f1", name="F1", cluster_id="c1"))  # no chat_id
    storage.put_plot(_plot("p1", "f1", 110))
    storage.put_harvest_confirmation(_confirmed())
    forecast = [ForecastDay("d0", 12.0)] + [ForecastDay(f"d{i}", 0.0) for i in range(1, 16)]
    client = _FakeClient()

    watcher_mod._check_drying_window_alerts(client, storage, _cluster(), SEASON, forecast, TODAY)

    assert client.sent == []
    assert storage.get_harvest_confirmation("p1", SEASON).drying_alert_sent is False


def test_drying_alert_fires_from_within_the_real_run_daily_watch_pipeline(
    tmp_path, monkeypatch
) -> None:
    """Integration point: run_daily_watch (not just the helper directly)
    reaches _check_drying_window_alerts using the same forecast it
    already fetched for the scheduling trigger -- no second weather call."""
    storage = FileStorage(tmp_path / "s.json")
    _seed(storage, [_plot("p1", "f1", 5)], [_farmer("f1")])
    storage.put_harvest_confirmation(_confirmed(plot_id="p1", farmer_id="f1"))
    forecast = [ForecastDay("d0", 12.0)] + [ForecastDay(f"d{i}", 0.0) for i in range(1, 16)]
    _patch_weather(monkeypatch, forecast)

    client = _FakeClient()
    watcher_mod.run_daily_watch(
        "c1", SEASON, storage=storage, today=TODAY, telegram_client=client
    )

    chat_ids_sent = {c for c, _, _ in client.sent}
    assert 101 in chat_ids_sent
    assert storage.get_harvest_confirmation("p1", SEASON).drying_alert_sent is True
