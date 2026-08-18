"""Tamil farmer-facing strings. See docs/adr/ADR-008-tn-generalization-and-tamil.md
Part 2. Shape mirrors messages_en.py exactly (same function names, same
PROMPTS keys).

FIRST DRAFT, NOT A DELIVERABLE. I am not a native Tamil speaker. Every
string here is printed in one block by scripts/print_tamil_strings.py for
native-speaker review and correction -- do not treat any wording below as
final. Western digits used throughout (0-9, not ௦-௯) -- a stated
choice (ADR-008 Decision 10): contemporary Tamil newspapers/government
notices/SMS overwhelmingly use Western digits even in full Tamil-script
text, and Tamil numeral glyphs are largely unfamiliar in everyday reading
-- correct me if that's wrong for the farmers in mind here.

Revision log (native-speaker review round 1, 2026-08-17): date format was
month-first (wrong -- Indian convention is day-first, fixed); product
name was transliterated into Tamil script (wrong -- proper nouns stay in
Latin script, fixed); "operator" kept as ஆபரேட்டர் on your explicit
confirmation, not changed; not_ready and escalation_resolved_lost
trimmed to load-bearing content only, per your instruction; operator-
facing strings (route summary, escalation dispatch) are now here too --
previously English-only by disclosed decision (ADR-008 Decision 8), you
asked for that reversed.
"""

from __future__ import annotations

from datetime import date

# Standard Tamil transliterations of the Gregorian month names, as used
# in Tamil newspapers/government notices -- an ordinary linguistic
# convention, not a sourced/derived agronomy constant.
_MONTH_NAMES = [
    "",
    "ஜனவரி",       # January
    "பிப்ரவரி",  # February
    "மார்ச்",     # March
    "ஏப்ரல்",     # April
    "மே",          # May
    "ஜூன்",        # June
    "ஜூலை",        # July
    "ஆகஸ்ட்",     # August
    "செப்டம்பர்",  # September
    "அக்டோபர்",   # October
    "நவம்பர்",     # November
    "டிசம்பர்",   # December
]


def format_date(d: date) -> str:
    # Day-first (Indian convention) -- was month-first, a real bug caught
    # in review, not a style choice. "18 ஆகஸ்ட் 2026", not "ஆகஸ்ட் 18, 2026".
    return f"{d.day} {_MONTH_NAMES[d.month]} {d.year}"


def _day_word(n: int, *, adverbial: bool = False) -> str:
    """நாள் (singular) / நாட்கள் (plural) -- Tamil count-noun agreement.
    `adverbial=True` gives the "for/as N days" case (நாளாக/நாட்களாக) used
    in resolution_reason. Round-2 review caught "1 நாட்கள்" (plural with
    the count 1) in overripe_phrase -- fixed here, and applied everywhere
    else a day count is interpolated, not just the one spot flagged."""
    if adverbial:
        return "நாளாக" if n == 1 else "நாட்களாக"
    return "நாள்" if n == 1 else "நாட்கள்"


def format_area(area_acres: float, area_unit: str) -> str:
    """ஏக்கர் (acre) and சென்ட் (cent) are invariant for singular/plural
    in Tamil -- no pluralization needed, unlike English."""
    if area_unit == "cent":
        value = area_acres * 100
        unit = "சென்ட்"  # cent
    else:
        value = area_acres
        unit = "ஏக்கர்"  # acre
    return f"{value:g} {unit}"


# Product name stays in Latin script -- proper nouns aren't transliterated.
GREETING_INTRO = "Harvest Convoy-க்கு வரவேற்கிறோம்!"
# "Welcome to Harvest Convoy!"

LANGUAGE_ACK = "தமிழ் தேர்ந்தெடுக்கப்பட்டது."
# "Tamil selected."

LOCATION_RETRY_PREFIX = "அது பகிரப்பட்ட இருப்பிடம் போல் தெரியவில்லை. "
# "That didn't look like a shared location. "

DEFAULT_OTHER_FARMER_LABEL = "மற்றொரு விவசாயியின்"
# "another farmer's"

