"""ADR-009 Part 2: harvest confirmation loop. Two groups of tests:

1. The webhook callback itself (parsing, yes/no/duplicate/not-found/
   season-rollover handling) -- mechanical, `tests/test_webhook.py`-style.
2. The fairness gate, same shape as `test_fairness_ledger_gate.py`: a
   farmer who reports a no-show via a real handle_confirmation_callback
   call (not a direct record_bump()) gains real fairness weight the
   following season -- and, the asymmetry this Part exists to prove,
   silence does NOT. Read together: either alone would be a half-truth.
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.agents.coordinator import (
    CLEAR_MARGIN,
    MAX_FAIRNESS_BONUS,
    build_plot_facts,
    negotiate_pair,
)
from harvest_convoy.agents.contracts import AdvocateClaim
from harvest_convoy.models import Farmer, Plot
from harvest_convoy.scheduling.solver import PlotDecision, PlotOutcome
from harvest_convoy.storage.fairness import operator_follow_through_rate
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import HarvestConfirmation
from harvest_convoy.telegram import webhook
from harvest_convoy.telegram.client import SendResult

SEASON = "2026-kuruvai"


class _FakeClient:
    def __init__(self):
        self.sent_messages: list[tuple] = []
        self.answered_callbacks: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent_messages.append((chat_id, text, reply_markup))
        return SendResult(success=True)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answered_callbacks.append((callback_query_id, text, show_alert))
        return SendResult(success=True)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        return SendResult(success=True)


def _callback_query(plot_id: str, season_id: str, answer: str) -> dict:
    return {
        "id": "cbq1",
        "data": f"confirm:{plot_id}:{season_id}:{answer}",
        "message": {"chat": {"id": 101}, "message_id": 42},
    }


def _seed_confirmation(storage, plot_id="p1", farmer_id="f1", cluster_id="c", season_id=SEASON):
    storage.put_farmer(Farmer(farmer_id=farmer_id, name="Farmer", cluster_id=cluster_id, telegram_chat_id=101))
    storage.mark_plot_harvested(plot_id, cluster_id, season_id, dispatched_at="2026-08-16")
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id=cluster_id, season_id=season_id,
        scheduled_date="2026-08-16", asked_at="2026-08-16T18:00:00+00:00",
    ))


# --- Callback parsing and mechanics ---

def test_parse_confirmation_callback_data_valid() -> None:
    assert webhook.parse_confirmation_callback_data("confirm:p1:2026-kuruvai:yes") == ("p1", "2026-kuruvai", True)
    assert webhook.parse_confirmation_callback_data("confirm:p1:2026-kuruvai:no") == ("p1", "2026-kuruvai", False)


def test_parse_confirmation_callback_data_rejects_malformed() -> None:
    assert webhook.parse_confirmation_callback_data("confirm:p1:2026-kuruvai") is None
    assert webhook.parse_confirmation_callback_data("confirm:p1:2026-kuruvai:maybe") is None
    assert webhook.parse_confirmation_callback_data("resolve:c:p1:p2:p1") is None


def test_yes_corroborates_and_changes_nothing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage)
    client = _FakeClient()

    webhook.handle_confirmation_callback(client, _callback_query("p1", SEASON, "yes"), storage)

    confirmation = storage.get_harvest_confirmation("p1", SEASON)
    assert confirmation.confirmed is True
    assert confirmation.confirmed_at is not None
    # Still harvested -- a "yes" never touches Part 1.5's harvest state.
    assert "p1" in storage.get_harvested_plot_ids("c", SEASON)
    assert storage.get_ledger_entries("f1") == []


def test_no_clears_harvest_state_and_credits_the_ledger(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage)
    client = _FakeClient()

    webhook.handle_confirmation_callback(client, _callback_query("p1", SEASON, "no"), storage)

    confirmation = storage.get_harvest_confirmation("p1", SEASON)
    assert confirmation.confirmed is False
    assert "p1" not in storage.get_harvested_plot_ids("c", SEASON)  # reversal hook fired
    entries = storage.get_ledger_entries("f1")
    assert len(entries) == 1
    assert entries[0].outcome == "harvest_no_show"
    assert entries[0].plot_id == "p1"


def test_duplicate_no_tap_does_not_double_credit_the_ledger(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage)
    client = _FakeClient()

    webhook.handle_confirmation_callback(client, _callback_query("p1", SEASON, "no"), storage)
    webhook.handle_confirmation_callback(client, _callback_query("p1", SEASON, "no"), storage)

    assert len(storage.get_ledger_entries("f1")) == 1
    # Both taps still get a friendly toast -- the farmer isn't shown an error.
    assert len(client.answered_callbacks) == 2


def test_no_then_yes_does_not_reverse_the_already_recorded_bump(tmp_path) -> None:
    """A self-contradicting double-tap: the second answer updates the
    stored `confirmed` value (truth on record), but the irreversible
    side effects (ledger credit, harvest-state clear) already happened
    on the first "no" and are not undone -- same discipline as a late
    reply never retracting an already-recorded bump."""
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage)
    client = _FakeClient()

    webhook.handle_confirmation_callback(client, _callback_query("p1", SEASON, "no"), storage)
    webhook.handle_confirmation_callback(client, _callback_query("p1", SEASON, "yes"), storage)

    confirmation = storage.get_harvest_confirmation("p1", SEASON)
    assert confirmation.confirmed is True  # last write wins, informational
    assert "p1" not in storage.get_harvested_plot_ids("c", SEASON)  # not re-marked harvested
    assert len(storage.get_ledger_entries("f1")) == 1  # not reversed


def test_a_late_reply_after_the_window_closed_is_processed_normally(tmp_path) -> None:
    """No sweep ever wrote anything for silence, so there is nothing to
    reconcile against -- a reply that arrives long after
    UNCONFIRMED_HARVEST_WINDOW_DAYS is handled exactly like an on-time one."""
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage)
    # Backdate asked_at well past the window -- the handler doesn't look
    # at asked_at at all, only confirmation_status() (a read-side
    # diagnostic) does.
    old = storage.get_harvest_confirmation("p1", SEASON)
    from dataclasses import replace
    storage.put_harvest_confirmation(replace(old, asked_at="2026-08-01T18:00:00+00:00"))
    client = _FakeClient()

    webhook.handle_confirmation_callback(client, _callback_query("p1", SEASON, "no"), storage)

    confirmation = storage.get_harvest_confirmation("p1", SEASON)
    assert confirmation.confirmed is False
    assert len(storage.get_ledger_entries("f1")) == 1


def test_unknown_confirmation_gets_a_toast_and_does_not_crash(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()

    webhook.handle_confirmation_callback(client, _callback_query("never-scheduled", SEASON, "no"), storage)

    assert storage.get_harvest_confirmation("never-scheduled", SEASON) is None
    assert storage.get_ledger_entries("f1") == []
    assert len(client.answered_callbacks) == 1


def test_confirmation_for_a_rolled_over_season_only_touches_its_own_season(tmp_path) -> None:
    """A "no" tap for an old season's confirmation clears that plot's
    harvest state only for the season embedded in the callback data --
    the current season's harvested_plot_ids (a different storage key
    entirely) is untouched."""
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage, season_id="2025-kuruvai")
    # The same plot is independently harvested again in the current season.
    storage.mark_plot_harvested("p1", "c", "2026-kuruvai", dispatched_at="2026-08-16")
    client = _FakeClient()

    webhook.handle_confirmation_callback(
        client, _callback_query("p1", "2025-kuruvai", "no"), storage
    )

    assert "p1" not in storage.get_harvested_plot_ids("c", "2025-kuruvai")
    assert "p1" in storage.get_harvested_plot_ids("c", "2026-kuruvai")  # untouched


def test_malformed_confirmation_data_defaults_to_tamil_toast(tmp_path) -> None:
    from harvest_convoy.telegram import messages_ta

    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()
    bad_query = {"id": "cbq1", "data": "confirm:garbage", "message": {}}

    webhook.handle_confirmation_callback(client, bad_query, storage)

    assert client.answered_callbacks[0][1] == messages_ta.unrecognized_action()


# --- operator_follow_through_rate ---

def test_follow_through_rate_is_none_with_no_replies(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage)
    assert operator_follow_through_rate("c", SEASON, storage) is None


def test_follow_through_rate_excludes_pending_and_unknown(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p1", farmer_id="f1", cluster_id="c", season_id=SEASON,
        scheduled_date="2026-08-16", confirmed=True, confirmed_at="x",
    ))
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p2", farmer_id="f2", cluster_id="c", season_id=SEASON,
        scheduled_date="2026-08-16", confirmed=False, confirmed_at="x",
    ))
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="p3", farmer_id="f3", cluster_id="c", season_id=SEASON,
        scheduled_date="2026-08-16",  # never answered -- confirmed stays None
    ))

    rate = operator_follow_through_rate("c", SEASON, storage)

    assert rate == 0.5  # 1 yes / (1 yes + 1 no), p3 excluded entirely


# --- The fairness gate: gains weight on a real no-show, not on silence ---


def _plot(plot_id: str, farmer_id: str) -> Plot:
    return Plot(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id="c",
        lat=9.865, lon=77.454, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=2.0,
    )


def _decision(plot_id: str, days_past_maturity: int, urgency: float) -> PlotDecision:
    return PlotDecision(
        plot_id=plot_id, outcome=PlotOutcome.CONTESTED, accumulated_gdd=2000.0,
        days_past_maturity=days_past_maturity, urgency=urgency, route_position=None,
    )


def _truthful_claim(facts, round_num, opponent_argument) -> AdvocateClaim:
    return AdvocateClaim.from_facts(facts, argument="claim", concedes=False)


def test_a_reported_no_show_gains_fairness_weight_the_following_season(tmp_path) -> None:
    """Same shape as test_fairness_ledger_gate.py's
    test_bumped_farmer_wins_a_close_call_in_the_next_season, except the
    season-1 ledger entry is produced by a real farmer tapping "no" on
    the confirmation prompt (webhook.handle_confirmation_callback), not
    a direct record_bump() call -- proves the whole loop, not just the
    ledger math at the end of it."""
    # webhook.handle_confirmation_callback uses DEFAULT_DAYS_BUMPED (1),
    # not test_fairness_ledger_gate.py's hand-picked 4 -- so this test's
    # urgency gap is tuned for the real, smaller bonus a single reported
    # no-show actually grants: min(MAX_FAIRNESS_BONUS, 1 * 0.01) = 0.01.
    plot_f = _plot("season-plot-f", "farmer_f")
    plot_u = _plot("season-plot-u", "farmer_u")
    decision_f = _decision("season-plot-f", days_past_maturity=8, urgency=0.42)
    decision_u = _decision("season-plot-u", days_past_maturity=6, urgency=0.275)
    assert decision_f.urgency - decision_u.urgency < CLEAR_MARGIN  # a genuine close call
    assert decision_f.urgency - decision_u.urgency + 0.01 > CLEAR_MARGIN  # the real bonus resolves it

    # --- Season 1: farmer_f's plot was dispatched, but the machine never
    # actually came -- reported via a real confirmation tap. ---
    storage_season_1 = FileStorage(tmp_path / "season_1.json")
    storage_season_1.put_farmer(Farmer(farmer_id="farmer_f", name="F", cluster_id="c", telegram_chat_id=101))
    storage_season_1.mark_plot_harvested("season-plot-f", "c", "season-1", dispatched_at="2026-08-16")
    storage_season_1.put_harvest_confirmation(HarvestConfirmation(
        plot_id="season-plot-f", farmer_id="farmer_f", cluster_id="c", season_id="season-1",
        scheduled_date="2026-08-16", asked_at="2026-08-16T18:00:00+00:00",
    ))
    client = _FakeClient()
    webhook.handle_confirmation_callback(
        client, _callback_query("season-plot-f", "season-1", "no"), storage_season_1
    )
    assert "season-plot-f" not in storage_season_1.get_harvested_plot_ids("c", "season-1")

    # --- Season 2: identical plots, identical urgency, only history differs. ---
    storage_season_2 = FileStorage(tmp_path / "season_2.json")
    # Carry the recorded season-1 outcome forward the way a real farmer's
    # ledger would persist across seasons in one real storage backend --
    # simulated here the same way test_fairness_ledger_gate.py does it,
    # by writing the same entry storage_season_1 already produced.
    entry = storage_season_1.get_ledger_entries("farmer_f")[0]
    storage_season_2.put_ledger_entry(entry)

    facts_f_s2 = build_plot_facts(plot_f, decision_f, storage_season_2)
    facts_u_s2 = build_plot_facts(plot_u, decision_u, storage_season_2)
    assert facts_f_s2.weighted_bump_days == entry.days_bumped
    assert facts_u_s2.weighted_bump_days == 0.0

    result_season_2 = negotiate_pair(facts_f_s2, facts_u_s2, _truthful_claim)

    assert result_season_2.escalated is False
    assert result_season_2.winner_plot_id == "season-plot-f"


def test_silence_does_not_gain_fairness_weight_the_following_season(tmp_path) -> None:
    """The asymmetry this Part exists to prove: a dispatched harvest that
    nobody ever confirmed (no tap at all, ever) produces NO ledger entry
    -- season 2 must be unaffected, unlike the reported-no-show case
    above. Same plots, same urgency gap, same close call."""
    plot_f = _plot("season-plot-f", "farmer_f")
    plot_u = _plot("season-plot-u", "farmer_u")
    decision_f = _decision("season-plot-f", days_past_maturity=8, urgency=0.42)
    decision_u = _decision("season-plot-u", days_past_maturity=6, urgency=0.30)

    # --- Season 1: dispatched, asked, window closes, nobody ever replies. ---
    storage_season_1 = FileStorage(tmp_path / "season_1.json")
    storage_season_1.put_farmer(Farmer(farmer_id="farmer_f", name="F", cluster_id="c", telegram_chat_id=101))
    storage_season_1.mark_plot_harvested("season-plot-f", "c", "season-1", dispatched_at="2026-08-16")
    storage_season_1.put_harvest_confirmation(HarvestConfirmation(
        plot_id="season-plot-f", farmer_id="farmer_f", cluster_id="c", season_id="season-1",
        scheduled_date="2026-08-16", asked_at="2026-08-16T18:00:00+00:00",
    ))
    # No callback is ever invoked -- this is the entire point of the test.
    assert storage_season_1.get_ledger_entries("farmer_f") == []

    # --- Season 2: identical plots, identical urgency -- no history to carry. ---
    storage_season_2 = FileStorage(tmp_path / "season_2.json")
    facts_f_s2 = build_plot_facts(plot_f, decision_f, storage_season_2)
    facts_u_s2 = build_plot_facts(plot_u, decision_u, storage_season_2)
    assert facts_f_s2.weighted_bump_days == 0.0  # the asymmetry, pinned down
    assert facts_u_s2.weighted_bump_days == 0.0

    result_season_2 = negotiate_pair(facts_f_s2, facts_u_s2, _truthful_claim)

    # Unchanged from a plain, no-history close call: escalates, same as
    # test_fairness_ledger_gate.py's season-1 (no-history) result.
    assert result_season_2.escalated is True
    assert result_season_2.winner_plot_id is None
