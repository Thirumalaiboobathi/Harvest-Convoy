"""Prints every Tamil string in messages_ta.py, plus the Tamil-relevant
word lists/tables in registration.py (yes/no matcher, month names), in
one block for native-speaker review. See ADR-008 Part 2 and Decisions
14-16 for the four review rounds this wording went through before it
shipped.

Usage:
    uv run python -m scripts.print_tamil_strings
"""

from __future__ import annotations

import sys
from datetime import date

from harvest_convoy.telegram import messages_ta
from harvest_convoy.telegram import registration


def _reconfigure_stdout_utf8() -> None:
    # Windows consoles often default to cp1252, which can't encode Tamil
    # script -- reconfigure if possible so this actually prints instead
    # of raising UnicodeEncodeError. No-op on platforms where stdout is
    # already UTF-8 or doesn't support reconfigure().
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main() -> None:
    _reconfigure_stdout_utf8()

    _section("GREETING_INTRO")
    print(messages_ta.GREETING_INTRO)

    _section("Bilingual greeting (message 1, as actually sent)")
    print(registration._bilingual_greeting_text())

    _section("LANGUAGE_ACK")
    print(messages_ta.LANGUAGE_ACK)

    _section("LOCATION_RETRY_PREFIX")
    print(messages_ta.LOCATION_RETRY_PREFIX)

    _section("PROMPTS")
    for key, value in messages_ta.PROMPTS.items():
        print(f"[{key}]")
        print(value)
        print()

    _section("COMPLETE_MESSAGE")
    print(messages_ta.COMPLETE_MESSAGE)

    _section("CROP_CONFIRM_DECLINED_MESSAGE (sent when a farmer replies 'no' at crop-confirm)")
    print(messages_ta.CROP_CONFIRM_DECLINED_MESSAGE)

    _section("MISSING_DATE_LABEL / MISSING_AREA_LABEL / transplant_info_missing_prefix")
    print("MISSING_DATE_LABEL:", messages_ta.MISSING_DATE_LABEL)
    print("MISSING_AREA_LABEL:", messages_ta.MISSING_AREA_LABEL)
    print(messages_ta.transplant_info_missing_prefix([messages_ta.MISSING_DATE_LABEL]))
    print(messages_ta.transplant_info_missing_prefix([messages_ta.MISSING_AREA_LABEL]))
    print(messages_ta.transplant_info_missing_prefix(
        [messages_ta.MISSING_DATE_LABEL, messages_ta.MISSING_AREA_LABEL]
    ))

    _section("harvest_scheduled (acre, then cent) -- route_position 0, 1, 2 (1st/2nd/3rd)")
    print(messages_ta.harvest_scheduled(2.5, "acre", route_position=0))
    print(messages_ta.harvest_scheduled(3.0, "acre", route_position=1))
    print(messages_ta.harvest_scheduled(0.5, "cent", route_position=2))

    _section("not_ready (acre, then cent)")
    print(messages_ta.not_ready(0.75, "acre"))
    print(messages_ta.not_ready(1.0, "cent"))

    _section("escalation_resolved_won")
    print(messages_ta.escalation_resolved_won(1.0, "acre"))

    _section("escalation_resolved_lost (named farmer + reason, then default fallback)")
    print(messages_ta.escalation_resolved_lost(
        1.25, "acre",
        other_farmer_name="Kannan Raja",
        reason="அவர்களின் தானியம் 6 நாட்களாக தயாராக நின்று கொண்டிருக்கிறது, உங்களை விட அதிக நாட்கள்",
    ))
    print()
    print(messages_ta.escalation_resolved_lost(1.25, "acre"))

    _section("resolution_reason -- all three branches")
    print("bumped_winner=True, bumped_loser=False:")
    print(messages_ta.resolution_reason(
        bumped_winner=True, bumped_loser=False,
        winner_days_past_maturity=3, loser_days_past_maturity=3,
    ))
    print()
    print("winner more overdue than loser:")
    print(messages_ta.resolution_reason(
        bumped_winner=False, bumped_loser=False,
        winner_days_past_maturity=6, loser_days_past_maturity=0,
    ))
    print()
    print("neither -- operator judgment fallback:")
    print(messages_ta.resolution_reason(
        bumped_winner=False, bumped_loser=False,
        winner_days_past_maturity=0, loser_days_past_maturity=0,
    ))

    _section("format_date -- all 12 months")
    for month in range(1, 13):
        print(messages_ta.format_date(date(2026, month, 18)))

    _section("format_area -- acre and cent")
    print(messages_ta.format_area(2.5, "acre"))
    print(messages_ta.format_area(1.0, "acre"))
    print(messages_ta.format_area(2.5, "cent"))  # displays as 250 cents

    _section("Operator-facing (route summary, escalation dispatch) -- Cluster.operator_language")
    print("overripe_phrase(0):", messages_ta.overripe_phrase(0))
    print("overripe_phrase(1):", messages_ta.overripe_phrase(1))
    print("overripe_phrase(6):", messages_ta.overripe_phrase(6))
    print("bumped_suffix():", messages_ta.bumped_suffix())
    print("route_summary_empty('Kamatchipuram'):", messages_ta.route_summary_empty("Kamatchipuram"))
    print("route_summary_header('Kamatchipuram'):", messages_ta.route_summary_header("Kamatchipuram"))
    print("escalation_intro():", messages_ta.escalation_intro())
    print("escalation_question():", messages_ta.escalation_question())
    print("escalation_argument_label():", messages_ta.escalation_argument_label())

    _section("Compass directions (format_direction) -- all 16 points")
    for direction in ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                       "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]:
        print(f"{direction} -> {messages_ta.format_direction(direction)}")

    _section("location_hint -- full route-stop line, as actually rendered")
    hint = messages_ta.location_hint(0.8, messages_ta.format_direction("NNW"))
    print("location_hint(0.8, NNW):", hint)
    print("route_stop_line(1, 'Muthu Pandian, 2.5 ஏக்கர், ' + hint):",
          messages_ta.route_stop_line(1, f"Muthu Pandian, 2.5 ஏக்கர், {hint}"))

    _section("Operator-facing callback-query toasts (escalation button taps)")
    print("escalation_already_resolved():", messages_ta.escalation_already_resolved())
    print("escalation_resolved_assigned('Kannan Raja'):",
          messages_ta.escalation_resolved_assigned("Kannan Raja"))
    print("escalation_resolved_assigned(DEFAULT_WINNER_LABEL):",
          messages_ta.escalation_resolved_assigned(messages_ta.DEFAULT_WINNER_LABEL))
    print("unrecognized_action():", messages_ta.unrecognized_action())

    _section("advocate_argument -- templated (not model-generated) Tamil, all branches")
    branches = [
        ("too-green, concedes", dict(is_ready=False, days_past_maturity=0, urgency=0.0,
                                      rain_vulnerability="none", bumped_last_season=False, concedes=True)),
        ("ready but weak, concedes", dict(is_ready=True, days_past_maturity=0, urgency=0.0,
                                           rain_vulnerability="none", bumped_last_season=False, concedes=True)),
        ("contests, bumped last season", dict(is_ready=True, days_past_maturity=6, urgency=0.3,
                                               rain_vulnerability="low", bumped_last_season=True, concedes=False)),
        ("contests, severe rain risk", dict(is_ready=True, days_past_maturity=6, urgency=0.3,
                                             rain_vulnerability="severe", bumped_last_season=False, concedes=False)),
        ("contests, 1 day overdue", dict(is_ready=True, days_past_maturity=1, urgency=0.05,
                                          rain_vulnerability="low", bumped_last_season=False, concedes=False)),
        ("contests, 6 days overdue", dict(is_ready=True, days_past_maturity=6, urgency=0.3,
                                           rain_vulnerability="low", bumped_last_season=False, concedes=False)),
        ("contests, generic fallback", dict(is_ready=True, days_past_maturity=0, urgency=0.0,
                                             rain_vulnerability="none", bumped_last_season=False, concedes=False)),
    ]
    for label, kwargs in branches:
        print(f"[{label}] {messages_ta.advocate_argument(**kwargs)}")

    _section("registration.py -- crop-confirm yes/no matcher (_YES_WORDS / _NO_WORDS)")
    print("YES:", sorted(registration._YES_WORDS))
    print("NO: ", sorted(registration._NO_WORDS))

    _section("registration.py -- Tamil month-name parsing table (_TAMIL_MONTHS)")
    for name, num in registration._TAMIL_MONTHS.items():
        print(f"{name} -> {num}")

    _section("registration.py -- Tanglish month-name parsing table (_TANGLISH_MONTHS)")
    for name, num in registration._TANGLISH_MONTHS.items():
        print(f"{name} -> {num}")

    _section("registration.py -- LANGUAGE_KEYBOARD button labels")
    print(registration.LANGUAGE_KEYBOARD)

    print()
    print("=" * 70)
    print("End of Tamil string dump. See ADR-008 Decisions 14-16 for review history.")
    print("=" * 70)


if __name__ == "__main__":
    main()
