"""ADR-013 Part 3: a farmer's one-tap "why" on a not_ready or
escalation_resolved_lost message. Hermetic -- FileStorage only, no
live calls, no Bedrock; every answer is a direct read of a
DecisionRecord already in storage, never recomputed.
"""

from __future__ import annotations

from datetime import date

import pytest

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import DecisionRecord
from harvest_convoy.telegram import farmer_why, messages_en, messages_ta, webhook
from harvest_convoy.telegram.client import SendResult

SEASON = "2026-kuruvai"
CLUSTER_ID = "why-cluster"
REAL_FARMER_CHAT_ID = 501
IMPOSTER_CHAT_ID = 66666


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


def _cluster() -> Cluster:
    return Cluster(
        cluster_id=CLUSTER_ID, name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
    )


def _farmer(fid: str, name: str = "Real Farmer", chat_id: int | None = REAL_FARMER_CHAT_ID) -> Farmer:
    return Farmer(farmer_id=fid, name=name, cluster_id=CLUSTER_ID, telegram_chat_id=chat_id)


def _plot(pid: str, fid: str) -> Plot:
    return Plot(
        plot_id=pid, farmer_id=fid, cluster_id=CLUSTER_ID,
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=2.0,
    )


def _not_ready_record(plot_id: str, fid: str, decision_date: str, **overrides) -> DecisionRecord:
    base = dict(
        plot_id=plot_id, farmer_id=fid, cluster_id=CLUSTER_ID,
        season_id=SEASON, decision_date=decision_date,
        accumulated_gdd=950.0, maturity_gdd_used=1637.0, threshold_source="calibrated",
        outcome="too_green", days_past_maturity=None, urgency=0.1, route_position=None,
        rain_threshold_mm=5.0, forecast_horizon_days=16, usable_harvest_days=1,
        machine_capacity_acres_per_day=3.5, capacity_budget_acres=3.5,
        resolved_at=f"{decision_date}T12:00:00+00:00",
    )
    base.update(overrides)
    return DecisionRecord(**base)


def _lost_record(plot_id: str, fid: str, decision_date: str, opponent_id: str, **overrides) -> DecisionRecord:
    base = dict(
        plot_id=plot_id, farmer_id=fid, cluster_id=CLUSTER_ID,
        season_id=SEASON, decision_date=decision_date,
        accumulated_gdd=1700.0, maturity_gdd_used=1637.0, threshold_source="calibrated",
        outcome="contested", days_past_maturity=3, urgency=0.4, route_position=None,
        rain_threshold_mm=5.0, forecast_horizon_days=16, usable_harvest_days=3,
        machine_capacity_acres_per_day=3.5, capacity_budget_acres=10.5,
        opponent_plot_id=opponent_id, rounds_run=3,
        own_claim={"plot_id": plot_id, "bumped_last_season": False, "days_past_maturity": 3},
        opponent_claim={"plot_id": opponent_id, "bumped_last_season": True, "days_past_maturity": 1},
        resolution="escalated_lost", resolved_at=f"{decision_date}T18:00:00+00:00",
    )
    base.update(overrides)
    return DecisionRecord(**base)


# --- farmer_why.why_not_ready_text ---

