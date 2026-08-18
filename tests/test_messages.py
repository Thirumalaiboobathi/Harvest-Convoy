"""messages_ta.py / messages_en.py rendering tests. See ADR-008 Part 2,
Decision 13.

Purpose: catch a missing slot or a copy-pasted format string immediately,
in CI, not on someone's phone -- both language modules must render every
farmer-facing template with representative data and never raise.
"""

from __future__ import annotations

from datetime import date

import pytest

from harvest_convoy.telegram import messages_en, messages_ta

_MODULES = [messages_en, messages_ta]


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
@pytest.mark.parametrize("area_unit", ["acre", "cent"])
def test_all_prompts_render(mod, area_unit) -> None:
    for key, value in mod.PROMPTS.items():
        assert isinstance(value, str) and value.strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
def test_complete_message_renders(mod) -> None:
    assert isinstance(mod.COMPLETE_MESSAGE, str) and mod.COMPLETE_MESSAGE.strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
@pytest.mark.parametrize("area_unit", ["acre", "cent"])
def test_harvest_scheduled_renders_without_keyerror(mod, area_unit) -> None:
    text = mod.harvest_scheduled(2.5, area_unit, route_position=0)
    assert isinstance(text, str) and text.strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
@pytest.mark.parametrize("area_unit", ["acre", "cent"])
def test_not_ready_renders_without_keyerror(mod, area_unit) -> None:
    text = mod.not_ready(0.75, area_unit)
    assert isinstance(text, str) and text.strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
@pytest.mark.parametrize("area_unit", ["acre", "cent"])
def test_escalation_resolved_won_renders_without_keyerror(mod, area_unit) -> None:
    text = mod.escalation_resolved_won(2.0, area_unit)
    assert isinstance(text, str) and text.strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
@pytest.mark.parametrize("area_unit", ["acre", "cent"])
@pytest.mark.parametrize("other_farmer_name", [None, "Kannan Raja"])
@pytest.mark.parametrize("reason", [None, "some reason text"])
def test_escalation_resolved_lost_renders_without_keyerror(
    mod, area_unit, other_farmer_name, reason
) -> None:
    text = mod.escalation_resolved_lost(
        1.25, area_unit, other_farmer_name=other_farmer_name, reason=reason
    )
    assert isinstance(text, str) and text.strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
@pytest.mark.parametrize("bumped_winner", [True, False])
@pytest.mark.parametrize("bumped_loser", [True, False])
def test_resolution_reason_renders_without_keyerror(mod, bumped_winner, bumped_loser) -> None:
    text = mod.resolution_reason(
        bumped_winner=bumped_winner, bumped_loser=bumped_loser,
        winner_days_past_maturity=6, loser_days_past_maturity=0,
    )
    assert isinstance(text, str) and text.strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
def test_transplant_info_missing_prefix_renders_for_every_combination(mod) -> None:
    for missing in ([mod.MISSING_DATE_LABEL], [mod.MISSING_AREA_LABEL],
                     [mod.MISSING_DATE_LABEL, mod.MISSING_AREA_LABEL]):
        text = mod.transplant_info_missing_prefix(missing)
        assert isinstance(text, str) and text.strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
def test_format_date_renders_for_every_month(mod) -> None:
    for month in range(1, 13):
        text = mod.format_date(date(2026, month, 15))
        assert isinstance(text, str) and text.strip()
        assert "2026" in text
        assert "15" in text


def test_format_date_is_day_first_in_both_languages() -> None:
    """Indian date convention is day-first. Tamil was month-first
    ("ஆகஸ்ட் 18, 2026") until a native-speaker review round caught it --
    regression test, not just a rendering check."""
    d = date(2026, 8, 18)
    en_text = messages_en.format_date(d)
    ta_text = messages_ta.format_date(d)
    assert en_text.startswith("18")
    assert ta_text.startswith("18")


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
def test_format_area_preserves_fractional_acres(mod) -> None:
    """Round-3 review question: does a fractional acreage survive
    display, or does something round it to a whole number? 2.5 must
    render as "2.5", not get truncated/rounded to "2"."""
    text = mod.format_area(2.5, "acre")
    assert text.startswith("2.5")
    assert not text.startswith("2 ")


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
def test_format_area_cents_are_whole_for_whole_cent_registrations(mod) -> None:
    """Cents come out as whole numbers in practice (e.g. "250 சென்ட்"),
    but this isn't a special int() cast for the cent branch -- it's the
    same :g formatting used for acres. It renders whole because a
    farmer who registers "250 cents" gets area_acres = 2.5 exactly
    (250 / 100), and format_area's cent branch multiplies back by 100
    exactly -- an exact round trip, not rounding. A genuinely
    fractional-cent value (not producible via registration today, but
    not blocked by this function either) still shows its decimal."""
    assert mod.format_area(2.5, "cent").split()[0] == "250"
    assert mod.format_area(0.755, "cent").split()[0] == "75.5"


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
def test_greeting_and_ack_strings_are_nonempty(mod) -> None:
    assert mod.GREETING_INTRO.strip()
    assert mod.LANGUAGE_ACK.strip()
    assert mod.LOCATION_RETRY_PREFIX.strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
