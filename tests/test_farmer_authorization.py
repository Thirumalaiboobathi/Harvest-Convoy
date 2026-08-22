"""ADR-012: the full-callback audit found two more instances of the same
authorization bug class as the escalation-resolve hole (see
test_operator_authorization.py) -- except farmer-owned, not
operator-owned: handle_confirmation_callback and handle_rollover_callback
never checked the tapping user's identity against the plot's actual
farmer at all. Anyone who obtained a confirm:/rollover: callback_data
string could record a "no" (freeing the plot and crediting the fairness
ledger, or excluding a farmer from next season) or a "yes" on someone
else's behalf. Fixed via _is_farmer (webhook.py), the same
callback_query["from"]["id"]-based check _is_operator already used.
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.fairness import get_ledger_history
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import HarvestConfirmation, SeasonRolloverPrompt
from harvest_convoy.telegram import messages_ta, rollover, webhook
from harvest_convoy.telegram.client import SendResult

SEASON = "2026-kuruvai"
SEASON_2 = "2026-samba"
CLUSTER_ID = "farmer-auth-cluster"
REAL_FARMER_CHAT_ID = 301
IMPOSTER_CHAT_ID = 66666


class _FakeClient:
    def __init__(self):
        self.sent_messages: list[tuple] = []
        self.answered_callbacks: list[tuple] = []
        self.edited_markups: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent_messages.append((chat_id, text, reply_markup))
        return SendResult(success=True)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answered_callbacks.append((callback_query_id, text, show_alert))
        return SendResult(success=True)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edited_markups.append((chat_id, message_id, reply_markup))
        return SendResult(success=True)


def _cluster() -> Cluster:
    return Cluster(
        cluster_id=CLUSTER_ID, name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
    )


def _farmer(fid: str = "farmer-auth-f1") -> Farmer:
    return Farmer(
        farmer_id=fid, name="Real Farmer", cluster_id=CLUSTER_ID,
        telegram_chat_id=REAL_FARMER_CHAT_ID,
    )


def _plot(pid: str = "farmer-auth-p1", fid: str = "farmer-auth-f1") -> Plot:
    return Plot(
        plot_id=pid, farmer_id=fid, cluster_id=CLUSTER_ID,
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=2.0,
    )


# --- Harvest confirmation ---

def _confirm_update(answer: str, tapper_id: int | None, plot_id: str = "farmer-auth-p1") -> dict:
    callback_query = {
        "id": "cbq-confirm",
        "data": f"confirm:{plot_id}:{SEASON}:{answer}",
        "message": {"chat": {"id": REAL_FARMER_CHAT_ID}, "message_id": 1},
    }
    if tapper_id is not None:
        callback_query["from"] = {"id": tapper_id}
    return {"callback_query": callback_query}


def _seed_confirmation(storage) -> None:
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer())
    storage.put_plot(_plot())
    storage.mark_plot_harvested("farmer-auth-p1", CLUSTER_ID, SEASON, dispatched_at="2026-08-16")
    storage.put_harvest_confirmation(HarvestConfirmation(
        plot_id="farmer-auth-p1", farmer_id="farmer-auth-f1", cluster_id=CLUSTER_ID,
        season_id=SEASON, scheduled_date="2026-08-16", asked_at="2026-08-16T18:00:00+00:00",
    ))


def test_confirmation_no_tap_from_a_non_farmer_chat_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage)
    client = _FakeClient()

    webhook.handle_update(client, _confirm_update("no", IMPOSTER_CHAT_ID), storage, SEASON)

    assert client.answered_callbacks == [
        ("cbq-confirm", messages_ta.unrecognized_action(), True)
    ]
    # No reversal: plot still harvested, no ledger credit, confirmation
    # record untouched.
    assert "farmer-auth-p1" in storage.get_harvested_plot_ids(CLUSTER_ID, SEASON)
    assert get_ledger_history("farmer-auth-f1", storage) == []
    confirmation = storage.get_harvest_confirmation("farmer-auth-p1", SEASON)
    assert confirmation.confirmed is None


def test_confirmation_yes_tap_from_a_non_farmer_chat_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage)
    client = _FakeClient()

    webhook.handle_update(client, _confirm_update("yes", IMPOSTER_CHAT_ID), storage, SEASON)

    confirmation = storage.get_harvest_confirmation("farmer-auth-p1", SEASON)
    assert confirmation.confirmed is None


def test_confirmation_tap_with_no_from_field_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage)
    client = _FakeClient()

    webhook.handle_update(client, _confirm_update("no", None), storage, SEASON)

    confirmation = storage.get_harvest_confirmation("farmer-auth-p1", SEASON)
    assert confirmation.confirmed is None


def test_the_real_farmers_own_confirmation_tap_still_works(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_confirmation(storage)
    client = _FakeClient()

    webhook.handle_update(
        client, _confirm_update("no", REAL_FARMER_CHAT_ID), storage, SEASON
    )

    assert "farmer-auth-p1" not in storage.get_harvested_plot_ids(CLUSTER_ID, SEASON)
    loser_history = get_ledger_history("farmer-auth-f1", storage)
    assert len(loser_history) == 1
    assert loser_history[0].outcome == "harvest_no_show"


# --- Season rollover ---

def _rollover_update(answer: str, tapper_id: int | None, plot_id: str = "farmer-auth-p1") -> dict:
    callback_query = {
        "id": "cbq-rollover",
        "data": f"rollover:{plot_id}:{SEASON_2}:{answer}",
        "message": {"chat": {"id": REAL_FARMER_CHAT_ID}, "message_id": 1},
    }
    if tapper_id is not None:
        callback_query["from"] = {"id": tapper_id}
    return {"callback_query": callback_query}


def _seed_rollover(storage) -> None:
    storage.put_cluster(_cluster())
    storage.put_farmer(_farmer())
    storage.put_plot(_plot())
    storage.put_season_rollover_prompt(SeasonRolloverPrompt(
        plot_id="farmer-auth-p1", farmer_id="farmer-auth-f1", cluster_id=CLUSTER_ID,
        old_season_id=SEASON, new_season_id=SEASON_2, asked_at="2026-12-01T09:00:00+00:00",
    ))


def test_rollover_no_tap_from_a_non_farmer_chat_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_rollover(storage)
    client = _FakeClient()

    webhook.handle_update(client, _rollover_update("no", IMPOSTER_CHAT_ID), storage, SEASON_2)

    assert client.answered_callbacks == [
        ("cbq-rollover", messages_ta.unrecognized_action(), True)
    ]
    prompt = storage.get_season_rollover_prompt("farmer-auth-p1", SEASON_2)
    assert prompt.replied is None  # untouched, not set to False


def test_rollover_yes_tap_from_a_non_farmer_chat_is_refused(tmp_path) -> None:
    """The more dangerous half: an unauthorized "yes" would previously
    have let the IMPOSTER's own chat start receiving the follow-up date
    prompt and set THIS farmer's new transplant_date."""
    storage = FileStorage(tmp_path / "s.json")
    _seed_rollover(storage)
    client = _FakeClient()

    webhook.handle_update(client, _rollover_update("yes", IMPOSTER_CHAT_ID), storage, SEASON_2)

    prompt = storage.get_season_rollover_prompt("farmer-auth-p1", SEASON_2)
    assert prompt.replied is None
    assert rollover.get_pending_state(IMPOSTER_CHAT_ID) is None
    # The real farmer was never sent the date-prompt follow-up either --
    # nothing at all should have happened.
    assert client.sent_messages == []


def test_the_real_farmers_own_rollover_tap_still_works(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    _seed_rollover(storage)
    client = _FakeClient()

    webhook.handle_update(
        client, _rollover_update("yes", REAL_FARMER_CHAT_ID), storage, SEASON_2
    )

    assert rollover.get_pending_state(REAL_FARMER_CHAT_ID) is not None
    assert len(client.sent_messages) == 1
    assert client.sent_messages[0][0] == REAL_FARMER_CHAT_ID