PROMPTS: dict[str, str] = {
    "awaiting_village": "உங்கள் வயல் இருக்கும் கிராமத்தின் பெயர் என்ன?",
    # "What village is your plot in?"
    "awaiting_location": (
        "நன்றி. இப்போது உங்கள் வயலின் இருப்பிடத்தைப் பகிரவும்: "
        "📎 இணைப்பு பட்டனைத் தட்டி, Location / இருப்பிடம் ஐத் "
        "தேர்ந்தெடுக்கவும்."
    ),
    # "Thanks. Now share your plot's location: tap the 📎 attachment
    #  button and choose Location / இருப்பிடம்." -- both words shown
    #  because Telegram's own menu label follows the farmer's Telegram
    #  app language setting, not this bot's language choice.
    "awaiting_crop_confirm": (
        "இந்த பருவத்தில் நாங்கள் நெல் (ADT 45) பயிரை மட்டும் "
        "ஒருங்கிணைக்கிறோம். இந்த வயலை ADT 45 நெல் பயிராக பதிவு "
        "செய்ய 'ஆம்' என்று பதிலளிக்கவும்."
    ),
    # "This season we're coordinating paddy (ADT 45) only. Reply
    #  'yes' to register this plot as paddy ADT 45."
    "awaiting_transplant_info": (
        "கடைசி படி: நீங்கள் எப்போது நடவு செய்தீர்கள், எத்தனை "
        "ஏக்கர் (அல்லது சென்ட்)? உதாரணமாக: \"18 மே 2026, 2.5 "
        "ஏக்கர்\" அல்லது \"18 மே 2026, 250 சென்ட்\"."
    ),
    # "Last step: when did you transplant, and how many acres (or
    #  cents)? For example: "18 May 2026, 2.5 acres" or "18 May 2026,
    #  250 cents"."
}

MISSING_DATE_LABEL = "தேதி"
# "a date"
MISSING_AREA_LABEL = "ஏக்கர் அல்லது சென்ட் பரப்பளவு"
# "an area in acres or cents"


def transplant_info_missing_prefix(missing_parts: list[str]) -> str:
    joined = " மற்றும் ".join(missing_parts)
    return f"அந்த செய்தியில் {joined} கிடைக்கவில்லை. "
    # "I couldn't find {joined} in that message. "


COMPLETE_MESSAGE = (
    "பதிவு முடிந்தது. இயந்திரத்தின் பாதை முடிவு செய்யப்படும் வரை "
    "அல்லது உங்கள் வயலுக்கு கவனம் தேவைப்படும் வரை நாங்கள் "
    "உங்களைத் தொடர்பு கொள்ள மாட்டோம் -- நீங்கள் விசாரிக்க "
    "வேண்டிய அவசியமில்லை."
)
# "Registered. You won't hear from us again until the machine's route
#  is decided or your plot needs attention -- no need to check in."

CROP_CONFIRM_DECLINED_MESSAGE = (
    "புரிந்தது -- இந்த பருவத்தில் நாங்கள் நெல் (ADT 45) பயிரை "
    "மட்டும் ஒருங்கிணைக்கிறோம். வேறு பயிரை இப்போது பதிவு செய்ய "
    "முடியாது. விசாரித்ததற்கு நன்றி -- அடுத்த பருவத்தில் உங்களுடன் "
    "இணைந்து பணியாற்ற ஆவலாக உள்ளோம்."
)
# "Understood -- this season we're only coordinating paddy (ADT 45). We
#  can't register a different crop right now. Thanks for checking -- we
#  hope to work with you in a future season." Sent when a farmer replies
#  "no" at the crop-confirmation step (previously no such path existed
#  at all -- see ADR-008 Decision 11's revision note).


def harvest_scheduled(area_acres: float, area_unit: str, route_position: int) -> str:
    area = format_area(area_acres, area_unit)
    return (
        f"நல்ல செய்தி: இயந்திரம் இன்று உங்கள் {area} வயலுக்கு "
        f"வருகிறது. நீங்கள் பாதையில் வரிசையில் {route_position + 1}வது."
    )
    # "Good news: the machine is coming to your {area} plot today.
    #  You're {n}th in the route order." -- was "stop #{n} on the
    #  route," changed to the ordinal-in-line phrasing you asked for.


def not_ready(area_acres: float, area_unit: str) -> str:
    area = format_area(area_acres, area_unit)
    return (
        f"உங்கள் {area} வயலின் கதிர் இன்னும் முற்றவில்லை -- எதுவும் "
        "செய்ய வேண்டாம், விசாரிக்கவும் வேண்டாம். நாங்கள் தினமும் "
        "கண்காணிக்கிறோம்; நேரம் வரும்போது அல்லது மாற்றம் இருந்தால் "
        "தெரியப்படுத்துவோம்."
    )
    # "Your {area} plot's grain-head hasn't ripened yet -- do nothing,
    #  no need to ask either. We monitor daily; we'll tell you when
    #  it's time or if something changes." -- trimmed to load-bearing
    #  content (not ready / do nothing / we're tracking it / you'll
    #  hear from us); the "safer standing than cut early" rationale
    #  clause was cut, and "தானியம் இன்னும் நிரம்பிக்கொண்டிருக்கிறது"
    #  (grain still filling) replaced with the more natural agricultural
    #  phrase "கதிர் இன்னும் முற்றவில்லை" (grain-head not yet ripened)
    #  as the core not-ready statement itself.


