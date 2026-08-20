"""Season rollover reply flow -- ADR-011 Part 1. A much narrower cousin
of registration.py's four-message FSM: the yes/no tap is handled
directly by webhook.py's callback-query dispatch (see
webhook.handle_rollover_callback), and this module covers only what
happens after a "yes" tap -- one free-text date reply, nothing else.
Never re-asks village, location, crop, or area; those are already on
record and unaffected by a season rollover.

Same disclosed limitation as registration.py's _STATE_STORE: an
in-memory placeholder, does not survive a process restart. A farmer
mid-reply across a restart falls through to registration.handle_incoming
instead, which (having no pending rollover context) treats their date
reply as ordinary chat noise against whatever RegistrationStep they're
actually in -- almost always COMPLETE, which just re-sends
COMPLETE_MESSAGE. Disclosed, not silently different in kind from
registration's own long-standing restart gap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from harvest_convoy.storage import Storage
from harvest_convoy.storage.interface import SeasonRolloverPrompt
from harvest_convoy.telegram import messages_en, messages_ta
from harvest_convoy.telegram.registration import IncomingMessage, OutboundMessage, _parse_date

logger = logging.getLogger(__name__)

_LANGUAGE_MODULES = {"ta": messages_ta, "en": messages_en}


def _lang_module(language: str):
    return _LANGUAGE_MODULES.get(language, messages_ta)


@dataclass(frozen=True)
class RolloverState:
    chat_id: int
    plot_id: str
    new_season_id: str
    language: str = "ta"


# Placeholder store -- see module docstring.
_STATE_STORE: dict[int, RolloverState] = {}


def get_pending_state(chat_id: int) -> RolloverState | None:
    return _STATE_STORE.get(chat_id)


def start_awaiting_date(chat_id: int, plot_id: str, new_season_id: str, language: str) -> None:
    _STATE_STORE[chat_id] = RolloverState(
        chat_id=chat_id, plot_id=plot_id, new_season_id=new_season_id, language=language,
    )


def clear_state(chat_id: int) -> None:
    _STATE_STORE.pop(chat_id, None)


def advance_rollover_reply(
    state: RolloverState, incoming: IncomingMessage, storage: Storage,
) -> tuple[bool, OutboundMessage]:
    """Returns (resolved, outbound). resolved=True means the date was
    parsed and persisted -- Plot.transplant_date updated,
    SeasonRolloverPrompt.replied=True -- and the caller should clear the
    pending state. resolved=False means re-prompt for the date; the
    pending state stays as-is, same re-prompt discipline every other
    free-text step in this codebase already uses (never guesses, never
    silently drops the farmer into a longer conversation).
    """
    lang = _lang_module(state.language)
    text = incoming.text or ""
    transplant_date = _parse_date(text)
    if transplant_date is None:
        return False, OutboundMessage(lang.season_rollover_date_prompt())

    plot = storage.get_plot(state.plot_id)
    if plot is None:
        logger.error(
            "rollover: plot %s no longer exists in storage, cannot update "
            "transplant_date for chat_id=%s",
            state.plot_id, state.chat_id,
        )
        return True, OutboundMessage(lang.season_rollover_confirmed_ack())

    plot_result = storage.put_plot(replace(plot, transplant_date=transplant_date))
    if not plot_result.success:
        logger.error(
            "rollover: failed to update transplant_date for plot=%s: %s",
            state.plot_id, plot_result.error,
        )
        return True, OutboundMessage(lang.season_rollover_confirmed_ack())

    now_iso = datetime.now(timezone.utc).isoformat()
    prompt = storage.get_season_rollover_prompt(state.plot_id, state.new_season_id)
    if prompt is None:
        # No prompt record exists -- e.g. storage was reset between the
        # tap and this reply. The transplant_date update above still
        # succeeded (the farmer's real intent), but there is no prompt
        # record left to mark replied=True; the plot falls back to
        # Decision 2's default-include rule (no record => included),
        # which is the correct outcome here regardless.
        logger.warning(
            "rollover: no SeasonRolloverPrompt found for plot=%s season=%s "
            "when recording the date reply -- transplant_date was still "
            "updated",
            state.plot_id, state.new_season_id,
        )
        return True, OutboundMessage(lang.season_rollover_confirmed_ack())

    prompt_result = storage.put_season_rollover_prompt(
        replace(prompt, replied=True, replied_at=now_iso)
    )
    if not prompt_result.success:
        logger.error(
            "rollover: failed to record replied=True for plot=%s season=%s: %s",
            state.plot_id, state.new_season_id, prompt_result.error,
        )

    return True, OutboundMessage(lang.season_rollover_confirmed_ack())
