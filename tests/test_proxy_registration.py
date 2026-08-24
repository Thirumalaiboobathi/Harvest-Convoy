"""ADR-013 Part 2: /addfarmer proxy registration. Mirrors
test_registration.py's own testing shape -- the pure state machine
first, then persistence, then the operator-only authorization and the
webhook-level command/callback wiring.
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.models import Cluster
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.telegram import messages_ta, proxy_registration, webhook
from harvest_convoy.telegram.client import SendResult
from harvest_convoy.telegram.proxy_registration import (
    IncomingMessage,
    ProxyRegistrationState,
    ProxyRegistrationStep,
    advance_proxy_registration,
)


def _cluster(operator_chat_id: int = 999) -> Cluster:
    return Cluster(
        cluster_id="c1", name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
        operator_chat_id=operator_chat_id,
    )


class _FakeClient:
    def __init__(self):
        self.sent: list[tuple] = []
        self.answered: list[tuple] = []
        self.edited_markup: list[tuple] = []
        self.edited_text: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return SendResult(success=True)

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        self.answered.append((callback_query_id, text, show_alert))
        return SendResult(success=True)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.edited_markup.append((chat_id, message_id, reply_markup))
        return SendResult(success=True)

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self.edited_text.append((chat_id, message_id, text, reply_markup))
        return SendResult(success=True)


def _complete_proxy_flow(
    operator_chat_id: int, storage, *, has_phone_text: str = "no",
) -> ProxyRegistrationState:
    """Drives the pure state machine through every text step, stopping
    at AWAITING_CONFIRM (the confirm/cancel step is a callback tap, not
    text -- exercised separately)."""
    state, _ = proxy_registration.start_proxy_registration(operator_chat_id, "c1", "en")
    state, _ = advance_proxy_registration(state, IncomingMessage(text="Muthu Pandian"))
    state, _ = advance_proxy_registration(state, IncomingMessage(text="skip"))
    state, _ = advance_proxy_registration(state, IncomingMessage(text="Kamatchipuram"))
    state, _ = advance_proxy_registration(state, IncomingMessage(location=(9.87, 77.46)))
    state, _ = advance_proxy_registration(state, IncomingMessage(text="yes"))
    state, _ = advance_proxy_registration(state, IncomingMessage(text="18 May 2026, 2.5 acres"))
    state, _ = advance_proxy_registration(state, IncomingMessage(text=has_phone_text))
    return state


# ---------------------------------------------------------------------
# Pure state machine
# ---------------------------------------------------------------------

def test_full_flow_reaches_awaiting_confirm_with_all_fields_collected() -> None:
    state = _complete_proxy_flow(999, storage=None, has_phone_text="no")

    assert state.step == ProxyRegistrationStep.AWAITING_CONFIRM
    assert state.farmer_name == "Muthu Pandian"
    assert state.contact_note is None  # "skip" -> None
    assert state.village == "Kamatchipuram"
    assert (state.lat, state.lon) == (9.87, 77.46)
    assert state.transplant_date == date(2026, 5, 18)
    assert state.area_acres == 2.5
    assert state.has_phone is False


def test_contact_note_is_kept_verbatim_when_not_skipped() -> None:
    state, _ = proxy_registration.start_proxy_registration(999, "c1", "en")
    state, _ = advance_proxy_registration(state, IncomingMessage(text="Muthu Pandian"))
    state, _ = advance_proxy_registration(state, IncomingMessage(text="9876543210"))

    assert state.contact_note == "9876543210"
    assert state.step == ProxyRegistrationStep.AWAITING_VILLAGE


def test_empty_farmer_name_re_prompts_without_advancing() -> None:
    state, _ = proxy_registration.start_proxy_registration(999, "c1", "en")
    new_state, outbound = advance_proxy_registration(state, IncomingMessage(text="   "))

    assert new_state.step == ProxyRegistrationStep.AWAITING_FARMER_NAME
    assert "name" in outbound.text.lower()


def test_has_phone_yes_and_no_both_leave_no_persisted_difference_except_the_flag() -> None:
    """Decision 13: has_phone changes only the operator's closing
    message, never what gets persisted -- proven directly here rather
    than only asserted in the ADR."""
    yes_state = _complete_proxy_flow(999, storage=None, has_phone_text="yes")
    no_state = _complete_proxy_flow(999, storage=None, has_phone_text="no")

    assert yes_state.has_phone is True
    assert no_state.has_phone is False


def test_awaiting_confirm_re_shows_the_same_summary_on_a_stray_text_message() -> None:
    state = _complete_proxy_flow(999, storage=None)
    same_state, outbound = advance_proxy_registration(state, IncomingMessage(text="hello"))

    assert same_state == state
    assert outbound.reply_markup is not None


# ---------------------------------------------------------------------
# Persistence (persist_proxy_registration)
# ---------------------------------------------------------------------

def test_persist_proxy_registration_mints_a_proxy_id_and_leaves_no_chat_id(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    state = _complete_proxy_flow(999, storage=None, has_phone_text="no")

    result = proxy_registration.persist_proxy_registration(state, storage)

    assert result is not None
    farmer, plot = result
    assert farmer.farmer_id.startswith("farmer-proxy-")
    assert plot.plot_id.startswith("plot-proxy-")
    assert farmer.telegram_chat_id is None
    assert farmer.name == "Muthu Pandian"
    assert plot.village == "Kamatchipuram"
    assert plot.registered_by == "operator:999"
    assert plot.registered_at is not None
    assert plot.area_acres == 2.5
    assert plot.transplant_date == date(2026, 5, 18)
    # Persisted for real.
    assert storage.get_farmer(farmer.farmer_id) is not None
    assert storage.get_plot(plot.plot_id) is not None


def test_persist_proxy_registration_has_phone_yes_still_leaves_telegram_chat_id_none(tmp_path) -> None:
    """A proxy chat can never supply the farmer's own chat_id, regardless
    of the has_phone answer (ADR-013 Part 2 Decision 15/18)."""
    storage = FileStorage(tmp_path / "s.json")
    state = _complete_proxy_flow(999, storage=None, has_phone_text="yes")

    result = proxy_registration.persist_proxy_registration(state, storage)

    assert result is not None
    farmer, _plot = result
    assert farmer.telegram_chat_id is None


def test_contact_note_is_never_sent_to(tmp_path) -> None:
    """contact_note is stored, but nothing in persist_proxy_registration
    or apply_link ever treats it as a send target."""
    storage = FileStorage(tmp_path / "s.json")
    state, _ = proxy_registration.start_proxy_registration(999, "c1", "en")
    state, _ = advance_proxy_registration(state, IncomingMessage(text="Muthu Pandian"))
    state, _ = advance_proxy_registration(state, IncomingMessage(text="9876543210"))
    state, _ = advance_proxy_registration(state, IncomingMessage(text="Kamatchipuram"))
    state, _ = advance_proxy_registration(state, IncomingMessage(location=(9.87, 77.46)))
    state, _ = advance_proxy_registration(state, IncomingMessage(text="yes"))
    state, _ = advance_proxy_registration(state, IncomingMessage(text="18 May 2026, 2.5 acres"))
    state, _ = advance_proxy_registration(state, IncomingMessage(text="no"))

    result = proxy_registration.persist_proxy_registration(state, storage)

    assert result is not None
    farmer, _plot = result
    assert farmer.contact_note == "9876543210"


# ---------------------------------------------------------------------
# Webhook wiring: /addfarmer authorization + end-to-end confirm
# ---------------------------------------------------------------------

def _addfarmer_update(chat_id: int, text: str) -> dict:
    return {"message": {"chat": {"id": chat_id}, "text": text, "from": {"id": chat_id}}}


def test_addfarmer_from_non_operator_is_refused_and_starts_no_flow(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HARVEST_CONVOY_CLUSTER_ID", "c1")
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_chat_id=999))
    client = _FakeClient()

    webhook.handle_update(client, _addfarmer_update(12345, "/addfarmer"), storage, "2026-kuruvai")

    assert proxy_registration.get_pending_state(12345) is None
    assert client.sent == [(12345, messages_ta.operator_only_command(), None)]


def test_addfarmer_confirm_from_non_operator_is_refused_and_persists_nothing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_chat_id=999))
    proxy_registration.save_state(_complete_proxy_flow(999, storage=None, has_phone_text="no"))
    client = _FakeClient()

    callback_query = {
        "id": "cb1", "data": "addfarmer_confirm:c1:yes",
        "message": {"chat": {"id": 999}, "message_id": 1},
        "from": {"id": 54321},  # not the operator
    }
    webhook.handle_addfarmer_confirm_callback(client, callback_query, storage)

    assert len(storage.get_farmers_for_cluster("c1")) == 0
    # The pending state survives an unauthorized tap -- only an
    # authorized tap may consume it.
    assert proxy_registration.get_pending_state(999) is not None


def test_addfarmer_end_to_end_confirm_yes_persists_and_clears_state(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_chat_id=999))
    client = _FakeClient()

    webhook.handle_update(client, _addfarmer_update(999, "/addfarmer"), storage, "2026-kuruvai")
    for text in ("Muthu Pandian", "skip", "Kamatchipuram"):
        webhook.handle_update(client, _addfarmer_update(999, text), storage, "2026-kuruvai")
    webhook.handle_update(
        client,
        {"message": {"chat": {"id": 999}, "location": {"latitude": 9.87, "longitude": 77.46}, "from": {"id": 999}}},
        storage, "2026-kuruvai",
    )
    for text in ("yes", "18 May 2026, 2.5 acres", "no"):
        webhook.handle_update(client, _addfarmer_update(999, text), storage, "2026-kuruvai")

    state = proxy_registration.get_pending_state(999)
    assert state is not None and state.step == ProxyRegistrationStep.AWAITING_CONFIRM

    callback_query = {
        "id": "cb2", "data": "addfarmer_confirm:c1:yes",
        "message": {"chat": {"id": 999}, "message_id": 1},
        "from": {"id": 999},
    }
    webhook.handle_addfarmer_confirm_callback(client, callback_query, storage)

    assert proxy_registration.get_pending_state(999) is None
    farmers = storage.get_farmers_for_cluster("c1")
    assert len(farmers) == 1
    assert farmers[0].farmer_id.startswith("farmer-proxy-")
    assert farmers[0].telegram_chat_id is None
    # The fuller closing note was sent as a real chat message.
    assert any("Muthu Pandian" in text for _cid, text, _rm in client.sent)


def test_addfarmer_confirm_no_persists_nothing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    storage.put_cluster(_cluster(operator_chat_id=999))
    proxy_registration.save_state(_complete_proxy_flow(999, storage=None, has_phone_text="no"))
    client = _FakeClient()

    callback_query = {
        "id": "cb3", "data": "addfarmer_confirm:c1:no",
        "message": {"chat": {"id": 999}, "message_id": 1},
        "from": {"id": 999},
    }
    webhook.handle_addfarmer_confirm_callback(client, callback_query, storage)

    assert len(storage.get_farmers_for_cluster("c1")) == 0
    assert proxy_registration.get_pending_state(999) is None