def escalation_resolved_won(area_acres: float, area_unit: str) -> str:
    area = format_area(area_acres, area_unit)
    return f"புதுப்பிப்பு: உங்கள் {area} வயல் இன்றைய பாதைக்கு உறுதி செய்யப்பட்டுள்ளது."
    # "Update: your {area} plot has been confirmed for today's route."


def resolution_reason(
    *,
    bumped_winner: bool,
    bumped_loser: bool,
    winner_days_past_maturity: int,
    loser_days_past_maturity: int,
) -> str:
    """See messages_en.py's resolution_reason -- same logic, Tamil
    wording. Found while wiring this up: webhook.py's original
    _resolution_reason was hand-authored English with no language
    awareness at all, which would have produced a mixed-language sentence
    for a Tamil-registered farmer. Moved here and made per-language."""
    if bumped_winner and not bumped_loser:
        return (
            "கடந்த பருவத்தில் அவர்கள் தள்ளிவைக்கப்பட்டதால், "
            "இப்போது நியாயமான முறையில் வாய்ப்பு பெறுகிறார்கள்"
        )
        # "they were bumped last season and are due a fair turn"
    if winner_days_past_maturity > loser_days_past_maturity:
        return (
            f"அவர்களின் தானியம் {winner_days_past_maturity} "
            f"{_day_word(winner_days_past_maturity, adverbial=True)} "
            "தயாராக நின்று கொண்டிருக்கிறது, உங்களை விட அதிக நாட்கள்"
        )
        # "their grain has been standing {n} days past ready, longer
        #  than yours" -- day-count pluralization fixed via _day_word,
        #  same bug class as overripe_phrase's "1 நாட்கள்".
    return "அவர்களின் வயலுக்கு இன்றைய நேரம் மிகவும் தேவை என்று ஆபரேட்டர் தீர்மானித்தார்"
    # "the operator judged their plot needed today's slot more"


def escalation_resolved_lost(
    area_acres: float,
    area_unit: str,
    *,
    other_farmer_name: str | None = None,
    reason: str | None = None,
) -> str:
    area = format_area(area_acres, area_unit)
    possessive = (
        f"{other_farmer_name} உடைய" if other_farmer_name
        else DEFAULT_OTHER_FARMER_LABEL
    )
    # "{name} உடைய" -- "{name}'s" (genitive)
    why = f" -- {reason}" if reason else ""
    return (
        f"புதுப்பிப்பு: இன்று இயந்திரம் {possessive} வயலுக்குச் "
        f"செல்கிறது{why}. உங்கள் {area} வயல் இன்றைய பாதையில் "
        "இல்லை -- ஆபரேட்டர் அடுத்ததாக அனுப்புவார். நீண்ட நேரம் "
        "நிற்பது தானிய இழப்பு அபாயத்தை அதிகரிக்கும், எனவே உங்கள் "
        "வயலின் நிலைமை மாறினால் தெரியப்படுத்தவும்."
    )
    # "Update: today's machine is going to {possessive} plot instead
    #  {why}. Your {area} plot isn't on today's route -- the operator
    #  will send it next. Standing longer raises the risk of grain
    #  loss, so tell us if your plot's condition changes." -- trimmed
    #  to load-bearing content, safety meaning (standing longer = more
    #  grain-loss risk) kept unmistakable, per your instruction.


# --- Operator-facing (route summary, escalation dispatch) ---
# Previously English-only, disclosed as a deliberate scope boundary
# (ADR-008 Decision 8: no per-operator language field existed). You
# asked for that reversed -- Cluster.operator_language now selects these,
# same dispatch pattern as the farmer-facing strings above.

def overripe_phrase(days_past_maturity: int) -> str:
    if days_past_maturity <= 0:
        return "இன்று தயாரானது"
        # "just reached ready today"
    return f"{days_past_maturity} {_day_word(days_past_maturity)} அளவுக்கு மேல் பழுத்தது"
    # "{n} days ripened beyond the right point" -- was "அதிக பழுத்தது"
    # (literal "excessively ripened"); replaced per your word choice.
    # Was also "1 நாட்கள்" (plural with count 1) for the n=1 case --
    # fixed via _day_word.


