"""Operator self-enrollment -- ADR-012 Part 2. Replaces the manual
"set Cluster.operator_chat_id by hand in a seed script" step with a
one-time code (generate_operator_code.py) a real provisioner hands to a
real operator out-of-band. Structurally a much narrower cousin of
registration.py: one command starts the flow (`/operator <code>`),
everything after that is a button tap, never new free text -- see
webhook.py's operator_lang:/operator_replace: callback handlers for the
two tap steps this module's state supports.

Binding to the initiating chat_id (Decision 1, ADR-012): callback_data
carries the code and cluster_id, but a leaked or forwarded callback_data
string must not be enough to complete enrollment on its own -- Telegram's
protection against a forwarded message carrying a live, tappable
keyboard is not something this project verified from documentation, so
per explicit instruction it is NOT assumed. Every button tap in this
flow is checked against _STATE_STORE, keyed by the chat_id that sent the
original /operator command -- a tap from any other chat_id is refused
even if the callback_data itself is well-formed and references a
genuinely valid code. Same disclosed in-memory-only limitation as
rollover.py's _STATE_STORE: does not survive a process restart between
the command and the button tap. The code itself is still unused in that
case, so the fix is just re-sending /operator <code> -- a low-cost
degrade, not data loss or a security gap.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from harvest_convoy.models import Cluster
from harvest_convoy.storage import Storage
from harvest_convoy.storage.interface import OperatorAuditEvent, OperatorEnrollmentCode
from harvest_convoy.telegram import messages_en, messages_ta
from harvest_convoy.telegram.registration import OutboundMessage

logger = logging.getLogger(__name__)

_LANGUAGE_MODULES = {"ta": messages_ta, "en": messages_en}


def _lang_module(language: str):
    return _LANGUAGE_MODULES.get(language, messages_ta)


COMMAND = "/operator"

# Unsourced judgment call, same status as DRYING_WINDOW_DAYS/
# ADVANCE_NOTICE_DAYS_BEFORE_MATURITY elsewhere in this project -- long
# enough that a provisioner handing a code to an operator by phone/paper
# has real slack, short enough that a leaked, never-used code doesn't
# stay live indefinitely.
OPERATOR_ENROLLMENT_CODE_EXPIRY_DAYS = 14


def generate_code() -> str:
    """8 uppercase hex characters -- cryptographically random
    (secrets, not random), no ambiguous-character alphabet concerns
    (0-9a-f has no I/l/O confusion) and short enough to read over a
    phone call or copy off a paper slip."""
    return secrets.token_hex(4).upper()


@dataclass(frozen=True)
class PendingEnrollment:
    chat_id: int
    cluster_id: str
    code: str
    language: str | None = None


# Placeholder store -- see module docstring.
_STATE_STORE: dict[int, PendingEnrollment] = {}


def get_pending_state(chat_id: int) -> PendingEnrollment | None:
    return _STATE_STORE.get(chat_id)


def _start(chat_id: int, cluster_id: str, code: str) -> None:
    _STATE_STORE[chat_id] = PendingEnrollment(chat_id=chat_id, cluster_id=cluster_id, code=code)


def set_language(chat_id: int, language: str) -> None:
    state = _STATE_STORE.get(chat_id)
    if state is not None:
        _STATE_STORE[chat_id] = replace(state, language=language)


def clear_state(chat_id: int) -> None:
    _STATE_STORE.pop(chat_id, None)


def matches_pending(chat_id: int, cluster_id: str, code: str) -> bool:
    """The binding check every operator_lang:/operator_replace: callback
    must pass before doing anything else: the tapping chat_id must have
    its own pending enrollment for this exact cluster_id/code -- not
    just a well-formed callback_data string. See module docstring,
    Decision 1."""
    state = _STATE_STORE.get(chat_id)
    return state is not None and state.cluster_id == cluster_id and state.code == code


def operator_as_of(storage: Storage, cluster_id: str, at_iso: str) -> OperatorAuditEvent | None:
    """The most recent enrollment/replacement event in effect at
    `at_iso` -- the latest event with occurred_at <= at_iso, or None if
    no event has ever been recorded for this cluster by that point
    (predates ADR-012, or the operator was set by hand in a seed script
    and never enrolled through this flow at all). See ADR-012 Decision
    2: used by explain_decision.py to show which operator was active
    when a decision involved one (an escalation resolution, or a
    breakdown recompute) -- not shown for decisions an operator had no
    part in."""
    events = storage.get_operator_audit_events_for_cluster(cluster_id)
    eligible = [e for e in events if e.occurred_at <= at_iso]
    if not eligible:
        return None
    return max(eligible, key=lambda e: e.occurred_at)


def parse_operator_command(text: str | None) -> str | None:
    """Returns the normalized (uppercase) code from "/operator CODE", or
    None if this isn't a well-formed /operator command at all -- a
    malformed command (no code, or anything not starting with the
    command word) is not distinguished further; the caller sends one
    usage message either way."""
    if not text:
        return None
    stripped = text.strip()
    if not stripped.lower().startswith(COMMAND):
        return None
    rest = stripped[len(COMMAND):].strip()
    if not rest:
        return None
    return rest.split()[0].strip().upper() or None


def validate_code(
    storage: Storage, code: str, *, now_iso: str | None = None,
) -> tuple[OperatorEnrollmentCode | None, str]:
    """Returns (record, status). status is one of "invalid" (no such
    code -- record is None), "used", "expired", or "valid". record is
    the stored record whenever one exists, even for "used"/"expired",
    so a caller that wants to log the specific reason can."""
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    record = storage.get_operator_enrollment_code(code)
    if record is None:
        return None, "invalid"
    if record.used_at is not None:
        return record, "used"
    if record.expires_at < now_iso:
        return record, "expired"
    return record, "valid"


def _bilingual(en: str, ta: str) -> str:
    return f"{en} / {ta}"


def _bilingual_usage_text() -> str:
    return _bilingual(messages_en.operator_command_usage(), messages_ta.operator_command_usage())


def _bilingual_invalid_code_text() -> str:
    return _bilingual(messages_en.operator_invalid_code(), messages_ta.operator_invalid_code())


def _bilingual_expired_code_text() -> str:
    return _bilingual(messages_en.operator_expired_code(), messages_ta.operator_expired_code())


def _bilingual_language_prompt_text() -> str:
    return _bilingual(
        messages_en.operator_language_prompt(), messages_ta.operator_language_prompt()
    )


def build_operator_language_keyboard(cluster_id: str, code: str) -> dict:
    """Same two button labels as registration.py's LANGUAGE_KEYBOARD --
    the language names themselves, not looked up via _lang_module, since
    this keyboard IS the language choice. Distinct callback_data prefix
    (operator_lang:, not lang:) so an operator-enrollment tap can never
    be routed into a farmer's own registration state."""
    return {
        "inline_keyboard": [
            [
                {"text": "தமிழ்", "callback_data": f"operator_lang:{cluster_id}:{code}:ta"},
                {"text": "English", "callback_data": f"operator_lang:{cluster_id}:{code}:en"},
            ]
        ]
    }


