from datetime import date

from harvest_convoy.models import Farmer, Plot
from harvest_convoy.telegram import webhook
from harvest_convoy.telegram.client import SendResult
from harvest_convoy.telegram.registration import RegistrationState, save_state


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


def _plot(plot_id: str) -> Plot:
    return Plot(
        plot_id=plot_id, farmer_id=f"farmer-{plot_id}", cluster_id="c",
        lat=10.0, lon=77.5, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=1.0,
    )


def _farmer(plot_id: str, chat_id: int) -> Farmer:
    return Farmer(
        farmer_id=f"farmer-{plot_id}", name="Test Farmer", cluster_id="c",
        telegram_chat_id=chat_id,
    )


def test_parse_callback_data_valid() -> None:
    parsed = webhook.parse_callback_data("resolve:kamatchipuram:p03:p04:p03")
    assert parsed == ("kamatchipuram", "p03", "p04", "p03")


def test_parse_callback_data_rejects_chosen_plot_not_in_pair() -> None:
    assert webhook.parse_callback_data("resolve:c:p03:p04:p99") is None


def test_parse_callback_data_rejects_malformed() -> None:
    assert webhook.parse_callback_data("garbage") is None
    assert webhook.parse_callback_data("resolve:c:p03:p04") is None


def test_handle_update_dispatches_message_to_registration(monkeypatch) -> None:
    save_state(RegistrationState(chat_id=42))
    client = _FakeClient()
    update = {"message": {"chat": {"id": 42}, "text": "Kamatchipuram"}}

    webhook.handle_update(client, update)

    assert len(client.sent_messages) == 1
    assert client.sent_messages[0][0] == 42


def test_handle_update_with_no_chat_id_is_ignored() -> None:
    client = _FakeClient()
    webhook.handle_update(client, {"message": {"text": "hi"}})
    assert client.sent_messages == []


def test_callback_resolves_and_notifies_both_farmers() -> None:
    client = _FakeClient()
    plots = {"p03": (_farmer("p03", 111), _plot("p03")), "p04": (_farmer("p04", 222), _plot("p04"))}

    update = {
        "callback_query": {
            "id": "cbq1",
            "data": "resolve:kamatchipuram-test:p03:p04:p03",
            "message": {"chat": {"id": 999}, "message_id": 55},
        }
    }

    webhook.handle_update(client, update, lookup_farmer_for_plot=lambda pid: plots.get(pid))

    assert len(client.answered_callbacks) == 1
    assert "assigned to p03" in client.answered_callbacks[0][1]
    assert client.edited_markups == [(999, 55, None)]
    assert len(client.sent_messages) == 2
    chat_ids_notified = {m[0] for m in client.sent_messages}
    assert chat_ids_notified == {111, 222}


def test_double_tap_on_resolved_escalation_does_not_renotify() -> None:
    client = _FakeClient()
    plots = {"a1": (_farmer("a1", 1), _plot("a1")), "a2": (_farmer("a2", 2), _plot("a2"))}
    update = {
        "callback_query": {
            "id": "cbq-first",
            "data": "resolve:double-tap-test:a1:a2:a1",
            "message": {"chat": {"id": 9}, "message_id": 1},
        }
    }
    webhook.handle_update(client, update, lookup_farmer_for_plot=lambda pid: plots.get(pid))
    first_notify_count = len(client.sent_messages)

    update2 = dict(update)
    update2["callback_query"] = dict(update["callback_query"], id="cbq-second")
    webhook.handle_update(client, update2, lookup_farmer_for_plot=lambda pid: plots.get(pid))

    assert len(client.sent_messages) == first_notify_count  # no new notifications
    assert client.answered_callbacks[-1][1] == "This conflict was already resolved."


def test_missing_farmer_lookup_degrades_without_crashing() -> None:
    client = _FakeClient()
    update = {
        "callback_query": {
            "id": "cbq-missing",
            "data": "resolve:missing-lookup-test:x1:x2:x1",
            "message": {"chat": {"id": 9}, "message_id": 1},
        }
    }
    # default lookup (unset) returns None for everything -- must not raise.
    webhook.handle_update(client, update)
    assert client.sent_messages == []  # no farmers found, nothing sent, no crash
