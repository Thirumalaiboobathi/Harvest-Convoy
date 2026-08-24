"""ADR-011 Part 1: seasonal rollover, end to end. Weather is
monkeypatched (same pattern as test_watcher.py); Telegram sends are
captured via a fake client -- hermetic, no live calls, no Bedrock (every
get_claim here is injected, matching every other coordinator test in
this suite).
"""

from __future__ import annotations

from datetime import date, timedelta

import harvest_convoy.watcher as watcher_mod
from harvest_convoy.agents.contracts import AdvocateClaim, PlotFacts
from harvest_convoy.agents.coordinator import (
    CLEAR_MARGIN,
    FAIRNESS_WEIGHT_PER_BUMPED_DAY,
    MAX_FAIRNESS_BONUS,
    build_plot_facts,
    negotiate_pair,
)
from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.scheduling.solver import PlotDecision, PlotOutcome
from harvest_convoy.storage.fairness import weighted_bump_days
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.telegram import webhook
from harvest_convoy.telegram.client import SendResult

SEASON_1 = "2026-kuruvai"
SEASON_2 = "2026-samba"
TODAY_S1 = date(2026, 8, 16)
# 110 days after the rollover reply date used throughout ("10 January
# 2027") -- far enough past transplant for a rolled-over plot to be
# genuinely ready again in season 2, not just re-registered same-day.
TODAY_S2 = date(2027, 4, 30)


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


def _cluster() -> Cluster:
    return Cluster(
        cluster_id="c1", name="Test Cluster", machine_capacity_acres_per_day=1.0,
        machine_start_lat=9.865, machine_start_lon=77.454, operator_chat_id=999,
    )


def _farmer(fid: str, chat_id: int | None) -> Farmer:
    return Farmer(farmer_id=fid, name=f"Farmer {fid}", cluster_id="c1", telegram_chat_id=chat_id)


def _plot(pid: str, fid: str, transplant_date: date, area: float = 2.0) -> Plot:
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
    """Forces every CONTESTED pair to escalate -- same trick
    test_run_cluster_produces_exactly_one_escalation_for_a_tied_contested_pair
    (test_coordinator.py) uses."""
    if not facts.is_ready:
        return _claim(facts, argument="not ready", concedes=True)
    return _claim(facts, argument=f"round {round_num}", concedes=False, urgency_score=0.5)


def _resolve_escalation(client, storage, cluster_id, winner_plot_id, loser_plot_id, plots_by_id, farmers_by_id) -> None:
    update = {
        "callback_query": {
            "id": "cbq-resolve",
            "data": f"resolve:{cluster_id}:{winner_plot_id}:{loser_plot_id}:{winner_plot_id}",
            "message": {"chat": {"id": 999}, "message_id": 1},
            "from": {"id": 999},
        }
    }

    def lookup(plot_id):
        plot = plots_by_id[plot_id]
        return farmers_by_id[plot.farmer_id], plot

    webhook.handle_update(client, update, storage, SEASON_1, lookup_farmer_for_plot=lookup)


def _tap_rollover_yes_and_send_date(client, storage, plot_id, chat_id, new_season_id, date_text) -> None:
    yes_update = {
        "callback_query": {
            "id": f"cbq-roll-{plot_id}",
            "data": f"rollover:{plot_id}:{new_season_id}:yes",
            "message": {"chat": {"id": chat_id}, "message_id": 2},
            "from": {"id": chat_id},
        }
    }
    webhook.handle_update(client, yes_update, storage, new_season_id)

    date_update = {
        "message": {
            "chat": {"id": chat_id},
            "text": date_text,
            "from": {"id": chat_id, "first_name": "Farmer"},
        }
    }
    webhook.handle_update(client, date_update, storage, new_season_id)


def _tap_rollover_no(client, storage, plot_id, chat_id, new_season_id) -> None:
    no_update = {
        "callback_query": {
            "id": f"cbq-roll-no-{plot_id}",
            "data": f"rollover:{plot_id}:{new_season_id}:no",
            "message": {"chat": {"id": chat_id}, "message_id": 2},
            "from": {"id": chat_id},
        }
    }
    webhook.handle_update(client, no_update, storage, new_season_id)


