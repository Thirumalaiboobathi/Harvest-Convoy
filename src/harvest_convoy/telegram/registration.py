"""Four-message registration flow. See docs/adr/ADR-004-telegram.md
Decision 2: the state transition is a pure function, fully testable
without Telegram or storage; the per-chat store is an explicit in-memory
placeholder until Phase 5's DynamoDB backing exists.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

CROP = "paddy"
VARIETY = "ADT45"


class RegistrationStep(str, Enum):
    AWAITING_VILLAGE = "awaiting_village"
    AWAITING_LOCATION = "awaiting_location"
    AWAITING_CROP_CONFIRM = "awaiting_crop_confirm"
    AWAITING_TRANSPLANT_INFO = "awaiting_transplant_info"
    COMPLETE = "complete"


@dataclass(frozen=True)
class RegistrationState:
    chat_id: int
    step: RegistrationStep = RegistrationStep.AWAITING_VILLAGE
    village: str | None = None
    lat: float | None = None
    lon: float | None = None
    transplant_date: date | None = None
    area_acres: float | None = None


@dataclass(frozen=True)
class IncomingMessage:
    text: str | None = None
    location: tuple[float, float] | None = None  # (lat, lon)


PROMPTS = {
    RegistrationStep.AWAITING_VILLAGE: (
        "Welcome to Harvest Convoy. What village is your plot in?"
    ),
    RegistrationStep.AWAITING_LOCATION: (
        "Thanks. Now share your plot's location: tap the paperclip icon "
        "and choose Location."
    ),
    RegistrationStep.AWAITING_CROP_CONFIRM: (
        "This season we're coordinating paddy (ADT 45) only. Reply 'yes' "
        "to register this plot as paddy ADT 45."
    ),
    RegistrationStep.AWAITING_TRANSPLANT_INFO: (
        "Last step: when did you transplant, and how many acres? "
        "For example: \"18 May 2026, 2.5 acres\"."
    ),
}

COMPLETE_MESSAGE = (
    "Registered. You won't hear from us again until the machine's route "
    "is decided or your plot needs attention -- no need to check in."
)

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
_MONTH_NAME_PATTERN = re.compile(
    r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})"
)
_AREA_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:acres?|ac\b)", re.IGNORECASE)


def _parse_date(text: str) -> date | None:
    match = _MONTH_NAME_PATTERN.search(text)
    if match:
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.lower())
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


def _parse_area(text: str) -> float | None:
    match = _AREA_PATTERN.search(text)
    if match:
        return float(match.group(1))
    return None


def advance_registration(
    state: RegistrationState, incoming: IncomingMessage
) -> tuple[RegistrationState, str]:
    """Pure state transition: given the current step and one incoming
    message, return the next state and the text to send back. Never
    raises on malformed input -- re-prompts instead.
    """
    if state.step == RegistrationStep.AWAITING_VILLAGE:
        if not incoming.text or not incoming.text.strip():
            return state, PROMPTS[RegistrationStep.AWAITING_VILLAGE]
        new_state = replace(
            state, village=incoming.text.strip(), step=RegistrationStep.AWAITING_LOCATION
        )
        return new_state, PROMPTS[RegistrationStep.AWAITING_LOCATION]

    if state.step == RegistrationStep.AWAITING_LOCATION:
        if incoming.location is None:
            return state, (
                "That didn't look like a shared location. "
                + PROMPTS[RegistrationStep.AWAITING_LOCATION]
            )
        lat, lon = incoming.location
        new_state = replace(
            state, lat=lat, lon=lon, step=RegistrationStep.AWAITING_CROP_CONFIRM
        )
        return new_state, PROMPTS[RegistrationStep.AWAITING_CROP_CONFIRM]

    if state.step == RegistrationStep.AWAITING_CROP_CONFIRM:
        if not incoming.text or not incoming.text.strip():
            return state, PROMPTS[RegistrationStep.AWAITING_CROP_CONFIRM]
        new_state = replace(state, step=RegistrationStep.AWAITING_TRANSPLANT_INFO)
        return new_state, PROMPTS[RegistrationStep.AWAITING_TRANSPLANT_INFO]

    if state.step == RegistrationStep.AWAITING_TRANSPLANT_INFO:
        text = incoming.text or ""
        transplant_date = _parse_date(text)
        area = _parse_area(text)
        if transplant_date is None or area is None:
            missing = []
            if transplant_date is None:
                missing.append("a date")
            if area is None:
                missing.append("an area in acres")
            return state, (
                f"I couldn't find {' and '.join(missing)} in that message. "
                + PROMPTS[RegistrationStep.AWAITING_TRANSPLANT_INFO]
            )
        new_state = replace(
            state,
            transplant_date=transplant_date,
            area_acres=area,
            step=RegistrationStep.COMPLETE,
        )
        return new_state, COMPLETE_MESSAGE

    # COMPLETE: nothing further expected from this farmer this season.
    return state, COMPLETE_MESSAGE


# Phase 4 placeholder store -- see ADR-004 Decision 2. Replaced by
# DynamoDB in Phase 5; does not survive a process restart.
_STATE_STORE: dict[int, RegistrationState] = {}


def get_or_create_state(chat_id: int) -> RegistrationState:
    if chat_id not in _STATE_STORE:
        _STATE_STORE[chat_id] = RegistrationState(chat_id=chat_id)
    return _STATE_STORE[chat_id]


def save_state(state: RegistrationState) -> None:
    _STATE_STORE[state.chat_id] = state


def handle_incoming(chat_id: int, incoming: IncomingMessage) -> str:
    """Stateful entrypoint webhook.py calls: loads state, advances it,
    persists it, returns the reply text to send."""
    state = get_or_create_state(chat_id)
    new_state, reply = advance_registration(state, incoming)
    save_state(new_state)
    return reply
