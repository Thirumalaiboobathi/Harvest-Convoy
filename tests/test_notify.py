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


def _claim(
    plot_id="p01", days_past_maturity=6, acres=3.0, bumped=False,
    argument="x", degraded=False,
) -> AdvocateClaim:
    return AdvocateClaim(
        plot_id=plot_id, urgency_score=0.3, days_past_maturity=days_past_maturity,
        rain_vulnerability="low", acres=acres, bumped_last_season=bumped,
        argument=argument, concedes=False, degraded=degraded,
    )


def test_not_ready_message_gives_no_action_and_promises_silence() -> None:
    # Trimmed to load-bearing content in native-speaker review round 2 --
    # see messages_en.py's not_ready() comment. Assertions updated to
    # match, same load-bearing claims (not ready / no action / tracking
    # it / you'll hear from us), not the old, longer wording.
    text = notify.build_not_ready_text(_plot(), language="en")
    assert "not ready" in text.lower() or "isn't ready" in text.lower()
    assert "no action needed" in text.lower()
    assert "no need to check in" in text.lower()
    assert "you'll hear from us" in text.lower()
    # must not imply the farmer will be pinged on a cadence
    assert "we'll check again" not in text.lower()


def test_not_ready_message_in_tamil_is_distinct_from_english() -> None:
    """A Tamil-registered farmer gets genuinely different (Tamil) text,
    not the English string with a language flag ignored. See ADR-008
    Decision 6/8."""
    ta_text = notify.build_not_ready_text(_plot(), language="ta")
    en_text = notify.build_not_ready_text(_plot(), language="en")
    assert ta_text != en_text
    assert "முற்றவில்லை" in ta_text  # "hasn't ripened yet"


def test_harvest_scheduled_message_gives_stop_number() -> None:
    text = notify.build_harvest_scheduled_text(_plot(), route_position=2, language="en")
    assert "#3" in text


def test_harvest_scheduled_message_gives_ordinal_in_tamil() -> None:
    """Tamil uses "வரிசையில் மூன்றாவது" (third in the line), not "#N" -- a
    native-speaker review round-1 wording fix, not a translation of "#3".
    Spelled ordinal, not digit+suffix -- see messages_ta._ORDINAL_WORDS."""
    text = notify.build_harvest_scheduled_text(_plot(), route_position=2, language="ta")
    assert "மூன்றாவது" in text
    assert "#3" not in text


def test_escalation_resolved_winner_vs_loser_wording_differs() -> None:
    winner_text = notify.build_escalation_resolved_text(_plot(), won=True, language="en")
    loser_text = notify.build_escalation_resolved_text(
        _plot(), won=False, language="en", other_farmer_name="Kannan Raja",
        reason="their grain has been standing 6 days past ready, longer than yours",
    )
    assert winner_text != loser_text
    assert "confirmed" in winner_text.lower()


def test_loser_message_names_who_and_why_not_vague() -> None:
    text = notify.build_escalation_resolved_text(
        _plot(), won=False, language="en", other_farmer_name="Kannan Raja",
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
        "kamatchipuram", "p03", farmer_a, plot_a, "p04", farmer_b, plot_b, language="en",
    )
    buttons = keyboard["inline_keyboard"][0]
    assert buttons[0]["text"] == "Kannan Raja, 3 acres"
    assert buttons[1]["text"] == "Meena Subramani, 1.25 acres"
    # plot_id is allowed (expected) inside callback_data -- machine field, not human-read
    assert buttons[0]["callback_data"] == "resolve:kamatchipuram:p03:p04:p03"
    assert buttons[1]["callback_data"] == "resolve:kamatchipuram:p03:p04:p04"


def test_escalation_text_has_no_negotiation_mechanics() -> None:
    farmer_a, farmer_b = _farmer("Kannan Raja"), _farmer("Meena Subramani")
    plot_a, plot_b = _plot(area_acres=3.0), _plot(area_acres=1.25)
    claim_a = _claim(days_past_maturity=6)
    claim_b = _claim(days_past_maturity=0, bumped=True)
    text = notify.build_escalation_text(
        farmer_a, plot_a, claim_a, farmer_b, plot_b, claim_b, language="en",
    )
    assert "round" not in text.lower()
    assert "negotiat" not in text.lower()
    assert "Kannan Raja" in text and "Meena Subramani" in text
    assert "6 days overripe" in text
    assert "bumped last season" in text


def test_escalation_text_in_tamil_has_no_negotiation_mechanics() -> None:
    """Operator-facing text is now per-language too (Cluster.operator_language)
    -- previously English-only by disclosed decision, reversed on
    request. See ADR-008 Decision 8's revision note."""
    farmer_a, farmer_b = _farmer("Kannan Raja"), _farmer("Meena Subramani")
    plot_a, plot_b = _plot(area_acres=3.0), _plot(area_acres=1.25)
    claim_a = _claim(days_past_maturity=6)
    claim_b = _claim(days_past_maturity=0, bumped=True)
    text = notify.build_escalation_text(
        farmer_a, plot_a, claim_a, farmer_b, plot_b, claim_b, language="ta",
    )
    assert "round" not in text.lower()
    assert "negotiat" not in text.lower()
    assert "Kannan Raja" in text and "Meena Subramani" in text
    assert "6" in text and "பழுத்தது" in text  # "overripe"
    assert "தள்ளிவைக்கப்பட்டது" in text  # "bumped"


