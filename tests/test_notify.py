from datetime import date

from harvest_convoy.agents.contracts import AdvocateClaim
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.telegram import notify
from harvest_convoy.telegram.client import SendResult


class _FakeClient:
    def __init__(self):
        self.sent: list[tuple] = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return SendResult(success=True)


def _plot(area_acres: float = 2.5, lat: float = 9.870, lon: float = 77.460) -> Plot:
    return Plot(
        plot_id="p01", farmer_id="f01", cluster_id="c",
        lat=lat, lon=lon, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=area_acres,
    )


def _cluster() -> Cluster:
    return Cluster(
        cluster_id="c", name="Kamatchipuram", machine_capacity_acres_per_day=3.5,
        machine_start_lat=9.865, machine_start_lon=77.454,
    )


def _farmer(name: str = "Muthu Pandian") -> Farmer:
    return Farmer(farmer_id="f01", name=name, cluster_id="c")


def _claim(plot_id="p01", days_past_maturity=6, acres=3.0, bumped=False) -> AdvocateClaim:
    return AdvocateClaim(
        plot_id=plot_id, urgency_score=0.3, days_past_maturity=days_past_maturity,
        rain_vulnerability="low", acres=acres, bumped_last_season=bumped,
        argument="x", concedes=False,
    )


def test_not_ready_message_gives_no_action_and_promises_silence() -> None:
    text = notify.build_not_ready_text(_plot())
    assert "not ready" in text.lower() or "isn't ready" in text.lower()
    assert "No action needed" in text
    assert "no need to check in" in text.lower()
    assert "you'll only hear from us again" in text.lower()
    # must not imply the farmer will be pinged on a cadence
    assert "we'll check again" not in text.lower()


def test_harvest_scheduled_message_gives_stop_number() -> None:
    text = notify.build_harvest_scheduled_text(_plot(), route_position=2)
    assert "#3" in text


def test_escalation_resolved_winner_vs_loser_wording_differs() -> None:
    winner_text = notify.build_escalation_resolved_text(_plot(), won=True)
    loser_text = notify.build_escalation_resolved_text(
        _plot(), won=False, other_farmer_name="Kannan Raja",
        reason="their grain has been standing 6 days past ready, longer than yours",
    )
    assert winner_text != loser_text
    assert "confirmed" in winner_text.lower()


def test_loser_message_names_who_and_why_not_vague() -> None:
    text = notify.build_escalation_resolved_text(
        _plot(), won=False, other_farmer_name="Kannan Raja",
        reason="their grain has been standing 6 days past ready, longer than yours",
    )
    assert "Kannan Raja" in text
    assert "6 days past ready" in text
    assert "nearby plot first" not in text.lower()
    assert "closer conflict" not in text.lower()


def test_no_human_readable_text_contains_a_raw_plot_id() -> None:
    farmer = _farmer()
    plot = _plot()
    cluster = _cluster()
    claim = _claim()
    texts = [
        notify.build_harvest_scheduled_text(plot, 0),
        notify.build_not_ready_text(plot),
        notify.build_escalation_resolved_text(plot, won=True),
        notify.build_escalation_resolved_text(
            plot, won=False, other_farmer_name="X", reason="y"
        ),
        notify.build_operator_route_summary_text(cluster, [(farmer, plot)]),
        notify.build_escalation_text(farmer, plot, claim, farmer, plot, claim),
    ]
    for text in texts:
        assert "p01" not in text


def test_escalation_keyboard_button_text_has_no_plot_id() -> None:
    farmer_a, farmer_b = _farmer("Kannan Raja"), _farmer("Meena Subramani")
    plot_a, plot_b = _plot(area_acres=3.0), _plot(area_acres=1.25)
    keyboard = notify.build_escalation_keyboard(
        "kamatchipuram", "p03", farmer_a, plot_a, "p04", farmer_b, plot_b
    )
    buttons = keyboard["inline_keyboard"][0]
    assert buttons[0]["text"] == "Kannan Raja, 3.0ac"
    assert buttons[1]["text"] == "Meena Subramani, 1.25ac"
    # plot_id is allowed (expected) inside callback_data -- machine field, not human-read
    assert buttons[0]["callback_data"] == "resolve:kamatchipuram:p03:p04:p03"
    assert buttons[1]["callback_data"] == "resolve:kamatchipuram:p03:p04:p04"


def test_escalation_text_has_no_negotiation_mechanics() -> None:
    farmer_a, farmer_b = _farmer("Kannan Raja"), _farmer("Meena Subramani")
    plot_a, plot_b = _plot(area_acres=3.0), _plot(area_acres=1.25)
    claim_a = _claim(days_past_maturity=6)
    claim_b = _claim(days_past_maturity=0, bumped=True)
    text = notify.build_escalation_text(farmer_a, plot_a, claim_a, farmer_b, plot_b, claim_b)
    assert "round" not in text.lower()
    assert "negotiat" not in text.lower()
    assert "Kannan Raja" in text and "Meena Subramani" in text
    assert "6 days overripe" in text
    assert "bumped last season" in text


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
    result = notify.send_operator_route_summary(client, None, _cluster(), [])
    assert result.success is False
    assert client.sent == []


def test_send_escalation_degrades_when_no_operator_configured() -> None:
    client = _FakeClient()
    farmer_a, farmer_b = _farmer("Kannan Raja"), _farmer("Meena Subramani")
    plot_a, plot_b = _plot(), _plot()
    claim_a, claim_b = _claim(plot_id="p03"), _claim(plot_id="p04")
    result = notify.send_escalation(
        client, None, "kamatchipuram",
        "p03", farmer_a, plot_a, claim_a,
        "p04", farmer_b, plot_b, claim_b,
    )
    assert result.success is False
    assert client.sent == []


def test_location_hint_gives_direction_and_distance() -> None:
    cluster = _cluster()
    plot = _plot(lat=cluster.machine_start_lat + 0.01, lon=cluster.machine_start_lon)
    hint = notify.location_hint(cluster, plot)
    assert "km" in hint
    assert "of village center" in hint
    assert "N" in hint.split("km")[1]  # due north offset -> N-ish direction
