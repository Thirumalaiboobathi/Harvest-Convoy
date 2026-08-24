from datetime import date

from harvest_convoy.agents.contracts import AdvocateClaim, EscalationPayload
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.fairness import get_ledger_history
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import DecisionRecord
from harvest_convoy.telegram import messages_ta, webhook
from harvest_convoy.telegram.client import SendResult
from harvest_convoy.telegram.registration import RegistrationState, save_state

SEASON = "2026-kuruvai"

# ADR-012: an escalation "resolve:" tap is now checked against the
# cluster's registered operator_chat_id (see _is_operator, webhook.py),
# the same authorization already required for breakdown/machine_back
# taps -- see test_operator_authorization.py for the dedicated refusal
# tests. Every escalation-resolution test below must register a real
# Cluster with this chat_id and tap from it, or the resolution is
# refused before it ever reaches the code being tested.
OPERATOR_CHAT_ID = 900


def _operator_cluster(cluster_id: str, *, operator_language: str = "ta") -> Cluster:
    return Cluster(
        cluster_id=cluster_id, name="Test", machine_capacity_acres_per_day=3.5,
        machine_start_lat=10.0, machine_start_lon=77.5,
        operator_language=operator_language, operator_chat_id=OPERATOR_CHAT_ID,
    )


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


def test_malformed_callback_answer_defaults_to_tamil_not_hardcoded_english(tmp_path) -> None:
    """Regression: webhook.py's four callback-query "toast" answers
    (unrecognized action, already-resolved x2, machine-assigned) were
    hardcoded English strings sent regardless of Cluster.operator_language
    -- the same half-translation pattern as the original location_hint
    bug, caught in the same audit pass. No cluster_id parses out of
    malformed data, so this one can't dispatch by operator_language --
    defaults to Tamil, the product-wide default, not English."""
    storage = FileStorage(tmp_path / "storage.json")
    client = _FakeClient()
    update = {"callback_query": {"id": "cbq-bad", "data": "garbage"}}

    webhook.handle_update(client, update, storage, SEASON)

    assert client.answered_callbacks == [
        ("cbq-bad", messages_ta.unrecognized_action(), True)
    ]


def test_escalation_toast_text_follows_cluster_operator_language(tmp_path) -> None:
    """A registered Cluster with operator_language="en" gets the English
    "Machine assigned to ..." toast, not the Tamil default -- proves the
    dispatch is real, not just a hardcoded fallback that happens to look
    like the right thing."""
    from harvest_convoy.telegram import messages_en

    storage = FileStorage(tmp_path / "storage.json")
    storage.put_cluster(_operator_cluster("en-op-test", operator_language="en"))
    client = _FakeClient()
    farmer_a = _farmer("e1", 111, name="Kannan Raja")
    farmer_b = _farmer("e2", 222, name="Meena Subramani")
    plots = {"e1": (farmer_a, _plot("e1")), "e2": (farmer_b, _plot("e2"))}
    update = {
        "callback_query": {
            "id": "cbq-en",
            "data": "resolve:en-op-test:e1:e2:e1",
            "message": {"chat": {"id": 999}, "message_id": 1},
            "from": {"id": OPERATOR_CHAT_ID},
        }
    }

    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    assert client.answered_callbacks[-1][1] == messages_en.escalation_resolved_assigned(
        "Kannan Raja"
    )


def test_escalation_resolved_assigned_renders_correct_tamil_grammar_both_ways() -> None:
    """A real farmer's name is a proper noun and takes a spaced case
    marker ("{name} க்கு"); the no-farmer-found fallback is a genuine
    Tamil common noun and must FUSE its case marker instead ("வயலுக்கு",
    never "வயல் க்கு") -- see the grammar-rule comment beside
    messages_ta.DEFAULT_WINNER_LABEL. Regression guard for the bug found
    2026-08-24: the fallback used to flow through this function's
    proper-noun template unchanged, producing "வயல் க்கு"."""
    assert messages_ta.escalation_resolved_assigned("Kannan Raja") == (
        "இயந்திரம் Kannan Raja க்கு ஒதுக்கப்பட்டது."
    )
    fallback_text = messages_ta.escalation_resolved_assigned(None)
    assert fallback_text == "இயந்திரம் தேர்ந்தெடுக்கப்பட்ட வயலுக்கு ஒதுக்கப்பட்டது."
    assert "வயல் க்கு" not in fallback_text


