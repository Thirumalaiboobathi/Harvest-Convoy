"""ADR-011 Part 4: advance harvest notice. Weather is monkeypatched (same
pattern as test_watcher.py/test_machine_breakdown.py); Telegram sends are
captured via a fake client -- hermetic, no live calls.

Most tests call watcher._check_advance_harvest_notices directly with
watcher_mod.project_maturity_from_days monkeypatched to a fixed date --
this isolates the notice-window/suppression logic from the real GDD math
(already covered by test_calibration.py and the equivalence test below),
the same way test_watcher.py isolates trigger/idempotency logic from
weather integration.
"""

from __future__ import annotations

from datetime import date, timedelta

import harvest_convoy.agronomy.calibration as calibration_mod
import harvest_convoy.watcher as watcher_mod
from harvest_convoy.agronomy.calibration import (
    project_maturity_for_plot,
    project_maturity_from_days,
)
from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import AdvanceNoticeRecord
from harvest_convoy.telegram.client import SendResult
from harvest_convoy.weather.openmeteo import WeatherError

SEASON = "2026-kuruvai"
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


def _farmer(fid: str, chat_id: int | None = 101) -> Farmer:
    return Farmer(farmer_id=fid, name=f"Farmer {fid}", cluster_id="c1", telegram_chat_id=chat_id)


def _plot(pid: str = "p1", fid: str = "f1", transplant_days_ago: int = 60) -> Plot:
    return Plot(
        plot_id=pid, farmer_id=fid, cluster_id="c1",
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=TODAY - timedelta(days=transplant_days_ago),
        area_acres=2.0,
    )


def _seed(storage, plots, farmers):
    storage.put_cluster(_cluster())
    for f in farmers:
        storage.put_farmer(f)
    for p in plots:
        storage.put_plot(p)


def _run_check(monkeypatch, storage, plots, projected_date: date, *, today=TODAY, harvested=None):
    monkeypatch.setattr(
        watcher_mod, "project_maturity_from_days",
        lambda days, transplant_date, cluster, *, today: projected_date.isoformat(),
    )
    client = _FakeClient()
    plot_days = {p.plot_id: [] for p in plots}
    watcher_mod._check_advance_harvest_notices(
        client, storage, _cluster(), SEASON, plots, plot_days, harvested or set(), today,
    )
    return client


