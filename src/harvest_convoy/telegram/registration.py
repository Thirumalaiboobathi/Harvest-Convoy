"""Four-message registration flow. See docs/adr/ADR-004-telegram.md
Decision 2: the state transition is a pure function, fully testable
without Telegram or storage; the per-chat store is an explicit in-memory
placeholder until Phase 5's DynamoDB backing exists.

Tamil support (ADR-008 Part 2): language choice is folded into message 1
rather than added as a fifth message. Message 1 is bilingual (greeting +
தமிழ்/English tap buttons + the village question in both languages); a
button tap answers via Telegram's callback_query mechanism (a toast, not
a chat message) and never counts against the four-message budget. If the
farmer skips the buttons and just types the village name, the language is
inferred from script (Tamil Unicode present -> Tamil; pure Latin script
never auto-switches to English, since that's ambiguous with Tanglish --
only an explicit tap sets "en"). See ADR-008 Decision 7.

All display strings live in messages_ta.py/messages_en.py -- this module
holds only the state machine and input parsing (which must accept Tamil
script AND Tanglish, but isn't itself a "string" in the localization
sense).

Persistence (ADR-009 Part 3): advance_registration() above stays a pure
function, unchanged -- persistence and the live maturity-projection call
happen only in handle_incoming(), the stateful wrapper, at the exact
moment a farmer's step transitions into COMPLETE. A farmer's Telegram-
provided first_name/username (IncomingMessage.sender_name, threaded from
webhook.parse_incoming_message) becomes Farmer.name -- the four-message
flow itself never asks for one, and Telegram already gives it to us for
free on every message, so asking would fail the "does the agent already
know enough to speak first" test in reverse.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum
from typing import Literal

from harvest_convoy.agronomy.calibration import project_maturity_for_plot
from harvest_convoy.models import Farmer, Plot
from harvest_convoy.storage import Storage
from harvest_convoy.telegram import messages_en, messages_ta
from harvest_convoy.telegram.client import TelegramClient
from harvest_convoy.weather.openmeteo import WeatherError

logger = logging.getLogger(__name__)

CROP = "paddy"
VARIETY = "ADT45"

_LANGUAGE_MODULES = {"ta": messages_ta, "en": messages_en}


def _lang_module(language: str):
    """Unknown/legacy language codes default to Tamil, same as
    Farmer.language's dataclass default -- never KeyErrors on an
    unexpected value."""
    return _LANGUAGE_MODULES.get(language, messages_ta)


class RegistrationStep(str, Enum):
    AWAITING_VILLAGE = "awaiting_village"
    AWAITING_LOCATION = "awaiting_location"
    AWAITING_CROP_CONFIRM = "awaiting_crop_confirm"
    AWAITING_TRANSPLANT_INFO = "awaiting_transplant_info"
    COMPLETE = "complete"
    # Terminal, like COMPLETE, but reached via an explicit "no" at
    # crop-confirmation rather than finishing registration -- see
    # ADR-008 Decision 11's revision note: this path didn't exist before
    # (no no/yes check at all existed), a real dead end for any farmer
    # with a non-paddy plot.
    CROP_CONFIRM_DECLINED = "crop_confirm_declined"


@dataclass(frozen=True)
class RegistrationState:
    chat_id: int
    step: RegistrationStep = RegistrationStep.AWAITING_VILLAGE
    language: Literal["ta", "en"] = "ta"
    greeted: bool = False  # has message 1 (the bilingual greeting) been sent yet
    village: str | None = None
    lat: float | None = None
    lon: float | None = None
    transplant_date: date | None = None
    area_acres: float | None = None
    area_unit: Literal["acre", "cent"] = "acre"


@dataclass(frozen=True)
class IncomingMessage:
    text: str | None = None
    location: tuple[float, float] | None = None  # (lat, lon)
    # Telegram's own User.first_name/username (see webhook.parse_incoming_message)
    # -- becomes Farmer.name at the COMPLETE transition. See ADR-009 Part 3.
    sender_name: str | None = None


@dataclass(frozen=True)
class OutboundMessage:
    text: str
    reply_markup: dict | None = None


LANGUAGE_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "தமிழ்", "callback_data": "lang:ta"},
            {"text": "English", "callback_data": "lang:en"},
        ]
    ]
}


def _bilingual_greeting_text() -> str:
    """The one genuinely bilingual message -- composed from each
    language module's own strings (GREETING_INTRO, the village prompt),
    not a separate hardcoded string. Every substring here still lives in
    messages_en.py/messages_ta.py; this function only orders them."""
    village_en = messages_en.PROMPTS[RegistrationStep.AWAITING_VILLAGE]
    village_ta = messages_ta.PROMPTS[RegistrationStep.AWAITING_VILLAGE]
    return (
        f"{messages_en.GREETING_INTRO} / {messages_ta.GREETING_INTRO}\n"
        f"தமிழ் / English?\n\n"
        f"{village_en} / {village_ta}"
    )


_TAMIL_SCRIPT_RANGE = (0x0B80, 0x0BFF)


def _contains_tamil_script(text: str) -> bool:
    return any(_TAMIL_SCRIPT_RANGE[0] <= ord(ch) <= _TAMIL_SCRIPT_RANGE[1] for ch in text)


# Crop-confirmation yes/no matcher (ADR-008 Decision 11). Confirmed bug
# fix independent of Tamil support: the old code accepted *any* non-empty
# reply as confirmation -- no actual yes/no check existed, and no "no"
# path existed at all (a farmer with a non-paddy plot hit a dead end).
# Round 2 (native-speaker review, 2026-08-17): word list widened for
# real phone typing, not textbook forms -- still a first pass, still
# expected to grow.
_YES_WORDS = {
    # English
    "yes", "y", "ok", "okay", "ok ok", "done", "confirm",
    # Tamil script
    "ஆம்", "ஆமாம்", "ஆமா", "சரி", "சரிங்க", "ஓகே", "ஆகட்டும்",
    # Tanglish (romanized Tamil)
    "aam", "aama", "aamaa", "seri", "sari", "seringa", "oke",
}

_NO_WORDS = {
    # English
    "no", "n", "not", "no no",
    # Tamil script
    "இல்லை", "வேண்டாம்",
    # Tanglish (romanized Tamil)
    "illai", "vendam", "venam",
}


def _is_yes(text: str) -> bool:
    return text.strip().lower() in _YES_WORDS


def _is_no(text: str) -> bool:
    return text.strip().lower() in _NO_WORDS


_DATE_PATTERNS = [
    (r"(\d{4})-(\d{1,2})-(\d{1,2})", lambda m: date(int(m[1]), int(m[2]), int(m[3]))),
    (r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", lambda m: date(int(m[3]), int(m[2]), int(m[1]))),
]

_MONTHS = {
    name: i
    for i, name in enumerate(
        [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ],
        start=1,
    )
}

# Standard Tamil transliterations, matching messages_ta.py's _MONTH_NAMES.
_TAMIL_MONTHS = {
    "ஜனவரி": 1, "பிப்ரவரி": 2, "மார்ச்": 3, "ஏப்ரல்": 4, "மே": 5,
    "ஜூன்": 6, "ஜூலை": 7, "ஆகஸ்ட்": 8, "செப்டம்பர்": 9,
    "அக்டோபர்": 10, "நவம்பர்": 11, "டிசம்பர்": 12,
}

# Common romanized-Tamil (Tanglish) and English-abbreviation spellings.
# First pass -- flagged for native-speaker correction like every other
# string/word-list in ADR-008 Part 2.
_TANGLISH_MONTHS = {
    "jan": 1, "pebravari": 2, "peb": 2, "marc": 3, "maarch": 3,
    "april": 4, "ap": 4, "mei": 5, "jun": 6, "julai": 7,
    "augast": 8, "agasth": 8, "sep": 9, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}

_ALL_MONTHS = {**_MONTHS, **_TAMIL_MONTHS, **_TANGLISH_MONTHS}

# [A-Za-z] for English/Tanglish, U+0B80-U+0BFF for Tamil script.
_MONTH_NAME_PATTERN = re.compile(r"(\d{1,2})\s+([A-Za-z஀-௿]+)\s+(\d{4})")

_ACRE_WORDS = r"(?:acres?|ac\b|ஏக்கர்|ekar|eekar)"
_CENT_WORDS = r"(?:cents?|சென்ட்|sent)"
_AREA_PATTERN = re.compile(rf"(\d+(?:\.\d+)?)\s*({_ACRE_WORDS}|{_CENT_WORDS})", re.IGNORECASE)
_CENT_ONLY_PATTERN = re.compile(_CENT_WORDS, re.IGNORECASE)


def _parse_date(text: str) -> date | None:
    match = _MONTH_NAME_PATTERN.search(text)
    if match:
        day, month_name, year = match.groups()
        month = _ALL_MONTHS.get(month_name.lower())
        if month:
            try:
                return date(int(year), month, int(day))
            except ValueError:
                return None
    for pattern, build in _DATE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            try:
                return build(match)
            except ValueError:
                continue
    return None


def _parse_area(text: str) -> tuple[float, Literal["acre", "cent"]] | None:
    """Accepts ஏக்கர்/acre and சென்ட்/cent (plus Tanglish spellings),
    converts to canonical area_acres, and reports which unit the farmer
    actually used for display later (Plot.area_unit). Requires an
    explicit unit word -- no bare-number fallback: a bare number in this
    free-text field is ambiguous with the date's own digits (e.g. "18"
    in "18 May 2026"), so guessing would silently misread the wrong
    number as area. Same requirement the original English-only parser
    already had; not a new restriction."""
    match = _AREA_PATTERN.search(text)
    if not match:
        return None
    raw_value = float(match.group(1))
    unit_word = match.group(2)
    if _CENT_ONLY_PATTERN.fullmatch(unit_word):
        return raw_value / 100.0, "cent"
    return raw_value, "acre"


def advance_registration(
    state: RegistrationState, incoming: IncomingMessage
) -> tuple[RegistrationState, OutboundMessage]:
    """Pure state transition: given the current step and one incoming
    message, return the next state and the message to send back. Never
    raises on malformed input -- re-prompts instead.
    """
    lang = _lang_module(state.language)

    if state.step == RegistrationStep.AWAITING_VILLAGE:
        if not state.greeted:
            new_state = replace(state, greeted=True)
            return new_state, OutboundMessage(
                text=_bilingual_greeting_text(), reply_markup=LANGUAGE_KEYBOARD
            )
        if not incoming.text or not incoming.text.strip():
            return state, OutboundMessage(lang.PROMPTS[RegistrationStep.AWAITING_VILLAGE])
        village = incoming.text.strip()
        # Only Tamil script infers a language switch; Latin script never
        # does (ambiguous with Tanglish) -- see ADR-008 Decision 7.
        inferred_language = "ta" if _contains_tamil_script(village) else state.language
        new_state = replace(
            state, village=village, language=inferred_language,
            step=RegistrationStep.AWAITING_LOCATION,
        )
        return new_state, OutboundMessage(
            _lang_module(new_state.language).PROMPTS[RegistrationStep.AWAITING_LOCATION]
        )

    if state.step == RegistrationStep.AWAITING_LOCATION:
        if incoming.location is None:
            return state, OutboundMessage(
                lang.LOCATION_RETRY_PREFIX + lang.PROMPTS[RegistrationStep.AWAITING_LOCATION]
            )
        lat, lon = incoming.location
        new_state = replace(
            state, lat=lat, lon=lon, step=RegistrationStep.AWAITING_CROP_CONFIRM
        )
        return new_state, OutboundMessage(
            lang.PROMPTS[RegistrationStep.AWAITING_CROP_CONFIRM]
        )

    if state.step == RegistrationStep.AWAITING_CROP_CONFIRM:
        if incoming.text and _is_no(incoming.text):
            new_state = replace(state, step=RegistrationStep.CROP_CONFIRM_DECLINED)
            return new_state, OutboundMessage(lang.CROP_CONFIRM_DECLINED_MESSAGE)
        if not incoming.text or not _is_yes(incoming.text):
            return state, OutboundMessage(lang.PROMPTS[RegistrationStep.AWAITING_CROP_CONFIRM])
        new_state = replace(state, step=RegistrationStep.AWAITING_TRANSPLANT_INFO)
        return new_state, OutboundMessage(
            lang.PROMPTS[RegistrationStep.AWAITING_TRANSPLANT_INFO]
        )

    if state.step == RegistrationStep.AWAITING_TRANSPLANT_INFO:
        text = incoming.text or ""
        transplant_date = _parse_date(text)
        area_result = _parse_area(text)
        if transplant_date is None or area_result is None:
            missing = []
            if transplant_date is None:
                missing.append(lang.MISSING_DATE_LABEL)
            if area_result is None:
                missing.append(lang.MISSING_AREA_LABEL)
            prefix = lang.transplant_info_missing_prefix(missing)
            return state, OutboundMessage(
                prefix + lang.PROMPTS[RegistrationStep.AWAITING_TRANSPLANT_INFO]
            )
        area_acres, area_unit = area_result
        new_state = replace(
            state,
            transplant_date=transplant_date,
            area_acres=area_acres,
            area_unit=area_unit,
            step=RegistrationStep.COMPLETE,
        )
        return new_state, OutboundMessage(lang.COMPLETE_MESSAGE)

    if state.step == RegistrationStep.CROP_CONFIRM_DECLINED:
        # Stable, like COMPLETE -- nothing further expected this season.
        return state, OutboundMessage(lang.CROP_CONFIRM_DECLINED_MESSAGE)

    # COMPLETE: nothing further expected from this farmer this season.
    return state, OutboundMessage(lang.COMPLETE_MESSAGE)


# Phase 4 placeholder store -- see ADR-004 Decision 2. Replaced by
# DynamoDB in Phase 5; does not survive a process restart.
_STATE_STORE: dict[int, RegistrationState] = {}


def get_or_create_state(chat_id: int) -> RegistrationState:
    if chat_id not in _STATE_STORE:
        _STATE_STORE[chat_id] = RegistrationState(chat_id=chat_id)
    return _STATE_STORE[chat_id]


def save_state(state: RegistrationState) -> None:
    _STATE_STORE[state.chat_id] = state


def _persist_completed_registration(
    new_state: RegistrationState, incoming: IncomingMessage, storage: Storage
) -> str:
    """Called only on the step-transitions-into-COMPLETE edge. Persists a
    Farmer/Plot with deterministic IDs (farmer-{chat_id}/plot-{chat_id})
    -- an upsert, so a second completed registration from the same
    chat_id updates them rather than duplicating or being rejected, per
    ADR-009's Prerequisite: one Telegram chat is one farmer's one active
    plot in this system's model, whether the reason for registering
    again is a typo five minutes later or a new season five months
    later. Then tries to compose the one-sentence maturity projection.

    Never raises, never blocks registration: cluster misconfiguration,
    a storage write failure, or a WeatherError all degrade to returning
    the plain COMPLETE_MESSAGE, logged loudly. The farmer still completes
    registration from their point of view either way.
    """
    lang = _lang_module(new_state.language)
    cluster_id = os.environ.get("HARVEST_CONVOY_CLUSTER_ID")
    if not cluster_id:
        logger.error(
            "HARVEST_CONVOY_CLUSTER_ID is not set -- chat_id=%s completed "
            "registration but nothing was persisted",
            new_state.chat_id,
        )
        return lang.COMPLETE_MESSAGE

    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        logger.error(
            "HARVEST_CONVOY_CLUSTER_ID=%s not found in storage -- chat_id=%s "
            "completed registration but nothing was persisted",
            cluster_id, new_state.chat_id,
        )
        return lang.COMPLETE_MESSAGE

    name = (incoming.sender_name or "").strip() or f"Farmer {new_state.chat_id}"
    farmer = Farmer(
        farmer_id=f"farmer-{new_state.chat_id}", name=name, cluster_id=cluster_id,
        telegram_chat_id=new_state.chat_id, language=new_state.language,
    )
    plot = Plot(
        plot_id=f"plot-{new_state.chat_id}", farmer_id=farmer.farmer_id,
        cluster_id=cluster_id, lat=new_state.lat, lon=new_state.lon,
        crop=CROP, variety=VARIETY, transplant_date=new_state.transplant_date,
        area_acres=new_state.area_acres, area_unit=new_state.area_unit,
    )
    farmer_result = storage.put_farmer(farmer)
    plot_result = storage.put_plot(plot)
    if not farmer_result.success or not plot_result.success:
        logger.error(
            "failed to persist registration for chat_id=%s: farmer=%s plot=%s",
            new_state.chat_id, farmer_result.error, plot_result.error,
        )
        return lang.COMPLETE_MESSAGE

    try:
        projected_iso = project_maturity_for_plot(plot, cluster)
        formatted = lang.format_date(date.fromisoformat(projected_iso))
        return lang.COMPLETE_MESSAGE + " " + lang.projected_maturity_sentence(formatted)
    except WeatherError as exc:
        logger.warning(
            "maturity projection failed for chat_id=%s: %s -- registration "
            "still completed, message omits the projection sentence",
            new_state.chat_id, exc,
        )
        return lang.COMPLETE_MESSAGE


def handle_incoming(
    chat_id: int, incoming: IncomingMessage, storage: Storage | None = None
) -> OutboundMessage:
    """Stateful entrypoint webhook.py calls: loads state, advances it,
    persists it, returns the outbound message (text + optional keyboard)
    to send.

    storage (ADR-009 Part 3): optional. Real callers (webhook.py) always
    pass one; pure state-machine tests that never reach COMPLETE can omit
    it -- persistence is simply skipped, not defaulted to some other
    behavior. When the step transitions into COMPLETE and storage was
    given, this is also where the Farmer/Plot get persisted and the
    maturity-projection sentence gets composed -- see
    _persist_completed_registration above.
    """
    state = get_or_create_state(chat_id)
    new_state, outbound = advance_registration(state, incoming)
    save_state(new_state)

    if (
        storage is not None
        and state.step != RegistrationStep.COMPLETE
        and new_state.step == RegistrationStep.COMPLETE
    ):
        text = _persist_completed_registration(new_state, incoming, storage)
        outbound = replace(outbound, text=text)

    return outbound


def handle_language_callback(client: TelegramClient, callback_query: dict) -> None:
    """Handles a "lang:ta"/"lang:en" button tap from message 1's inline
    keyboard. This is a callback_query (a toast/alert), not a sent chat
    message -- it never counts against the four-message budget. Sets the
    farmer's language explicitly; does not re-send the greeting (already
    shown bilingually) or advance the registration step."""
    callback_query_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")

    _, _, code = data.partition(":")
    if chat_id is None or code not in _LANGUAGE_MODULES:
        # An unrecognized code means no language was ever established for
        # this tap -- defaults to Tamil, same as the product-wide default
        # everywhere else a language isn't yet resolvable. Was hardcoded
        # English regardless, same bug class as webhook.py's identically-
        # named toast; caught in the same audit pass. See ADR-008 follow-up.
        client.answer_callback_query(
            callback_query_id, _lang_module("ta").unrecognized_action(), show_alert=True
        )
        return

    state = get_or_create_state(chat_id)
    save_state(replace(state, language=code, greeted=True))
    client.answer_callback_query(callback_query_id, _lang_module(code).LANGUAGE_ACK)