def bumped_suffix() -> str:
    return " (கடந்த பருவத்தில் தள்ளிவைக்கப்பட்டது)"
    # " (bumped last season)"


def route_summary_empty(cluster_name: str) -> str:
    return f"{cluster_name}: இன்றைய பாதையில் வயல்கள் இல்லை."
    # "{cluster_name}: no plots on today's route."


def route_summary_header(cluster_name: str) -> str:
    return f"{cluster_name} -- இன்றைய பாதை:"
    # "{cluster_name} route for today:"


def route_stop_line(index: int, label: str) -> str:
    return f"{index}. {label}"
    # "{index}. {label}" -- numerals are language-agnostic, no change needed


def escalation_intro() -> str:
    return "இன்றைய இயந்திரத்தை ஒரே ஒரு வயலுக்குத்தான் கொடுக்க முடியும்."
    # "Only one plot can get today's machine."


def escalation_question() -> str:
    return "யாருக்கு கொடுக்க வேண்டும்?"
    # "Who should get it?"


def escalation_argument_label() -> str:
    return "ஏஜென்ட்டின் பரிந்துரை:"
    # "agent's recommendation:" -- went through two prior wordings: first
    # "ஏஜென்ட்டின் வாதம் (உண்மை அல்ல)" ("agent's argument (not true)"),
    # which read as a truth-value disclaimer; then "ஏஜென்ட்டின் கருத்து:"
    # (agent's opinion), which lost the fact/reasoning distinction the
    # operator needs (this line is generated, everything else in the
    # message is measured). Picked after seeing both rendered in the
    # full escalation context, round 3 review.


# --- Advocate argument, templated (not LLM-generated) for Tamil ---
#
# Live-verified before this was written (ADR-008 Decision 14): Nova Pro,
# asked via its system prompt to write `argument` in Tamil script,
# produces text using only valid Tamil Unicode code points but
# grammatically impossible letter sequences -- not stilted Tamil,
# not-Tamil. Five real samples and the verification method are in the
# ADR. Every OTHER structured field (concedes, urgency_score,
# days_past_maturity, rain_vulnerability, acres, bumped_last_season) was
# separately confirmed correct and identical between English- and
# Tamil-instructed calls on the same facts -- the damage is isolated to
# this one free-text field, not a general degradation.
#
# Decision: `argument` for a Tamil-registered farmer's plot is rendered
# here, deterministically, from the same ground-truth facts and the
# model's own `concedes` decision (still a genuine live judgment call --
# only the PROSE is templated, not the decision it reflects). English
# keeps the model's own generated argument text unchanged. See
# agents/advocate.py:get_advocate_claim for where this is called.
#
# A Tanglish (romanized Tamil) system-prompt variant was also tested and
# came back mostly clean/readable ("Ithu ready aayila, action aanathe
# vendaam." -- genuinely correct Tanglish) -- a real, usable middle path
# if native Tamil-script generation is wanted again later. Not used here;
# recorded in the ADR as a live option, not implemented.
def advocate_argument(
    *,
    is_ready: bool,
    days_past_maturity: int,
    urgency: float,
    rain_vulnerability: str,
    bumped_last_season: bool,
    concedes: bool,
) -> str:
    if concedes and not is_ready:
        return "இந்த வயல் இன்னும் தயாராகவில்லை; அவசரம் இல்லை."
        # "This plot isn't ready yet; no urgency."
    if concedes:
        return "இந்த வயலுக்கு இப்போது குறிப்பிடத்தக்க அவசரம் இல்லை."
        # "This plot has no significant urgency right now."
    if bumped_last_season:
        return "கடந்த பருவத்தில் தள்ளிவைக்கப்பட்டது; இந்த முறை முன்னுரிமை தேவை."
        # "Was bumped last season; needs priority this time."
    if rain_vulnerability in ("moderate", "severe"):
        return "மழையால் சேத ஆபத்து அதிகம்; விரைவில் அறுவடை தேவை."
        # "High risk of rain damage; needs harvesting soon."
    if days_past_maturity > 0:
        day_word = _day_word(days_past_maturity, adverbial=True)
        return f"{days_past_maturity} {day_word} தயாராக நிற்கிறது; விரைவில் அறுவடை தேவை."
        # "Has been standing ready for {n} days; needs harvesting soon."
    return "இந்த வயலுக்கு இன்று முன்னுரிமை தேவை."
    # "This plot needs priority today."