def build_operator_replace_keyboard(cluster_id: str, code: str, language: str) -> dict:
    mod = _lang_module(language)

    def callback_data(answer: str) -> str:
        return f"operator_replace:{cluster_id}:{code}:{language}:{answer}"

    return {
        "inline_keyboard": [
            [
                {"text": mod.CONFIRMATION_YES_LABEL, "callback_data": callback_data("yes")},
                {"text": mod.CONFIRMATION_NO_LABEL, "callback_data": callback_data("no")},
            ]
        ]
    }


def handle_operator_command(chat_id: int, text: str | None, storage: Storage) -> OutboundMessage:
    """Entry point for a "/operator <code>" message -- webhook.py's
    handle_update checks for this before falling through to
    registration.handle_incoming, so it's never parsed as farmer
    registration text."""
    code = parse_operator_command(text)
    if code is None:
        return OutboundMessage(_bilingual_usage_text())

    record, status = validate_code(storage, code)
    if status in ("invalid", "used"):
        logger.info(
            "operator enrollment: code=%s status=%s chat_id=%s -- refused",
            code, status, chat_id,
        )
        return OutboundMessage(_bilingual_invalid_code_text())
    if status == "expired":
        logger.info(
            "operator enrollment: code=%s status=expired chat_id=%s -- refused",
            code, chat_id,
        )
        return OutboundMessage(_bilingual_expired_code_text())

    cluster = storage.get_cluster(record.cluster_id)
    if cluster is None:
        logger.error(
            "operator enrollment: code=%s references cluster_id=%s which no "
            "longer exists -- refusing (data-consistency problem, not a user error)",
            code, record.cluster_id,
        )
        return OutboundMessage(_bilingual_invalid_code_text())

    _start(chat_id, record.cluster_id, code)
    return OutboundMessage(
        _bilingual_language_prompt_text(),
        reply_markup=build_operator_language_keyboard(record.cluster_id, code),
    )