def test_route_drop_confirm_prompt_renders_correct_tamil_grammar_both_ways() -> None:
    """Same bug class, same fix shape as escalation_resolved_assigned's
    fallback (see the test above): a real farmer's name is a proper noun
    and takes a hyphenated genitive ("{name}-ன்"); the no-farmer-found
    fallback is a genuine Tamil common noun. Genitive-fusing
    DEFAULT_WINNER_LABEL and reusing the template's own trailing
    "வயலை" would double the noun ("வயலின் வயலை" -- "the selected
    plot's plot"), so DEFAULT_WINNER_ACCUSATIVE stands in for the whole
    fragment instead. Regression guard for the bug found 2026-08-24."""
    assert messages_ta.route_drop_confirm_prompt("Kannan Raja", is_last_plot=False) == (
        "Kannan Raja-ன் வயலை இன்றைய பாதையிலிருந்து நீக்கவா?"
    )
    fallback_text = messages_ta.route_drop_confirm_prompt(None, is_last_plot=False)
    assert fallback_text == "தேர்ந்தெடுக்கப்பட்ட வயலை இன்றைய பாதையிலிருந்து நீக்கவா?"
    assert "வயலின் வயலை" not in fallback_text
    assert "வயல்-ன்" not in fallback_text


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
    storage.put_cluster(_operator_cluster("kamatchipuram-test"))
    client = _FakeClient()
    farmer_a = _farmer("p03", 111, name="Kannan Raja")
    farmer_b = _farmer("p04", 222, name="Meena Subramani")
    plots = {"p03": (farmer_a, _plot("p03")), "p04": (farmer_b, _plot("p04"))}

    update = {
        "callback_query": {
            "id": "cbq1",
            "data": "resolve:kamatchipuram-test:p03:p04:p03",
            "message": {"chat": {"id": 999}, "message_id": 55},
            "from": {"id": OPERATOR_CHAT_ID},
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
    storage.put_cluster(_operator_cluster("ledger-test"))
    client = _FakeClient()
    farmer_a = _farmer("p03", 111, name="Kannan Raja")
    farmer_b = _farmer("p04", 222, name="Meena Subramani")
    plots = {"p03": (farmer_a, _plot("p03")), "p04": (farmer_b, _plot("p04"))}

    update = {
        "callback_query": {
            "id": "cbq-ledger",
            "data": "resolve:ledger-test:p03:p04:p03",
            "message": {"chat": {"id": 999}, "message_id": 1},
            "from": {"id": OPERATOR_CHAT_ID},
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
    # ADR-013 "Resolved on review": an escalation resolution is a human
    # decision, distinguished from an operator override (decided_by=
    # "operator_override") because it resolves a tie the agent's own
    # process asked a human to break, rather than reversing a confident
    # agent decision -- but it is not "agent"-decided either.
    assert loser_history[0].decided_by == "operator_escalation"

    winner_history = get_ledger_history(farmer_a.farmer_id, storage)
    assert len(winner_history) == 1
    assert winner_history[0].days_bumped == 0
    assert winner_history[0].outcome == "won"
    assert winner_history[0].decided_by == "operator_escalation"


def _decision_record_for(plot_id: str, opponent_id: str, cluster_id: str, decision_date: str) -> DecisionRecord:
    return DecisionRecord(
        plot_id=plot_id, farmer_id=f"farmer-{plot_id}", cluster_id=cluster_id,
        season_id=SEASON, decision_date=decision_date,
        accumulated_gdd=1681.4, maturity_gdd_used=1637.0, threshold_source="calibrated",
        outcome="contested", days_past_maturity=6, urgency=0.3, route_position=None,
        rain_threshold_mm=5.0, forecast_horizon_days=16, usable_harvest_days=3,
        machine_capacity_acres_per_day=3.5, capacity_budget_acres=10.5,
        opponent_plot_id=opponent_id, own_claim={"plot_id": plot_id}, rounds_run=3,
        resolution="escalated",
    )


def test_callback_resolution_updates_the_decision_record_to_the_human_outcome(tmp_path) -> None:
    """ADR-010 Part 0.5 Decision D: a human tap must update the
    DecisionRecord the coordinator wrote at trigger time (resolution=
    "escalated", resolved_at=None) to its final outcome -- and must never
    record fairness_decisive as True/False, since a human tap is not a
    score comparison."""
    storage = FileStorage(tmp_path / "storage.json")
    storage.put_cluster(_operator_cluster("decision-test"))
    client = _FakeClient()
    farmer_a = _farmer("p03", 111, name="Kannan Raja")
    farmer_b = _farmer("p04", 222, name="Meena Subramani")
    plots = {"p03": (farmer_a, _plot("p03")), "p04": (farmer_b, _plot("p04"))}
    decision_date = "2026-09-09"

    storage.put_decision_record(_decision_record_for("p03", "p04", "decision-test", decision_date))
    storage.put_decision_record(_decision_record_for("p04", "p03", "decision-test", decision_date))

    escalation = EscalationPayload(
        cluster_id="decision-test", plot_a_id="p03", plot_b_id="p04",
        claim_a=_claim("p03", days_past_maturity=6),
        claim_b=_claim("p04", days_past_maturity=0, bumped=True),
        rounds_run=3, reason="tied", decision_date=decision_date,
    )
    webhook.register_escalation(escalation)

    update = {
        "callback_query": {
            "id": "cbq-decision",
            "data": "resolve:decision-test:p03:p04:p03",
            "message": {"chat": {"id": 999}, "message_id": 1},
            "from": {"id": OPERATOR_CHAT_ID},
        }
    }
    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    winner_record = storage.get_decision_record("p03", SEASON, decision_date)
    loser_record = storage.get_decision_record("p04", SEASON, decision_date)
    assert winner_record.resolution == "escalated_won"
    assert winner_record.resolved_at is not None
    assert winner_record.fairness_decisive is None
    assert loser_record.resolution == "escalated_lost"
    assert loser_record.resolved_at is not None

    # ADR-013 Part 3: the loser's message carries a "why" button pinned
    # to the escalation's real decision_date -- the winner's does not.
    winner_send = next(m for m in client.sent_messages if m[0] == 111)
    loser_send = next(m for m in client.sent_messages if m[0] == 222)
    assert winner_send[2] is None
    loser_callback_data = loser_send[2]["inline_keyboard"][0][0]["callback_data"]
    assert loser_callback_data == f"why_lost:p04:{SEASON}:{decision_date}"


def test_callback_resolution_without_a_decision_date_does_not_crash(tmp_path) -> None:
    """An EscalationPayload with no decision_date (the default for a
    payload built before ADR-010 Part 0.5, or hand-built without it, as
    every other EscalationPayload in this test file is) must still
    resolve and notify both farmers normally -- it just can't update a
    DecisionRecord it has no key for."""
    storage = FileStorage(tmp_path / "storage.json")
    storage.put_cluster(_operator_cluster("no-date-test"))
    client = _FakeClient()
    farmer_a = _farmer("p03", 111, name="Kannan Raja")
    farmer_b = _farmer("p04", 222, name="Meena Subramani")
    plots = {"p03": (farmer_a, _plot("p03")), "p04": (farmer_b, _plot("p04"))}

    escalation = EscalationPayload(
        cluster_id="no-date-test", plot_a_id="p03", plot_b_id="p04",
        claim_a=_claim("p03", days_past_maturity=6),
        claim_b=_claim("p04", days_past_maturity=0, bumped=True),
        rounds_run=3, reason="tied",
    )
    webhook.register_escalation(escalation)

    update = {
        "callback_query": {
            "id": "cbq-no-date",
            "data": "resolve:no-date-test:p03:p04:p03",
            "message": {"chat": {"id": 999}, "message_id": 1},
            "from": {"id": OPERATOR_CHAT_ID},
        }
    }
    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    assert len(client.sent_messages) == 2
    # ADR-013 Part 3, Decision 26 (Gap B): no decision_date was ever
    # known for this escalation, so no "why" button is attached rather
    # than one that would always dead-end into "not recorded."
    loser_send = next(m for m in client.sent_messages if m[0] == 222)
    assert loser_send[2] is None


def test_registered_escalation_gives_loser_a_specific_reason(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    storage.put_cluster(_operator_cluster("reason-test"))
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
            "from": {"id": OPERATOR_CHAT_ID},
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
    storage.put_cluster(_operator_cluster("reason-test-ta"))
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
            "from": {"id": OPERATOR_CHAT_ID},
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
    storage.put_cluster(_operator_cluster("double-tap-test"))
    client = _FakeClient()
    plots = {"a1": (_farmer("a1", 1), _plot("a1")), "a2": (_farmer("a2", 2), _plot("a2"))}
    update = {
        "callback_query": {
            "id": "cbq-first",
            "data": "resolve:double-tap-test:a1:a2:a1",
            "message": {"chat": {"id": 9}, "message_id": 1},
            "from": {"id": OPERATOR_CHAT_ID},
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
    # No cluster record exists for "double-tap-test" -- defaults to
    # Tamil, same as the product-wide default, not a leftover English
    # string (see ADR-008 follow-up on webhook.py's callback-answer text).
    assert client.answered_callbacks[-1][1] == messages_ta.escalation_already_resolved()


def test_double_tap_survives_a_process_restart_via_the_ledger_write(tmp_path) -> None:
    """The in-memory _RESOLVED_ESCALATIONS set is a fast local cache, not
    the real idempotency guarantee -- the durable ledger write is. Clear
    the in-memory cache (simulating a restart) between two resolution
    attempts against the SAME storage, and confirm the second attempt
    still can't double-notify: the loser's ledger write fails cleanly
    because the entry already exists.
    """
    storage = FileStorage(tmp_path / "storage.json")
    storage.put_cluster(_operator_cluster("restart-test"))
    plots = {"b1": (_farmer("b1", 1), _plot("b1")), "b2": (_farmer("b2", 2), _plot("b2"))}
    update = {
        "callback_query": {
            "id": "cbq-restart-1",
            "data": "resolve:restart-test:b1:b2:b1",
            "message": {"chat": {"id": 9}, "message_id": 1},
            "from": {"id": OPERATOR_CHAT_ID},
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
    assert client2.answered_callbacks[-1][1] == messages_ta.escalation_already_resolved()


def test_missing_farmer_lookup_degrades_without_crashing(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    storage.put_cluster(_operator_cluster("missing-lookup-test"))
    client = _FakeClient()
    update = {
        "callback_query": {
            "id": "cbq-missing",
            "data": "resolve:missing-lookup-test:x1:x2:x1",
            "message": {"chat": {"id": 9}, "message_id": 1},
            "from": {"id": OPERATOR_CHAT_ID},
        }
    }
    # default lookup (unset) returns None for everything -- must not raise.
    webhook.handle_update(client, update, storage, SEASON)
    assert client.sent_messages == []  # no farmers found, nothing sent, no crash


# --- parse_incoming_message sender_name extraction (ADR-009 Part 3) ---

def test_parse_incoming_message_prefers_first_name() -> None:
    message = {"text": "hi", "from": {"first_name": "Muthu", "username": "muthu99"}}
    assert webhook.parse_incoming_message(message).sender_name == "Muthu"


def test_parse_incoming_message_falls_back_to_username_without_first_name() -> None:
    message = {"text": "hi", "from": {"username": "muthu99"}}
    assert webhook.parse_incoming_message(message).sender_name == "muthu99"


def test_parse_incoming_message_sender_name_is_none_without_a_from_field() -> None:
    message = {"text": "hi"}
    assert webhook.parse_incoming_message(message).sender_name is None
