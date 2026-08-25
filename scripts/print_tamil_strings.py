"""Prints every Tamil string in messages_ta.py, plus the Tamil-relevant
word lists/tables in registration.py (yes/no matcher, month names), in
one block for native-speaker review. See ADR-008 Part 2 and Decisions
14-16 for the four review rounds this wording went through before it
shipped, and ADR-009 Parts 2-4 for the newer strings appended at the
end of this dump (harvest confirmation prompt/toasts, the maturity
projection sentence, the drying-window alert) -- all still first
drafts, not yet reviewed.

Usage:
    uv run python -m scripts.print_tamil_strings
"""

from __future__ import annotations

import sys
from datetime import date

from harvest_convoy.agronomy import market_params
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

    _section("NEW (ADR-011 Part 3) -- not_ready with rain_event_classification -- DRAFT, unreviewed")
    print("rain_event_classification='none' (byte-identical to the baseline above):")
    print(messages_ta.not_ready(0.75, "acre", rain_event_classification="none"))
    print()
    print("rain_event_classification='brief' (also byte-identical to the baseline):")
    print(messages_ta.not_ready(0.75, "acre", rain_event_classification="brief"))
    print()
    print("rain_event_classification='sustained' (new clause appended):")
    print(messages_ta.not_ready(0.75, "acre", rain_event_classification="sustained"))

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
    print("escalation_resolved_assigned(None) -- no farmer record found:",
          messages_ta.escalation_resolved_assigned(None))
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

    _section("NEW (ADR-009 Part 2) -- harvest confirmation prompt, buttons, toasts -- DRAFT, unreviewed")
    print("harvest_confirmation_prompt(2.5, 'acre'):")
    print(messages_ta.harvest_confirmation_prompt(2.5, "acre"))
    print()
    print("harvest_confirmation_prompt(0.5, 'cent'):")
    print(messages_ta.harvest_confirmation_prompt(0.5, "cent"))
    print()
    print("CONFIRMATION_YES_LABEL:", messages_ta.CONFIRMATION_YES_LABEL)
    print("CONFIRMATION_NO_LABEL:", messages_ta.CONFIRMATION_NO_LABEL)
    print("confirmation_thanks():", messages_ta.confirmation_thanks())
    print("confirmation_not_found():", messages_ta.confirmation_not_found())

    _section("NEW (ADR-009 Part 3) -- projected maturity date, one sentence appended to COMPLETE_MESSAGE -- DRAFT, unreviewed")
    sample_date = messages_ta.format_date(date(2026, 8, 20))
    print(f"projected_maturity_sentence('{sample_date}'):")
    print(messages_ta.projected_maturity_sentence(sample_date))
    print()
    print("As it actually renders, appended to COMPLETE_MESSAGE:")
    print(messages_ta.COMPLETE_MESSAGE + " " + messages_ta.projected_maturity_sentence(sample_date))

    _section("NEW (ADR-009 Part 4) -- post-harvest drying-window alert -- DRAFT, unreviewed")
    print(
        "*** moisture=14, msp=2300 below are FAKE, ILLUSTRATIVE NUMBERS "
        "hardcoded in THIS SCRIPT to show what the message looks like "
        "once real figures exist. They are NOT in agronomy/market_params.py "
        "and NEVER reach the real message path -- see the real-state render "
        "at the end of this section, which has no numbers at all. ***"
    )
    print()
    print("Two lines, chosen over an earlier single-paragraph draft: the")
    print("rain warning stands alone; moisture/MSP context is a separate line.")
    print()
    print("[illustrative] moisture=14, msp=2300:")
    print(messages_ta.drying_window_alert(moisture=14, msp=2300))
    print()
    print("[illustrative] moisture=14, msp=None:")
    print(messages_ta.drying_window_alert(moisture=14, msp=None))
    print()
    print("[illustrative] moisture=None, msp=2300:")
    print(messages_ta.drying_window_alert(moisture=None, msp=2300))
    print()
    print(
        "--- REAL current state (market_params.py's actual values right "
        "now -- both None, no illustrative numbers) ---"
    )
    print(messages_ta.drying_window_alert(
        moisture=market_params.DPC_MOISTURE_THRESHOLD_PERCENT,
        msp=market_params.MSP_PADDY_COMMON_PER_QUINTAL,
    ))

    _section("NEW (ADR-011 Part 4) -- advance harvest notice, ~1 week before projected maturity -- DRAFT, unreviewed")
    print("advance_harvest_notice(2.5, 'acre', formatted_date):")
    print(messages_ta.advance_harvest_notice(2.5, "acre", sample_date))
    print()
    print("advance_harvest_notice(0.5, 'cent', formatted_date):")
    print(messages_ta.advance_harvest_notice(0.5, "cent", sample_date))

    _section("NEW (ADR-012 Part 2) -- operator self-enrollment -- DRAFT, unreviewed")
    print("operator_command_usage():", messages_ta.operator_command_usage())
    print()
    print("operator_invalid_code():", messages_ta.operator_invalid_code())
    print()
    print("operator_expired_code():", messages_ta.operator_expired_code())
    print()
    print("operator_language_prompt():", messages_ta.operator_language_prompt())
    print()
    print("operator_replacement_prompt('Kamatchipuram Cluster'):")
    print(messages_ta.operator_replacement_prompt("Kamatchipuram Cluster"))
    print()
    print("operator_replacement_declined():", messages_ta.operator_replacement_declined())
    print()
    print("operator_enrolled('Kamatchipuram Cluster'):")
    print(messages_ta.operator_enrolled("Kamatchipuram Cluster"))

    _section("NEW (ADR-013) -- route proposal and operator override -- DRAFT, unreviewed")
    print("route_summary_header('Kamatchipuram') (now framed as a proposal):")
    print(messages_ta.route_summary_header("Kamatchipuram"))
    print()
    print("ROUTE_ACCEPT_BUTTON_LABEL:", messages_ta.ROUTE_ACCEPT_BUTTON_LABEL)
    print("ROUTE_MODIFY_BUTTON_LABEL:", messages_ta.ROUTE_MODIFY_BUTTON_LABEL)
    print("ROUTE_DROP_BUTTON_LABEL:", messages_ta.ROUTE_DROP_BUTTON_LABEL)
    print("ROUTE_SWAP_UP_BUTTON_LABEL:", messages_ta.ROUTE_SWAP_UP_BUTTON_LABEL)
    print("ROUTE_DONE_BUTTON_LABEL:", messages_ta.ROUTE_DONE_BUTTON_LABEL)
    print("ROUTE_DROP_CONFIRM_YES_LABEL:", messages_ta.ROUTE_DROP_CONFIRM_YES_LABEL)
    print("ROUTE_DROP_CONFIRM_NO_LABEL:", messages_ta.ROUTE_DROP_CONFIRM_NO_LABEL)
    print()
    print("route_accept_ack():", messages_ta.route_accept_ack())
    print("route_edit_header('Kamatchipuram'):", messages_ta.route_edit_header("Kamatchipuram"))
    print("route_done_ack():", messages_ta.route_done_ack())
    print()
    print("route_drop_confirm_prompt('Muthu Pandian', is_last_plot=False):")
    print(messages_ta.route_drop_confirm_prompt("Muthu Pandian", is_last_plot=False))
    print()
    print("route_drop_confirm_prompt('Muthu Pandian', is_last_plot=True):")
    print(messages_ta.route_drop_confirm_prompt("Muthu Pandian", is_last_plot=True))
    print()
    print("route_drop_confirm_prompt(None, is_last_plot=False) -- no farmer record found:")
    print(messages_ta.route_drop_confirm_prompt(None, is_last_plot=False))
    print()
    print("route_already_confirmed():", messages_ta.route_already_confirmed())
    print()
    print("route_stale('2026-09-08'):", messages_ta.route_stale("2026-09-08"))
    print()
    print("route_dropped_notice(2.5, 'acre') -- sent to the dropped farmer immediately:")
    print(messages_ta.route_dropped_notice(2.5, "acre"))
    print()
    print("route_dropped_notice(0.5, 'cent'):")
    print(messages_ta.route_dropped_notice(0.5, "cent"))

    _section("NEW (ADR-013 Part 2) -- proxy registration (/addfarmer) and farmer linking (/linkfarmer) -- DRAFT, unreviewed")
    print("operator_only_command():", messages_ta.operator_only_command())
    print()
    print("addfarmer_farmer_name_prompt():", messages_ta.addfarmer_farmer_name_prompt())
    print("addfarmer_contact_note_prompt():", messages_ta.addfarmer_contact_note_prompt())
    print("addfarmer_has_phone_prompt():", messages_ta.addfarmer_has_phone_prompt())
    print()
    print("addfarmer_confirm_summary('Muthu Pandian', 'Kamatchipuram', '2.5 acres', '18 May 2026'):")
    print(messages_ta.addfarmer_confirm_summary("Muthu Pandian", "Kamatchipuram", "2.5 acres", "18 May 2026"))
    print()
    print("addfarmer_registered_toast():", messages_ta.addfarmer_registered_toast())
    print("addfarmer_cancelled():", messages_ta.addfarmer_cancelled())
    print("addfarmer_nothing_pending():", messages_ta.addfarmer_nothing_pending())
    print()
    print("addfarmer_complete_no_phone('Muthu Pandian'):")
    print(messages_ta.addfarmer_complete_no_phone("Muthu Pandian"))
    print()
    print("addfarmer_complete_has_phone('Muthu Pandian'):")
    print(messages_ta.addfarmer_complete_has_phone("Muthu Pandian"))
    print()
    print("linkfarmer_nothing_to_link():", messages_ta.linkfarmer_nothing_to_link())
    print("linkfarmer_pick_proxy_prompt():", messages_ta.linkfarmer_pick_proxy_prompt())
    print("linkfarmer_pick_match_prompt('Muthu Pandian'):", messages_ta.linkfarmer_pick_match_prompt("Muthu Pandian"))
    print()
    print("linkfarmer_confirm_prompt('Muthu Pandian', 'Muthu P.'):")
    print(messages_ta.linkfarmer_confirm_prompt("Muthu Pandian", "Muthu P."))
    print()
    print("linkfarmer_linked_toast():", messages_ta.linkfarmer_linked_toast())
    print("linkfarmer_cancelled_toast():", messages_ta.linkfarmer_cancelled_toast())
    print()
    print("NEW (added 2026-08-24) -- /linkfarmer Undo, bounded-window reversal:")
    print("LINKFARMER_UNDO_BUTTON_LABEL:", messages_ta.LINKFARMER_UNDO_BUTTON_LABEL)
    print("linkfarmer_linked_with_undo_text():", messages_ta.linkfarmer_linked_with_undo_text())
    print("linkfarmer_undo_expired():", messages_ta.linkfarmer_undo_expired())
    print("linkfarmer_undo_failed():", messages_ta.linkfarmer_undo_failed())
    print("linkfarmer_undone_toast():", messages_ta.linkfarmer_undone_toast())

    print()
    print("NEW (ADR-013 Part 3) -- farmer's own \"why?\" answers:")
    print("WHY_BUTTON_LABEL:", messages_ta.WHY_BUTTON_LABEL)
    print("why_not_recorded(None):", messages_ta.why_not_recorded(None))
    formatted_date = messages_ta.format_date(date(2026, 9, 9))
    print(f"why_not_recorded('{formatted_date}'):", messages_ta.why_not_recorded(formatted_date))
    print(f"why_not_ready_answer('{formatted_date}', 58, 3.5, 1):")
    print(messages_ta.why_not_ready_answer(formatted_date, 58, 3.5, 1))
    print()
    reason = messages_ta.resolution_reason(
        bumped_winner=True, bumped_loser=False,
        winner_days_past_maturity=1, loser_days_past_maturity=3,
    )
    print(f"why_lost_answer('{formatted_date}', 'Meena Subramani', reason):")
    print(messages_ta.why_lost_answer(formatted_date, "Meena Subramani", reason))

    print()
    print("NEW (ADR-014) -- /help, read-only status:")
    print("help_unavailable():", messages_ta.help_unavailable())
    print("help_unregistered():", messages_ta.help_unregistered())
    print("help_no_plot_found():", messages_ta.help_no_plot_found())
    print("help_farmer_reply('Kamatchipuram', '2.5 ஏக்கர்', '12 மே 2026', '9 செப்டம்பர் 2026'):")
    print(messages_ta.help_farmer_reply("Kamatchipuram", "2.5 ஏக்கர்", "12 மே 2026", "9 செப்டம்பர் 2026"))
    print()
    print("help_farmer_reply(..., projected_ready_text=None) -- no advance notice yet:")
    print(messages_ta.help_farmer_reply("Kamatchipuram", "2.5 ஏக்கர்", "12 மே 2026", None))
    print()
    print("help_operator_reply('Kamatchipuram'):")
    print(messages_ta.help_operator_reply("Kamatchipuram"))
    print()
    print("help_operator_addendum('Kamatchipuram'):")
    print(messages_ta.help_operator_addendum("Kamatchipuram"))

    print()
    print("=" * 70)
    print("End of Tamil string dump. See ADR-008 Decisions 14-16 for the")
    print("pre-existing strings' review history, and ADR-009 Parts 2-4,")
    print("ADR-011, ADR-012, ADR-013 (Parts 1-3), and ADR-014 for the newer")
    print("strings above -- drafts, pending the same review process.")
    print("=" * 70)


if __name__ == "__main__":
    main()
