"""ADR-011 Part 2: machine breakdown. Weather is monkeypatched (same
pattern as test_watcher.py); Telegram sends are captured via a fake
client -- hermetic, no live calls, no Bedrock (every get_claim here is
injected).
"""

from __future__ import annotations

from datetime import date, timedelta

import harvest_convoy.watcher as watcher_mod
from harvest_convoy.agents.contracts import AdvocateClaim, PlotFacts
from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.storage.fairness import weighted_bump_days
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.telegram import webhook
from harvest_convoy.telegram.client import SendResult

SEASON = "2026-kuruvai"
SEASON_2 = "2026-samba"
TODAY = date(2026, 8, 16)


class _FakeClient:
    def __init__(self):
        self.sent: list[tuple] = []
        self.answered: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return SendResult(success=True)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answered.append((callback_query_id, text, show_alert))
        return SendResult(success=True)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        return SendResult(success=True)


def _cluster(capacity: float = 1.0) -> Cluster:
    return Cluster(
        cluster_id="c1", name="Test Cluster", machine_capacity_acres_per_day=capacity,
        machine_start_lat=9.865, machine_start_lon=77.454, operator_chat_id=999,
    )


def _farmer(fid: str, chat_id: int | None) -> Farmer:
    return Farmer(farmer_id=fid, name=f"Farmer {fid}", cluster_id="c1", telegram_chat_id=chat_id)


def _plot(pid: str, fid: str, transplant_date: date, area: float = 1.5) -> Plot:
    return Plot(
        plot_id=pid, farmer_id=fid, cluster_id="c1",
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=transplant_date, area_acres=area,
    )


def _synthetic_days(transplant_date, today, daily_gdd_temp=30.0):
    n = (today - transplant_date).days + 1
    return [
        DailyTemperature(
            date=(transplant_date + timedelta(days=i)).isoformat(),
            t_max_c=daily_gdd_temp, t_min_c=daily_gdd_temp,
        )
        for i in range(n)
    ]


def _patch_weather(monkeypatch, forecast, mean_temp=30.0):
    def fake_get_daily_temperatures(lat, lon, start, end):
        return _synthetic_days(start, end, mean_temp)

    def fake_get_precipitation_forecast(lat, lon, start, end):
        return forecast

    monkeypatch.setattr(watcher_mod, "get_daily_temperatures", fake_get_daily_temperatures)
    monkeypatch.setattr(watcher_mod, "get_precipitation_forecast", fake_get_precipitation_forecast)


def _truthful_claim(facts: PlotFacts, round_num, opponent_argument) -> AdvocateClaim:
    return AdvocateClaim.from_facts(facts, argument="x", concedes=not facts.is_ready)


def _seed_two_plots_both_dispatched(storage, monkeypatch, client):
    """Both plots FIT the original 3-usable-day/3.0-acre budget exactly
    (2 x 1.5 acres). p1 transplanted 5 days earlier than p2, so it's
    unambiguously more urgent and ranks first in any recompute."""
    storage.put_cluster(_cluster(capacity=1.0))
    farmer_a = _farmer("f1", 101)
    farmer_b = _farmer("f2", 102)
    storage.put_farmer(farmer_a)
    storage.put_farmer(farmer_b)
    plot_a = _plot("p1", "f1", TODAY - timedelta(days=115))
    plot_b = _plot("p2", "f2", TODAY - timedelta(days=110))
    storage.put_plot(plot_a)
    storage.put_plot(plot_b)
    _patch_weather(monkeypatch, [
        ForecastDay("d0", 0.0), ForecastDay("d1", 0.0), ForecastDay("d2", 0.0), ForecastDay("d3", 20.0),
    ])

    result = watcher_mod.run_daily_watch(
        "c1", SEASON, storage=storage, today=TODAY, telegram_client=client, get_claim=_truthful_claim,
    )
    assert result["status"] == "triggered"
    assert storage.get_harvested_plot_ids("c1", SEASON) == {"p1", "p2"}
    return farmer_a, farmer_b, plot_a, plot_b


