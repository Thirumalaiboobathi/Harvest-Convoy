"""ADR-012: an escalation "resolve:" tap now goes through the same
_is_operator authorization check breakdown/machine_back taps already had
(ADR-011 Part 2, Decision 6) -- see webhook.py:handle_callback_query.
Before this, anyone who obtained or was forwarded an escalation message
(not just the operator it was sent to) could tap it and mutate the
fairness ledger. This file proves the fix: a non-operator tap is refused
with a clear message and mutates no state at all.
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.agents.contracts import AdvocateClaim, EscalationPayload
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.fairness import get_ledger_history
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.telegram import messages_ta, webhook
from harvest_convoy.telegram.client import SendResult

SEASON = "2026-kuruvai"
OPERATOR_CHAT_ID = 900
IMPOSTER_CHAT_ID = 55555


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


CLUSTER_ID = "auth-test-cluster"


def _cluster(cluster_id: str = CLUSTER_ID) -> Cluster:
    return Cluster(
        cluster_id=cluster_id, name="Test Cluster", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454, operator_chat_id=OPERATOR_CHAT_ID,
    )


def _farmer(fid: str, chat_id: int) -> Farmer:
    return Farmer(
        farmer_id=fid, name=f"Farmer {fid}", cluster_id=CLUSTER_ID, telegram_chat_id=chat_id,
    )


def _plot(pid: str) -> Plot:
    return Plot(
        plot_id=pid, farmer_id=f"farmer-{pid}", cluster_id=CLUSTER_ID,
        lat=9.87, lon=77.46, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=2.0,
    )


def _claim(plot_id: str, days_past_maturity: int, bumped: bool = False) -> AdvocateClaim:
    return AdvocateClaim(
        plot_id=plot_id, urgency_score=0.3, days_past_maturity=days_past_maturity,
        rain_vulnerability="low", acres=2.0, bumped_last_season=bumped,
        argument="x", concedes=False,
    )


def _escalation_update(callback_query_id: str, tapper_id: int | None, message_id: int = 1) -> dict:
    callback_query = {
        "id": callback_query_id,
        "data": f"resolve:{CLUSTER_ID}:auth-p1:auth-p2:auth-p1",
        "message": {"chat": {"id": OPERATOR_CHAT_ID}, "message_id": message_id},
    }
    if tapper_id is not None:
        callback_query["from"] = {"id": tapper_id}
    return {"callback_query": callback_query}


def _seed(storage, farmer_a, farmer_b):
    storage.put_cluster(_cluster())
    storage.put_farmer(farmer_a)
    storage.put_farmer(farmer_b)
    storage.put_plot(_plot("auth-p1"))
    storage.put_plot(_plot("auth-p2"))
    escalation = EscalationPayload(
        cluster_id=CLUSTER_ID, plot_a_id="auth-p1", plot_b_id="auth-p2",
        claim_a=_claim("auth-p1", days_past_maturity=6),
        claim_b=_claim("auth-p2", days_past_maturity=0, bumped=True),
        rounds_run=3, reason="tied",
    )
    webhook.register_escalation(escalation)


def test_escalation_tap_from_a_non_operator_chat_is_refused(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")
    farmer_a = _farmer("auth-p1", 111)
    farmer_b = _farmer("auth-p2", 222)
    _seed(storage, farmer_a, farmer_b)
    client = _FakeClient()
    plots = {"auth-p1": (farmer_a, _plot("auth-p1")), "auth-p2": (farmer_b, _plot("auth-p2"))}
    update = _escalation_update("cbq-imposter", IMPOSTER_CHAT_ID)

    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    # A clear, distinct refusal -- not silence, not a generic error.
    assert client.answered_callbacks == [
        ("cbq-imposter", messages_ta.unrecognized_action(), True)
    ]
    # No state mutation of any kind: no farmer notified, no ledger entry
    # for either side, and the inline keyboard is left in place (not
    # cleared, since nothing was actually resolved).
    assert client.sent_messages == []
    assert client.edited_markups == []
    assert get_ledger_history(farmer_a.farmer_id, storage) == []
    assert get_ledger_history(farmer_b.farmer_id, storage) == []


def test_escalation_tap_with_no_from_field_at_all_is_refused(tmp_path) -> None:
    """A malformed/stripped callback_query with no "from" identity at
    all must be refused, not treated as trusted by omission."""
    storage = FileStorage(tmp_path / "s.json")
    farmer_a = _farmer("auth-p1", 111)
    farmer_b = _farmer("auth-p2", 222)
    _seed(storage, farmer_a, farmer_b)
    client = _FakeClient()
    plots = {"auth-p1": (farmer_a, _plot("auth-p1")), "auth-p2": (farmer_b, _plot("auth-p2"))}
    update = _escalation_update("cbq-no-from", None)

    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    assert client.sent_messages == []
    assert get_ledger_history(farmer_a.farmer_id, storage) == []
    assert get_ledger_history(farmer_b.farmer_id, storage) == []


def test_escalation_tap_for_an_unregistered_cluster_is_refused_not_silently_resolved(
    tmp_path,
) -> None:
    """No Cluster record at all for the tapped cluster_id -- there is no
    operator_chat_id to check against, so this must refuse, not fall
    back to resolving the escalation for whoever tapped it."""
    storage = FileStorage(tmp_path / "s.json")
    client = _FakeClient()
    farmer_a = _farmer("auth-p1", 111)
    farmer_b = _farmer("auth-p2", 222)
    storage.put_farmer(farmer_a)
    storage.put_farmer(farmer_b)
    plots = {"auth-p1": (farmer_a, _plot("auth-p1")), "auth-p2": (farmer_b, _plot("auth-p2"))}
    update = {
        "callback_query": {
            "id": "cbq-no-cluster",
            "data": "resolve:nonexistent-cluster:auth-p1:auth-p2:auth-p1",
            "message": {"chat": {"id": OPERATOR_CHAT_ID}, "message_id": 1},
            "from": {"id": OPERATOR_CHAT_ID},
        }
    }

    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    assert client.sent_messages == []
    assert get_ledger_history(farmer_a.farmer_id, storage) == []
    assert get_ledger_history(farmer_b.farmer_id, storage) == []


def test_the_real_operators_own_tap_still_resolves_normally(tmp_path) -> None:
    """Companion to the refusal tests above: the fix must not have
    broken the legitimate path."""
    storage = FileStorage(tmp_path / "s.json")
    farmer_a = _farmer("auth-p1", 111)
    farmer_b = _farmer("auth-p2", 222)
    _seed(storage, farmer_a, farmer_b)
    client = _FakeClient()
    plots = {"auth-p1": (farmer_a, _plot("auth-p1")), "auth-p2": (farmer_b, _plot("auth-p2"))}
    update = _escalation_update("cbq-real-operator", OPERATOR_CHAT_ID)

    webhook.handle_update(
        client, update, storage, SEASON, lookup_farmer_for_plot=lambda pid: plots.get(pid)
    )

    assert len(client.sent_messages) == 2
    loser_history = get_ledger_history(farmer_b.farmer_id, storage)
    assert len(loser_history) == 1
    assert loser_history[0].outcome == "bumped"
