"""`/help` -- ADR-014. A read-only status check for a registered farmer
or the cluster's operator, assembled entirely from stored records --
never a live weather/GDD recompute, never a model call. The one
deliberate exception to "every farmer-facing message is either the
agent speaking first or a bounded reply" (see the ADR's central
decision): one exact command string, one deterministic reply, no
keyboard, no follow-up, nothing left pending.
"""

from __future__ import annotations

import logging
from datetime import date

from harvest_convoy.models import Plot
from harvest_convoy.storage import Storage
from harvest_convoy.telegram import notify, registration
from harvest_convoy.telegram import messages_en, messages_ta

logger = logging.getLogger(__name__)

HELP_COMMAND = "/help"


def help_unregistered_text() -> str:
    """Bilingual -- we genuinely don't know this person's language yet,
    same reasoning as registration._bilingual_greeting_text()."""
    return f"{messages_en.help_unregistered()} / {messages_ta.help_unregistered()}"


def _active_plot_for(storage: Storage, cluster_id: str, farmer_id: str) -> Plot | None:
    """By construction (every registration path in this codebase mints
    exactly one farmer_id/plot_id pair, and ADR-013 Part 2's
    /linkfarmer clears the losing side's own telegram_chat_id the
    moment it retires that side's plot) a Farmer reachable by chat_id
    should have exactly one active plot. Handled defensively anyway:
    zero or more than one is logged, never silently guessed at or
    listed in full (see ADR-014 Decision 2)."""
    plots = [
        p for p in storage.get_plots_for_cluster(cluster_id)
        if p.farmer_id == farmer_id and p.retired_reason is None
    ]
    if not plots:
        return None
    if len(plots) > 1:
        logger.error(
            "help: farmer %s has %d active plots on record -- showing the first",
            farmer_id, len(plots),
        )
    return sorted(plots, key=lambda p: p.plot_id)[0]


def build_help_reply(
    storage: Storage, cluster_id: str | None, chat_id: int, season_id: str,
) -> str:
    if not cluster_id:
        logger.error(
            "HARVEST_CONVOY_CLUSTER_ID not set -- /help from chat_id=%s answered generically",
            chat_id,
        )
        return messages_ta.help_unavailable()
    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        logger.error(
            "HARVEST_CONVOY_CLUSTER_ID=%s not found in storage -- /help from chat_id=%s "
            "answered generically",
            cluster_id, chat_id,
        )
        return messages_ta.help_unavailable()

    farmer = registration.find_farmer_by_chat_id(storage, cluster_id, chat_id)
    is_operator = cluster.operator_chat_id == chat_id

    if farmer is None:
        if is_operator:
            mod = notify._lang_module(cluster.operator_language)
            return mod.help_operator_reply(cluster.name)
        return help_unregistered_text()

    mod = notify._lang_module(farmer.language)
    plot = _active_plot_for(storage, cluster_id, farmer.farmer_id)
    if plot is None:
        logger.error("help: farmer %s has no active plot on record", farmer.farmer_id)
        return mod.help_no_plot_found()

    notice = storage.get_advance_notice_record(plot.plot_id, season_id)
    projected_text = (
        mod.format_date(date.fromisoformat(notice.projected_maturity_date))
        if notice is not None else None
    )
    reply = mod.help_farmer_reply(
        village=plot.village,
        area_text=mod.format_area(plot.area_acres, plot.area_unit),
        transplant_date_text=mod.format_date(plot.transplant_date),
        projected_ready_text=projected_text,
    )
    if is_operator:
        # Farmer view always wins (ADR-014 Decision 2, reversed on
        # review) -- an operator who also farms must still see his own
        # plot; this one line still surfaces his operator access.
        reply += "\n" + mod.help_operator_addendum(cluster.name)
    return reply