def test_full_recompute_un_harvests_and_redispatches(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()
    _seed_two_plots_both_dispatched(storage, monkeypatch, client)
    sent_before = len(client.sent)

    result = watcher_mod.handle_machine_breakdown(
        "c1", SEASON, TODAY, storage=storage, telegram_client=client, get_claim=_truthful_claim,
    )

    assert result["status"] == "recomputed"
    assert result["displaced"] == 2
    # Reduced remaining budget (2 usable days x 1.0 acre/day = 2.0 acres)
    # only covers the more urgent 1.5-acre plot -- the other becomes
    # CONTESTED, no longer dispatched today.
    assert storage.get_harvested_plot_ids("c1", SEASON) == {"p1"}
    assert len(client.sent) > sent_before  # farmers/operator were re-notified


def test_breakdown_displacement_written_never_a_ledger_entry(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()
    farmer_a, farmer_b, _, _ = _seed_two_plots_both_dispatched(storage, monkeypatch, client)

    watcher_mod.handle_machine_breakdown(
        "c1", SEASON, TODAY, storage=storage, telegram_client=client, get_claim=_truthful_claim,
    )

    displacements = storage.get_breakdown_displacements_for_cluster("c1", SEASON)
    assert {d.plot_id for d in displacements} == {"p1", "p2"}
    assert all(d.reason == "machine_breakdown" for d in displacements)
    assert storage.get_ledger_entries("f1") == []
    assert storage.get_ledger_entries("f2") == []


def test_equity_report_shows_breakdown_displacement_separately_from_bumps(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()
    _seed_two_plots_both_dispatched(storage, monkeypatch, client)
    watcher_mod.handle_machine_breakdown(
        "c1", SEASON, TODAY, storage=storage, telegram_client=client, get_claim=_truthful_claim,
    )

    from scripts import equity_report

    result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON])
    section = result.sections[0]

    assert len(section.breakdown_displacements) == 2
    assert result.repeat_bumps == []  # never counted as a bump
    text = equity_report.render_text(result)
    assert "Breakdown displacements: 2 plot-day(s)" in text
    assert "never counted as a fairness bump" in text


def test_cancelled_confirmation_is_skipped_by_evening_sweep(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()
    _seed_two_plots_both_dispatched(storage, monkeypatch, client)
    watcher_mod.handle_machine_breakdown(
        "c1", SEASON, TODAY, storage=storage, telegram_client=client, get_claim=_truthful_claim,
    )
    # p2 is now CONTESTED -- its original confirmation stays cancelled,
    # with no fresh dispatch to replace it.
    p2_confirmation = storage.get_harvest_confirmation("p2", SEASON)
    assert p2_confirmation.cancelled is True

    watcher_mod.run_evening_confirmations(
        "c1", SEASON, storage=storage, today=TODAY, telegram_client=client,
    )

    # p2's own record was never asked about -- the cancelled filter kept
    # it out of the sweep entirely.
    assert storage.get_harvest_confirmation("p2", SEASON).asked_at is None


def test_late_no_tap_on_cancelled_confirmation_does_not_credit_the_ledger(tmp_path, monkeypatch) -> None:
    """The race case named explicitly in the ADR: a breakdown reported
    after the day's confirmations already went out. A truthful "no" tap
    must still not reach record_bump."""
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()
    _, farmer_b, _, plot_b = _seed_two_plots_both_dispatched(storage, monkeypatch, client)

    # Evening confirmations go out BEFORE the breakdown is reported.
    watcher_mod.run_evening_confirmations("c1", SEASON, storage=storage, today=TODAY, telegram_client=client)
    assert storage.get_harvest_confirmation("p2", SEASON).asked_at is not None

    watcher_mod.handle_machine_breakdown(
        "c1", SEASON, TODAY, storage=storage, telegram_client=client, get_claim=_truthful_claim,
    )
    assert storage.get_harvest_confirmation("p2", SEASON).cancelled is True

    # The farmer taps "no" -- truthfully, the machine never came.
    update = {
        "callback_query": {
            "id": "cbq-late-no",
            "data": f"confirm:p2:{SEASON}:no",
            "message": {"chat": {"id": 102}, "message_id": 5},
            "from": {"id": 102},
        }
    }
    webhook.handle_update(client, update, storage, SEASON)

    assert storage.get_harvest_confirmation("p2", SEASON).confirmed is False  # truth on record
    assert storage.get_ledger_entries("f2") == []  # never credited


def test_no_route_that_day_is_a_graceful_noop(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    client = _FakeClient()

    result = watcher_mod.handle_machine_breakdown(
        "c1", SEASON, TODAY, storage=storage, telegram_client=client,
    )

    assert result["status"] == "no_route"
    assert result["displaced"] == 0
    assert storage.get_breakdown_displacements_for_cluster("c1", SEASON) == []


def test_double_tap_is_idempotent(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()
    _seed_two_plots_both_dispatched(storage, monkeypatch, client)

    first = watcher_mod.handle_machine_breakdown(
        "c1", SEASON, TODAY, storage=storage, telegram_client=client, get_claim=_truthful_claim,
    )
    assert first["status"] == "recomputed"

    second = watcher_mod.handle_machine_breakdown(
        "c1", SEASON, TODAY, storage=storage, telegram_client=client, get_claim=_truthful_claim,
    )
    assert second["status"] == "already_reported"
    assert second["displaced"] == 0


def test_wrong_tapper_is_refused(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()
    _seed_two_plots_both_dispatched(storage, monkeypatch, client)

    update = {
        "callback_query": {
            "id": "cbq-wrong",
            "data": f"breakdown:c1:{SEASON}:{TODAY.isoformat()}",
            "message": {"chat": {"id": 999}, "message_id": 1},
            "from": {"id": 55555},  # not the operator (999)
        }
    }
    webhook.handle_update(client, update, storage, SEASON)

    # Nothing was recomputed -- both plots remain harvested from the
    # original dispatch, and no BreakdownDisplacement was written.
    assert storage.get_harvested_plot_ids("c1", SEASON) == {"p1", "p2"}
    assert storage.get_breakdown_displacements_for_cluster("c1", SEASON) == []


def test_breakdown_callback_end_to_end_via_the_route_summary_button(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()
    _seed_two_plots_both_dispatched(storage, monkeypatch, client)

    update = {
        "callback_query": {
            "id": "cbq-breakdown",
            "data": f"breakdown:c1:{SEASON}:{TODAY.isoformat()}",
            "message": {"chat": {"id": 999}, "message_id": 1},
            "from": {"id": 999},  # the real operator
        }
    }
    webhook.handle_update(client, update, storage, SEASON)

    assert len(storage.get_breakdown_displacements_for_cluster("c1", SEASON)) == 2
    # The follow-up (back tomorrow / down indefinitely) keyboard was sent.
    followup_sent = any(
        rm and "breakdown_followup" in str(rm) for _, _, rm in client.sent
    )
    assert followup_sent


def test_down_indefinitely_suppresses_capacity_until_machine_is_back(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1", 101))
    storage.put_plot(_plot("p1", "f1", TODAY - timedelta(days=110), area=1.0))
    client = _FakeClient()

    watcher_mod.set_machine_down("c1", storage=storage, today=TODAY)
    assert storage.get_machine_status("c1").status == "down"

    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])
    result = watcher_mod.run_daily_watch(
        "c1", SEASON, storage=storage, today=TODAY, telegram_client=client, get_claim=_truthful_claim,
    )
    assert result["status"] == "machine_down"
    assert storage.get_harvested_plot_ids("c1", SEASON) == set()

    watcher_mod.clear_machine_down("c1", storage=storage)
    assert storage.get_machine_status("c1") is None

    result2 = watcher_mod.run_daily_watch(
        "c1", SEASON, storage=storage, today=TODAY + timedelta(days=1), telegram_client=client,
        get_claim=_truthful_claim,
    )
    assert result2["status"] == "triggered"
    assert storage.get_harvested_plot_ids("c1", SEASON) == {"p1"}


def _resolve_escalation(
    client, storage, cluster_id, winner_plot_id, loser_plot_id, plots_by_id, farmers_by_id,
    *, operator_chat_id: int = 999,
) -> None:
    update = {
        "callback_query": {
            "id": "cbq-resolve",
            "data": f"resolve:{cluster_id}:{winner_plot_id}:{loser_plot_id}:{winner_plot_id}",
            "message": {"chat": {"id": operator_chat_id}, "message_id": 1},
            "from": {"id": operator_chat_id},
        }
    }

    def lookup(plot_id):
        plot = plots_by_id[plot_id]
        return farmers_by_id[plot.farmer_id], plot

    webhook.handle_update(client, update, storage, SEASON, lookup_farmer_for_plot=lookup)


def _claim(facts: PlotFacts, *, argument: str, concedes: bool, **overrides) -> AdvocateClaim:
    data = dict(
        plot_id=facts.plot_id, urgency_score=facts.urgency,
        days_past_maturity=facts.days_past_maturity, rain_vulnerability=facts.rain_vulnerability,
        acres=facts.acres, bumped_last_season=facts.bumped_last_season,
        weighted_bump_days=facts.weighted_bump_days, argument=argument, concedes=concedes,
    )
    data.update(overrides)
    return AdvocateClaim(**data)


def _tied_claim(facts, round_num, opponent_argument):
    if not facts.is_ready:
        return _claim(facts, argument="not ready", concedes=True)
    return _claim(facts, argument=f"round {round_num}", concedes=False, urgency_score=0.5)


def test_breakdown_displacement_produces_zero_change_in_next_seasons_weighted_bump_days(tmp_path, monkeypatch) -> None:
    """The invariant test, per explicit instruction: not just "no
    LedgerEntry was written," but that the fairness weight a real
    negotiation would read next season is provably unmoved for a
    breakdown-displaced farmer -- contrasted, in the same test, against a
    farmer genuinely bumped by another farmer's stronger claim."""
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()

    # Farmer f-displaced: dispatched, then displaced by a breakdown.
    storage.put_cluster(_cluster(capacity=1.0))
    farmer_displaced = _farmer("f-displaced", 201)
    storage.put_farmer(farmer_displaced)
    storage.put_plot(_plot("p-displaced", "f-displaced", TODAY - timedelta(days=110), area=1.0))
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])
    r1 = watcher_mod.run_daily_watch(
        "c1", SEASON, storage=storage, today=TODAY, telegram_client=client, get_claim=_truthful_claim,
    )
    assert r1["status"] == "triggered"
    assert storage.get_harvested_plot_ids("c1", SEASON) == {"p-displaced"}

    watcher_mod.handle_machine_breakdown(
        "c1", SEASON, TODAY, storage=storage, telegram_client=client, get_claim=_truthful_claim,
    )
    assert storage.get_ledger_entries("f-displaced") == []

    # Farmer f-bumped: genuinely loses a real escalation to f-winner, in
    # a *separate* cluster/day so the two scenarios don't interact.
    storage.put_cluster(Cluster(
        cluster_id="c2", name="Cluster Two", machine_capacity_acres_per_day=1.0,
        machine_start_lat=10.0, machine_start_lon=77.5, operator_chat_id=998,
    ))
    farmer_winner = _farmer("f-winner", 202)
    farmer_bumped = _farmer("f-bumped", 203)
    storage.put_farmer(farmer_winner)
    storage.put_farmer(farmer_bumped)
    plot_winner = Plot(
        plot_id="p-winner", farmer_id="f-winner", cluster_id="c2",
        lat=10.0, lon=77.5, crop="paddy", variety="ADT45",
        transplant_date=TODAY - timedelta(days=110), area_acres=2.0,
    )
    plot_bumped = Plot(
        plot_id="p-bumped", farmer_id="f-bumped", cluster_id="c2",
        lat=10.0, lon=77.5, crop="paddy", variety="ADT45",
        transplant_date=TODAY - timedelta(days=110), area_acres=2.0,
    )
    storage.put_plot(plot_winner)
    storage.put_plot(plot_bumped)

    def fake_temps_c2(lat, lon, start, end):
        return _synthetic_days(start, end, 30.0)

    def fake_forecast_c2(lat, lon, start, end):
        return [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)]

    monkeypatch.setattr(watcher_mod, "get_daily_temperatures", fake_temps_c2)
    monkeypatch.setattr(watcher_mod, "get_precipitation_forecast", fake_forecast_c2)

    r2 = watcher_mod.run_daily_watch(
        "c2", SEASON, storage=storage, today=TODAY, telegram_client=client, get_claim=_tied_claim,
    )
    assert r2["escalations"] == 1
    _resolve_escalation(
        client, storage, "c2", "p-winner", "p-bumped",
        {"p-winner": plot_winner, "p-bumped": plot_bumped},
        {"f-winner": farmer_winner, "f-bumped": farmer_bumped},
        operator_chat_id=998,  # c2's own operator, not c1's (999)
    )
    assert len(storage.get_ledger_entries("f-bumped")) == 1

    # Season rollover for both farmers.
    watcher_mod.run_season_rollover("c1", SEASON, SEASON_2, storage=storage, today=TODAY, telegram_client=client)
    watcher_mod.run_season_rollover("c2", SEASON, SEASON_2, storage=storage, today=TODAY, telegram_client=client)

    def _tap_yes_and_date(plot_id, chat_id, cluster_hint):
        yes_update = {
            "callback_query": {
                "id": f"cbq-{plot_id}", "data": f"rollover:{plot_id}:{SEASON_2}:yes",
                "message": {"chat": {"id": chat_id}, "message_id": 2}, "from": {"id": chat_id},
            }
        }
        webhook.handle_update(client, yes_update, storage, SEASON_2)
        date_update = {
            "message": {"chat": {"id": chat_id}, "text": "1 January 2027", "from": {"id": chat_id, "first_name": "F"}}
        }
        webhook.handle_update(client, date_update, storage, SEASON_2)

    _tap_yes_and_date("p-displaced", 201, "c1")
    _tap_yes_and_date("p-winner", 202, "c2")
    _tap_yes_and_date("p-bumped", 203, "c2")

    # The provable claim: the breakdown-displaced farmer's real
    # weighted_bump_days, as read by the exact function
    # agents/coordinator.py itself uses, is exactly zero next season --
    # while the genuinely bumped farmer's is measurably nonzero.
    displaced_wbd = weighted_bump_days("f-displaced", storage)
    bumped_wbd = weighted_bump_days("f-bumped", storage)
    winner_wbd = weighted_bump_days("f-winner", storage)

    assert displaced_wbd == 0.0
    assert bumped_wbd > 0.0
    assert winner_wbd == 0.0