def test_real_rollover_and_ledger_measurably_tilts_season_2(tmp_path, monkeypatch) -> None:
    """The proof this ADR asks for explicitly: season 1 runs and produces
    a real escalation that resolves against one farmer (a real
    LedgerEntry, written through the real webhook resolution path);
    run_season_rollover is triggered; that farmer taps Yes and replies
    with a new date; and the resulting weighted_bump_days, read back from
    storage after all of that, is what a close-call negotiation in season
    2 actually uses -- not a fixture-seeded ledger."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    farmer_a = _farmer("f-p03", 103)
    farmer_b = _farmer("f-p04", 104)
    storage.put_farmer(farmer_a)
    storage.put_farmer(farmer_b)
    transplant = TODAY_S1 - timedelta(days=110)  # far past maturity
    plot_a = _plot("p03", "f-p03", transplant)
    plot_b = _plot("p04", "f-p04", transplant)
    storage.put_plot(plot_a)
    storage.put_plot(plot_b)
    # 1.0 acre/day capacity, two 2.0-acre plots -- neither fits, both CONTESTED.
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    client = _FakeClient()
    result = watcher_mod.run_daily_watch(
        "c1", SEASON_1, storage=storage, today=TODAY_S1, telegram_client=client,
        get_claim=_tied_claim,
    )
    assert result["escalations"] == 1

    plots_by_id = {"p03": plot_a, "p04": plot_b}
    farmers_by_id = {"f-p03": farmer_a, "f-p04": farmer_b}
    _resolve_escalation(client, storage, "c1", "p03", "p04", plots_by_id, farmers_by_id)

    loser_history = storage.get_ledger_entries("f-p04")
    assert len(loser_history) == 1
    assert loser_history[0].outcome == "bumped"

    # Rollover.
    rollover_result = watcher_mod.run_season_rollover(
        "c1", SEASON_1, SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client,
    )
    assert rollover_result["asked"] == 2

    _tap_rollover_yes_and_send_date(client, storage, "p03", 103, SEASON_2, "10 January 2027")
    _tap_rollover_yes_and_send_date(client, storage, "p04", 104, SEASON_2, "10 January 2027")

    assert storage.get_season_rollover_prompt("p03", SEASON_2).replied is True
    assert storage.get_season_rollover_prompt("p04", SEASON_2).replied is True
    assert storage.get_plot("p03").transplant_date == date(2027, 1, 10)
    assert storage.get_plot("p04").transplant_date == date(2027, 1, 10)

    # The real, measurable proof: weighted_bump_days for the previously
    # bumped farmer, read back after walking the entire rollover path.
    real_wbd = weighted_bump_days("f-p04", storage)
    assert real_wbd > 0.0

    bonus = min(MAX_FAIRNESS_BONUS, real_wbd * FAIRNESS_WEIGHT_PER_BUMPED_DAY)
    gap = CLEAR_MARGIN - 0.005  # just under the clear-margin threshold on its own
    urgency_fresh = 0.30
    urgency_bumped = urgency_fresh + gap
    assert gap < CLEAR_MARGIN  # would NOT resolve outright without the real bonus
    assert gap + bonus > CLEAR_MARGIN  # the real bonus is what pushes it over

    storage.put_farmer(_farmer("f-p05", 105))
    fresh_plot = _plot("p05", "f-p05", TODAY_S2 - timedelta(days=90))
    storage.put_plot(fresh_plot)

    decision_bumped = PlotDecision(
        plot_id="p04", outcome=PlotOutcome.CONTESTED, accumulated_gdd=2000.0,
        days_past_maturity=5, urgency=urgency_bumped, route_position=None,
    )
    decision_fresh = PlotDecision(
        plot_id="p05", outcome=PlotOutcome.CONTESTED, accumulated_gdd=2000.0,
        days_past_maturity=5, urgency=urgency_fresh, route_position=None,
    )
    facts_bumped = build_plot_facts(storage.get_plot("p04"), decision_bumped, storage)
    facts_fresh = build_plot_facts(fresh_plot, decision_fresh, storage)
    assert facts_bumped.weighted_bump_days == real_wbd  # the real number, not a stand-in

    def get_claim(facts, round_num, opponent_argument):
        urgency = urgency_bumped if facts.plot_id == "p04" else urgency_fresh
        return _claim(facts, argument="x", concedes=False, urgency_score=urgency)

    negotiation = negotiate_pair(facts_bumped, facts_fresh, get_claim)

    assert negotiation.escalated is False
    assert negotiation.winner_plot_id == "p04"
    assert negotiation.rounds_used == 1


def test_never_replies_excludes_the_plot_from_scheduling(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1", 101))
    storage.put_plot(_plot("p1", "f1", TODAY_S2 - timedelta(days=110)))
    watcher_mod.run_season_rollover("c1", SEASON_1, SEASON_2, storage=storage, today=TODAY_S2, telegram_client=_FakeClient())
    # No reply at all.
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    result = watcher_mod.run_daily_watch(
        "c1", SEASON_2, storage=storage, today=TODAY_S2, telegram_client=_FakeClient(),
    )

    assert result["status"] == "no_plots"


def test_explicit_no_excludes_and_does_not_touch_the_ledger(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    farmer = _farmer("f1", 101)
    storage.put_farmer(farmer)
    storage.put_plot(_plot("p1", "f1", TODAY_S2 - timedelta(days=110)))
    client = _FakeClient()
    watcher_mod.run_season_rollover("c1", SEASON_1, SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client)

    _tap_rollover_no(client, storage, "p1", 101, SEASON_2)

    prompt = storage.get_season_rollover_prompt("p1", SEASON_2)
    assert prompt.replied is False
    assert storage.get_ledger_entries("f1") == []  # declining is not a fairness event

    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])
    result = watcher_mod.run_daily_watch(
        "c1", SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client,
    )
    assert result["status"] == "no_plots"


def test_declined_and_unresponsive_are_distinguishable_not_collapsed(tmp_path, monkeypatch) -> None:
    """The asymmetry test, per explicit instruction: both plots are
    excluded from scheduling identically, but rollover_status() and
    equity_report.py's Season participation section must still report
    them as different facts, not one collapsed 'excluded' state."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f-declined", 101))
    storage.put_farmer(_farmer("f-silent", 102))
    storage.put_plot(_plot("p-declined", "f-declined", TODAY_S2 - timedelta(days=110)))
    storage.put_plot(_plot("p-silent", "f-silent", TODAY_S2 - timedelta(days=110)))
    client = _FakeClient()
    watcher_mod.run_season_rollover("c1", SEASON_1, SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client)
    _tap_rollover_no(client, storage, "p-declined", 101, SEASON_2)
    # p-silent: no reply at all.

    # 1. Both excluded from solve()'s schedulable pool identically.
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])
    result = watcher_mod.run_daily_watch(
        "c1", SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client,
    )
    assert result["status"] == "no_plots"  # both plots excluded, nothing left to check

    # 2. rollover_status() distinguishes them.
    declined_prompt = storage.get_season_rollover_prompt("p-declined", SEASON_2)
    silent_prompt = storage.get_season_rollover_prompt("p-silent", SEASON_2)
    assert watcher_mod.rollover_status(declined_prompt) == "declined"
    assert watcher_mod.rollover_status(silent_prompt) == "unknown"

    # 3. equity_report.py's Season participation section keeps them separate.
    from scripts import equity_report

    eq_result = equity_report.build_result(storage, cluster_id="c1", season_ids=[SEASON_2])
    section = eq_result.sections[0]
    assert section.rollover_declined_plot_ids == ["p-declined"]
    assert section.rollover_unknown_plot_ids == ["p-silent"]
    assert "p-declined" not in section.rollover_unknown_plot_ids
    assert "p-silent" not in section.rollover_declined_plot_ids