def test_escalation_text_includes_labeled_argument_line_below_facts() -> None:
    """ADR-008 Decision 12: facts first, unchanged; the advocate's
    argument is one added, clearly-labeled line below them."""
    farmer_a, farmer_b = _farmer("Kannan Raja"), _farmer("Meena Subramani")
    plot_a, plot_b = _plot(area_acres=3.0), _plot(area_acres=1.25)
    claim_a = _claim(days_past_maturity=6, argument="This plot has waited longest.")
    claim_b = _claim(days_past_maturity=0, bumped=True, argument="I was bumped last season.")

    text = notify.build_escalation_text(
        farmer_a, plot_a, claim_a, farmer_b, plot_b, claim_b, language="en",
    )

    assert '"This plot has waited longest."' in text
    assert '"I was bumped last season."' in text
    # labeled as reasoning, not fact
    assert "agent's case" in text.lower()
    # facts lines still come first, unchanged
    fact_line_index = text.index("6 days overripe")
    argument_line_index = text.index("This plot has waited longest.")
    assert fact_line_index < argument_line_index


def test_escalation_text_argument_label_is_tamil_when_operator_language_is_tamil() -> None:
    farmer_a, farmer_b = _farmer("Kannan Raja"), _farmer("Meena Subramani")
    plot_a, plot_b = _plot(area_acres=3.0), _plot(area_acres=1.25)
    claim_a = _claim(days_past_maturity=6, argument="This plot has waited longest.")
    claim_b = _claim(days_past_maturity=0, bumped=True, argument="I was bumped last season.")

    text = notify.build_escalation_text(
        farmer_a, plot_a, claim_a, farmer_b, plot_b, claim_b, language="ta",
    )

    # The argument TEXT stays whatever language it was generated in
    # (English here, since these are hand-built claims) -- only the
    # LABEL wrapping it follows the operator's language.
    assert '"This plot has waited longest."' in text
    assert "ஏஜென்ட்டின் பரிந்துரை" in text  # "agent's recommendation", in Tamil


def test_escalation_text_omits_argument_line_for_a_degraded_fallback_claim() -> None:
    """A model string must never block the escalation -- a fallback-path
    claim (claim.degraded=True) still renders complete, valid text from
    facts alone, with no argument line at all rather than showing generic
    placeholder text as if it were real reasoning."""
    farmer_a, farmer_b = _farmer("Kannan Raja"), _farmer("Meena Subramani")
    plot_a, plot_b = _plot(area_acres=3.0), _plot(area_acres=1.25)
    claim_a = _claim(
        days_past_maturity=6,
        argument="Not ready to harvest; standing safely.",  # advocate.py's real fallback text
        degraded=True,
    )
    claim_b = _claim(days_past_maturity=0, bumped=True, argument="A real live argument.")

    text = notify.build_escalation_text(
        farmer_a, plot_a, claim_a, farmer_b, plot_b, claim_b, language="en",
    )

    assert "Not ready to harvest; standing safely." not in text
    assert "A real live argument." in text  # the non-degraded side is unaffected
    assert "Only one plot can get today's machine." in text
    assert "Who should get it?" in text
    assert "Kannan Raja" in text and "Meena Subramani" in text
    assert "6 days overripe" in text


def test_escalation_text_omits_argument_line_for_blank_argument() -> None:
    """Empty/whitespace-only argument (malformed model output, or a
    hand-built claim in a test) degrades the same way as claim.degraded --
    no crash, no blank line, facts still render."""
    farmer_a, farmer_b = _farmer("Kannan Raja"), _farmer("Meena Subramani")
    plot_a, plot_b = _plot(area_acres=3.0), _plot(area_acres=1.25)
    claim_a = _claim(days_past_maturity=6, argument="   ")
    claim_b = _claim(days_past_maturity=0, bumped=True, argument="")

    text = notify.build_escalation_text(
        farmer_a, plot_a, claim_a, farmer_b, plot_b, claim_b, language="en",
    )

    assert "agent's case" not in text.lower()
    assert "Who should get it?" in text
    assert "6 days overripe" in text


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
    hint = notify.location_hint(cluster, plot, language="en")
    assert "km" in hint
    assert "of village center" in hint
    assert "N" in hint.split("km")[1]  # due north offset -> N-ish direction


def test_location_hint_is_tamil_by_default_not_half_translated() -> None:
    """Regression: location_hint used to be a single hardcoded English
    sentence regardless of language -- a Tamil-registered farmer's route
    summary read "...NNW of village center" verbatim, mid-Tamil-sentence.
    Caught live on the deployed path."""
    cluster = _cluster()
    plot = _plot(lat=cluster.machine_start_lat + 0.01, lon=cluster.machine_start_lon)
    hint = notify.location_hint(cluster, plot)  # default language="ta"
    assert "km" in hint
    assert "of village center" not in hint
    assert "வடக்கு" in hint  # due north offset -> தமிழ் "N"-ish direction


def test_location_hint_is_compressed_in_tamil() -> None:
    """Density fix: the per-stop Tamil hint no longer repeats "திசையில்"
    or "கிராம மையத்திலிருந்து" -- that's now stated once in the route
    summary header (build_operator_route_summary_text)."""
    cluster = _cluster()
    plot = _plot(lat=cluster.machine_start_lat + 0.01, lon=cluster.machine_start_lon)
    hint = notify.location_hint(cluster, plot)  # default language="ta"
    assert "திசையில்" not in hint
    assert "கிராம மையத்திலிருந்து" not in hint
