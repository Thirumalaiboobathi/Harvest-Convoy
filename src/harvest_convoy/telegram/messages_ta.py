"""Tamil farmer-facing strings. See docs/adr/ADR-008-tn-generalization-and-tamil.md
Part 2. Shape mirrors messages_en.py exactly (same function names, same
PROMPTS keys).

I am not a native Tamil speaker, so every string here went through four
rounds of native-speaker review (scripts/print_tamil_strings.py dumps
them all in one block for that purpose) before shipping -- see ADR-008
Decisions 14-16 for the full record of what changed and why. Western
digits used throughout (0-9, not ௦-௯) -- a stated choice (ADR-008
Decision 10): contemporary Tamil newspapers/government notices/SMS
overwhelmingly use Western digits even in full Tamil-script text, and
Tamil numeral glyphs are largely unfamiliar in everyday reading.

Revision log (native-speaker review, 2026-08-17 to 2026-08-18): date
format was month-first (wrong -- Indian convention is day-first, fixed);
product name was transliterated into Tamil script (wrong -- proper nouns
stay in Latin script, fixed); "operator" kept as ஆபரேட்டர் on explicit
confirmation, not changed; not_ready and escalation_resolved_lost
trimmed to load-bearing content only; operator-facing strings (route
summary, escalation dispatch) are now here too -- previously
English-only by disclosed decision (ADR-008 Decision 8), reversed on
request; day-count pluralization bug fixed everywhere it occurred
(நாள்/நாட்கள்); escalation_argument_label went through three wordings
before landing on "ஏஜென்ட்டின் பரிந்துரை:", picked after being rendered
in a full escalation message rather than in isolation.

Revision log (live-output review, 2026-08-23): route-position ordinals
(harvest_scheduled) were only special-cased for position 1 (முதலாவது);
positions 2-8 rendered the un-natural "Nவது" digit+suffix hybrid --
fixed for the full set, see _ORDINAL_WORDS. The operator route summary's
per-stop location_hint dropped "திசையில்" and "கிராம மையத்திலிருந்து" --
both wrapped every stop to two lines in practice, turning a five-stop
route into ten lines of prose; the village-center convention is now
stated once in route_summary_header instead.
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


def _day_word(n: int, *, adverbial: bool = False, locative: bool = False) -> str:
    """நாள் (singular) / நாட்கள் (plural) -- Tamil count-noun agreement.
    `adverbial=True` gives the "for/as N days" case (நாளாக/நாட்களாக) used
    in resolution_reason. `locative=True` gives the "in N days" case
    (நாளில்/நாட்களில்) used in why_not_ready_answer (ADR-013 Part 3) --
    a fully-formed word for each case, not string concatenation: நாள்'s
    singular sandhi with இல் is நாளில், not "நாள்" + "இல்" glued
    literally (that produces the ungrammatical "நாள்இல்") -- caught
    while rendering an actual example for review, not by re-reading the
    code. Round-2 review caught "1 நாட்கள்" (plural with the count 1) in
    overripe_phrase -- fixed here, and applied everywhere else a day
    count is interpolated, not just the one spot flagged."""
    if adverbial:
        return "நாளாக" if n == 1 else "நாட்களாக"
    if locative:
        return "நாளில்" if n == 1 else "நாட்களில்"
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


# 16-point compass, in Tamil. The 4 cardinals have single established
# words; the 12 finer points are hyphenated compounds of two cardinals,
# the same additive pattern English uses to build "north-northwest" from
# "north" + "northwest" -- a legitimate construction, not an invented
# abbreviation scheme. Picked over keeping the English abbreviations
# (N/NNW/...) after rendering both in the actual route-summary sentence
# and comparing directly -- round 4 native-speaker review.
_TAMIL_COMPASS = {
    "N": "வடக்கு", "NNE": "வடக்கு-வடகிழக்கு", "NE": "வடகிழக்கு",
    "ENE": "கிழக்கு-வடகிழக்கு", "E": "கிழக்கு", "ESE": "கிழக்கு-தென்கிழக்கு",
    "SE": "தென்கிழக்கு", "SSE": "தெற்கு-தென்கிழக்கு", "S": "தெற்கு",
    "SSW": "தெற்கு-தென்மேற்கு", "SW": "தென்மேற்கு", "WSW": "மேற்கு-தென்மேற்கு",
    "W": "மேற்கு", "WNW": "மேற்கு-வடமேற்கு", "NW": "வடமேற்கு",
    "NNW": "வடக்கு-வடமேற்கு",
}


def format_direction(direction: str) -> str:
    return _TAMIL_COMPASS[direction]


def location_hint(distance_km: float, direction: str) -> str:
    """e.g. "0.8km வடக்கு-வடமேற்கு" ("0.8km NNW"). `direction` is already
    resolved via format_direction(). Compressed on live-output review: the
    full sentence form ("...திசையில் கிராம மையத்திலிருந்து", "...in the
    direction of, from the village center") wrapped to two lines per stop
    in the operator's five-stop route summary -- ten lines of prose for a
    list meant to be scanned at 6 AM, not read. "திசையில்" (in the
    direction of) and "கிராம மையத்திலிருந்து" (from the village center)
    are both dropped here; the village-center convention is now stated
    once in route_summary_header() instead of on every line. Checked
    against the worst case before this shipped -- the longest farmer name
    across both seeded clusters (Rajendran Mudaliar) paired with the
    longest 16-point compass word (கிழக்கு-தென்கிழக்கு, ESE) still renders
    as one line. English was checked too and doesn't have the same
    problem -- it already uses the bare compass letters (NNW) rather than
    a spelled-out compound, so it was left as-is. See
    notify.build_operator_route_summary_text / full_label."""
    return f"{distance_km:.1f}km {direction}"


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


def projected_maturity_sentence(formatted_date: str) -> str:
    return (
        f"இன்றைய வானிலையின் அடிப்படையில், உங்கள் வயல் {formatted_date} "
        "அளவில் தயாராக இருக்கக்கூடும்."
    )
    # "Based on today's weather, your plot may become ready around
    #  {date}." -- DRAFT, pending native-speaker review (ADR-009 Part 3).
    # "இருக்கக்கூடும்" (may be/could be) carries the non-guaranteed,
    # projected nature the way "should" does in the English draft, on
    # top of "around" already doing that work in both.

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


# Full spelled ordinal words, 1-8 -- covers every position a route
# realistically reaches in this project's seeded clusters (8 plots).
# "Nவது" (digit + suffix, no spelled stem) was the prior form for n != 1
# -- "1வது" was caught and special-cased to "முதலாவது" in round 4
# review, but "4வது"/"2வது"/etc. are the same un-natural hybrid, just
# not caught because review happened on position 1 in isolation rather
# than the full rendered set. Spelling every position out (முதலாவது,
# இரண்டாவது, மூன்றாவது, ...) is the form these actually take in Tamil --
# the suffix "-ஆவது" attaches to the ordinal stem, not to a bare digit.
_ORDINAL_WORDS = {
    1: "முதலாவது",   # first
    2: "இரண்டாவது",  # second
    3: "மூன்றாவது",  # third
    4: "நான்காவது",  # fourth
    5: "ஐந்தாவது",   # fifth
    6: "ஆறாவது",     # sixth
    7: "ஏழாவது",     # seventh
    8: "எட்டாவது",   # eighth
}


def _ordinal_word(n: int) -> str:
    """Spelled Tamil ordinal for 1-8 (see _ORDINAL_WORDS). Beyond 8 --
    not reachable by any seeded cluster today, but routes aren't
    hard-capped at 8 in general -- falls back to the digit+hyphen+ஆவது
    form Tamil actually uses for larger ordinals ("18-ஆவது நூற்றாண்டு",
    18th century), rather than growing the spelled table indefinitely or
    reusing the un-natural bare "Nவது" this replaces."""
    if n in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[n]
    return f"{n}-ஆவது"


def harvest_scheduled(area_acres: float, area_unit: str, route_position: int) -> str:
    area = format_area(area_acres, area_unit)
    position = _ordinal_word(route_position + 1)
    return (
        f"நல்ல செய்தி: இயந்திரம் இன்று உங்கள் {area} வயலுக்கு "
        f"வருகிறது. நீங்கள் பாதையில் வரிசையில் {position}."
    )
    # "Good news: the machine is coming to your {area} plot today.
    #  You're {n}th in the route order." -- was "stop #{n} on the
    #  route," changed to the ordinal-in-line phrasing you asked for.


def not_ready(area_acres: float, area_unit: str, *, rain_event_classification: str = "none") -> str:
    area = format_area(area_acres, area_unit)
    text = (
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
    if rain_event_classification == "sustained":
        text += (
            " பல நாட்களுக்கு மழை நீடிக்கும் என்பதால், வழக்கத்தை விட "
            "சற்று தாமதமாகலாம்."
        )
        # "Since rain looks set to continue for several days, it may be
        #  a little later than usual." -- ADR-011 Decision 14 wording.
    return text


def drying_window_alert(*, moisture: int | None, msp: int | None) -> str:
    # DRAFT, pending native-speaker review (ADR-009 Part 4). Two lines,
    # chosen over a single-paragraph draft after review: the urgent
    # rain-warning stands alone on its own line so it doesn't compete
    # for attention with the moisture/MSP context on the second.
    #
    # Grade fix: `msp` is specifically MSP_PADDY_COMMON_PER_QUINTAL --
    # Grade A and Common carry different MSPs, and we don't know which
    # grade a given farmer's paddy will be assessed at. An earlier draft
    # said "இந்த தரத்திற்கான" ("for this grade"), which wrongly implied
    # the figure applies to whatever grade the farmer's paddy turns out
    # to be. Now names "காமன் தரம்" (Common grade) explicitly instead. A
    # farmer expecting the wrong DPC rate is a real harm.
    #
    # When both moisture and msp are None there's no figure to state, and
    # a bare "paddy needs to dry" line tells the farmer nothing new -- so
    # the second line is dropped entirely and only the rain warning goes
    # out. Caught on review.
    line1 = (
        "அடுத்த சில நாட்களில் மழை வரக்கூடும். அறுவடை செய்த "
        "நெல்லை மூடி வையுங்கள் -- உலர்த்தும் போது மழை நிறம் "
        "மாறுவதற்கும் முளைப்பதற்கும் காரணமாகலாம்."
    )
    # "Rain is expected over the next few days. Cover your harvested
    #  grain -- rain during drying can cause discolouration and
    #  sprouting."
    if moisture is None and msp is None:
        return line1
    if moisture is not None:
        line2 = (
            f"DPC முழு விலைக்கு ஏற்க நெல் ஈரப்பதம் சுமார் {moisture}% "
            "அளவுக்குக் குறையவேண்டும்."
        )
        # "Paddy needs to dry to about {moisture}% moisture before a DPC
        #  will accept it at full price."
    else:
        line2 = "DPC முழு விலைக்கு ஏற்க நெல் உலரவேண்டும்."
        # "Paddy needs to dry before a DPC will accept it at full price."
    if msp is not None:
        line2 += f" காமன் தர நெல்லுக்கான குறைந்தபட்ச ஆதரவு விலை குயின்டால் ஒன்றுக்கு ரூ.{msp}."
        # " Common-grade MSP is Rs {msp} per quintal." -- names the grade
        # explicitly ("காமன் தர நெல்லுக்கான" = "for Common-grade paddy"),
        # states the published figure, never a personal payment promise.
    return f"{line1}\n\n{line2}"


def advance_harvest_notice(area_acres: float, area_unit: str, formatted_date: str) -> str:
    # DRAFT, pending native-speaker review (ADR-011 Part 4). Sent exactly
    # once per plot per season, roughly a week before projected maturity,
    # so the farmer can start arranging transport, gunny bags, and drying
    # space ahead of time. "எதிர்பார்க்கப்படுகிறது" (expected) / "அளவில்"
    # (around) carry the non-guaranteed nature; "பதிலளிக்க வேண்டியதில்லை"
    # (no need to reply) states plainly that this isn't a prompt. Never
    # resent or corrected if a later projection shifts.
    area = format_area(area_acres, area_unit)
    return (
        f"உங்கள் {area} வயல் {formatted_date} அளவில் தயாராக இருக்கும் "
        "என எதிர்பார்க்கப்படுகிறது. அதற்கு அருகில் இயந்திரம் "
        "திட்டமிடப்படும். பதிலளிக்க வேண்டியதில்லை -- இப்போதே "
        "போக்குவரத்து, சாக்குப்பைகள், உலர்த்தும் இடம் ஆகியவற்றை "
        "ஏற்பாடு செய்ய ஏற்ற நேரம்."
    )
    # "Your {area} plot is expected to be ready around {date}. The
    #  machine will be scheduled close to then. No need to reply -- a
    #  good time to start arranging transport, gunny bags, and drying
    #  space."


def harvest_confirmation_prompt(area_acres: float, area_unit: str) -> str:
    area = format_area(area_acres, area_unit)
    return (
        f"இன்று இயந்திரம் உங்கள் {area} வயலுக்கு வந்ததா? கீழே "
        "ஆம் அல்லது இல்லை என்பதைத் தட்டவும்."
    )
    # "Did the machine come to your {area} plot today? Tap Yes or No
    #  below." -- DRAFT, pending native-speaker review (ADR-009 Part 2).


WHY_BUTTON_LABEL = "❓ ஏன்?"
# "Why?" -- DRAFT, pending native-speaker review (ADR-013 Part 3).


def why_not_recorded(formatted_date: str | None = None) -> str:
    # DRAFT, pending native-speaker review (ADR-013 Part 3).
    if formatted_date:
        return (
            f"{formatted_date} அன்றைய முடிவு பதிவு செய்யப்படவில்லை, எனவே "
            "காரணத்தை மீண்டும் கூற முடியாது -- நாங்கள் யூகிக்க மாட்டோம்."
        )
        # "{date}'s decision was not recorded, so we cannot restate the
        #  reason -- we will not guess."
    return (
        "அந்த நாளின் முடிவு பதிவு செய்யப்படவில்லை, எனவே காரணத்தை "
        "மீண்டும் கூற முடியாது -- நாங்கள் யூகிக்க மாட்டோம்."
    )
    # "That day's decision was not recorded, so we cannot restate the
    #  reason -- we will not guess."


def why_not_ready_answer(
    formatted_date: str, pct_grown: int, capacity_budget_acres: float, usable_harvest_days: int,
) -> str:
    # DRAFT, pending native-speaker review (ADR-013 Part 3). Percent-
    # grown carries the whole answer; the capacity clause explicitly
    # says it did not affect this plot -- same reasoning as
    # messages_en.py's why_not_ready_answer, see that docstring.
    day_word_locative = _day_word(usable_harvest_days, locative=True)
    return (
        f"{formatted_date}: உங்கள் பயிர் அறுவடைக்குத் தேவையான "
        f"வளர்ச்சியில் சுமார் {pct_grown}% ஐ எட்டியிருந்தது -- அன்று "
        f"தயாராக இல்லாததற்கு அதுவே காரணம். (குறிப்புக்கு: அன்று "
        f"இயந்திரத்தின் மொத்த திறன், {usable_harvest_days} நல்ல "
        f"{day_word_locative}, சுமார் {capacity_budget_acres:.1f} ஏக்கர் -- "
        f"இது உங்கள் வயலைப் பாதிக்கவில்லை, அது எப்படியிருந்தாலும் "
        f"தயாராக இருக்கவில்லை.)"
    )
    # "{date}: your crop had reached about {pct}% of the growth needed
    #  for harvest -- that is why it was not ready that day. (For
    #  reference: that day the machine's total capacity, across {n} good
    #  days, was about {x} acres -- this did not affect your plot, which
    #  was not ready regardless.)"


def why_lost_answer(formatted_date: str, winner_name: str | None, reason: str) -> str:
    # DRAFT, pending native-speaker review (ADR-013 Part 3).
    # winner_name=None -- no farmer record found for the winning plot --
    # reuses DEFAULT_WINNER_DATIVE (already the correct fused dative for
    # the fallback noun) rather than genitive-suffixing DEFAULT_WINNER_
    # LABEL and keeping this template's own trailing "வயலுக்கு", which
    # would double the noun ("வயல் உடைய வயலுக்கு" -- "the selected
    # plot's plot") -- found via the 2026-08-24 grep sweep, same class
    # as escalation_resolved_assigned/route_drop_confirm_prompt's fixes.
    dative_phrase = f"{winner_name} உடைய வயலுக்கு" if winner_name else DEFAULT_WINNER_DATIVE
    return f"{formatted_date} அன்று, இயந்திரம் {dative_phrase}ச் சென்றது -- {reason}."
    # "On {date}, the machine went to {winner_name}'s plot instead --
    #  {reason}."


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
    return f"{cluster_name} -- இன்றைய பரிந்துரைக்கப்பட்ட பாதை (தூரங்கள் கிராம மையத்திலிருந்து):"
    # "{cluster_name} -- today's proposed route (distances from village
    # center):" -- "பரிந்துரைக்கப்பட்ட" (proposed/recommended) added per
    # ADR-013: same posture change as the English "Proposed route..."
    # header, reusing the root already picked for the escalation argument
    # label (ADR-008 Decision 15, "ஏஜென்ட்டின் பரிந்துரை" -- "agent's
    # recommendation") rather than inventing a second word for the same
    # posture. States the village-center convention once here instead of
    # on every stop line, now that location_hint() no longer repeats it.
    # See location_hint()'s docstring for the density finding.


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


# --- Operator-facing callback-query answers (the small toast shown when
# the operator taps an escalation button) -- previously hardcoded
# directly in webhook.py in English only, regardless of
# Cluster.operator_language -- the same half-translation pattern as the
# original location_hint bug, just in a different message shape. Caught
# in the same audit pass. See ADR-008 follow-up.

def escalation_already_resolved() -> str:
    return "இந்த முரண்பாடு ஏற்கனவே தீர்க்கப்பட்டது."
    # "This conflict was already resolved."


DEFAULT_WINNER_LABEL = "தேர்ந்தெடுக்கப்பட்ட வயல்"
# "the selected plot" -- fallback when no farmer record was found for
# the winning plot; same defensive-fallback pattern as
# DEFAULT_OTHER_FARMER_LABEL above.
#
# TAMIL GRAMMAR RULE -- read this before adding a template that takes a
# case marker after a name-or-fallback value: a proper noun (a real
# person's name, kept untranslated) takes a case marker as its own,
# spaced word -- "Kannan Raja க்கு". A genuine Tamil common noun instead
# FUSES the case marker onto itself with sandhi -- வயல் (plot) + க்கு
# (dative) is வயலுக்கு, never "வயல் க்கு". A template written for the
# proper-noun shape (spaced marker) silently produces broken Tamil the
# moment a common-noun fallback like this constant flows through it.
# Found four times in this codebase this way -- DEFAULT_WINNER_DATIVE
# (reused by escalation_resolved_assigned AND why_lost_answer, both
# needing a dative) and DEFAULT_WINNER_ACCUSATIVE just below
# (route_drop_confirm_prompt's fallback), plus ADR-013 Part 3's
# "Resolved on review" section for the sibling bug in why_not_ready_
# answer's day-count suffix (same root cause, a different case marker).
# All four were found by grepping every use of this constant, not by
# re-reading each call site in isolation -- if you're adding a fifth
# use, grep "DEFAULT_WINNER_LABEL" first, and check both (a) whether
# the case marker fuses for a common noun and (b) whether the template
# already has its own trailing noun that a genitive/possessive fallback
# would double (see DEFAULT_WINNER_ACCUSATIVE's comment for that second
# failure mode -- it's not just about the case marker).

DEFAULT_WINNER_DATIVE = "தேர்ந்தெடுக்கப்பட்ட வயலுக்கு"
# Pre-fused dative for the fallback case, per the rule above -- used
# directly (never suffixed again) by both escalation_resolved_assigned
# ("{name} க்கு" template) and why_lost_answer ("{name} உடைய வயலுக்கு"
# template, which independently doubled the noun for the same reason
# before this fix -- see that function's own comment).

DEFAULT_WINNER_ACCUSATIVE = "தேர்ந்தெடுக்கப்பட்ட வயலை"
# For route_drop_confirm_prompt's fallback. Not the genitive sibling of
# DEFAULT_WINNER_DATIVE, on inspection: that template's real-name path
# builds a POSSESSOR (genitive "{name}-ன்") in front of a separate
# POSSESSED noun ("வயலை", accusative -- the plot being dropped). Fusing
# a genitive onto DEFAULT_WINNER_LABEL ("தேர்ந்தெடுக்கப்பட்ட வயலின்")
# and keeping the template's own trailing "வயலை" would read "the
# selected plot's plot" -- grammatically fused, but doubling the noun,
# the exact class of bug messages_en.py's escalation_resolved_lost
# comment already documents catching once before. There's no possessor
# in the fallback case, so this constant stands in for the whole
# "{possessor}-ன் வயலை" fragment at once, already accusative-marked,
# used as a complete phrase and never suffixed further.


def escalation_resolved_assigned(winner_name: str | None) -> str:
    # winner_name=None means no farmer record was found for the winning
    # plot -- uses the pre-fused DEFAULT_WINNER_DATIVE rather than
    # gluing this function's own spaced "{name} க்கு" onto
    # DEFAULT_WINNER_LABEL, which would be exactly the bug described
    # above the constant.
    dative_phrase = f"{winner_name} க்கு" if winner_name else DEFAULT_WINNER_DATIVE
    return f"இயந்திரம் {dative_phrase} ஒதுக்கப்பட்டது."
    # "Machine assigned to {winner_name}." -- winner_name is a farmer's
    # name, a proper noun, not translated -- same rule as everywhere else.


def unrecognized_action() -> str:
    return "அடையாளம் தெரியாத செயல்."
    # "Unrecognized action." -- fires when the callback data itself
    # doesn't parse, before any cluster_id is known, so this one can't be
    # dispatched by Cluster.operator_language -- defaults to Tamil, same
    # as the product-wide default everywhere else a language isn't yet
    # resolvable.
    # "agent's recommendation:" -- went through two prior wordings: first
    # "ஏஜென்ட்டின் வாதம் (உண்மை அல்ல)" ("agent's argument (not true)"),
    # which read as a truth-value disclaimer; then "ஏஜென்ட்டின் கருத்து:"
    # (agent's opinion), which lost the fact/reasoning distinction the
    # operator needs (this line is generated, everything else in the
    # message is measured). Picked after seeing both rendered in the
    # full escalation context, round 3 review.


# --- Machine breakdown (ADR-011 Part 2) -- operator-only. DRAFT, pending
# native-speaker review, same print_tamil_strings.py discipline as every
# other string in this file.

BREAKDOWN_BUTTON_LABEL = "🚜 இன்று இயந்திரம் பழுதடைந்தது"
# "🚜 Machine down today"
MACHINE_BACK_BUTTON_LABEL = "🚜 இயந்திரம் மீண்டும் இயங்குகிறது"
# "🚜 Machine is back"
BREAKDOWN_FOLLOWUP_TOMORROW_LABEL = "நாளை திரும்பும்"
# "Back tomorrow"
BREAKDOWN_FOLLOWUP_INDEFINITE_LABEL = "காலவரையின்றி பழுதடைந்தது"
# "Down indefinitely"


def breakdown_acknowledged() -> str:
    return "பதிவு செய்யப்பட்டது; பாதை மறு கணக்கீடு செய்யப்படுகிறது."
    # "Recorded; recalculating the route."


def breakdown_already_reported() -> str:
    return "இன்று ஏற்கனவே பதிவு செய்யப்பட்டுள்ளது."
    # "Already reported today."


def breakdown_no_route() -> str:
    return "இன்று பழுது பதிவு செய்ய பாதை எதுவும் இல்லை."
    # "No route today to report a breakdown for."


def breakdown_recompute_failed() -> str:
    return "பதிவு செய்யப்பட்டது, ஆனால் மறு கணக்கீடு இப்போது இயங்கவில்லை -- அடுத்த சோதனையில் மீண்டும் முயற்சிக்கப்படும்."
    # "Recorded, but the recompute couldn't run right now -- will retry
    #  on the next scheduled check."


def breakdown_followup_prompt() -> str:
    return "இது ஒரு நாள் பிரச்சனையா, அல்லது தொடர்கிறதா?"
    # "Is this a one-day issue, or ongoing?"


def breakdown_back_tomorrow_ack() -> str:
    return "குறிப்பிடப்பட்டது -- நாளை வழக்கம் போல் இயங்கும்."
    # "Noted -- tomorrow's run will proceed normally."


def machine_down_indefinite_ack() -> str:
    return "மேலும் அறிவிக்கும் வரை பழுதடைந்ததாகக் குறிக்கப்பட்டது. இயந்திரம் திரும்பியதும் கீழே தட்டவும்."
    # "Marked down until further notice. Tap below when it's back."


def machine_back_ack() -> str:
    return "மீண்டும் இயங்குவதாகக் குறிக்கப்பட்டது."
    # "Marked operational again."


# --- Harvest confirmation loop (ADR-009 Part 2) ---
# DRAFT, pending native-speaker review, same process as every other
# string in this file (scripts/print_tamil_strings.py dump-and-review).

CONFIRMATION_YES_LABEL = "ஆம்"
# "Yes"
CONFIRMATION_NO_LABEL = "இல்லை"
# "No"


def confirmation_thanks() -> str:
    return "நன்றி, பதிவு செய்யப்பட்டது."
    # "Thanks, recorded."


def confirmation_not_found() -> str:
    return "அந்த உறுதிப்படுத்தல் கிடைக்கவில்லை -- இது பழைய செய்தியாக இருக்கலாம்."
    # "That confirmation wasn't found -- this may be an old message."


# --- Season rollover (ADR-011 Part 1) ---
# DRAFT, pending native-speaker review, same print_tamil_strings.py
# dump-and-review discipline as every other string in this file. Two taps
# (CONFIRMATION_YES_LABEL/CONFIRMATION_NO_LABEL above, reused as-is) plus,
# only on "yes," one free-text date reply.

def season_rollover_prompt() -> str:
    return "இந்த பருவத்திலும் நடவு செய்தீர்களா? 'ஆம்' எனத் தட்டி தேதியைச் சொல்லுங்கள்."
    # "Transplanting again this season? Tap yes and tell me the date."


def season_rollover_declined_ack() -> str:
    return "புரிந்தது -- அடுத்த பருவத்தில் உங்களைச் சந்திக்க ஆவலாக உள்ளோம்."
    # "Understood -- hope to see you next season."


def season_rollover_date_prompt() -> str:
    return "எந்த தேதி நடவு செய்தீர்கள்?"
    # "What date did you transplant?"


def season_rollover_confirmed_ack() -> str:
    return "பதிவு செய்யப்பட்டது -- இந்த பருவத்திற்கான பட்டியலில் நீங்கள் உள்ளீர்கள்."
    # "Got it -- you're on the list for this season."


# --- Operator enrollment (ADR-012 Part 2) -- DRAFT, pending
# native-speaker review, same status as every other string in this file
# when first written. Reuses CONFIRMATION_YES_LABEL/CONFIRMATION_NO_LABEL
# for the replacement prompt's buttons.

def operator_command_usage() -> str:
    return "/operator என்று தட்டச்சு செய்து, அதற்குப் பின் உங்கள் குறியீட்டைச் சேர்க்கவும்."
    # "Send /operator followed by your enrolment code."


def operator_invalid_code() -> str:
    return (
        "இந்தக் குறியீடு செல்லுபடியாகாது அல்லது ஏற்கனவே "
        "பயன்படுத்தப்பட்டுவிட்டது. புதிய குறியீட்டிற்கு உங்கள் "
        "கிளஸ்டர் ஒருங்கிணைப்பாளரிடம் கேளுங்கள்."
    )
    # "That code isn't valid or has already been used. Ask your cluster
    #  coordinator for a new one."


def operator_expired_code() -> str:
    return (
        "இந்தக் குறியீட்டின் காலம் முடிந்துவிட்டது. புதிய "
        "குறியீட்டிற்கு உங்கள் கிளஸ்டர் ஒருங்கிணைப்பாளரிடம் கேளுங்கள்."
    )
    # "That code has expired. Ask your cluster coordinator for a new one."


def operator_language_prompt() -> str:
    return "குறியீடு ஏற்கப்பட்டது. உங்கள் மொழியைத் தேர்ந்தெடுக்கவும்."
    # "Enrolment code accepted. Please choose your language."


def operator_replacement_prompt(cluster_name: str) -> str:
    return (
        f"{cluster_name}-க்கு ஏற்கனவே ஒரு ஆபரேட்டர் பதிவு "
        "செய்யப்பட்டுள்ளார். அவரை நீங்கள் மாற்ற விரும்புகிறீர்களா?"
    )
    # "{cluster_name} already has a registered operator. Replace them
    #  with you?"


def operator_replacement_declined() -> str:
    return "புரிந்தது -- தற்போதைய ஆபரேட்டரே தொடர்வார்."
    # "Understood -- the existing operator stays registered."


def operator_enrolled(cluster_name: str) -> str:
    return (
        f"நீங்கள் இப்போது {cluster_name}-க்கான ஆபரேட்டராகப் பதிவு "
        "செய்யப்பட்டுள்ளீர்கள். இங்கு பாதைச் சுருக்கங்களைப் "
        "பெறுவீர்கள், இயந்திர நிலைக்கான பட்டன்களைத் தட்டலாம்."
    )
    # "You're now registered as the operator for {cluster_name}. You'll
    #  get route summaries here and can tap buttons for machine status."


# --- Route proposal and operator override (ADR-013) -- operator-only.
# DRAFT, pending native-speaker review, same print_tamil_strings.py
# dump-and-review discipline as every other string in this file,
# including a live-Telegram-receipt round given ADR-008 Decision 17's
# finding that string-level review alone has missed real bugs before.

ROUTE_ACCEPT_BUTTON_LABEL = "✅ ஏற்றுக்கொள்"
# "✅ Accept"
ROUTE_MODIFY_BUTTON_LABEL = "✏️ மாற்று"
# "✏️ Modify"
ROUTE_DROP_BUTTON_LABEL = "✕"
# symbol only, same as English -- no translation needed for a single glyph
ROUTE_SWAP_UP_BUTTON_LABEL = "↑"
# symbol only, same as English
ROUTE_DONE_BUTTON_LABEL = "✅ முடிந்தது"
# "✅ Done"
ROUTE_DROP_CONFIRM_YES_LABEL = "✅ உறுதி"
# "✅ Confirm"
ROUTE_DROP_CONFIRM_NO_LABEL = "❌ ரத்து"
# "❌ Cancel"


def route_accept_ack() -> str:
    return "✅ ஏற்றுக்கொள்ளப்பட்டது"
    # "✅ Accepted"


def route_edit_header(cluster_name: str) -> str:
    return f"{cluster_name} -- இன்றைய பாதையைத் திருத்துகிறீர்கள்:"
    # "{cluster_name} -- editing today's route:"


def route_drop_confirm_prompt(farmer_name: str | None, *, is_last_plot: bool) -> str:
    # farmer_name=None means no farmer record was found for this plot --
    # uses the pre-formed DEFAULT_WINNER_ACCUSATIVE rather than gluing
    # this function's own "{name}-ன் வயலை" onto DEFAULT_WINNER_LABEL,
    # which would double "plot" (see DEFAULT_WINNER_ACCUSATIVE's comment).
    subject = f"{farmer_name}-ன் வயலை" if farmer_name else DEFAULT_WINNER_ACCUSATIVE
    text = f"{subject} இன்றைய பாதையிலிருந்து நீக்கவா?"
    # "Drop {farmer_name}'s plot from today's route?"
    if is_last_plot:
        text += (
            " இது இன்றைய பாதையின் கடைசி வயல் -- உறுதிப்படுத்தினால் "
            "இன்று யாருக்கும் இயந்திரம் வராது."
        )
        # " This is the last plot on today's route -- confirming leaves
        #  nobody scheduled today."
    return text


def route_done_ack() -> str:
    return "பாதை புதுப்பிக்கப்பட்டது."
    # "Route updated."


def route_already_confirmed() -> str:
    return (
        "இந்த வயலின் அறுவடை ஏற்கனவே உறுதிப்படுத்தப்பட்டது -- இன்றைய "
        "பாதையை இதற்கு மாற்ற முடியாது."
    )
    # "This plot's harvest was already confirmed -- today's route can't
    #  be changed for it."


def route_stale(decision_date: str) -> str:
    return f"இந்த பாதை {decision_date} தேதியிலிருந்தது, இப்போது செல்லுபடியாகாது."
    # "This route is from {decision_date} and is no longer active."


def route_dropped_notice(area_acres: float, area_unit: str) -> str:
    # Names ஆபரேட்டர் (the operator) as the one who changed the route,
    # matching escalation_resolved_lost's precedent of naming the actor
    # rather than using passive voice -- passive would obscure that a
    # person made this call. Doesn't invent a reason: this system
    # doesn't know which is true. Doesn't promise he's first next time
    # either -- see the English version's comment for why (capped
    # fairness bump, MAX_FAIRNESS_BONUS invariant, can't outrank real
    # urgency). See ADR-013 Decision 11 and "Resolved on review."
    area = format_area(area_acres, area_unit)
    return (
        f"புதுப்பிப்பு: ஆபரேட்டர் இன்றைய பாதையை மாற்றியுள்ளார் -- இன்று "
        f"உங்கள் {area} வயலுக்கு இயந்திரம் வராது. உங்கள் வயல் மீண்டும் "
        "வழக்கமான பட்டியலில் சேர்க்கப்பட்டுள்ளது, அடுத்த பாதையில் "
        "முதலில் வரும் என உறுதியில்லை."
    )
    # "Update: the operator has changed today's route -- the machine
    #  won't be coming to your {area} plot today. Your plot goes back
    #  into the normal pool; there's no guarantee of going first in the
    #  next route."


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


# --- Proxy registration and farmer linking (ADR-013 Part 2) -- DRAFT,
# pending native-speaker review like every string in this project. ---

def operator_only_command() -> str:
    return "இந்தக் கட்டளையை ஆபரேட்டர் மட்டுமே பயன்படுத்த முடியும்."
    # "Only the operator can use this command."


def addfarmer_farmer_name_prompt() -> str:
    return "விவசாயியின் பெயர் என்ன?"
    # "What is the farmer's name?"


def addfarmer_contact_note_prompt() -> str:
    return (
        "தொடர்பு எண் அல்லது குறிப்பு (இருந்தால்) தட்டச்சு செய்யவும், "
        "இல்லையெனில் 'skip' என தட்டச்சு செய்யவும்."
    )
    # "Type a contact number or note if there is one, otherwise type 'skip'."


def addfarmer_has_phone_prompt() -> str:
    return "இந்த விவசாயிக்கு டெலிகிராம் உள்ள மொபைல் போன் உள்ளதா?"
    # "Does this farmer have a mobile phone with Telegram?"


def addfarmer_confirm_summary(name: str, village: str, area_text: str, date_text: str) -> str:
    return f"{name}, {village}, {area_text}, நடவு தேதி {date_text} — பதிவு செய்யவா?"
    # "{name}, {village}, {area_text}, transplanted {date_text} — register?"


def addfarmer_registered_toast() -> str:
    return "பதிவு செய்யப்பட்டது."
    # "Registered."


def addfarmer_cancelled() -> str:
    return "பதிவு ரத்து செய்யப்பட்டது -- எதுவும் சேமிக்கப்படவில்லை."
    # "Registration cancelled -- nothing was saved."


def addfarmer_nothing_pending() -> str:
    return "எந்தப் பதிவும் நடைபெறவில்லை. தொடங்க /addfarmer அனுப்பவும்."
    # "No registration in progress. Send /addfarmer to start one."


def addfarmer_complete_no_phone(name: str) -> str:
    return (
        f"பதிவு முடிந்தது. {name}-க்கு தொலைபேசி இல்லாததால், அவருக்கான "
        f"தகவல்களை நீங்கள் நேரடியாகத் தெரிவிக்க வேண்டும்."
    )
    # "Registration complete. Since {name} has no phone, you'll need to
    #  inform them directly."


def addfarmer_complete_has_phone(name: str) -> str:
    return (
        f"பதிவு முடிந்தது. {name}-க்கு தொலைபேசி இல்லாததால், அவருக்கான "
        f"தகவல்களை நீங்கள் நேரடியாகத் தெரிவிக்க வேண்டும். பின்னர் அவர் "
        f"சொந்தமாக இந்த போட்டுக்கு செய்தி அனுப்பினால், இரண்டு பதிவுகளையும் "
        f"இணைக்க /linkfarmer பயன்படுத்தவும்."
    )
    # "Registration complete. Since {name} has no phone, you'll need to
    #  inform them directly. If they later message this bot themselves,
    #  use /linkfarmer to connect the two records."


def linkfarmer_nothing_to_link() -> str:
    return "இணைக்க எந்த விவசாயியும் இல்லை."
    # "There is no farmer to link."


def linkfarmer_pick_proxy_prompt() -> str:
    return "எந்த விவசாயியை இணைக்க விரும்புகிறீர்கள்?"
    # "Which farmer do you want to link?"


def linkfarmer_pick_match_prompt(proxy_name: str) -> str:
    return f"{proxy_name}-ஐ எந்த சுய-பதிவு செய்த விவசாயியுடன் இணைப்பது?"
    # "Link {proxy_name} with which self-registered farmer?"


def linkfarmer_confirm_prompt(proxy_name: str, candidate_name: str) -> str:
    return (
        f"{proxy_name}-ஐ (நீங்கள் பதிவு செய்தது) {candidate_name}-உடன் "
        f"(சுய-பதிவு) இணைக்கவா? {candidate_name}-ன் தனி வயல் இனி "
        f"பட்டியலிடப்படாது -- {proxy_name}-ன் வயல் தொடரும், இப்போது "
        f"நேரடியாக டெலிகிராமில் சென்றடையும்."
    )
    # "Link {proxy_name} (registered by you) with {candidate_name}
    #  (self-registered)? {candidate_name}'s separate plot will stop
    #  being scheduled -- {proxy_name}'s plot continues, now reaching
    #  them directly on Telegram."


def linkfarmer_linked_toast() -> str:
    return "இணைக்கப்பட்டது."
    # "Linked."


LINKFARMER_UNDO_BUTTON_LABEL = "↩️ செயல்தவிர்"
# "Undo"


def linkfarmer_linked_with_undo_text() -> str:
    return (
        "இணைக்கப்பட்டது. தவறான பொருத்தமாக இருந்தால், அடுத்த ஒரு மணி "
        "நேரத்திற்குள் இதைத் திரும்பப் பெறலாம்."
    )
    # "Linked. You can undo this for the next hour if it was the wrong match."


def linkfarmer_undo_expired() -> str:
    return "திரும்பப் பெற தாமதமாகிவிட்டது -- ஒரு மணி நேரத்திற்கும் மேலாகிவிட்டது."
    # "Too late to undo -- more than an hour has passed."


def linkfarmer_undo_failed() -> str:
    return "திரும்பப் பெற முடியவில்லை -- இந்த இணைப்பு ஏற்கனவே மாறியிருக்கலாம்."
    # "Couldn't undo -- this link may have already changed."


def linkfarmer_undone_toast() -> str:
    return "திரும்பப் பெறப்பட்டது -- எதுவும் இணைக்கப்படவில்லை."
    # "Undone -- nothing is linked."


def linkfarmer_cancelled_toast() -> str:
    return "ரத்து செய்யப்பட்டது -- எதுவும் இணைக்கப்படவில்லை."
    # "Cancelled -- nothing was linked."


# --- /help (ADR-014) -- read-only status, assembled from stored records
# only, never a live recompute. See telegram/help.py. DRAFTS, pending
# native-speaker review.

def help_unavailable() -> str:
    return "எங்கள் தரப்பில் ஏதோ சரியாக அமைக்கப்படவில்லை -- பின்னர் முயற்சிக்கவும்."
    # "Something's not set up right on our end -- please try again later."


def help_unregistered() -> str:
    return "நீங்கள் இன்னும் பதிவு செய்யவில்லை -- தொடங்க எனக்கு ஏதேனும் செய்தி அனுப்புங்கள்."
    # "You're not registered yet -- send me any message to get started."


def help_no_plot_found() -> str:
    return "உங்கள் பதிவு உள்ளது, ஆனால் வயல் பதிவில் இல்லை -- உங்கள் ஆபரேட்டரைத் தொடர்பு கொள்ளவும்."
    # "We have your registration but no plot on record -- please contact your operator."


def help_farmer_reply(
    village: str | None, area_text: str, transplant_date_text: str, projected_ready_text: str | None,
) -> str:
    village_text = village or "கிராமம் பதிவு செய்யப்படவில்லை"
    if projected_ready_text:
        ready_text = f"{projected_ready_text} அளவில் தயாராக இருக்கும் என எதிர்பார்க்கப்படுகிறது"
    else:
        ready_text = (
            "இன்னும் கிடைக்கவில்லை -- உங்கள் வயல் தயாராகும் ஒரு வாரத்திற்கு "
            "முன்பு அந்த மதிப்பீட்டை அனுப்புவோம்"
        )
    return (
        f"உங்கள் வயல்: {village_text}, {area_text}, நடவு தேதி {transplant_date_text}.\n"
        f"தயாராகும் தேதி: {ready_text}.\n"
        f"இயந்திரம் திட்டமிடப்படும்போது அல்லது உங்கள் வயலுக்கு கவனம் "
        f"தேவைப்படும்போது நாங்கள் உங்களுக்குச் செய்தி அனுப்புவோம் -- விசாரிக்க "
        f"வேண்டாம்."
    )
    # "Your plot: {village}, {area}, transplanted {date}. Ready date:
    #  {ready}. We'll message you when the machine is scheduled or your
    #  plot needs attention -- no need to ask." -- ready_text's fallback:
    #  "not available yet -- we send that estimate about a week before
    #  your plot is ready."


def help_operator_reply(cluster_name: str) -> str:
    return (
        f"நீங்கள் {cluster_name}-க்கான ஆபரேட்டர்.\n"
        f"கட்டளைகள்: தொலைபேசி இல்லாத விவசாயியைப் பதிவு செய்ய /addfarmer, "
        f"சுய-பதிவுக்குப் பிறகு இணைக்க /linkfarmer, உங்களை மாற்ற "
        f"/operator <code>. பாதை மாற்றங்களும் பழுது அறிக்கைகளும் இன்றைய "
        f"செய்திகளில் உள்ள பொத்தான்கள் மூலம் நடக்கும்."
    )
    # "You're the operator for {cluster_name}. Commands: /addfarmer to
    #  register a phone-less farmer, /linkfarmer to connect after
    #  self-registration, /operator <code> to replace yourself. Route
    #  changes and breakdown reports happen via the buttons on today's
    #  messages."


def help_operator_addendum(cluster_name: str) -> str:
    return (
        f"நீங்கள் {cluster_name}-க்கான ஆபரேட்டரும் கூட -- கட்டளைகள்: "
        f"/addfarmer, /linkfarmer, /operator <code>."
    )
    # "You're also the operator for {cluster_name} -- commands:
    #  /addfarmer, /linkfarmer, /operator <code>."