def test_why_not_ready_includes_date_percent_and_capacity(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_decision_record(_not_ready_record("p1", "f1", "2026-09-09"))

    text = farmer_why.why_not_ready_text(storage, "p1", SEASON, "2026-09-09", language="en")

    assert "9 September 2026" in text
    assert "58%" in text  # round(950.0 / 1637.0 * 100) == 58
    assert "3.5 acres" in text
    assert "didn't affect your plot" in text


def test_why_not_ready_percent_is_capped_at_99(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_decision_record(
        _not_ready_record("p1", "f1", "2026-09-09", accumulated_gdd=1636.9, maturity_gdd_used=1637.0)
    )

    text = farmer_why.why_not_ready_text(storage, "p1", SEASON, "2026-09-09", language="en")

    assert "99%" in text
    assert "100%" not in text


def test_why_not_ready_with_no_record_says_not_recorded_with_the_date(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")

    text = farmer_why.why_not_ready_text(storage, "p1", SEASON, "2026-09-09", language="en")

    assert text == messages_en.why_not_recorded("9 September 2026")


def test_why_not_ready_with_empty_decision_date_never_calls_storage(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")

    text = farmer_why.why_not_ready_text(storage, "p1", SEASON, "", language="en")

    assert text == messages_en.why_not_recorded(None)


# --- farmer_why.why_lost_text ---

def test_why_lost_reason_matches_a_direct_resolution_reason_call(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_farmer(_farmer("f-winner", name="Meena Subramani"))
    storage.put_plot(_plot("p-winner", "f-winner"))
    storage.put_decision_record(_lost_record("p1", "f1", "2026-09-09", "p-winner"))

    text = farmer_why.why_lost_text(storage, "p1", SEASON, "2026-09-09", language="en")

    expected_reason = messages_en.resolution_reason(
        bumped_winner=True, bumped_loser=False,
        winner_days_past_maturity=1, loser_days_past_maturity=3,
    )
    assert text == f"On 9 September 2026, the machine went to Meena Subramani's plot instead -- {expected_reason}."


def test_why_lost_falls_back_to_default_winner_label_when_winner_unresolvable(tmp_path) -> None:
    """Regression guard for a doubled-noun bug found 2026-08-24: the
    fallback used to flow through why_lost_answer's "{name}'s plot"
    template unchanged, producing "the selected plot's plot instead"."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_decision_record(_lost_record("p1", "f1", "2026-09-09", "p-winner"))
    # No winner farmer/plot seeded -- opponent_plot_id doesn't resolve.

    text = farmer_why.why_lost_text(storage, "p1", SEASON, "2026-09-09", language="en")

    assert messages_en.DEFAULT_WINNER_LABEL in text
    assert "plot's plot" not in text

    text_ta = farmer_why.why_lost_text(storage, "p1", SEASON, "2026-09-09", language="ta")
    assert messages_ta.DEFAULT_WINNER_DATIVE in text_ta
    assert "வயல் உடைய வயலுக்கு" not in text_ta


@pytest.mark.parametrize("missing_field", ["own_claim", "opponent_claim"])
def test_why_lost_with_missing_claim_data_says_not_recorded(tmp_path, missing_field) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_decision_record(
        _lost_record("p1", "f1", "2026-09-09", "p-winner", **{missing_field: None})
    )

    text = farmer_why.why_lost_text(storage, "p1", SEASON, "2026-09-09", language="en")

    assert text == messages_en.why_not_recorded("9 September 2026")


def test_why_lost_with_no_record_says_not_recorded(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")

    text = farmer_why.why_lost_text(storage, "p1", SEASON, "2026-09-09", language="en")

    assert text == messages_en.why_not_recorded("9 September 2026")


def test_why_answers_render_in_tamil_when_asked(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_decision_record(_not_ready_record("p1", "f1", "2026-09-09"))

    text = farmer_why.why_not_ready_text(storage, "p1", SEASON, "2026-09-09", language="ta")

    formatted_date = messages_ta.format_date(date(2026, 9, 9))
    assert text == messages_ta.why_not_ready_answer(formatted_date, 58, 3.5, 1)


# --- webhook dispatch and authorization ---

def _why_update(prefix: str, plot_id: str, tapper_id: int | None, decision_date: str = "2026-09-09") -> dict:
    callback_query = {
        "id": f"cbq-{prefix}",
        "data": f"{prefix}:{plot_id}:{SEASON}:{decision_date}",
        "message": {"chat": {"id": REAL_FARMER_CHAT_ID}, "message_id": 1},
    }
    if tapper_id is not None:
        callback_query["from"] = {"id": tapper_id}
    return {"callback_query": callback_query}


def _seed_not_ready(storage) -> None:
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer("f1"))
    storage.put_plot(_plot("p1", "f1"))
    storage.put_decision_record(_not_ready_record("p1", "f1", "2026-09-09"))


@pytest.mark.parametrize("prefix", ["why_notready", "why_lost"])
def test_the_owning_farmers_tap_gets_an_answer(tmp_path, prefix) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_not_ready(storage)
    client = _FakeClient()

    webhook.handle_update(client, _why_update(prefix, "p1", REAL_FARMER_CHAT_ID), storage, SEASON)

    assert len(client.sent_messages) == 1
    assert client.sent_messages[0][0] == REAL_FARMER_CHAT_ID
    assert client.answered_callbacks == [(f"cbq-{prefix}", None, False)]


@pytest.mark.parametrize("prefix", ["why_notready", "why_lost"])
def test_a_non_owning_farmers_tap_is_refused(tmp_path, prefix) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_not_ready(storage)
    client = _FakeClient()

    webhook.handle_update(client, _why_update(prefix, "p1", IMPOSTER_CHAT_ID), storage, SEASON)

    assert client.sent_messages == []
    # f1 (registration.py's default language, "ta") never set an
    # explicit language -- the refusal renders in the plot's own
    # farmer's language, not the tapper's.
    assert client.answered_callbacks == [(f"cbq-{prefix}", messages_ta.unrecognized_action(), True)]


def test_a_tap_with_no_from_field_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_not_ready(storage)
    client = _FakeClient()

    webhook.handle_update(client, _why_update("why_notready", "p1", None), storage, SEASON)

    assert client.sent_messages == []


def test_malformed_callback_data_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_not_ready(storage)
    client = _FakeClient()
    update = {"callback_query": {
        "id": "cbq-bad", "data": "why_notready:p1:only-two-parts",
        "from": {"id": REAL_FARMER_CHAT_ID}, "message": {"chat": {"id": REAL_FARMER_CHAT_ID}, "message_id": 1},
    }}

    webhook.handle_update(client, update, storage, SEASON)

    assert client.sent_messages == []
    assert client.answered_callbacks == [("cbq-bad", messages_ta.unrecognized_action(), True)]


def test_an_unresolvable_plot_id_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_not_ready(storage)
    client = _FakeClient()

    webhook.handle_update(
        client, _why_update("why_notready", "no-such-plot", REAL_FARMER_CHAT_ID), storage, SEASON,
    )

    assert client.sent_messages == []


def test_tapping_twice_sends_the_same_answer_twice(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_not_ready(storage)
    client = _FakeClient()
    update = _why_update("why_notready", "p1", REAL_FARMER_CHAT_ID)

    webhook.handle_update(client, update, storage, SEASON)
    webhook.handle_update(client, update, storage, SEASON)

    assert len(client.sent_messages) == 2
    assert client.sent_messages[0][1] == client.sent_messages[1][1]