def test_sent_at_the_7_day_boundary(monkeypatch, tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    plot = _plot()
    _seed(storage, [plot], [_farmer("f1")])
    projected = TODAY + timedelta(days=7)  # exactly ADVANCE_NOTICE_DAYS_BEFORE_MATURITY

    client = _run_check(monkeypatch, storage, [plot], projected)

    assert len(client.sent) == 1
    assert client.sent[0][0] == 101
    record = storage.get_advance_notice_record("p1", SEASON)
    assert record is not None
    assert record.projected_maturity_date == projected.isoformat()


def test_not_sent_one_day_outside_the_window(monkeypatch, tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    plot = _plot()
    _seed(storage, [plot], [_farmer("f1")])
    projected = TODAY + timedelta(days=8)  # one day past the boundary

    client = _run_check(monkeypatch, storage, [plot], projected)

    assert client.sent == []
    assert storage.get_advance_notice_record("p1", SEASON) is None


def test_never_resent_even_when_a_later_projection_would_differ_materially(
    monkeypatch, tmp_path
) -> None:
    """Decision 17: the AdvanceNoticeRecord's mere existence suppresses
    forever, regardless of what a later, more accurate projection says."""
    storage = FileStorage(tmp_path / "s.json")
    plot = _plot()
    _seed(storage, [plot], [_farmer("f1")])

    first_projection = TODAY + timedelta(days=5)
    client1 = _run_check(monkeypatch, storage, [plot], first_projection)
    assert len(client1.sent) == 1
    original_record = storage.get_advance_notice_record("p1", SEASON)
    assert original_record.projected_maturity_date == first_projection.isoformat()

    # A later trigger day, materially different (but still in-window)
    # projection -- must NOT trigger a second message or update the record.
    later_today = TODAY + timedelta(days=1)
    materially_different_projection = later_today + timedelta(days=3)
    client2 = _run_check(
        monkeypatch, storage, [plot], materially_different_projection, today=later_today,
    )

    assert client2.sent == []
    unchanged_record = storage.get_advance_notice_record("p1", SEASON)
    assert unchanged_record.projected_maturity_date == first_projection.isoformat()


def test_skipped_permanently_when_already_at_or_past_maturity_at_first_check(
    monkeypatch, tmp_path
) -> None:
    """days_until <= 0 the first time a plot is checked -- never sent
    late or same-day, and stays suppressed on later checks too (the
    natural pinning behavior of project_maturity_from_days once real GDD
    has crossed the threshold: remaining GDD floors at 0, so the
    projection is pinned at exactly `today` forever)."""
    storage = FileStorage(tmp_path / "s.json")
    plot = _plot()
    _seed(storage, [plot], [_farmer("f1")])

    # First check: maturity already 3 days past.
    client1 = _run_check(monkeypatch, storage, [plot], TODAY - timedelta(days=3))
    assert client1.sent == []
    assert storage.get_advance_notice_record("p1", SEASON) is None

    # Second check, a later trigger day: pinned at exactly `today` (the
    # real project_maturity_from_days behavior post-maturity) -- still
    # excluded (days_until == 0), still no record, still nothing sent.
    later_today = TODAY + timedelta(days=2)
    client2 = _run_check(monkeypatch, storage, [plot], later_today, today=later_today)
    assert client2.sent == []
    assert storage.get_advance_notice_record("p1", SEASON) is None


def test_plot_registered_after_its_own_notice_window_already_passed(
    monkeypatch, tmp_path
) -> None:
    """A plot registered late enough that its projected maturity is
    already within 0 days (or past) the first time the watcher ever
    checks it -- hits the same days_until<=0 branch, correctly, per the
    ADR's explicit instruction: no same-day notice, ever."""
    storage = FileStorage(tmp_path / "s.json")
    plot = _plot(transplant_days_ago=95)  # a very mature plot, registered late
    _seed(storage, [plot], [_farmer("f1")])

    client = _run_check(monkeypatch, storage, [plot], TODAY)  # days_until == 0

    assert client.sent == []
    assert storage.get_advance_notice_record("p1", SEASON) is None


def test_skipped_for_an_already_harvested_plot(monkeypatch, tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    plot = _plot()
    _seed(storage, [plot], [_farmer("f1")])

    client = _run_check(
        monkeypatch, storage, [plot], TODAY + timedelta(days=3), harvested={"p1"},
    )

    assert client.sent == []
    assert storage.get_advance_notice_record("p1", SEASON) is None


def test_no_reachable_farmer_skips_without_writing_a_record(monkeypatch, tmp_path) -> None:
    """No chat_id (ADR-011's standing failure-path requirement) -- skip
    silently, write nothing, so a later run (once the farmer registers a
    chat_id) can still send it."""
    storage = FileStorage(tmp_path / "s.json")
    plot = _plot()
    _seed(storage, [plot], [_farmer("f1", chat_id=None)])

    client = _run_check(monkeypatch, storage, [plot], TODAY + timedelta(days=3))

    assert client.sent == []
    assert storage.get_advance_notice_record("p1", SEASON) is None


def test_project_maturity_from_days_and_project_maturity_for_plot_agree(monkeypatch) -> None:
    """The refactor (Decision 15) changed nothing about the math --
    proven, not just asserted, by calling both and comparing output for
    identical inputs."""
    cluster = Cluster(
        cluster_id="c", name="c", machine_capacity_acres_per_day=3.5,
        machine_start_lat=10.0, machine_start_lon=77.5,
    )
    today = date(2026, 8, 16)
    transplant_date = today - timedelta(days=40)
    days = [
        DailyTemperature(
            date=(transplant_date + timedelta(days=i)).isoformat(),
            t_max_c=32.0, t_min_c=22.0,
        )
        for i in range((today - transplant_date).days + 1)
    ]

    def fake_get_daily_temperatures(lat, lon, start, end):
        assert start == transplant_date and end == today
        return days

    monkeypatch.setattr(calibration_mod, "get_daily_temperatures", fake_get_daily_temperatures)

    plot = Plot(
        plot_id="p", farmer_id="f", cluster_id="c", lat=10.0, lon=77.5,
        crop="paddy", variety="ADT45", transplant_date=transplant_date, area_acres=2.0,
    )

    via_wrapper = project_maturity_for_plot(plot, cluster, today=today)
    via_pure = project_maturity_from_days(days, transplant_date, cluster, today=today)

    assert via_wrapper == via_pure


def test_weather_unavailable_degrades_via_existing_path_no_notice_no_crash(
    monkeypatch, tmp_path
) -> None:
    """The whole trigger aborts cleanly on WeatherError before
    _check_advance_harvest_notices is ever reached -- no separate case
    needed, and no notice is sent from partial/missing data."""
    storage = FileStorage(tmp_path / "s.json")
    plot = _plot()
    _seed(storage, [plot], [_farmer("f1")])

    def boom(*args, **kwargs):
        raise WeatherError("Open-Meteo unreachable")

    monkeypatch.setattr(watcher_mod, "get_precipitation_forecast", boom)

    client = _FakeClient()
    result = watcher_mod.run_daily_watch(
        "c1", SEASON, storage=storage, today=TODAY, telegram_client=client,
        get_claim=lambda facts, round_num, opponent_argument: None,
    )

    assert result["status"] == "error"
    assert client.sent == []
    assert storage.get_advance_notice_record("p1", SEASON) is None


def test_advance_notice_record_round_trips_through_file_storage(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    record = AdvanceNoticeRecord(
        plot_id="p1", farmer_id="f1", cluster_id="c1", season_id=SEASON,
        sent_at="2026-08-16T10:00:00+00:00", projected_maturity_date="2026-08-23",
    )
    result = storage.put_advance_notice_record(record)
    assert result.success is True

    fetched = storage.get_advance_notice_record("p1", SEASON)
    assert fetched == record
    assert storage.get_advance_notice_record("p1", "other-season") is None
    assert storage.get_advance_notice_record("nonexistent-plot", SEASON) is None