def test_harvest_state_resets_across_a_real_rollover(tmp_path, monkeypatch) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    farmer = _farmer("f1", 101)
    storage.put_farmer(farmer)
    storage.put_plot(_plot("p1", "f1", TODAY_S1 - timedelta(days=110), area=0.5))
    client = _FakeClient()
    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])

    result = watcher_mod.run_daily_watch(
        "c1", SEASON_1, storage=storage, today=TODAY_S1, telegram_client=client,
        get_claim=_tied_claim,
    )
    assert result["status"] == "triggered"
    assert storage.get_harvested_plot_ids("c1", SEASON_1) == {"p1"}

    watcher_mod.run_season_rollover("c1", SEASON_1, SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client)
    _tap_rollover_yes_and_send_date(client, storage, "p1", 101, SEASON_2, "10 January 2027")

    # Season 2 has no harvest record at all -- by construction, per
    # ADR-009 Part 1.5 -- and the plot is confirmed to participate.
    assert storage.get_harvested_plot_ids("c1", SEASON_2) == set()

    result_s2 = watcher_mod.run_daily_watch(
        "c1", SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client,
        get_claim=_tied_claim,
    )
    assert result_s2["status"] == "triggered"
    assert storage.get_harvested_plot_ids("c1", SEASON_2) == {"p1"}


