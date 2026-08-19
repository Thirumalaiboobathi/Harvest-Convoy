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


def not_ready(area_acres: float, area_unit: str) -> str:
    # Trimmed to load-bearing content (not ready / do nothing / we're
    # tracking it / you'll hear from us) -- the "grain still filling,
    # safer standing than cut early" rationale clause was cut. Kept in
    # parity with messages_ta.py's trim, same length reduction, same
    # information content minus the dropped rationale.
    return (
        f"Your {format_area(area_acres, area_unit)} plot isn't ready to "
        f"harvest yet -- no action needed, and no need to check in. "
        f"We're tracking it every day; you'll hear from us when it's "
        f"time or something changes."
    )


def drying_window_alert(*, moisture: int | None, msp: int | None) -> str:
    """Sent at most once per confirmed harvest's 4-day drying window
    (ADR-009 Part 4), only when rain enters the near-term forecast.
    Worded to cover a period ("over the next few days"), not a moment --
    the alert may fire on day 1 for rain arriving day 3, or after a dry
    gap, and must stay accurate either way. `moisture`/`msp` come from
    agronomy/market_params.py -- both manually set, source-cited,
    independently omitted (never a guessed number) if either is None."""
    if moisture is not None:
        core = (
            "Rain is expected over the next few days. Cover your "
            f"harvested grain -- paddy needs to dry to about {moisture}% "
            "moisture before a DPC will accept it at full price, and "
            "rain during drying can cause discolouration and sprouting."
        )
    else:
        core = (
            "Rain is expected over the next few days. Cover your "
            "harvested grain -- paddy needs to dry before a DPC will "
            "accept it at full price, and rain during drying can cause "
            "discolouration and sprouting."
        )
    if msp is not None:
        core += f" MSP for this grade is Rs {msp} per quintal."
    return core


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
    return f"{cluster_name} route for today:"


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


# --- Harvest confirmation loop (ADR-009 Part 2) ---

CONFIRMATION_YES_LABEL = "Yes"
CONFIRMATION_NO_LABEL = "No"


def confirmation_thanks() -> str:
    return "Thanks, recorded."


def confirmation_not_found() -> str:
    return "Couldn't find that confirmation -- it may be from an old message."
