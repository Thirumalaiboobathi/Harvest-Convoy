from datetime import date

from harvest_convoy.agents.contracts import AdvocateClaim
from harvest_convoy.models import Farmer, Plot
from harvest_convoy.telegram import notify
from harvest_convoy.telegram.client import SendResult


class _FakeClient:
    def __init__(self):
        self.sent: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return SendResult(success=True)


def _plot(area_acres: float = 2.5) -> Plot:
    return Plot(
        plot_id="p01", farmer_id="f01", cluster_id="c",
        lat=10.0, lon=77.5, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=area_acres,
    )


def test_not_ready_message_explains_why_and_gives_no_action() -> None:
    text = notify.build_not_ready_text(_plot(), next_check_days=3)
    assert "not ready" in text.lower() or "isn't ready" in text.lower()
    assert "No action needed" in text
    assert "3 days" in text


def test_not_ready_message_singular_day() -> None:
    text = notify.build_not_ready_text(_plot(), next_check_days=1)
    assert "1 day" in text
    assert "1 days" not in text


def test_harvest_scheduled_message_gives_stop_number() -> None:
    text = notify.build_harvest_scheduled_text(_plot(), route_position=2)
    assert "#3" in text


def test_escalation_resolved_winner_vs_loser_wording_differs() -> None:
    winner_text = notify.build_escalation_resolved_text(_plot(), won=True)
    loser_text = notify.build_escalation_resolved_text(_plot(), won=False)
    assert winner_text != loser_text
    assert "confirmed" in winner_text.lower()
    assert "nearby plot first" in loser_text.lower()


def test_send_harvest_scheduled_uses_farmer_chat_id() -> None:
    client = _FakeClient()
    farmer = Farmer(farmer_id="f01", name="Muthu", cluster_id="c", telegram_chat_id=555)
    result = notify.send_harvest_scheduled(client, farmer, _plot(), route_position=0)
    assert result.success is True
    assert client.sent[0][0] == 555


def test_send_degrades_gracefully_when_farmer_has_no_chat_id() -> None:
    client = _FakeClient()
    farmer = Farmer(farmer_id="f01", name="Muthu", cluster_id="c", telegram_chat_id=None)
    result = notify.send_not_ready(client, farmer, _plot())
    assert result.success is False
    assert client.sent == []


def test_send_operator_route_summary_degrades_when_no_operator_configured() -> None:
    client = _FakeClient()
    result = notify.send_operator_route_summary(client, None, "Kamatchipuram", ["p01"])
    assert result.success is False
    assert client.sent == []


def test_send_escalation_degrades_when_no_operator_configured() -> None:
    client = _FakeClient()
    claim = AdvocateClaim(
        plot_id="p03", urgency_score=0.3, days_past_maturity=6,
        rain_vulnerability="low", acres=3.0, bumped_last_season=False,
        argument="x", concedes=False,
    )
    escalation_claim_b = AdvocateClaim(
        plot_id="p04", urgency_score=0.0, days_past_maturity=0,
        rain_vulnerability="none", acres=1.25, bumped_last_season=True,
        argument="y", concedes=False,
    )
    from harvest_convoy.agents.contracts import EscalationPayload

    escalation = EscalationPayload(
        cluster_id="c", plot_a_id="p03", plot_b_id="p04",
        claim_a=claim, claim_b=escalation_claim_b, rounds_run=3, reason="tied",
    )
    result = notify.send_escalation(client, None, escalation)
    assert result.success is False
    assert client.sent == []


def test_escalation_keyboard_has_two_buttons_with_correct_callback_data() -> None:
    from harvest_convoy.agents.contracts import EscalationPayload

    claim_a = AdvocateClaim(
        plot_id="p03", urgency_score=0.3, days_past_maturity=6,
        rain_vulnerability="low", acres=3.0, bumped_last_season=False,
        argument="x", concedes=False,
    )
    claim_b = AdvocateClaim(
        plot_id="p04", urgency_score=0.0, days_past_maturity=0,
        rain_vulnerability="none", acres=1.25, bumped_last_season=True,
        argument="y", concedes=False,
    )
    escalation = EscalationPayload(
        cluster_id="kamatchipuram", plot_a_id="p03", plot_b_id="p04",
        claim_a=claim_a, claim_b=claim_b, rounds_run=3, reason="tied",
    )
    keyboard = notify.build_escalation_keyboard(escalation)
    buttons = keyboard["inline_keyboard"][0]
    assert len(buttons) == 2
    assert buttons[0]["callback_data"] == "resolve:kamatchipuram:p03:p04:p03"
    assert buttons[1]["callback_data"] == "resolve:kamatchipuram:p03:p04:p04"
