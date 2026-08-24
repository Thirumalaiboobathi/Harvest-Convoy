"""ADR-013 Part 2: proxy registration (/addfarmer) and farmer linking
(/linkfarmer) -- letting the cluster's operator register a phone-less
farmer's plot on his behalf, and later connect that record to the
farmer's own self-registration once he has a phone.

Both commands are operator-only text entry points, checked in
webhook.handle_update before any other text routing (mirrors
operator_enrollment.py's "/operator <code>" precedent). Everything
after /addfarmer's first message is still free text (the operator
describing someone else's plot -- see Decision 13's justification for
why that's unavoidable here even though the ordinary registration flow
avoids it). Everything in /linkfarmer is taps -- no free text at all,
and no in-memory state either: each step's callback_data carries every
id the next step needs, so there is nothing to lose on a restart.

See docs/adr/ADR-013-route-proposal-and-operator-override.md, Part 2,
Decisions 12-20.
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal

from harvest_convoy.models import Farmer, Plot
from harvest_convoy.storage import Storage
from harvest_convoy.telegram import messages_en, messages_ta
from harvest_convoy.telegram.registration import (
    CROP,
    VARIETY,
    IncomingMessage,
    OutboundMessage,
    _contains_tamil_script,
    _is_no,
    _is_yes,
    _parse_area,
    _parse_date,
)

logger = logging.getLogger(__name__)

ADD_FARMER_COMMAND = "/addfarmer"
LINK_FARMER_COMMAND = "/linkfarmer"

_LANGUAGE_MODULES = {"ta": messages_ta, "en": messages_en}


def _lang_module(language: str):
    return _LANGUAGE_MODULES.get(language, messages_ta)


class ProxyRegistrationStep(str, Enum):
    AWAITING_FARMER_NAME = "awaiting_farmer_name"
    AWAITING_CONTACT_NOTE = "awaiting_contact_note"
    AWAITING_VILLAGE = "awaiting_village"
    AWAITING_LOCATION = "awaiting_location"
    AWAITING_CROP_CONFIRM = "awaiting_crop_confirm"
    AWAITING_TRANSPLANT_INFO = "awaiting_transplant_info"
    AWAITING_HAS_PHONE = "awaiting_has_phone"
    AWAITING_CONFIRM = "awaiting_confirm"
    COMPLETE = "complete"
    CROP_CONFIRM_DECLINED = "crop_confirm_declined"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ProxyRegistrationState:
    operator_chat_id: int
    cluster_id: str
    step: ProxyRegistrationStep = ProxyRegistrationStep.AWAITING_FARMER_NAME
    language: Literal["ta", "en"] = "ta"
    farmer_name: str | None = None
    contact_note: str | None = None
    village: str | None = None
    lat: float | None = None
    lon: float | None = None
    transplant_date: date | None = None
    area_acres: float | None = None
    area_unit: Literal["acre", "cent"] = "acre"
    has_phone: bool | None = None


def _confirm_keyboard(cluster_id: str, language: str) -> dict:
    # cluster_id, not operator_chat_id: the addfarmer_confirm callback
    # re-derives cluster.operator_chat_id and checks it against the
    # tapper via _is_operator, same as every route_* callback -- an
    # embedded operator_chat_id would be self-referential (the callback
    # data claiming its own author is authorized proves nothing).
    mod = _lang_module(language)
    return {
        "inline_keyboard": [[
            {"text": mod.CONFIRMATION_YES_LABEL, "callback_data": f"addfarmer_confirm:{cluster_id}:yes"},
            {"text": mod.CONFIRMATION_NO_LABEL, "callback_data": f"addfarmer_confirm:{cluster_id}:no"},
        ]]
    }


def _confirm_summary_message(state: ProxyRegistrationState) -> OutboundMessage:
    lang = _lang_module(state.language)
    area_text = lang.format_area(state.area_acres, state.area_unit)
    date_text = lang.format_date(state.transplant_date)
    summary = lang.addfarmer_confirm_summary(state.farmer_name, state.village, area_text, date_text)
    return OutboundMessage(summary, reply_markup=_confirm_keyboard(state.cluster_id, state.language))


def start_proxy_registration(
    operator_chat_id: int, cluster_id: str, language: str
) -> tuple[ProxyRegistrationState, OutboundMessage]:
    state = ProxyRegistrationState(operator_chat_id=operator_chat_id, cluster_id=cluster_id, language=language)
    return state, OutboundMessage(_lang_module(language).addfarmer_farmer_name_prompt())


def advance_proxy_registration(
    state: ProxyRegistrationState, incoming: IncomingMessage
) -> tuple[ProxyRegistrationState, OutboundMessage]:
    """Pure state transition, mirroring registration.advance_registration's
    own shape -- reuses that module's date/area/yes-no parsing verbatim
    rather than duplicating it. Never raises on malformed input --
    re-prompts instead."""
    lang = _lang_module(state.language)

    if state.step == ProxyRegistrationStep.AWAITING_FARMER_NAME:
        name = (incoming.text or "").strip()
        if not name:
            return state, OutboundMessage(lang.addfarmer_farmer_name_prompt())
        new_state = replace(state, farmer_name=name, step=ProxyRegistrationStep.AWAITING_CONTACT_NOTE)
        return new_state, OutboundMessage(lang.addfarmer_contact_note_prompt())

    if state.step == ProxyRegistrationStep.AWAITING_CONTACT_NOTE:
        text = (incoming.text or "").strip()
        note = None if not text or text.strip().lower() == "skip" else text
        new_state = replace(state, contact_note=note, step=ProxyRegistrationStep.AWAITING_VILLAGE)
        return new_state, OutboundMessage(lang.PROMPTS["awaiting_village"])

    if state.step == ProxyRegistrationStep.AWAITING_VILLAGE:
        if not incoming.text or not incoming.text.strip():
            return state, OutboundMessage(lang.PROMPTS["awaiting_village"])
        village = incoming.text.strip()
        inferred_language = "ta" if _contains_tamil_script(village) else state.language
        new_state = replace(
            state, village=village, language=inferred_language,
            step=ProxyRegistrationStep.AWAITING_LOCATION,
        )
        return new_state, OutboundMessage(
            _lang_module(new_state.language).PROMPTS["awaiting_location"]
        )

    if state.step == ProxyRegistrationStep.AWAITING_LOCATION:
        if incoming.location is None:
            return state, OutboundMessage(
                lang.LOCATION_RETRY_PREFIX + lang.PROMPTS["awaiting_location"]
            )
        lat, lon = incoming.location
        new_state = replace(state, lat=lat, lon=lon, step=ProxyRegistrationStep.AWAITING_CROP_CONFIRM)
        return new_state, OutboundMessage(lang.PROMPTS["awaiting_crop_confirm"])

    if state.step == ProxyRegistrationStep.AWAITING_CROP_CONFIRM:
        if incoming.text and _is_no(incoming.text):
            new_state = replace(state, step=ProxyRegistrationStep.CROP_CONFIRM_DECLINED)
            return new_state, OutboundMessage(lang.CROP_CONFIRM_DECLINED_MESSAGE)
        if not incoming.text or not _is_yes(incoming.text):
            return state, OutboundMessage(lang.PROMPTS["awaiting_crop_confirm"])
        new_state = replace(state, step=ProxyRegistrationStep.AWAITING_TRANSPLANT_INFO)
        return new_state, OutboundMessage(lang.PROMPTS["awaiting_transplant_info"])

    if state.step == ProxyRegistrationStep.AWAITING_TRANSPLANT_INFO:
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
            return state, OutboundMessage(prefix + lang.PROMPTS["awaiting_transplant_info"])
        area_acres, area_unit = area_result
        new_state = replace(
            state, transplant_date=transplant_date, area_acres=area_acres,
            area_unit=area_unit, step=ProxyRegistrationStep.AWAITING_HAS_PHONE,
        )
        return new_state, OutboundMessage(lang.addfarmer_has_phone_prompt())

    if state.step == ProxyRegistrationStep.AWAITING_HAS_PHONE:
        if incoming.text and _is_yes(incoming.text):
            has_phone = True
        elif incoming.text and _is_no(incoming.text):
            has_phone = False
        else:
            return state, OutboundMessage(lang.addfarmer_has_phone_prompt())
        new_state = replace(state, has_phone=has_phone, step=ProxyRegistrationStep.AWAITING_CONFIRM)
        return new_state, _confirm_summary_message(new_state)

    if state.step == ProxyRegistrationStep.AWAITING_CONFIRM:
        # Confirmation happens via the addfarmer_confirm: callback tap,
        # not text -- a stray text message here just re-shows the same
        # summary and keyboard.
        return state, _confirm_summary_message(state)

    if state.step == ProxyRegistrationStep.CROP_CONFIRM_DECLINED:
        return state, OutboundMessage(lang.CROP_CONFIRM_DECLINED_MESSAGE)

    # COMPLETE/CANCELLED: nothing further expected in this chat.
    return state, OutboundMessage(lang.addfarmer_nothing_pending())


# Phase 4-style in-memory placeholder store, same limitation class as
# registration.py's and operator_enrollment.py's own -- does not survive
# a process restart. Keyed by operator_chat_id, not farmer chat_id.
_STATE_STORE: dict[int, ProxyRegistrationState] = {}


def get_pending_state(operator_chat_id: int) -> ProxyRegistrationState | None:
    return _STATE_STORE.get(operator_chat_id)


def save_state(state: ProxyRegistrationState) -> None:
    _STATE_STORE[state.operator_chat_id] = state


def clear_state(operator_chat_id: int) -> None:
    _STATE_STORE.pop(operator_chat_id, None)


def handle_addfarmer_command(operator_chat_id: int, storage: Storage) -> OutboundMessage:
    """/addfarmer entry point. webhook.py has already verified
    operator_chat_id == cluster.operator_chat_id (Decision 13/19) before
    calling this -- always starts a fresh flow, overwriting any
    in-progress one, since a deliberate re-invocation is the only way an
    operator can restart after a mistake (Decision 20's failure path 1)."""
    cluster_id = os.environ.get("HARVEST_CONVOY_CLUSTER_ID")
    cluster = storage.get_cluster(cluster_id) if cluster_id else None
    if cluster_id is None or cluster is None:
        logger.error(
            "handle_addfarmer_command: cluster not resolvable (cluster_id=%s) "
            "-- operator_chat_id=%s, nothing started",
            cluster_id, operator_chat_id,
        )
        return OutboundMessage(_lang_module("ta").addfarmer_farmer_name_prompt())
    state, outbound = start_proxy_registration(operator_chat_id, cluster_id, cluster.operator_language)
    save_state(state)
    return outbound


def handle_incoming(operator_chat_id: int, incoming: IncomingMessage, storage: Storage) -> OutboundMessage | None:
    """Stateful wrapper webhook.py calls for a text message from a chat
    with a pending /addfarmer flow. Returns None if nothing is pending
    -- caller falls through to ordinary registration/rollover routing."""
    state = get_pending_state(operator_chat_id)
    if state is None:
        return None
    new_state, outbound = advance_proxy_registration(state, incoming)
    save_state(new_state)
    return outbound


def _mint_proxy_ids(storage: Storage) -> tuple[str, str]:
    """farmer-proxy-{hex}/plot-proxy-{hex} -- a disjoint namespace from
    the ordinary farmer-{chat_id}/plot-{chat_id} scheme (ADR-013 Part 2
    Decision 14), since a proxy registration has no chat_id to derive
    from. Collision-checked the same defensive way
    generate_operator_code.py checks its own code space."""
    for _ in range(10):
        suffix = secrets.token_hex(3)
        farmer_id, plot_id = f"farmer-proxy-{suffix}", f"plot-proxy-{suffix}"
        if storage.get_farmer(farmer_id) is None and storage.get_plot(plot_id) is None:
            return farmer_id, plot_id
    raise RuntimeError("proxy_registration: could not mint a unique id after 10 attempts")


def persist_proxy_registration(state: ProxyRegistrationState, storage: Storage) -> tuple[Farmer, Plot] | None:
    """Called only on an authorized 'yes' tap at AWAITING_CONFIRM.
    telegram_chat_id is always None here regardless of the has_phone
    answer -- a proxy chat can never supply the farmer's own chat_id
    (ADR-013 Part 2 Decision 15/18). Never raises -- returns None on any
    write failure, logged loudly; caller degrades to an honest failure
    message rather than claiming success."""
    farmer_id, plot_id = _mint_proxy_ids(storage)
    now_iso = datetime.now(timezone.utc).isoformat()
    farmer = Farmer(
        farmer_id=farmer_id, name=state.farmer_name, cluster_id=state.cluster_id,
        telegram_chat_id=None, language=state.language, contact_note=state.contact_note,
    )
    plot = Plot(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id=state.cluster_id,
        lat=state.lat, lon=state.lon, crop=CROP, variety=VARIETY,
        transplant_date=state.transplant_date, area_acres=state.area_acres,
        area_unit=state.area_unit, village=state.village,
        registered_by=f"operator:{state.operator_chat_id}", registered_at=now_iso,
    )
    farmer_result = storage.put_farmer(farmer)
    plot_result = storage.put_plot(plot)
    if not farmer_result.success or not plot_result.success:
        logger.error(
            "persist_proxy_registration: failed for operator_chat_id=%s: "
            "farmer=%s plot=%s",
            state.operator_chat_id, farmer_result.error, plot_result.error,
        )
        return None
    return farmer, plot


# --- /linkfarmer: fully stateless, taps only -- every id the next step
# needs travels in callback_data, so there is no _STATE_STORE to lose. ---

def _plots_by_farmer_id(storage: Storage, cluster_id: str) -> dict[str, Plot]:
    return {p.farmer_id: p for p in storage.get_plots_for_cluster(cluster_id)}


def _is_unlinked_proxy_farmer(farmer: Farmer, plots_by_farmer_id: dict[str, Plot]) -> bool:
    if not farmer.farmer_id.startswith("farmer-proxy-") or farmer.telegram_chat_id is not None:
        return False
    plot = plots_by_farmer_id.get(farmer.farmer_id)
    return plot is not None and plot.retired_reason is None


def unlinked_proxy_farmers(storage: Storage, cluster_id: str) -> list[Farmer]:
    plots = _plots_by_farmer_id(storage, cluster_id)
    return [f for f in storage.get_farmers_for_cluster(cluster_id) if _is_unlinked_proxy_farmer(f, plots)]


def link_candidates(storage: Storage, cluster_id: str, exclude_farmer_id: str) -> list[Farmer]:
    """Every other farmer in the cluster that isn't itself a proxy
    record and isn't already retired away -- sorted most-recently-
    registered first (Plot.registered_at), since the intended match is
    almost always whoever just self-registered. No stronger filtering
    than that: there is no reliable automatic signal for "same person",
    which is exactly why this is a human's tap, not a computed match
    (ADR-013 Part 2 Decision 18)."""
    plots = _plots_by_farmer_id(storage, cluster_id)

    def _registered_at(farmer: Farmer) -> str:
        plot = plots.get(farmer.farmer_id)
        return plot.registered_at or "" if plot is not None else ""

    candidates = [
        f for f in storage.get_farmers_for_cluster(cluster_id)
        if not f.farmer_id.startswith("farmer-proxy-")
        and f.farmer_id != exclude_farmer_id
        and (plots.get(f.farmer_id) is None or plots.get(f.farmer_id).retired_reason is None)
    ]
    candidates.sort(key=_registered_at, reverse=True)
    return candidates


def _linkfarmer_proxy_keyboard(farmers: list[Farmer]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": f.name, "callback_data": f"linkfarmer_proxy:{f.farmer_id}"}]
            for f in farmers
        ]
    }


