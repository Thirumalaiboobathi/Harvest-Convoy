"""English farmer-facing strings. See docs/adr/ADR-008-tn-generalization-and-tamil.md
Part 2. Shape mirrors messages_ta.py exactly (same function names, same
PROMPTS keys) so a third language later is "add a matching module," not a
refactor -- see notify.py/registration.py's _lang_module() dispatch.

Deliberately no import of RegistrationStep (would create a circular
import with registration.py, which imports this module). PROMPTS is keyed
by the plain string values ("awaiting_village", etc.) -- RegistrationStep
is a `str, Enum` subclass, so `RegistrationStep.AWAITING_VILLAGE` compares
and hashes equal to the plain string, and dict lookups from either side
work identically.
"""

from __future__ import annotations

from datetime import date

_MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def format_date(d: date) -> str:
    return f"{d.day} {_MONTH_NAMES[d.month]} {d.year}"


def format_area(area_acres: float, area_unit: str) -> str:
    """Display in whichever unit the farmer originally used at
    registration -- area_acres stays the canonical value everywhere else;
    this is presentation only. See ADR-008 Decision 9."""
    if area_unit == "cent":
        value = area_acres * 100
        unit = "cent" if value == 1 else "cents"
    else:
        value = area_acres
        unit = "acre" if value == 1 else "acres"
    return f"{value:g} {unit}"


def format_direction(direction: str) -> str:
    """English keeps the compass abbreviation as-is (N, NNW, ...) -- the
    16-point key notify.py computes from a bearing IS the English label
    already, no translation step needed here."""
    return direction


def location_hint(distance_km: float, direction: str) -> str:
    """e.g. "0.8km NNW of village center". `direction` is already
    resolved via format_direction() -- this function only builds the
    sentence around it. See ADR-008 follow-up: this used to be a single
    hardcoded string in notify.py regardless of language; a Tamil farmer
    was getting "...of village center" verbatim in an otherwise-Tamil
    message."""
    return f"{distance_km:.1f}km {direction} of village center"


GREETING_INTRO = "Welcome to Harvest Convoy!"
LANGUAGE_ACK = "English selected."
LOCATION_RETRY_PREFIX = "That didn't look like a shared location. "
DEFAULT_OTHER_FARMER_POSSESSIVE = "another farmer's"

PROMPTS: dict[str, str] = {
    "awaiting_village": "What village is your plot in?",
    "awaiting_location": (
        "Thanks. Now share your plot's location: tap the paperclip icon "
        "and choose Location."
    ),
    "awaiting_crop_confirm": (
        "This season we're coordinating paddy (ADT 45) only. Reply 'yes' "
        "to register this plot as paddy ADT 45."
    ),
    "awaiting_transplant_info": (
        "Last step: when did you transplant, and how many acres (or "
        "cents)? For example: \"18 May 2026, 2.5 acres\" or \"18 May "
        "2026, 250 cents\"."
    ),
}

MISSING_DATE_LABEL = "a date"
MISSING_AREA_LABEL = "an area in acres or cents"


def transplant_info_missing_prefix(missing_parts: list[str]) -> str:
    return f"I couldn't find {' and '.join(missing_parts)} in that message. "


COMPLETE_MESSAGE = (
    "Registered. You won't hear from us again until the machine's route "
    "is decided or your plot needs attention -- no need to check in."
)


def projected_maturity_sentence(formatted_date: str) -> str:
    """One sentence, appended to COMPLETE_MESSAGE -- a forecast
    projection, never a promise. "Should be ready around" carries the
    non-guaranteed nature the same way "around" does on its own; this
    isn't a report, so it stays exactly one sentence. See ADR-009
    Part 3."""
    return f"Based on today's weather, your plot should be ready around {formatted_date}."

CROP_CONFIRM_DECLINED_MESSAGE = (
    "Understood -- this season we're only coordinating paddy (ADT 45). "
    "We can't register a different crop right now. Thanks for checking "
    "-- we hope to work with you in a future season."
)


def harvest_scheduled(area_acres: float, area_unit: str, route_position: int) -> str:
    return (
        f"Good news: the machine is coming to your "
        f"{format_area(area_acres, area_unit)} plot today. You're stop "
        f"#{route_position + 1} on the route."
    )