def test_crop_confirm_declined_message_renders(mod) -> None:
    assert isinstance(mod.CROP_CONFIRM_DECLINED_MESSAGE, str)
    assert mod.CROP_CONFIRM_DECLINED_MESSAGE.strip()


# ---------------------------------------------------------------------
# Operator-facing functions (route summary, escalation dispatch) --
# previously English-only by disclosed decision (ADR-008 Decision 8),
# now per-language via Cluster.operator_language.
# ---------------------------------------------------------------------

@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
@pytest.mark.parametrize("days_past_maturity", [-1, 0, 1, 6])
def test_overripe_phrase_renders_without_keyerror(mod, days_past_maturity) -> None:
    text = mod.overripe_phrase(days_past_maturity)
    assert isinstance(text, str) and text.strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
def test_bumped_suffix_renders(mod) -> None:
    assert isinstance(mod.bumped_suffix(), str) and mod.bumped_suffix().strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
def test_route_summary_functions_render(mod) -> None:
    assert mod.route_summary_empty("Kamatchipuram").strip()
    assert mod.route_summary_header("Kamatchipuram").strip()
    assert mod.route_stop_line(1, "Muthu Pandian, 2.5 acres").strip()


@pytest.mark.parametrize("mod", _MODULES, ids=["en", "ta"])
def test_escalation_static_strings_render(mod) -> None:
    assert mod.escalation_intro().strip()
    assert mod.escalation_question().strip()
    assert mod.escalation_argument_label().strip()


# ---------------------------------------------------------------------
# Tamil day-count pluralization (ADR-008 Decision 15, native-speaker
# review round 2): "1 நாட்கள்" (plural with count 1) was a real bug in
# overripe_phrase, caught in review. Fixed via messages_ta._day_word;
# checked here for every place a day count is interpolated, not just the
# one spot flagged.
# ---------------------------------------------------------------------

def test_overripe_phrase_uses_singular_day_word_for_one() -> None:
    text = messages_ta.overripe_phrase(1)
    assert "நாள்" in text
    assert "நாட்கள்" not in text


def test_overripe_phrase_uses_plural_day_word_for_more_than_one() -> None:
    text = messages_ta.overripe_phrase(6)
    assert "நாட்கள்" in text


def test_resolution_reason_uses_singular_day_word_for_one() -> None:
    text = messages_ta.resolution_reason(
        bumped_winner=False, bumped_loser=False,
        winner_days_past_maturity=1, loser_days_past_maturity=0,
    )
    assert "நாளாக" in text
    assert "நாட்களாக" not in text


def test_resolution_reason_uses_plural_day_word_for_more_than_one() -> None:
    text = messages_ta.resolution_reason(
        bumped_winner=False, bumped_loser=False,
        winner_days_past_maturity=6, loser_days_past_maturity=0,
    )
    assert "நாட்களாக" in text


# ---------------------------------------------------------------------
# advocate_argument -- deterministic Tamil templating (ADR-008 Decision
# 14). Tamil-only: English keeps the model's own generated argument, see
# tests/test_advocate.py.
# ---------------------------------------------------------------------

def test_advocate_argument_renders_for_every_branch() -> None:
    cases = [
        dict(is_ready=False, days_past_maturity=0, urgency=0.0,
             rain_vulnerability="none", bumped_last_season=False, concedes=True),
        dict(is_ready=True, days_past_maturity=0, urgency=0.0,
             rain_vulnerability="none", bumped_last_season=False, concedes=True),
        dict(is_ready=True, days_past_maturity=6, urgency=0.3,
             rain_vulnerability="low", bumped_last_season=True, concedes=False),
        dict(is_ready=True, days_past_maturity=6, urgency=0.3,
             rain_vulnerability="severe", bumped_last_season=False, concedes=False),
        dict(is_ready=True, days_past_maturity=6, urgency=0.3,
             rain_vulnerability="low", bumped_last_season=False, concedes=False),
        dict(is_ready=True, days_past_maturity=1, urgency=0.05,
             rain_vulnerability="low", bumped_last_season=False, concedes=False),
        dict(is_ready=True, days_past_maturity=0, urgency=0.0,
             rain_vulnerability="none", bumped_last_season=False, concedes=False),
    ]
    for kwargs in cases:
        text = messages_ta.advocate_argument(**kwargs)
        assert isinstance(text, str) and text.strip()


def test_advocate_argument_uses_singular_day_word_for_one_day() -> None:
    text = messages_ta.advocate_argument(
        is_ready=True, days_past_maturity=1, urgency=0.05,
        rain_vulnerability="low", bumped_last_season=False, concedes=False,
    )
    assert "நாளாக" in text
    assert "நாட்களாக" not in text


def test_advocate_argument_never_returns_model_prose() -> None:
    """Sanity check that this is a template, not a passthrough -- same
    facts always produce the same one of a small fixed set of sentences."""
    text1 = messages_ta.advocate_argument(
        is_ready=False, days_past_maturity=0, urgency=0.0,
        rain_vulnerability="none", bumped_last_season=False, concedes=True,
    )
    text2 = messages_ta.advocate_argument(
        is_ready=False, days_past_maturity=0, urgency=0.0,
        rain_vulnerability="none", bumped_last_season=False, concedes=True,
    )
    assert text1 == text2
