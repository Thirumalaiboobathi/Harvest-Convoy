from datetime import date

from harvest_convoy.agents.contracts import AdvocateClaim, EscalationPayload
from harvest_convoy.models import Farmer, Plot
from harvest_convoy.storage.fairness import get_ledger_history
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.telegram import webhook
from harvest_convoy.telegram.client import SendResult
from harvest_convoy.telegram.registration import RegistrationState, save_state

SEASON = "2026-kuruvai"


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


def _farmer(plot_id: str, chat_id: int, name: str | None = None, language: str = "en") -> Farmer:
    # language="en" here (not Farmer's own "ta" default) so the existing
    # English-wording assertions below keep testing what they always
    # tested -- see test_registered_escalation_gives_loser_a_tamil_reason
    # below for the Tamil dispatch path, proven explicitly instead.
    return Farmer(
        farmer_id=f"farmer-{plot_id}", name=name or f"Farmer {plot_id}",
        cluster_id="c", telegram_chat_id=chat_id, language=language,
    )


def test_parse_callback_data_valid() -> None:
    parsed = webhook.parse_callback_data("resolve:kamatchipuram:p03:p04:p03")
    assert parsed == ("kamatchipuram", "p03", "p04", "p03")


def test_parse_callback_data_rejects_chosen_plot_not_in_pair() -> None:
    assert webhook.parse_callback_data("resolve:c:p03:p04:p99") is None


def test_parse_callback_data_rejects_malformed() -> None:
    assert webhook.parse_callback_data("garbage") is None
    assert webhook.parse_callback_data("resolve:c:p03:p04") is None


