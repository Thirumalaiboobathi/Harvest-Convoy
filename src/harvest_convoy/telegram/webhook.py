"""Inbound update handling: registration messages, and the escalation
inline-keyboard callback. See docs/adr/ADR-004-telegram.md Decision 4 for
the idempotency design -- a second tap on an already-resolved escalation
must not double-notify two farmers about the same conflict.

Keeps the full EscalationPayload (not just a resolved/not-resolved flag)
so the resolution message to the losing farmer can state a real, specific
reason -- "they were bumped last season" or "their grain has been
standing longer" -- instead of vague "a closer conflict" copy.

Phase 5 (ADR-005 Decision 4): resolution now records a real fairness
ledger entry. The storage layer's conditional write
(Storage.put_ledger_entry) is the actual idempotency boundary, not just
the in-memory _RESOLVED_ESCALATIONS set -- that set is a fast local
cache, but a second resolution attempt across a process restart (where
the set is empty again) still can't double-record, because the durable
ledger write for the loser is attempted first and fails cleanly if it
already exists.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from harvest_convoy.agents.contracts import EscalationPayload
from harvest_convoy.models import Farmer, Plot
from harvest_convoy.storage import Storage
from harvest_convoy.storage.fairness import record_bump
from harvest_convoy.storage.interface import HarvestConfirmation
from harvest_convoy.telegram import notify, registration
from harvest_convoy.telegram.client import TelegramClient
from harvest_convoy.telegram.registration import IncomingMessage

logger = logging.getLogger(__name__)

FarmerPlotLookup = Callable[[str], tuple[Farmer, Plot] | None]

# DERIVED, placeholder: days_bumped recorded per lost escalation. Refining
# this to actual calendar days lost (vs. a flat 1) is a reasonable future
# improvement, not blocking this phase. See ADR-005 Decision 4.
DEFAULT_DAYS_BUMPED = 1

# Phase 4 placeholder stores -- see ADR-004 Decision 4. The fast local
# cache; the real idempotency guarantee is now the storage layer's
# conditional write (see module docstring).
_PENDING_ESCALATIONS: dict[str, EscalationPayload] = {}
_RESOLVED_ESCALATIONS: set[str] = set()


def _escalation_key(cluster_id: str, plot_a_id: str, plot_b_id: str) -> str:
    return f"{cluster_id}:{plot_a_id}:{plot_b_id}"


def register_escalation(escalation: EscalationPayload) -> None:
    """Call this after sending an escalation message, so a later tap can
    look up its claims to build a specific resolution reason."""
    key = _escalation_key(
        escalation.cluster_id, escalation.plot_a_id, escalation.plot_b_id
    )
    _PENDING_ESCALATIONS[key] = escalation


def _default_lookup(plot_id: str) -> tuple[Farmer, Plot] | None:
    logger.error(
        "no farmer/plot lookup wired for webhook.py; cannot resolve plot_id=%s",
        plot_id,
    )
    return None


def parse_callback_data(data: str) -> tuple[str, str, str, str] | None:
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != "resolve":
        return None
    _, cluster_id, plot_a_id, plot_b_id, chosen_plot_id = parts
    if chosen_plot_id not in (plot_a_id, plot_b_id):
        return None
    return cluster_id, plot_a_id, plot_b_id, chosen_plot_id


def parse_confirmation_callback_data(data: str) -> tuple[str, str, bool] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "confirm":
        return None
    _, plot_id, season_id, answer = parts
    if answer not in ("yes", "no"):
        return None
    return plot_id, season_id, answer == "yes"


def handle_confirmation_callback(
    client: TelegramClient,
    callback_query: dict,
    storage: Storage,
) -> None:
    """ADR-009 Part 2: the farmer's yes/no tap on the evening "did the
    machine come?" prompt. One message, two taps, no follow-up -- this
    function's only job is to record the answer and, on "no", run the
    reversal (return the plot to the schedulable pool, credit the
    fairness ledger). "Yes" corroborates the harvested state Part 1.5
    already set -- nothing else happens.

    Idempotent by construction, not by a special case: put_harvest_
    confirmation is an overwrite (a duplicate tap just re-records the
    same answer), clear_plot_harvest is a no-op on an already-cleared
    plot, and record_bump's existing (farmer_id, season_id) uniqueness
    (see storage/fairness.py) silently prevents a second ledger credit
    -- the same guarantee ADR-005's escalation-resolution path already
    relies on. A late reply (after the confirmation window has closed)
    is processed exactly the same way as an on-time one: nothing was
    written on silence, so there's nothing to reconcile against.
    """
    callback_query_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    parsed = parse_confirmation_callback_data(data)

    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(),
            show_alert=True,
        )
        return

    plot_id, season_id, answer = parsed
    confirmation = storage.get_harvest_confirmation(plot_id, season_id)
    if confirmation is None:
        # No record for this exact (plot_id, season_id) key -- a stale or
        # forged callback. Defaults to Tamil: no farmer is resolvable yet.
        logger.warning(
            "confirmation callback for unknown plot=%s season=%s -- no "
            "record, nothing to update",
            plot_id, season_id,
        )
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").confirmation_not_found(),
            show_alert=True,
        )
        return

    farmer = storage.get_farmer(confirmation.farmer_id)
    language = farmer.language if farmer is not None else "ta"
    mod = notify._lang_module(language)

    updated = replace(
        confirmation, confirmed=answer, confirmed_at=datetime.now(timezone.utc).isoformat()
    )
    storage.put_harvest_confirmation(updated)

    if not answer:
        # The reversal hook: return the plot to the schedulable pool AND
        # credit the fairness ledger -- the farmer was effectively bumped
        # regardless of what the system decided. Both calls are safe to
        # repeat (see docstring above).
        storage.clear_plot_harvest(
            confirmation.plot_id, confirmation.cluster_id, confirmation.season_id
        )
        bump_result = record_bump(
            confirmation.farmer_id, confirmation.season_id,
            days_bumped=DEFAULT_DAYS_BUMPED, outcome="harvest_no_show",
            cluster_id=confirmation.cluster_id, plot_id=confirmation.plot_id,
            opponent_plot_id="",  # not a lost negotiation -- no opponent plot
            storage=storage,
        )
        if not bump_result.success:
            logger.info(
                "confirmation %s/%s: ledger write already exists (%s) -- "
                "duplicate 'no' tap, not double-crediting",
                plot_id, season_id, bump_result.error,
            )

    client.answer_callback_query(callback_query_id, mod.confirmation_thanks())

    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)


def handle_callback_query(
    client: TelegramClient,
    callback_query: dict,
    storage: Storage,
    season_id: str,
    lookup_farmer_for_plot: FarmerPlotLookup = _default_lookup,
) -> None:
    callback_query_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    parsed = parse_callback_data(data)

    if parsed is None:
        # No cluster_id parses out of malformed callback data, so this
        # can't be dispatched by Cluster.operator_language -- defaults to
        # Tamil, same as the product-wide default everywhere else a
        # language isn't yet resolvable.
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(),
            show_alert=True,
        )
        return

    cluster_id, plot_a_id, plot_b_id, chosen_plot_id = parsed
    key = _escalation_key(cluster_id, plot_a_id, plot_b_id)
    cluster = storage.get_cluster(cluster_id)
    operator_language = cluster.operator_language if cluster is not None else "ta"
    mod = notify._lang_module(operator_language)

    if key in _RESOLVED_ESCALATIONS:
        client.answer_callback_query(
            callback_query_id, mod.escalation_already_resolved(), show_alert=True
        )
        return

    loser_plot_id = plot_b_id if chosen_plot_id == plot_a_id else plot_a_id
    winner_result = lookup_farmer_for_plot(chosen_plot_id)
    loser_result = lookup_farmer_for_plot(loser_plot_id)

    # The durable idempotency boundary: attempt the loser's ledger write
    # before doing anything else. If it already exists, this escalation
    # was already resolved by an earlier (possibly pre-restart) attempt --
    # treat it exactly like the in-memory-cache hit above, and do not
    # send a second round of notifications.
    if loser_result is not None:
        loser_farmer, _ = loser_result
        bump_result = record_bump(
            loser_farmer.farmer_id, season_id,
            days_bumped=DEFAULT_DAYS_BUMPED, outcome="bumped",
            cluster_id=cluster_id, plot_id=loser_plot_id,
            opponent_plot_id=chosen_plot_id, storage=storage,
        )
        if not bump_result.success:
            logger.info(
                "escalation %s: ledger write already exists (%s) -- "
                "treating as already resolved, no new notifications sent",
                key, bump_result.error,
            )
            _RESOLVED_ESCALATIONS.add(key)
            client.answer_callback_query(
                callback_query_id, mod.escalation_already_resolved(), show_alert=True
            )
            return
    else:
        logger.error(
            "escalation %s: no farmer/plot found for loser %s -- "
            "cannot record a fairness ledger entry for them",
            key, loser_plot_id,
        )

    if winner_result is not None:
        winner_farmer, _ = winner_result
        winner_bump = record_bump(
            winner_farmer.farmer_id, season_id,
            days_bumped=0, outcome="won",
            cluster_id=cluster_id, plot_id=chosen_plot_id,
            opponent_plot_id=loser_plot_id, storage=storage,
        )
        if not winner_bump.success:
            # Not fatal -- the loser's record is the one that matters for
            # fairness weighting; log and continue to notifications.
            logger.warning(
                "escalation %s: winner ledger write failed (%s) -- "
                "continuing, this is not the idempotency-critical write",
                key, winner_bump.error,
            )

    _RESOLVED_ESCALATIONS.add(key)

    winner_name = winner_result[0].name if winner_result else mod.DEFAULT_WINNER_LABEL
    client.answer_callback_query(
        callback_query_id, mod.escalation_resolved_assigned(winner_name)
    )

    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)

    escalation = _PENDING_ESCALATIONS.pop(key, None)
    reason = None
    if escalation is not None:
        if chosen_plot_id == escalation.plot_a_id:
            winner_claim, loser_claim = escalation.claim_a, escalation.claim_b
        else:
            winner_claim, loser_claim = escalation.claim_b, escalation.claim_a
        loser_language = loser_result[0].language if loser_result is not None else "ta"
        reason = notify.resolution_reason_text(loser_language, winner_claim, loser_claim)
    else:
        logger.warning(
            "escalation %s resolved but no pending payload found (process "
            "restarted since it was sent?) -- resolution message to the "
            "losing farmer will omit the specific reason",
            key,
        )

    if winner_result is not None:
        farmer, plot = winner_result
        notify.send_escalation_resolved(client, farmer, plot, won=True)
    else:
        logger.error(
            "escalation %s resolved but no farmer/plot found for winner "
            "%s -- that farmer was not notified",
            key, chosen_plot_id,
        )

    if loser_result is not None:
        farmer, plot = loser_result
        notify.send_escalation_resolved(
            client, farmer, plot, won=False,
            other_farmer_name=winner_name if winner_result else None,
            reason=reason,
        )
    else:
        logger.error(
            "escalation %s resolved but no farmer/plot found for loser "
            "%s -- that farmer was not notified",
            key, loser_plot_id,
        )


def parse_incoming_message(message: dict) -> IncomingMessage:
    text = message.get("text")
    location = None
    loc = message.get("location")
    if loc:
        location = (loc["latitude"], loc["longitude"])
    # Telegram's own User object on every message -- free, no extra
    # farmer interaction. first_name over username: it's the display
    # name a real person actually recognizes; username is often unset or
    # a handle, not a name. See ADR-009 Part 3.
    sender = message.get("from") or {}
    sender_name = sender.get("first_name") or sender.get("username")
    return IncomingMessage(text=text, location=location, sender_name=sender_name)


def handle_update(
    client: TelegramClient,
    update: dict,
    storage: Storage,
    season_id: str,
    lookup_farmer_for_plot: FarmerPlotLookup = _default_lookup,
) -> None:
    """Top-level entrypoint for one Telegram Update payload. `storage` and
    `season_id` are required even though only the escalation-callback path
    uses them -- keeps the signature uniform rather than branching on
    which fields are needed for which update type.

    Three distinct callback_query shapes: a "lang:ta"/"lang:en"
    registration-language tap routes to
    registration.handle_language_callback (ADR-008 Decision 7); a
    "confirm:{plot_id}:{season_id}:{yes|no}" tap routes to
    handle_confirmation_callback (ADR-009 Part 2); anything else goes
    through the existing handle_callback_query escalation flow.
    """
    if "callback_query" in update:
        callback_query = update["callback_query"]
        data = callback_query.get("data", "")
        if data.startswith("lang:"):
            registration.handle_language_callback(client, callback_query)
            return
        if data.startswith("confirm:"):
            handle_confirmation_callback(client, callback_query, storage)
            return
        handle_callback_query(
            client, callback_query, storage, season_id, lookup_farmer_for_plot
        )
        return

    message = update.get("message")
    if not message:
        logger.warning("update with neither message nor callback_query: %s", update)
        return

    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        logger.warning("message with no chat id: %s", message)
        return

    incoming = parse_incoming_message(message)
    outbound = registration.handle_incoming(chat_id, incoming, storage)
    client.send_message(chat_id, outbound.text, reply_markup=outbound.reply_markup)