def not_ready(area_acres: float, area_unit: str, *, rain_event_classification: str = "none") -> str:
    # Trimmed to load-bearing content (not ready / do nothing / we're
    # tracking it / you'll hear from us) -- the "grain still filling,
    # safer standing than cut early" rationale clause was cut. Kept in
    # parity with messages_ta.py's trim, same length reduction, same
    # information content minus the dropped rationale.
    text = (
        f"Your {format_area(area_acres, area_unit)} plot isn't ready to "
        f"harvest yet -- no action needed, and no need to check in. "
        f"We're tracking it every day; you'll hear from us when it's "
        f"time or something changes."
    )
    if rain_event_classification == "sustained":
        text += (
            " Rain looks set in for several days, so it may be a little "
            "longer than usual."
        )
    return text


def drying_window_alert(*, moisture: int | None, msp: int | None) -> str:
    """Sent at most once per confirmed harvest's 4-day drying window
    (ADR-009 Part 4), only when rain enters the near-term forecast.
    Two lines, chosen over a single-paragraph draft after review: the
    urgent rain-warning stands alone on its own line so it doesn't
    compete for attention with the moisture/MSP context on the second.
    Worded to cover a period ("over the next few days"), not a moment --
    the alert may fire on day 1 for rain arriving day 3, or after a dry
    gap, and must stay accurate either way. `moisture`/`msp` come from
    agronomy/market_params.py -- both manually set, source-cited,
    independently omitted (never a guessed number) if either is None.

    `msp` is specifically MSP_PADDY_COMMON_PER_QUINTAL -- Grade A and
    Common carry different MSPs, and this system doesn't know which
    grade a given farmer's paddy will be assessed at. The wording names
    "Common grade" explicitly rather than saying "this grade" (which
    would wrongly imply the figure applies to whatever grade the
    farmer's paddy happens to be assessed at) -- caught on review: a
    farmer expecting the wrong rate at the DPC is a real harm, not a
    wording nicety.

    When both `moisture` and `msp` are None there is no figure to state,
    and a bare "paddy needs to dry" sentence tells the farmer nothing he
    doesn't already know -- so the second line is dropped entirely and
    only the rain warning is sent. Caught on review.
    """
    line1 = (
        "Rain is expected over the next few days. Cover your harvested "
        "grain -- rain during drying can cause discolouration and sprouting."
    )
    if moisture is None and msp is None:
        return line1
    if moisture is not None:
        line2 = (
            f"Paddy needs to dry to about {moisture}% moisture before a "
            "DPC will accept it at full price."
        )
    else:
        line2 = "Paddy needs to dry before a DPC will accept it at full price."
    if msp is not None:
        line2 += f" Common-grade MSP is Rs {msp} per quintal."
    return f"{line1}\n\n{line2}"


def advance_harvest_notice(area_acres: float, area_unit: str, formatted_date: str) -> str:
    """Sent exactly once per plot per season, roughly a week before
    projected maturity (ADR-011 Part 4) -- so the farmer can start
    arranging transport, gunny bags, and drying space ahead of time.
    "Expected"/"around" carries the non-guaranteed nature, same
    discipline as projected_maturity_sentence(); "no need to reply"
    states plainly that this isn't a prompt, matching the standing
    no-new-farmer-initiated-surface rule. Never resent or corrected if a
    later projection shifts -- see AdvanceNoticeRecord, Decision 17."""
    return (
        f"Your {format_area(area_acres, area_unit)} plot is expected to "
        f"be ready around {formatted_date}. The machine will be "
        f"scheduled close to then. No need to reply -- a good time to "
        f"start arranging transport, gunny bags, and drying space."
    )


def harvest_confirmation_prompt(area_acres: float, area_unit: str) -> str:
    return (
        f"Did the machine come to your {format_area(area_acres, area_unit)} "
        f"plot today? Tap Yes or No below."
    )


def escalation_resolved_won(area_acres: float, area_unit: str) -> str:
    return (
        f"Update: your {format_area(area_acres, area_unit)} plot has "
        f"been confirmed for today's route."
    )


