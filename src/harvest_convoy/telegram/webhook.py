"""Inbound update handling: registration messages, and the escalation
inline-keyboard callback. See docs/adr/ADR-004-telegram.md Decision 4 for
the idempotency design -- a second tap on an already-resolved escalation
must not double-notify two farmers about the same conflict.
"""

from __future__ import annotations

import logging
from typing import Callable

from harvest_convoy.models import Farmer, Plot
from harvest_convoy.telegram import notify, registration
from harvest_convoy.telegram.client import TelegramClient
from harvest_convoy.telegram.registration import IncomingMessage

logger = logging.getLogger(__name__)

FarmerPlotLookup = Callable[[str], tuple[Farmer, Plot] | None]

# Phase 4 placeholder store -- see ADR-004 Decision 4. Replaced by
# DynamoDB in Phase 5; does not survive a process restart.
_RESOLVED_ESCALATIONS: set[str] = set()


def _escalation_key(cluster_id: str, plot_a_id: str, plot_b_id: str) -> str:
    return f"{cluster_id}:{plot_a_id}:{plot_b_id}"


def _default_lookup(plot_id: str) -> tuple[Farmer, Plot] | None:
    logger.error(
        "no farmer/plot lookup wired for webhook.py (Phase 5 not built yet); "
        "cannot resolve plot_id=%s",
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


def handle_callback_query(
    client: TelegramClient,
    callback_query: dict,
    lookup_farmer_for_plot: FarmerPlotLookup = _default_lookup,
) -> None:
    callback_query_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    parsed = parse_callback_data(data)

    if parsed is None:
        client.answer_callback_query(
            callback_query_id, "Unrecognized action.", show_alert=True
        )
        return

    cluster_id, plot_a_id, plot_b_id, chosen_plot_id = parsed
    key = _escalation_key(cluster_id, plot_a_id, plot_b_id)

    if key in _RESOLVED_ESCALATIONS:
        client.answer_callback_query(
            callback_query_id, "This conflict was already resolved.", show_alert=True
        )
        return

    _RESOLVED_ESCALATIONS.add(key)
    client.answer_callback_query(
        callback_query_id, f"Machine assigned to {chosen_plot_id}."
    )

    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)

    loser_plot_id = plot_b_id if chosen_plot_id == plot_a_id else plot_a_id
    for plot_id, won in ((chosen_plot_id, True), (loser_plot_id, False)):
        result = lookup_farmer_for_plot(plot_id)
        if result is None:
            logger.error(
                "escalation %s resolved but no farmer/plot found for %s -- "
                "that farmer was not notified",
                key, plot_id,
            )
            continue
        farmer, plot = result
        notify.send_escalation_resolved(client, farmer, plot, won)


def parse_incoming_message(message: dict) -> IncomingMessage:
    text = message.get("text")
    location = None
    loc = message.get("location")
    if loc:
        location = (loc["latitude"], loc["longitude"])
    return IncomingMessage(text=text, location=location)


def handle_update(
    client: TelegramClient,
    update: dict,
    lookup_farmer_for_plot: FarmerPlotLookup = _default_lookup,
) -> None:
    """Top-level entrypoint for one Telegram Update payload."""
    if "callback_query" in update:
        handle_callback_query(client, update["callback_query"], lookup_farmer_for_plot)
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
    reply_text = registration.handle_incoming(chat_id, incoming)
    client.send_message(chat_id, reply_text)