def test_handle_update_dispatches_message_to_registration(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    save_state(RegistrationState(chat_id=42))
    client = _FakeClient()
    update = {"message": {"chat": {"id": 42}, "text": "Kamatchipuram"}}

    webhook.handle_update(client, update, storage, SEASON)

    assert len(client.sent_messages) == 1
    assert client.sent_messages[0][0] == 42


def test_handle_update_with_no_chat_id_is_ignored(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    client = _FakeClient()
    webhook.handle_update(client, {"message": {"text": "hi"}}, storage, SEASON)
    assert client.sent_messages == []


def _claim(plot_id: str, days_past_maturity: int, bumped: bool = False) -> AdvocateClaim:
    return AdvocateClaim(
        plot_id=plot_id, urgency_score=0.3, days_past_maturity=days_past_maturity,
        rain_vulnerability="low", acres=2.0, bumped_last_season=bumped,
        argument="x", concedes=False,
    )


def test_callback_resolves_and_notifies_both_farmers(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    client = _FakeClient()
    farmer_a = _farmer("p03", 111, name="Kannan Raja")
    farmer_b = _farmer("p04", 222, name="Meena Subramani")
    plots = {"p03": (farmer_a, _plot("p03")), "p04": (farmer_b, _plot("p04"))}

    update = {
        "callback_query": {
            "id": "cbq1",
            "data": "resolve:kamatchipuram-test:p03:p04:p03",
            "message": {"chat": {"id": 999}, "message_id": 55},
        }
    }

    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    assert len(client.answered_callbacks) == 1
    popup_text = client.answered_callbacks[0][1]
    assert "p03" not in popup_text  # no raw plot id in a human-read popup
    assert "Kannan Raja" in popup_text
    assert client.edited_markups == [(999, 55, None)]
    assert len(client.sent_messages) == 2
    chat_ids_notified = {m[0] for m in client.sent_messages}
    assert chat_ids_notified == {111, 222}


def test_callback_resolution_writes_a_real_ledger_entry(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    client = _FakeClient()
    farmer_a = _farmer("p03", 111, name="Kannan Raja")
    farmer_b = _farmer("p04", 222, name="Meena Subramani")
    plots = {"p03": (farmer_a, _plot("p03")), "p04": (farmer_b, _plot("p04"))}

    update = {
        "callback_query": {
            "id": "cbq-ledger",
            "data": "resolve:ledger-test:p03:p04:p03",
            "message": {"chat": {"id": 999}, "message_id": 1},
        }
    }
    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    loser_history = get_ledger_history(farmer_b.farmer_id, storage)
    assert len(loser_history) == 1
    assert loser_history[0].season_id == SEASON
    assert loser_history[0].days_bumped == webhook.DEFAULT_DAYS_BUMPED
    assert loser_history[0].outcome == "bumped"

    winner_history = get_ledger_history(farmer_a.farmer_id, storage)
    assert len(winner_history) == 1
    assert winner_history[0].days_bumped == 0
    assert winner_history[0].outcome == "won"


def test_registered_escalation_gives_loser_a_specific_reason(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    client = _FakeClient()
    farmer_a = _farmer("p03", 111, name="Kannan Raja")
    farmer_b = _farmer("p04", 222, name="Meena Subramani")
    plots = {"p03": (farmer_a, _plot("p03")), "p04": (farmer_b, _plot("p04"))}

    escalation = EscalationPayload(
        cluster_id="reason-test", plot_a_id="p03", plot_b_id="p04",
        claim_a=_claim("p03", days_past_maturity=6),
        claim_b=_claim("p04", days_past_maturity=0, bumped=True),
        rounds_run=3, reason="tied",
    )
    webhook.register_escalation(escalation)

    update = {
        "callback_query": {
            "id": "cbq-reason",
            "data": "resolve:reason-test:p03:p04:p03",
            "message": {"chat": {"id": 999}, "message_id": 1},
        }
    }
    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    loser_messages = [m for m in client.sent_messages if m[0] == 222]
    assert len(loser_messages) == 1
    loser_text = loser_messages[0][1]
    assert "Kannan Raja" in loser_text
    assert "6 days" in loser_text
    assert "round" not in loser_text.lower()
    assert "negotiat" not in loser_text.lower()


def test_registered_escalation_gives_a_tamil_registered_loser_a_tamil_reason(tmp_path) -> None:
    """The resolution reason is rendered in the LOSING farmer's own
    language -- found while wiring up Tamil support that the old
    _resolution_reason was hand-authored English with no language
    awareness at all. See ADR-008 Part 2."""
    storage = FileStorage(tmp_path / "storage.json")
    client = _FakeClient()
    farmer_a = _farmer("p03", 111, name="Kannan Raja", language="en")
    farmer_b = _farmer("p04", 222, name="Meena Subramani", language="ta")
    plots = {"p03": (farmer_a, _plot("p03")), "p04": (farmer_b, _plot("p04"))}

    escalation = EscalationPayload(
        cluster_id="reason-test-ta", plot_a_id="p03", plot_b_id="p04",
        claim_a=_claim("p03", days_past_maturity=6),
        claim_b=_claim("p04", days_past_maturity=0, bumped=True),
        rounds_run=3, reason="tied",
    )
    webhook.register_escalation(escalation)

    update = {
        "callback_query": {
            "id": "cbq-reason-ta",
            "data": "resolve:reason-test-ta:p03:p04:p03",
            "message": {"chat": {"id": 999}, "message_id": 1},
        }
    }
    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    loser_messages = [m for m in client.sent_messages if m[0] == 222]
    assert len(loser_messages) == 1
    loser_text = loser_messages[0][1]
    assert "6" in loser_text  # the days-past-maturity number carries through
    assert "நாட்களாக" in loser_text  # Tamil reason text, not English


def test_double_tap_on_resolved_escalation_does_not_renotify(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    client = _FakeClient()
    plots = {"a1": (_farmer("a1", 1), _plot("a1")), "a2": (_farmer("a2", 2), _plot("a2"))}
    update = {
        "callback_query": {
            "id": "cbq-first",
            "data": "resolve:double-tap-test:a1:a2:a1",
            "message": {"chat": {"id": 9}, "message_id": 1},
        }
    }
    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )
    first_notify_count = len(client.sent_messages)

    update2 = dict(update)
    update2["callback_query"] = dict(update["callback_query"], id="cbq-second")
    webhook.handle_update(
        client, update2, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    assert len(client.sent_messages) == first_notify_count  # no new notifications
    assert client.answered_callbacks[-1][1] == "This conflict was already resolved."


def test_double_tap_survives_a_process_restart_via_the_ledger_write(tmp_path) -> None:
    """The in-memory _RESOLVED_ESCALATIONS set is a fast local cache, not
    the real idempotency guarantee -- the durable ledger write is. Clear
    the in-memory cache (simulating a restart) between two resolution
    attempts against the SAME storage, and confirm the second attempt
    still can't double-notify: the loser's ledger write fails cleanly
    because the entry already exists.
    """
    storage = FileStorage(tmp_path / "storage.json")
    plots = {"b1": (_farmer("b1", 1), _plot("b1")), "b2": (_farmer("b2", 2), _plot("b2"))}
    update = {
        "callback_query": {
            "id": "cbq-restart-1",
            "data": "resolve:restart-test:b1:b2:b1",
            "message": {"chat": {"id": 9}, "message_id": 1},
        }
    }

    client1 = _FakeClient()
    webhook.handle_update(
        client1, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )
    assert len(client1.sent_messages) == 2

    # Simulate a restart: fresh in-memory caches, same durable storage.
    webhook._RESOLVED_ESCALATIONS.clear()
    webhook._PENDING_ESCALATIONS.clear()

    client2 = _FakeClient()
    update2 = dict(update)
    update2["callback_query"] = dict(update["callback_query"], id="cbq-restart-2")
    webhook.handle_update(
        client2, update2, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    assert client2.sent_messages == []  # no double notification after "restart"
    assert "already resolved" in client2.answered_callbacks[-1][1].lower()


def test_missing_farmer_lookup_degrades_without_crashing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    client = _FakeClient()
    update = {
        "callback_query": {
            "id": "cbq-missing",
            "data": "resolve:missing-lookup-test:x1:x2:x1",
            "message": {"chat": {"id": 9}, "message_id": 1},
        }
    }
    # default lookup (unset) returns None for everything -- must not raise.
    webhook.handle_update(client, update, storage, SEASON)
    assert client.sent_messages == []  # no farmers found, nothing sent, no crash