def resolution_reason(
    *,
    bumped_winner: bool,
    bumped_loser: bool,
    winner_days_past_maturity: int,
    loser_days_past_maturity: int,
) -> str:
    """A concrete, honest, human reason for the losing side -- never
    mentions negotiation rounds or internal scoring mechanics. Moved here
    from webhook.py's old _resolution_reason (same logic) so it can be
    rendered in the losing farmer's own language -- see ADR-008."""
    if bumped_winner and not bumped_loser:
        return "they were bumped last season and are due a fair turn"
    if winner_days_past_maturity > loser_days_past_maturity:
        return (
            f"their grain has been standing {winner_days_past_maturity} "
            f"days past ready, longer than yours"
        )
    return "the operator judged their plot needed today's slot more"


def escalation_resolved_lost(
    area_acres: float,
    area_unit: str,
    *,
    other_farmer_name: str | None = None,
    reason: str | None = None,
) -> str:
    # Small fix while extracting this from the old inline notify.py
    # version: that version built "{other_farmer_name or 'another
    # farmer's plot'}'s plot", which doubled "plot" on the no-name
    # fallback path ("...another farmer's plot's plot..."). Dead in
    # practice (webhook.py always passes a name), but fixed here since
    # it's being rewritten anyway.
    possessive = f"{other_farmer_name}'s" if other_farmer_name else DEFAULT_OTHER_FARMER_POSSESSIVE
    why = f" -- {reason}" if reason else ""
    # Trimmed to load-bearing content, kept in parity with messages_ta.py:
    # who/why, not on route, operator sends next, and the safety-relevant
    # clause (standing longer raises grain-loss risk) kept unmistakable.
    return (
        f"Update: today's machine is going to {possessive} plot "
        f"instead{why}. Your {format_area(area_acres, area_unit)} plot "
        f"isn't on today's route -- the operator will send it next. "
        f"Standing longer raises the risk of grain loss, so let us know "
        f"if your plot's condition changes."
    )


# --- Operator-facing (route summary, escalation dispatch) ---
# Previously hardcoded directly in notify.py, English-only by disclosed
# decision (ADR-008 Decision 8: no per-operator language field existed).
# Moved here, unchanged in wording, now selected via Cluster.operator_language.

def overripe_phrase(days_past_maturity: int) -> str:
    if days_past_maturity <= 0:
        return "just reached ready today"
    unit = "day" if days_past_maturity == 1 else "days"
    return f"{days_past_maturity} {unit} overripe"


def bumped_suffix() -> str:
    return " (bumped last season)"


def route_summary_empty(cluster_name: str) -> str:
    return f"{cluster_name}: no plots on today's route."


def route_summary_header(cluster_name: str) -> str:
    # "Proposed", not a declarative "route for today" -- ADR-013: same
    # content, different posture, so the message reads as a suggestion
    # from a tool the operator can accept or change, not an instruction
    # from a system he has already missed the chance to act on.
    return f"Proposed route for {cluster_name} today:"


def route_stop_line(index: int, label: str) -> str:
    return f"{index}. {label}"


def escalation_intro() -> str:
    return "Only one plot can get today's machine."


def escalation_question() -> str:
    return "Who should get it?"


def escalation_argument_label() -> str:
    return "agent's case (not fact):"


# --- Operator-facing callback-query answers (the small toast shown when
# the operator taps an escalation button) -- previously hardcoded
# directly in webhook.py, unconditionally English regardless of
# Cluster.operator_language. See ADR-008 follow-up.

def escalation_already_resolved() -> str:
    return "This conflict was already resolved."


DEFAULT_WINNER_LABEL = "the selected plot"


def escalation_resolved_assigned(winner_name: str) -> str:
    return f"Machine assigned to {winner_name}."


def unrecognized_action() -> str:
    return "Unrecognized action."


# --- Machine breakdown (ADR-011 Part 2) -- operator-only, dispatched by
# Cluster.operator_language, same as every other operator-facing string
# in this module.