def _linkfarmer_match_keyboard(proxy_farmer_id: str, candidates: list[Farmer]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": f.name, "callback_data": f"linkfarmer_match:{proxy_farmer_id}:{f.farmer_id}"}]
            for f in candidates
        ]
    }


def build_linkfarmer_confirm_keyboard(proxy_farmer_id: str, candidate_farmer_id: str, language: str) -> dict:
    mod = _lang_module(language)
    return {
        "inline_keyboard": [[
            {
                "text": mod.CONFIRMATION_YES_LABEL,
                "callback_data": f"linkfarmer_confirm:{proxy_farmer_id}:{candidate_farmer_id}:yes",
            },
            {
                "text": mod.CONFIRMATION_NO_LABEL,
                "callback_data": f"linkfarmer_confirm:{proxy_farmer_id}:{candidate_farmer_id}:no",
            },
        ]]
    }


def handle_linkfarmer_command(operator_chat_id: int, storage: Storage) -> OutboundMessage:
    cluster_id = os.environ.get("HARVEST_CONVOY_CLUSTER_ID")
    cluster = storage.get_cluster(cluster_id) if cluster_id else None
    language = cluster.operator_language if cluster is not None else "ta"
    lang = _lang_module(language)
    if cluster_id is None or cluster is None:
        logger.error(
            "handle_linkfarmer_command: cluster not resolvable (cluster_id=%s) "
            "-- operator_chat_id=%s",
            cluster_id, operator_chat_id,
        )
        return OutboundMessage(lang.linkfarmer_nothing_to_link())
    candidates = unlinked_proxy_farmers(storage, cluster_id)
    if not candidates:
        return OutboundMessage(lang.linkfarmer_nothing_to_link())
    return OutboundMessage(lang.linkfarmer_pick_proxy_prompt(), reply_markup=_linkfarmer_proxy_keyboard(candidates))