def complete_enrollment(
    storage: Storage, cluster: Cluster, code: str, new_chat_id: int, language: str,
    *, previous_chat_id: int | None,
) -> OperatorAuditEvent | None:
    """Writes the three things a successful enrollment or replacement
    touches: Cluster.operator_chat_id/operator_language, the code's
    used_at/used_by_chat_id, and an OperatorAuditEvent (ADR-012 Decision
    2) -- always all three together, so the audit trail can never
    silently fall out of sync with what Cluster actually says. Returns
    the event on success, None if the cluster write failed (logged,
    caller degrades to a generic failure message rather than claiming
    success). Pure logic, no Telegram I/O -- webhook.py's callback
    handlers call this, then send the confirmation themselves.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    cluster_result = storage.put_cluster(
        replace(cluster, operator_chat_id=new_chat_id, operator_language=language)
    )
    if not cluster_result.success:
        logger.error(
            "operator enrollment: failed to update cluster=%s: %s",
            cluster.cluster_id, cluster_result.error,
        )
        return None

    record = storage.get_operator_enrollment_code(code)
    if record is not None:
        code_result = storage.put_operator_enrollment_code(
            replace(record, used_at=now_iso, used_by_chat_id=new_chat_id)
        )
        if not code_result.success:
            logger.error(
                "operator enrollment: failed to mark code=%s used: %s -- "
                "cluster was already updated, so a second successful tap "
                "with the same code could re-run this (idempotent overwrite, "
                "not a double-enrollment of a different chat_id unless that "
                "second tap genuinely comes from a different chat)",
                code, code_result.error,
            )

    event_type = "replaced" if previous_chat_id is not None else "enrolled"
    event = OperatorAuditEvent(
        cluster_id=cluster.cluster_id, event_type=event_type, occurred_at=now_iso,
        code_used=code, new_operator_chat_id=new_chat_id,
        previous_operator_chat_id=previous_chat_id,
    )
    audit_result = storage.put_operator_audit_event(event)
    if not audit_result.success:
        logger.error(
            "OPERATOR AUDIT EVENT WRITE FAILED: cluster=%s event_type=%s "
            "new_chat_id=%s code=%s: %s -- the enrollment itself still "
            "succeeded (Cluster.operator_chat_id is updated); only the "
            "audit trail is incomplete for this change",
            cluster.cluster_id, event_type, new_chat_id, code, audit_result.error,
        )

    if event_type == "replaced":
        logger.warning(
            "OPERATOR REPLACED: cluster=%s old_chat_id=%s new_chat_id=%s "
            "code=%s at=%s",
            cluster.cluster_id, previous_chat_id, new_chat_id, code, now_iso,
        )
    else:
        logger.info(
            "OPERATOR ENROLLED: cluster=%s chat_id=%s code=%s at=%s",
            cluster.cluster_id, new_chat_id, code, now_iso,
        )

    return event