def test_idempotent_rerun_does_not_double_prompt(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1", 101))
    storage.put_plot(_plot("p1", "f1", TODAY_S2 - timedelta(days=110)))
    client = _FakeClient()

    first = watcher_mod.run_season_rollover(
        "c1", SEASON_1, SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client,
    )
    assert first["asked"] == 1
    first_send_count = len(client.sent)

    second = watcher_mod.run_season_rollover(
        "c1", SEASON_1, SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client,
    )
    assert second["asked"] == 0
    assert second["skipped_already_prompted"] == 1
    assert len(client.sent) == first_send_count  # no new message sent


def test_farmer_mid_rollover_reply_is_routed_to_rollover_not_registration(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1", 101))
    storage.put_plot(_plot("p1", "f1", TODAY_S2 - timedelta(days=110)))
    client = _FakeClient()
    watcher_mod.run_season_rollover("c1", SEASON_1, SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client)

    _tap_rollover_yes_and_send_date(client, storage, "p1", 101, SEASON_2, "10 January 2027")

    # The date reply must have updated the plot, not produced a plain
    # registration COMPLETE_MESSAGE re-send.
    from harvest_convoy.telegram import messages_en

    last_texts = [t for _, t, _ in client.sent]
    assert not any(t == messages_en.COMPLETE_MESSAGE for t in last_texts)
    assert storage.get_plot("p1").transplant_date == date(2027, 1, 10)


def test_no_chat_id_farmer_writes_no_prompt_and_is_included_by_default(tmp_path, monkeypatch) -> None:
    """Corrected, ADR-013 Part 2 Decision 16: this test used to assert
    the opposite of what it asserts now -- that an unreachable farmer's
    plot got a SeasonRolloverPrompt written specifically so it would be
    *excluded* next season. That was silently wrong for a farmer who is
    unreachable by design (ADR-013 Part 2's proxy-registered,
    notification-less farmers): it would have dropped exactly the
    population that feature exists to include, every single season,
    with no farmer ever able to change that outcome by replying, because
    there was never a message for them to reply to. The corrected
    behavior: no prompt is written at all, and the plot falls through to
    the same "no record means include" default proven by
    test_new_farmer_joining_mid_season_has_no_prompt_and_is_included_by_default
    below -- both cases are now, deliberately, the same mechanism."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1", None))  # no telegram_chat_id
    storage.put_plot(_plot("p1", "f1", TODAY_S2 - timedelta(days=110)))
    client = _FakeClient()

    result = watcher_mod.run_season_rollover(
        "c1", SEASON_1, SEASON_2, storage=storage, today=TODAY_S2, telegram_client=client,
    )

    assert result["skipped_no_chat_id"] == 1
    assert storage.get_season_rollover_prompt("p1", SEASON_2) is None

    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])
    daily_result = watcher_mod.run_daily_watch(
        "c1", SEASON_2, storage=storage, today=TODAY_S2, telegram_client=_FakeClient(),
        get_claim=_tied_claim,
    )
    assert daily_result["status"] == "triggered"


def test_new_farmer_joining_mid_season_has_no_prompt_and_is_included_by_default(tmp_path, monkeypatch) -> None:
    """A brand-new registration for this season -- no SeasonRolloverPrompt
    record exists at all -- must be scheduled normally, not excluded."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f-new", 106))
    storage.put_plot(_plot("p-new", "f-new", TODAY_S2 - timedelta(days=110)))
    assert storage.get_season_rollover_prompt("p-new", SEASON_2) is None

    _patch_weather(monkeypatch, [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)])
    result = watcher_mod.run_daily_watch(
        "c1", SEASON_2, storage=storage, today=TODAY_S2, telegram_client=_FakeClient(),
        get_claim=_tied_claim,
    )
    assert result["status"] == "triggered"