BREAKDOWN_BUTTON_LABEL = "🚜 Machine down today"
MACHINE_BACK_BUTTON_LABEL = "🚜 Machine is back"
BREAKDOWN_FOLLOWUP_TOMORROW_LABEL = "Back tomorrow"
BREAKDOWN_FOLLOWUP_INDEFINITE_LABEL = "Down indefinitely"


def breakdown_acknowledged() -> str:
    return "Recorded; recalculating the route."


def breakdown_already_reported() -> str:
    return "Already reported today."


def breakdown_no_route() -> str:
    return "No route today to report a breakdown for."


def breakdown_recompute_failed() -> str:
    return "Recorded, but the recompute couldn't run right now -- will retry on the next scheduled check."


def breakdown_followup_prompt() -> str:
    return "Is this a one-day issue, or ongoing?"


def breakdown_back_tomorrow_ack() -> str:
    return "Noted -- tomorrow's run will proceed normally."


def machine_down_indefinite_ack() -> str:
    return "Marked down until further notice. Tap below when it's back."


def machine_back_ack() -> str:
    return "Marked operational again."


# --- Harvest confirmation loop (ADR-009 Part 2) ---

CONFIRMATION_YES_LABEL = "Yes"
CONFIRMATION_NO_LABEL = "No"


def confirmation_thanks() -> str:
    return "Thanks, recorded."


def confirmation_not_found() -> str:
    return "Couldn't find that confirmation -- it may be from an old message."


# --- Season rollover (ADR-011 Part 1) ---
# Two taps (yes/no, reusing CONFIRMATION_YES_LABEL/CONFIRMATION_NO_LABEL
# above -- same buttons, same meaning) plus, only on "yes," one free-text
# date reply. Never re-asks village, location, crop, or area -- those are
# already on record.

def season_rollover_prompt() -> str:
    return "Transplanting again this season? Tap yes and tell me the date."


def season_rollover_declined_ack() -> str:
    return "Understood -- hope to see you next season."


def season_rollover_date_prompt() -> str:
    return "What date did you transplant?"


def season_rollover_confirmed_ack() -> str:
    return "Got it -- you're on the list for this season."


# --- Operator enrollment (ADR-012 Part 2) ---
# Reuses CONFIRMATION_YES_LABEL/CONFIRMATION_NO_LABEL for the replacement
# prompt's buttons -- same Yes/No meaning, no new label pair needed.

def operator_command_usage() -> str:
    return "Send /operator followed by your enrolment code."


def operator_invalid_code() -> str:
    return (
        "That code isn't valid or has already been used. Ask your "
        "cluster coordinator for a new one."
    )


def operator_expired_code() -> str:
    return "That code has expired. Ask your cluster coordinator for a new one."


def operator_language_prompt() -> str:
    return "Enrolment code accepted. Please choose your language."


def operator_replacement_prompt(cluster_name: str) -> str:
    return f"{cluster_name} already has a registered operator. Replace them with you?"


def operator_replacement_declined() -> str:
    return "Understood -- the existing operator stays registered."


def operator_enrolled(cluster_name: str) -> str:
    return (
        f"You're now registered as the operator for {cluster_name}. "
        f"You'll get route summaries here and can tap buttons for machine status."
    )


# --- Route proposal and operator override (ADR-013) -- operator-only,
# dispatched by Cluster.operator_language, same pattern as every other
# operator-facing string in this module. "Silence is not a veto" -- the
# proposed route stands with no message at all if the operator never
# taps anything; every string below is what happens only if he does.

ROUTE_ACCEPT_BUTTON_LABEL = "✅ Accept"
ROUTE_MODIFY_BUTTON_LABEL = "✏️ Modify"
ROUTE_DROP_BUTTON_LABEL = "✕"
ROUTE_SWAP_UP_BUTTON_LABEL = "↑"
ROUTE_DONE_BUTTON_LABEL = "✅ Done"
ROUTE_DROP_CONFIRM_YES_LABEL = "✅ Confirm"
ROUTE_DROP_CONFIRM_NO_LABEL = "❌ Cancel"


def route_accept_ack() -> str:
    return "✅ Accepted"


def route_edit_header(cluster_name: str) -> str:
    return f"{cluster_name} -- editing today's route:"