def build_linkfarmer_match_message(storage: Storage, proxy_farmer_id: str, language: str) -> OutboundMessage:
    lang = _lang_module(language)
    proxy_farmer = storage.get_farmer(proxy_farmer_id)
    cluster_id = proxy_farmer.cluster_id if proxy_farmer is not None else None
    candidates = link_candidates(storage, cluster_id, proxy_farmer_id) if cluster_id else []
    if not candidates:
        return OutboundMessage(lang.linkfarmer_nothing_to_link())
    proxy_name = proxy_farmer.name if proxy_farmer is not None else ""
    return OutboundMessage(
        lang.linkfarmer_pick_match_prompt(proxy_name),
        reply_markup=_linkfarmer_match_keyboard(proxy_farmer_id, candidates),
    )


def apply_link(storage: Storage, proxy_farmer_id: str, candidate_farmer_id: str) -> bool:
    """The one, narrow, purpose-built mutation Decision 18 performs --
    not a general merge primitive. The proxy farmer_id stays canonical
    (it may already carry scheduling/ledger history); the candidate's
    own telegram_chat_id is cleared, not just copied, so at most one
    farmer in this cluster ever holds a given chat_id at a time --
    registration.find_farmer_by_chat_id's lookup stays unambiguous with
    no reference-following needed. The candidate's plot is retired, not
    deleted. No reconciliation of conflicting field values: the proxy
    record's plot details stay authoritative unconditionally."""
    proxy_farmer = storage.get_farmer(proxy_farmer_id)
    candidate_farmer = storage.get_farmer(candidate_farmer_id)
    if proxy_farmer is None or candidate_farmer is None or candidate_farmer.telegram_chat_id is None:
        logger.error(
            "apply_link: cannot link proxy=%s candidate=%s -- missing farmer "
            "or candidate has no chat_id",
            proxy_farmer_id, candidate_farmer_id,
        )
        return False
    candidate_plot = next(
        (p for p in storage.get_plots_for_cluster(candidate_farmer.cluster_id) if p.farmer_id == candidate_farmer_id),
        None,
    )
    if candidate_plot is None:
        logger.error("apply_link: no plot found for candidate farmer=%s", candidate_farmer_id)
        return False

    farmer_result = storage.put_farmer(replace(proxy_farmer, telegram_chat_id=candidate_farmer.telegram_chat_id))
    cleared_result = storage.put_farmer(replace(candidate_farmer, telegram_chat_id=None))
    plot_result = storage.put_plot(replace(candidate_plot, retired_reason=f"linked_to:{proxy_farmer_id}"))
    if not farmer_result.success or not cleared_result.success or not plot_result.success:
        logger.error(
            "apply_link: write failed proxy=%s candidate=%s farmer=%s cleared=%s plot=%s",
            proxy_farmer_id, candidate_farmer_id,
            farmer_result.error, cleared_result.error, plot_result.error,
        )
        return False
    return True