def route_drop_confirm_prompt(farmer_name: str, *, is_last_plot: bool) -> str:
    text = f"Drop {farmer_name}'s plot from today's route?"
    if is_last_plot:
        text += (
            " This is the last plot on today's route -- confirming leaves "
            "nobody scheduled today."
        )
    return text


def route_done_ack() -> str:
    return "Route updated."


def route_already_confirmed() -> str:
    return (
        "This plot's harvest was already confirmed -- today's route "
        "can't be changed for it."
    )


def route_stale(decision_date: str) -> str:
    return f"This route is from {decision_date} and is no longer active."


def route_dropped_notice(area_acres: float, area_unit: str) -> str:
    # Names the operator as the one who changed the route -- passive
    # voice ("the route has changed") would obscure that a person made
    # this call, which is its own kind of dishonesty. Doesn't invent or
    # guess a reason (weather, another farmer, the operator's own
    # arrangement) -- this system doesn't know which is true, same
    # discipline as escalation_resolved_lost. Doesn't promise he's first
    # next time either: dropping a plot un-harvests it (clear_plot_harvest)
    # and records a fairness bump, but that bump only ever contributes a
    # capped tie-breaking weight in a future close call (coordinator.py's
    # MAX_FAIRNESS_BONUS invariant) -- it can't outrank a more urgent
    # plot. So the honest claim is "back in the normal pool," not "first
    # in line." See ADR-013 Decision 11 and "Resolved on review."
    return (
        f"Update: the operator has changed today's route -- the machine "
        f"won't be coming to your {format_area(area_acres, area_unit)} "
        f"plot today. Your plot goes back into the normal pool for the "
        f"next route, with no guarantee of going first."
    )


# --- Proxy registration and farmer linking (ADR-013 Part 2) ---
# /addfarmer and /linkfarmer are both operator-only, text-command
# entry points into otherwise taps-only flows -- same class of input as
# /operator <code> (ADR-012 Part 2), never farmer-facing.

def operator_only_command() -> str:
    return "Only the operator can use this command."


def addfarmer_farmer_name_prompt() -> str:
    return "What is the farmer's name?"


def addfarmer_contact_note_prompt() -> str:
    return (
        "Type a contact number or note if there is one, otherwise type "
        "'skip'."
    )


def addfarmer_has_phone_prompt() -> str:
    return "Does this farmer have a mobile phone with Telegram?"


def addfarmer_confirm_summary(name: str, village: str, area_text: str, date_text: str) -> str:
    return f"Register {name}, {village}, {area_text}, transplanted {date_text}?"


def addfarmer_registered_toast() -> str:
    return "Registered."


def addfarmer_cancelled() -> str:
    return "Registration cancelled -- nothing was saved."


def addfarmer_nothing_pending() -> str:
    return "No registration in progress. Send /addfarmer to start one."


def addfarmer_complete_no_phone(name: str) -> str:
    return (
        f"Registration complete. Since {name} has no phone, you'll need "
        f"to inform them directly."
    )


def addfarmer_complete_has_phone(name: str) -> str:
    return (
        f"Registration complete. Since {name} has no phone, you'll need "
        f"to inform them directly. If they later get their own phone and "
        f"message this bot themselves, use /linkfarmer to connect the "
        f"two records."
    )


def linkfarmer_nothing_to_link() -> str:
    return "There is no farmer to link."


def linkfarmer_pick_proxy_prompt() -> str:
    return "Which farmer do you want to link?"


def linkfarmer_pick_match_prompt(proxy_name: str) -> str:
    return f"Link {proxy_name} with which self-registered farmer?"


def linkfarmer_confirm_prompt(proxy_name: str, candidate_name: str) -> str:
    return (
        f"Link {proxy_name} (registered by you) with {candidate_name} "
        f"(self-registered)? {candidate_name}'s separate plot will stop "
        f"being scheduled -- {proxy_name}'s existing plot continues, now "
        f"reaching them directly on Telegram."
    )


def linkfarmer_linked_toast() -> str:
    return "Linked."


def linkfarmer_cancelled_toast() -> str:
    return "Cancelled -- nothing was linked."
