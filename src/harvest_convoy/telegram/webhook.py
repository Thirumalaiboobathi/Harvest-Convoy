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
import os
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Callable

from harvest_convoy.agents.contracts import EscalationPayload
from harvest_convoy.models import Farmer, Plot
from harvest_convoy.storage import Storage
from harvest_convoy.storage.fairness import record_bump
from harvest_convoy.storage.interface import HarvestConfirmation, RouteOverride
from harvest_convoy.telegram import (
    farmer_why,
    notify,
    operator_enrollment,
    proxy_registration,
    registration,
    rollover,
)
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


def _resolve_decision_record(
    storage: Storage, plot_id: str, season_id: str, decision_date: str, resolution: str,
) -> None:
    """Updates the DecisionRecord the coordinator wrote at trigger time
    (resolution="escalated", resolved_at=None) to its final, human-decided
    outcome. fairness_decisive is left None on the record -- a human tap
    is not a score comparison, and recording one as if it were would
    misattribute a human's judgment to the algorithm. See ADR-010 Part 0.5
    Decision D. Never blocks or crashes the resolution: a missing or
    failed-to-update record is logged, not raised -- the two farmers still
    get notified either way.
    """
    record = storage.get_decision_record(plot_id, season_id, decision_date)
    if record is None:
        logger.warning(
            "escalation resolution: no DecisionRecord found for plot=%s "
            "season=%s decision_date=%s -- cannot update it with the final "
            "resolution (the trigger-time write may have failed, or this "
            "escalation predates ADR-010 Part 0.5)",
            plot_id, season_id, decision_date,
        )
        return
    updated = replace(
        record, resolution=resolution, resolved_at=datetime.now(timezone.utc).isoformat()
    )
    result = storage.put_decision_record(updated)
    if not result.success:
        logger.error(
            "DECISION RECORD UPDATE FAILED: plot=%s season=%s decision_date=%s: %s",
            plot_id, season_id, decision_date, result.error,
        )


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

    if not _is_farmer(callback_query, farmer):
        # ADR-012: a confirmation reversal both frees the plot for
        # rescheduling and credits the fairness ledger -- a stranger
        # tapping this on another farmer's behalf could manufacture a
        # bump for them, or falsely corroborate a harvest that never
        # happened. Checked before any write below.
        logger.warning(
            "confirmation callback for plot=%s season=%s from a chat_id "
            "that doesn't match the registered farmer -- refused",
            plot_id, season_id,
        )
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    updated = replace(
        confirmation, confirmed=answer, confirmed_at=datetime.now(timezone.utc).isoformat()
    )
    storage.put_harvest_confirmation(updated)

    if confirmation.cancelled:
        # ADR-011 Part 2, Decision 9: this dispatch was already
        # invalidated by a machine breakdown before this reply arrived
        # (the race case named explicitly in the ADR -- confirmations
        # already sent before the breakdown was reported). The truth
        # (confirmed/confirmed_at above) still gets recorded, but the
        # reversal-and-ledger-credit branch below must NEVER fire here:
        # a breakdown already caused a BreakdownDisplacement record, not
        # a LedgerEntry, and a truthful "no" tap must not reach
        # record_bump through this second door.
        logger.info(
            "confirmation %s/%s: reply received against a cancelled "
            "confirmation (machine breakdown) -- recorded, not credited "
            "to the fairness ledger",
            plot_id, season_id,
        )
    elif not answer:
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


def parse_rollover_callback_data(data: str) -> tuple[str, str, bool] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "rollover":
        return None
    _, plot_id, new_season_id, answer = parts
    if answer not in ("yes", "no"):
        return None
    return plot_id, new_season_id, answer == "yes"


def handle_rollover_callback(
    client: TelegramClient,
    callback_query: dict,
    storage: Storage,
) -> None:
    """ADR-011 Part 1: the farmer's yes/no tap on the season-rollover
    prompt. "No" is terminal here -- recorded and done. "Yes" does not
    itself set replied=True; it only opens a short free-text exchange
    (telegram/rollover.py) for the one thing still missing, the new
    season's transplant date. Participation is only recorded once that
    date actually arrives -- a "yes" tap with no follow-up date leaves
    the plot correctly excluded (Decision 2's rule), rather than
    including it with a stale transplant_date from last season.
    """
    callback_query_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    parsed = parse_rollover_callback_data(data)

    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(),
            show_alert=True,
        )
        return

    plot_id, new_season_id, answer = parsed
    prompt = storage.get_season_rollover_prompt(plot_id, new_season_id)
    if prompt is None:
        logger.warning(
            "rollover callback for unknown plot=%s season=%s -- no record, "
            "nothing to update",
            plot_id, new_season_id,
        )
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").confirmation_not_found(),
            show_alert=True,
        )
        return

    farmer = storage.get_farmer(prompt.farmer_id)
    language = farmer.language if farmer is not None else "ta"
    mod = notify._lang_module(language)

    if not _is_farmer(callback_query, farmer):
        # ADR-012: without this, a "no" tap from anyone excludes this
        # farmer from next season's scheduling pool, and a "yes" tap
        # from anyone starts THEM (not the actual farmer) receiving the
        # follow-up date prompt and being able to set this farmer's new
        # transplant_date -- both are the same class of bug as an
        # unauthorized escalation resolution, just farmer-owned instead
        # of operator-owned state.
        logger.warning(
            "rollover callback for plot=%s season=%s from a chat_id that "
            "doesn't match the registered farmer -- refused",
            plot_id, new_season_id,
        )
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")

    if not answer:
        updated = replace(
            prompt, replied=False, replied_at=datetime.now(timezone.utc).isoformat()
        )
        storage.put_season_rollover_prompt(updated)
        client.answer_callback_query(callback_query_id, mod.season_rollover_declined_ack())
        if chat_id is not None and message_id is not None:
            client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
        return

    # "Yes" -- ask for the date via a real chat message (a toast alone
    # can't be replied to), and remember which plot/season this chat_id
    # is now mid-reply for.
    tapper_chat_id = (callback_query.get("from") or {}).get("id")
    if tapper_chat_id is not None:
        rollover.start_awaiting_date(tapper_chat_id, plot_id, new_season_id, language)
    client.answer_callback_query(callback_query_id, mod.season_rollover_date_prompt())
    if farmer is not None and farmer.telegram_chat_id is not None:
        client.send_message(farmer.telegram_chat_id, mod.season_rollover_date_prompt())
    if chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)


def _is_operator(callback_query: dict, cluster) -> bool:
    """The tapping user's own Telegram identity, not the chat the
    message lives in -- see ADR-011 Part 2, Decision 6. Only the
    operator's own tap on a machine-status action is honored."""
    tapper_id = (callback_query.get("from") or {}).get("id")
    return cluster is not None and tapper_id is not None and tapper_id == cluster.operator_chat_id


def _is_farmer(callback_query: dict, farmer) -> bool:
    """Same discipline as _is_operator, extended to callbacks that act on
    behalf of a specific farmer (confirmation, rollover) -- ADR-012. The
    tapping user's own Telegram identity must match the farmer who owns
    this plot; a stranger (or another farmer) tapping a callback_data
    string that references someone else's plot_id/season_id must not be
    able to record a confirmation or rollover reply on that farmer's
    behalf. farmer=None (no Farmer record resolvable) always refuses --
    fails closed, never assumes authorization when identity can't be
    established."""
    tapper_id = (callback_query.get("from") or {}).get("id")
    return (
        farmer is not None
        and tapper_id is not None
        and farmer.telegram_chat_id is not None
        and tapper_id == farmer.telegram_chat_id
    )


def parse_breakdown_callback_data(data: str) -> tuple[str, str, str] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "breakdown":
        return None
    _, cluster_id, season_id, report_date = parts
    return cluster_id, season_id, report_date


def handle_breakdown_callback(
    client: TelegramClient,
    callback_query: dict,
    storage: Storage,
) -> None:
    """ADR-011 Part 2: the operator's "machine down today" tap, attached
    to the day's route summary message. One tap is sufficient on its
    own -- the recompute runs immediately; the follow-up keyboard sent
    afterward is purely additive context.
    """
    callback_query_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    parsed = parse_breakdown_callback_data(data)

    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return

    cluster_id, season_id, report_date_str = parsed
    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    mod = notify._lang_module(cluster.operator_language)

    if not _is_operator(callback_query, cluster):
        logger.warning(
            "breakdown callback for cluster=%s from a non-operator tapper -- refused",
            cluster_id,
        )
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    try:
        report_date = date.fromisoformat(report_date_str)
    except ValueError:
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    # Deferred import: watcher.py imports this module (webhook.py) to
    # send escalation/notification messages, so importing watcher at
    # module load time here would be circular. Resolving it at call
    # time, after both modules are fully loaded, is the standard fix.
    from harvest_convoy import watcher as watcher_mod

    result = watcher_mod.handle_machine_breakdown(
        cluster_id, season_id, report_date, storage=storage, telegram_client=client,
    )

    status = result.get("status")
    if status == "already_reported":
        client.answer_callback_query(callback_query_id, mod.breakdown_already_reported(), show_alert=True)
        return
    if status == "no_route":
        client.answer_callback_query(callback_query_id, mod.breakdown_no_route(), show_alert=True)
        return
    if status in ("error", "weather_unavailable"):
        client.answer_callback_query(callback_query_id, mod.breakdown_recompute_failed(), show_alert=True)
        return

    client.answer_callback_query(callback_query_id, mod.breakdown_acknowledged())
    if cluster.operator_chat_id is not None:
        client.send_message(
            cluster.operator_chat_id, mod.breakdown_followup_prompt(),
            reply_markup=notify.build_breakdown_followup_keyboard(
                cluster_id, language=cluster.operator_language
            ),
        )

    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)


def parse_breakdown_followup_callback_data(data: str) -> tuple[str, str] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "breakdown_followup":
        return None
    _, cluster_id, choice = parts
    if choice not in ("tomorrow", "indefinite"):
        return None
    return cluster_id, choice


def handle_breakdown_followup_callback(
    client: TelegramClient,
    callback_query: dict,
    storage: Storage,
) -> None:
    """The optional second tap -- "back tomorrow" (the default; sets no
    state at all, tomorrow's trigger just runs normally) or "down
    indefinitely" (sets MachineStatus, and offers the symmetric
    "machine is back" action so the cluster can never get permanently
    stuck). See ADR-011 Part 2."""
    callback_query_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    parsed = parse_breakdown_followup_callback_data(data)

    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return

    cluster_id, choice = parsed
    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    mod = notify._lang_module(cluster.operator_language)

    if not _is_operator(callback_query, cluster):
        logger.warning(
            "breakdown_followup callback for cluster=%s from a non-operator "
            "tapper -- refused",
            cluster_id,
        )
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    if choice == "indefinite":
        from harvest_convoy import watcher as watcher_mod

        watcher_mod.set_machine_down(cluster_id, storage=storage)
        client.answer_callback_query(callback_query_id, mod.machine_down_indefinite_ack())
        if cluster.operator_chat_id is not None:
            client.send_message(
                cluster.operator_chat_id, mod.machine_down_indefinite_ack(),
                reply_markup=notify.build_machine_back_keyboard(
                    cluster_id, language=cluster.operator_language
                ),
            )
    else:
        client.answer_callback_query(callback_query_id, mod.breakdown_back_tomorrow_ack())

    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)


def parse_machine_back_callback_data(data: str) -> str | None:
    parts = data.split(":")
    if len(parts) != 2 or parts[0] != "machine_back":
        return None
    return parts[1]


def handle_machine_back_callback(
    client: TelegramClient,
    callback_query: dict,
    storage: Storage,
) -> None:
    """The symmetric clearing action for "down indefinitely". See
    ADR-011 Part 2."""
    callback_query_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    cluster_id = parse_machine_back_callback_data(data)

    if cluster_id is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return

    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    mod = notify._lang_module(cluster.operator_language)

    if not _is_operator(callback_query, cluster):
        logger.warning(
            "machine_back callback for cluster=%s from a non-operator tapper "
            "-- refused",
            cluster_id,
        )
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    from harvest_convoy import watcher as watcher_mod

    watcher_mod.clear_machine_down(cluster_id, storage=storage)
    client.answer_callback_query(callback_query_id, mod.machine_back_ack())

    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(chat_id, message_id, reply_markup=None)


def _resolve_route(storage: Storage, plot_ids: list[str]) -> list[tuple[Farmer, Plot]]:
    """Plot_ids from a RouteOverride resolved back to (Farmer, Plot)
    pairs via plain Storage lookups -- unlike escalation resolution,
    which uses an injectable FarmerPlotLookup for historical reasons,
    every route_* handler has Storage in scope already and needs no
    special wiring. A missing plot/farmer is logged and skipped, not
    raised -- a route.py rendering must never crash on a dangling id."""
    route = []
    for pid in plot_ids:
        plot = storage.get_plot(pid)
        if plot is None:
            logger.error("route override references missing plot=%s", pid)
            continue
        farmer = storage.get_farmer(plot.farmer_id)
        if farmer is None:
            logger.error("route override references plot=%s with a missing farmer", pid)
            continue
        route.append((farmer, plot))
    return route


def _resolve_route_context(
    client: TelegramClient,
    callback_query: dict,
    storage: Storage,
    cluster_id: str,
    season_id: str,
    decision_date: str,
    *,
    today: date,
):
    """Shared authorization + staleness + existence checks for every
    route_* callback (ADR-013 Decisions 8-9): cluster resolves, the
    tapper is the cluster's operator, decision_date is today (a tap on a
    still-visible prior day's message is refused, not silently applied
    to today), and a RouteOverride actually exists for this exact key.
    Returns (cluster, mod, override) on success; on any failure, the
    callback has already been answered with a refusal and the caller
    should return immediately without touching storage."""
    callback_query_id = callback_query.get("id", "")
    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return None
    mod = notify._lang_module(cluster.operator_language)

    if not _is_operator(callback_query, cluster):
        logger.warning(
            "route callback for cluster=%s from a non-operator tapper -- refused", cluster_id,
        )
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return None

    if decision_date != today.isoformat():
        client.answer_callback_query(callback_query_id, mod.route_stale(decision_date), show_alert=True)
        return None

    override = storage.get_route_override(cluster_id, season_id, decision_date)
    if override is None:
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return None

    return cluster, mod, override


def _edit_message(client: TelegramClient, callback_query: dict, text: str, reply_markup: dict | None) -> None:
    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is not None and message_id is not None:
        client.edit_message_text(chat_id, message_id, text, reply_markup=reply_markup)


def _parse_route_key_callback_data(data: str, prefix: str) -> tuple[str, str, str] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != prefix:
        return None
    _, cluster_id, season_id, decision_date = parts
    return cluster_id, season_id, decision_date


def parse_route_accept_callback_data(data: str) -> tuple[str, str, str] | None:
    return _parse_route_key_callback_data(data, "route_accept")


def parse_route_modify_callback_data(data: str) -> tuple[str, str, str] | None:
    return _parse_route_key_callback_data(data, "route_modify")


def parse_route_done_callback_data(data: str) -> tuple[str, str, str] | None:
    return _parse_route_key_callback_data(data, "route_done")


def parse_route_swap_callback_data(data: str) -> tuple[str, str, str, int] | None:
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != "route_swap":
        return None
    _, cluster_id, season_id, decision_date, position_str = parts
    try:
        position = int(position_str)
    except ValueError:
        return None
    if position < 2:
        return None
    return cluster_id, season_id, decision_date, position


def parse_route_drop_callback_data(data: str) -> tuple[str, str, str, str] | None:
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != "route_drop":
        return None
    _, cluster_id, season_id, decision_date, plot_id = parts
    return cluster_id, season_id, decision_date, plot_id


def parse_route_drop_confirm_callback_data(data: str) -> tuple[str, str, str, str, bool] | None:
    parts = data.split(":")
    if len(parts) != 6 or parts[0] != "route_drop_confirm":
        return None
    _, cluster_id, season_id, decision_date, plot_id, answer = parts
    if answer not in ("yes", "no"):
        return None
    return cluster_id, season_id, decision_date, plot_id, answer == "yes"


def handle_route_accept_callback(
    client: TelegramClient, callback_query: dict, storage: Storage, *, today: date | None = None,
) -> None:
    """ADR-013 Decision 3: records an explicit acceptance and changes
    nothing else -- a route that already stands (silence is not a veto)
    has no farmer-facing state left to touch."""
    today = today or date.today()
    callback_query_id = callback_query.get("id", "")
    parsed = parse_route_accept_callback_data(callback_query.get("data", ""))
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    cluster_id, season_id, decision_date = parsed
    ctx = _resolve_route_context(client, callback_query, storage, cluster_id, season_id, decision_date, today=today)
    if ctx is None:
        return
    _cluster, mod, override = ctx

    storage.put_route_override(
        replace(override, accepted_at=datetime.now(timezone.utc).isoformat())
    )
    client.answer_callback_query(callback_query_id, mod.route_accept_ack())


def handle_route_modify_callback(
    client: TelegramClient, callback_query: dict, storage: Storage, *, today: date | None = None,
) -> None:
    """ADR-013 Decision 4: edits the route-summary message in place into
    the per-stop editing view. No mutation of its own -- opening the
    editor changes nothing until a swap or a confirmed drop happens."""
    today = today or date.today()
    callback_query_id = callback_query.get("id", "")
    parsed = parse_route_modify_callback_data(callback_query.get("data", ""))
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    cluster_id, season_id, decision_date = parsed
    ctx = _resolve_route_context(client, callback_query, storage, cluster_id, season_id, decision_date, today=today)
    if ctx is None:
        return
    cluster, mod, override = ctx

    route = _resolve_route(storage, override.current_route)
    client.answer_callback_query(callback_query_id)
    _edit_message(
        client, callback_query,
        notify.build_route_edit_text(cluster, route, language=cluster.operator_language),
        notify.build_route_edit_keyboard(
            cluster_id, season_id, decision_date, route, language=cluster.operator_language
        ),
    )


def handle_route_swap_callback(
    client: TelegramClient, callback_query: dict, storage: Storage, *, today: date | None = None,
) -> None:
    """ADR-013 Decision 4: swaps the stop at `position` with the one
    above it. Low-stakes and self-correcting (tapping the row that moved
    down undoes it) -- no confirmation step, unlike a drop. No farmer
    notification here; position-change notices are batched to Done
    (Decision 5)."""
    today = today or date.today()
    callback_query_id = callback_query.get("id", "")
    parsed = parse_route_swap_callback_data(callback_query.get("data", ""))
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    cluster_id, season_id, decision_date, position = parsed
    ctx = _resolve_route_context(client, callback_query, storage, cluster_id, season_id, decision_date, today=today)
    if ctx is None:
        return
    cluster, mod, override = ctx

    current = list(override.current_route)
    if position > len(current):
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    # Failure path: a swap touching an already-confirmed plot is refused
    # -- the machine already visited (or was told not to), so reordering
    # around it can't mean anything (ADR-013 Decision 9, Case 1).
    plot_a_id, plot_b_id = current[position - 2], current[position - 1]
    for pid in (plot_a_id, plot_b_id):
        confirmation = storage.get_harvest_confirmation(pid, season_id)
        if confirmation is not None and confirmation.confirmed is not None:
            client.answer_callback_query(callback_query_id, mod.route_already_confirmed(), show_alert=True)
            return

    current[position - 2], current[position - 1] = current[position - 1], current[position - 2]
    storage.put_route_override(replace(
        override, current_route=current, last_modified_at=datetime.now(timezone.utc).isoformat(),
    ))

    route = _resolve_route(storage, current)
    client.answer_callback_query(callback_query_id)
    _edit_message(
        client, callback_query,
        notify.build_route_edit_text(cluster, route, language=cluster.operator_language),
        notify.build_route_edit_keyboard(
            cluster_id, season_id, decision_date, route, language=cluster.operator_language
        ),
    )


def handle_route_drop_callback(
    client: TelegramClient, callback_query: dict, storage: Storage, *, today: date | None = None,
) -> None:
    """ADR-013 Decision 4: the first tap of a drop -- shows a
    confirmation prompt, mutates nothing yet. The real side effects only
    happen on an explicit Confirm (handle_route_drop_confirm_callback)."""
    today = today or date.today()
    callback_query_id = callback_query.get("id", "")
    parsed = parse_route_drop_callback_data(callback_query.get("data", ""))
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    cluster_id, season_id, decision_date, plot_id = parsed
    ctx = _resolve_route_context(client, callback_query, storage, cluster_id, season_id, decision_date, today=today)
    if ctx is None:
        return
    cluster, mod, override = ctx

    if plot_id not in override.current_route:
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    confirmation = storage.get_harvest_confirmation(plot_id, season_id)
    if confirmation is not None and confirmation.confirmed is not None:
        client.answer_callback_query(callback_query_id, mod.route_already_confirmed(), show_alert=True)
        return

    plot = storage.get_plot(plot_id)
    farmer = storage.get_farmer(plot.farmer_id) if plot is not None else None
    # None (not mod.DEFAULT_WINNER_LABEL) when no farmer record was
    # found -- route_drop_confirm_prompt picks its own correctly-
    # grammared fallback phrase for that case (fixed 2026-08-24, same
    # class as escalation_resolved_assigned's fix above).
    farmer_name = farmer.name if farmer is not None else None
    # Failure path: dropping the only remaining stop is allowed (the
    # operator's prerogative), but the confirmation prompt gets an added
    # warning line so it isn't one accidental tap away (ADR-013 Decision
    # 9, Case 2).
    is_last_plot = len(override.current_route) == 1

    client.answer_callback_query(callback_query_id)
    _edit_message(
        client, callback_query,
        mod.route_drop_confirm_prompt(farmer_name, is_last_plot=is_last_plot),
        notify.build_route_drop_confirm_keyboard(
            cluster_id, season_id, decision_date, plot_id, language=cluster.operator_language
        ),
    )


def handle_route_drop_confirm_callback(
    client: TelegramClient, callback_query: dict, storage: Storage, *, today: date | None = None,
) -> None:
    """ADR-013 Decisions 6-7: on Confirm, un-harvests the plot (the exact
    reversal hook ADR-009 Part 1.5 built and ADR-011 Part 2 first used),
    cancels its HarvestConfirmation (reusing the same suppression
    ADR-011 Part 2 built for breakdowns, tagged with a distinct
    cancellation_reason so the two stay distinguishable), records a
    decided_by="operator_override" fairness bump, and notifies the
    dropped farmer immediately -- never batched, unlike a reorder. On
    Cancel, returns to the edit view unchanged."""
    today = today or date.today()
    callback_query_id = callback_query.get("id", "")
    parsed = parse_route_drop_confirm_callback_data(callback_query.get("data", ""))
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    cluster_id, season_id, decision_date, plot_id, answer = parsed
    ctx = _resolve_route_context(client, callback_query, storage, cluster_id, season_id, decision_date, today=today)
    if ctx is None:
        return
    cluster, mod, override = ctx

    if not answer:
        route = _resolve_route(storage, override.current_route)
        client.answer_callback_query(callback_query_id)
        _edit_message(
            client, callback_query,
            notify.build_route_edit_text(cluster, route, language=cluster.operator_language),
            notify.build_route_edit_keyboard(
                cluster_id, season_id, decision_date, route, language=cluster.operator_language
            ),
        )
        return

    if plot_id not in override.current_route:
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    confirmation = storage.get_harvest_confirmation(plot_id, season_id)
    if confirmation is not None and confirmation.confirmed is not None:
        client.answer_callback_query(callback_query_id, mod.route_already_confirmed(), show_alert=True)
        return

    plot = storage.get_plot(plot_id)
    farmer = storage.get_farmer(plot.farmer_id) if plot is not None else None
    now_iso = datetime.now(timezone.utc).isoformat()

    updated_route = [pid for pid in override.current_route if pid != plot_id]
    storage.put_route_override(replace(override, current_route=updated_route, last_modified_at=now_iso))

    storage.clear_plot_harvest(plot_id, cluster_id, season_id)
    if confirmation is not None:
        storage.put_harvest_confirmation(replace(
            confirmation, cancelled=True, cancellation_reason="operator_override",
        ))

    if farmer is not None:
        bump_result = record_bump(
            farmer.farmer_id, season_id,
            days_bumped=DEFAULT_DAYS_BUMPED, outcome="operator_override",
            cluster_id=cluster_id, plot_id=plot_id,
            opponent_plot_id=None, storage=storage,
            decided_by="operator_override",
        )
        if not bump_result.success:
            logger.info(
                "route drop %s/%s: ledger write already exists (%s) -- not double-crediting",
                plot_id, season_id, bump_result.error,
            )
        if plot is not None:
            notify.send_route_dropped_notice(client, farmer, plot)
    else:
        logger.error(
            "route drop: no farmer found for plot=%s -- cannot record a fairness bump "
            "or notify anyone",
            plot_id,
        )

    route = _resolve_route(storage, updated_route)
    client.answer_callback_query(callback_query_id, mod.route_done_ack())
    _edit_message(
        client, callback_query,
        notify.build_route_edit_text(cluster, route, language=cluster.operator_language),
        notify.build_route_edit_keyboard(
            cluster_id, season_id, decision_date, route, language=cluster.operator_language
        ),
    )


def handle_route_done_callback(
    client: TelegramClient, callback_query: dict, storage: Storage, *, today: date | None = None,
) -> None:
    """ADR-013 Decision 5: finalizes any accumulated reorders, sending
    each farmer whose position changed since the last notification an
    updated harvest_scheduled -- reusing the existing message shape,
    just called again with the new position. A plot dropped this session
    is excluded by construction (it's no longer in current_route) since
    it was already, separately, notified at drop-confirm time. Edits the
    message back to the summary view."""
    today = today or date.today()
    callback_query_id = callback_query.get("id", "")
    parsed = parse_route_done_callback_data(callback_query.get("data", ""))
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    cluster_id, season_id, decision_date = parsed
    ctx = _resolve_route_context(client, callback_query, storage, cluster_id, season_id, decision_date, today=today)
    if ctx is None:
        return
    cluster, mod, override = ctx

    baseline = override.last_notified_route or override.proposed_route
    baseline_position = {pid: i for i, pid in enumerate(baseline)}
    for new_position, plot_id in enumerate(override.current_route):
        if baseline_position.get(plot_id) == new_position:
            continue
        if plot_id not in baseline_position:
            continue  # not in the baseline at all -- nothing built adds a plot back
        plot = storage.get_plot(plot_id)
        farmer = storage.get_farmer(plot.farmer_id) if plot is not None else None
        if plot is None or farmer is None:
            logger.error(
                "route done: no plot/farmer for %s, position-change notice not sent", plot_id,
            )
            continue
        notify.send_harvest_scheduled(client, farmer, plot, new_position)

    storage.put_route_override(replace(override, last_notified_route=list(override.current_route)))

    route = _resolve_route(storage, override.current_route)
    client.answer_callback_query(callback_query_id, mod.route_done_ack())
    _edit_message(
        client, callback_query,
        notify.build_operator_route_summary_text(cluster, route, language=cluster.operator_language),
        notify.build_route_summary_keyboard(
            cluster_id, season_id, decision_date, route, language=cluster.operator_language
        ),
    )


def parse_operator_lang_callback_data(data: str) -> tuple[str, str, str] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "operator_lang":
        return None
    _, cluster_id, code, language = parts
    if language not in ("ta", "en"):
        return None
    return cluster_id, code, language


def handle_operator_lang_callback(
    client: TelegramClient,
    callback_query: dict,
    storage: Storage,
) -> None:
    """ADR-012 Part 2: the language-choice tap that follows a valid
    /operator <code> command. Re-validates the code's freshness (closes
    the race where two chats both hold a valid code and the first tap
    consumes it) and, critically, checks operator_enrollment.matches_pending
    -- this tap must come from the exact chat_id that sent the original
    command, not just carry well-formed callback_data (Decision 1)."""
    callback_query_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    parsed = parse_operator_lang_callback_data(data)

    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return

    cluster_id, code, language = parsed
    tapper_id = (callback_query.get("from") or {}).get("id")
    mod = notify._lang_module(language)

    if tapper_id is None or not operator_enrollment.matches_pending(tapper_id, cluster_id, code):
        logger.warning(
            "operator_lang callback for cluster=%s code=%s from a chat_id "
            "with no matching pending enrollment -- refused",
            cluster_id, code,
        )
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    record, status = operator_enrollment.validate_code(storage, code)
    if status != "valid":
        logger.info(
            "operator_lang callback: code=%s status=%s at the language step "
            "-- refused (consumed by a concurrent tap, or expired mid-flow)",
            code, status,
        )
        operator_enrollment.clear_state(tapper_id)
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        logger.error(
            "operator_lang callback: cluster=%s no longer exists -- refused",
            cluster_id,
        )
        operator_enrollment.clear_state(tapper_id)
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    operator_enrollment.set_language(tapper_id, language)

    message = callback_query.get("message") or {}
    msg_chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if msg_chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(msg_chat_id, message_id, reply_markup=None)

    if cluster.operator_chat_id is not None:
        # Existing operator already set -- require explicit replacement,
        # never a silent overwrite. Still just a button tap, not new free
        # text (Decision 6 of ADR-011 Part 2's spirit, extended here).
        client.answer_callback_query(callback_query_id, mod.LANGUAGE_ACK)
        client.send_message(
            tapper_id, mod.operator_replacement_prompt(cluster.name),
            reply_markup=operator_enrollment.build_operator_replace_keyboard(
                cluster_id, code, language
            ),
        )
        return

    event = operator_enrollment.complete_enrollment(
        storage, cluster, code, tapper_id, language, previous_chat_id=None,
    )
    operator_enrollment.clear_state(tapper_id)
    if event is None:
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return
    client.answer_callback_query(callback_query_id, mod.LANGUAGE_ACK)
    client.send_message(tapper_id, mod.operator_enrolled(cluster.name))


def parse_operator_replace_callback_data(data: str) -> tuple[str, str, str, bool] | None:
    parts = data.split(":")
    if len(parts) != 5 or parts[0] != "operator_replace":
        return None
    _, cluster_id, code, language, answer = parts
    if language not in ("ta", "en") or answer not in ("yes", "no"):
        return None
    return cluster_id, code, language, answer == "yes"


def handle_operator_replace_callback(
    client: TelegramClient,
    callback_query: dict,
    storage: Storage,
) -> None:
    """ADR-012 Part 2: the Yes/No reply to "this cluster already has an
    operator, replace them?". Same chat_id-binding check as the language
    step -- a stray tap on this callback_data must be refused even if
    the code it references is still genuinely valid."""
    callback_query_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    parsed = parse_operator_replace_callback_data(data)

    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return

    cluster_id, code, language, answer = parsed
    tapper_id = (callback_query.get("from") or {}).get("id")
    mod = notify._lang_module(language)

    if tapper_id is None or not operator_enrollment.matches_pending(tapper_id, cluster_id, code):
        logger.warning(
            "operator_replace callback for cluster=%s code=%s from a chat_id "
            "with no matching pending enrollment -- refused",
            cluster_id, code,
        )
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    message = callback_query.get("message") or {}
    msg_chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if msg_chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(msg_chat_id, message_id, reply_markup=None)

    if not answer:
        operator_enrollment.clear_state(tapper_id)
        client.answer_callback_query(callback_query_id, mod.operator_replacement_declined())
        return

    record, status = operator_enrollment.validate_code(storage, code)
    if status != "valid":
        logger.info(
            "operator_replace callback: code=%s status=%s at the replace "
            "step -- refused",
            code, status,
        )
        operator_enrollment.clear_state(tapper_id)
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        logger.error(
            "operator_replace callback: cluster=%s no longer exists -- refused",
            cluster_id,
        )
        operator_enrollment.clear_state(tapper_id)
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

    event = operator_enrollment.complete_enrollment(
        storage, cluster, code, tapper_id, language,
        previous_chat_id=cluster.operator_chat_id,
    )
    operator_enrollment.clear_state(tapper_id)
    if event is None:
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return
    client.answer_callback_query(callback_query_id, mod.operator_enrolled(cluster.name))


# --- ADR-013 Part 2: proxy registration confirm, and farmer linking ---

def parse_addfarmer_confirm_callback_data(data: str) -> tuple[str, bool] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "addfarmer_confirm" or parts[2] not in ("yes", "no"):
        return None
    _, cluster_id, answer = parts
    return cluster_id, answer == "yes"


def handle_addfarmer_confirm_callback(
    client: TelegramClient, callback_query: dict, storage: Storage,
) -> None:
    """The [Confirm]/[Cancel] tap ending /addfarmer (Decision 13).
    Re-derives cluster.operator_chat_id from the embedded cluster_id and
    checks the tapper against it directly, the same shape every route_*
    callback uses -- an operator_chat_id embedded in the callback data
    itself would prove nothing about who is actually tapping."""
    callback_query_id = callback_query.get("id", "")
    parsed = parse_addfarmer_confirm_callback_data(callback_query.get("data", ""))
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    cluster_id, answer = parsed
    cluster = storage.get_cluster(cluster_id)
    if not _is_operator(callback_query, cluster):
        logger.warning(
            "addfarmer_confirm callback for cluster=%s from a non-operator -- refused",
            cluster_id,
        )
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return

    tapper_id = callback_query["from"]["id"]
    mod = notify._lang_module(cluster.operator_language)
    state = proxy_registration.get_pending_state(tapper_id)
    if state is None or state.step != proxy_registration.ProxyRegistrationStep.AWAITING_CONFIRM:
        client.answer_callback_query(callback_query_id, mod.addfarmer_nothing_pending(), show_alert=True)
        return
    proxy_registration.clear_state(tapper_id)

    message = callback_query.get("message") or {}
    msg_chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if msg_chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(msg_chat_id, message_id, reply_markup=None)

    if not answer:
        client.answer_callback_query(callback_query_id, mod.addfarmer_cancelled())
        return

    result = proxy_registration.persist_proxy_registration(state, storage)
    if result is None:
        client.answer_callback_query(callback_query_id, mod.addfarmer_cancelled(), show_alert=True)
        return
    farmer, _plot = result
    client.answer_callback_query(callback_query_id, mod.addfarmer_registered_toast())
    closing = (
        mod.addfarmer_complete_has_phone(farmer.name) if state.has_phone
        else mod.addfarmer_complete_no_phone(farmer.name)
    )
    client.send_message(tapper_id, closing)


def parse_linkfarmer_proxy_callback_data(data: str) -> str | None:
    parts = data.split(":")
    if len(parts) != 2 or parts[0] != "linkfarmer_proxy":
        return None
    return parts[1]


def handle_linkfarmer_proxy_callback(
    client: TelegramClient, callback_query: dict, storage: Storage,
) -> None:
    """Step 1 of /linkfarmer (Decision 18): the operator picked which
    unlinked proxy farmer to link. Edits the same message in place into
    the candidate list, mirroring route_modify's edit-in-place shape."""
    callback_query_id = callback_query.get("id", "")
    proxy_farmer_id = parse_linkfarmer_proxy_callback_data(callback_query.get("data", ""))
    if proxy_farmer_id is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    proxy_farmer = storage.get_farmer(proxy_farmer_id)
    cluster = storage.get_cluster(proxy_farmer.cluster_id) if proxy_farmer is not None else None
    if not _is_operator(callback_query, cluster):
        logger.warning(
            "linkfarmer_proxy callback for farmer=%s from a non-operator -- refused",
            proxy_farmer_id,
        )
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    outbound = proxy_registration.build_linkfarmer_match_message(
        storage, proxy_farmer_id, cluster.operator_language
    )
    client.answer_callback_query(callback_query_id)
    _edit_message(client, callback_query, outbound.text, outbound.reply_markup)


def parse_linkfarmer_match_callback_data(data: str) -> tuple[str, str] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "linkfarmer_match":
        return None
    _, proxy_farmer_id, candidate_farmer_id = parts
    return proxy_farmer_id, candidate_farmer_id


def handle_linkfarmer_match_callback(
    client: TelegramClient, callback_query: dict, storage: Storage,
) -> None:
    """Step 2 of /linkfarmer: the operator picked which self-registered
    farmer matches. Shows the explicit two-name confirmation (Decision
    18, step 3) before anything is written."""
    callback_query_id = callback_query.get("id", "")
    parsed = parse_linkfarmer_match_callback_data(callback_query.get("data", ""))
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    proxy_farmer_id, candidate_farmer_id = parsed
    proxy_farmer = storage.get_farmer(proxy_farmer_id)
    cluster = storage.get_cluster(proxy_farmer.cluster_id) if proxy_farmer is not None else None
    if not _is_operator(callback_query, cluster):
        logger.warning(
            "linkfarmer_match callback proxy=%s candidate=%s from a non-operator -- refused",
            proxy_farmer_id, candidate_farmer_id,
        )
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    mod = notify._lang_module(cluster.operator_language)
    candidate_farmer = storage.get_farmer(candidate_farmer_id)
    if candidate_farmer is None:
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return
    client.answer_callback_query(callback_query_id)
    _edit_message(
        client, callback_query,
        mod.linkfarmer_confirm_prompt(proxy_farmer.name, candidate_farmer.name),
        proxy_registration.build_linkfarmer_confirm_keyboard(
            proxy_farmer_id, candidate_farmer_id, cluster.operator_language
        ),
    )


def parse_linkfarmer_confirm_callback_data(data: str) -> tuple[str, str, bool] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "linkfarmer_confirm" or parts[3] not in ("yes", "no"):
        return None
    _, proxy_farmer_id, candidate_farmer_id, answer = parts
    return proxy_farmer_id, candidate_farmer_id, answer == "yes"


def handle_linkfarmer_confirm_callback(
    client: TelegramClient, callback_query: dict, storage: Storage,
) -> None:
    """Step 3 of /linkfarmer -- the only step that mutates anything.
    On yes: proxy_registration.apply_link transplants the candidate's
    telegram_chat_id onto the proxy farmer_id and retires the
    candidate's plot (Decision 18), then the message is edited to offer
    a bounded-window Undo rather than just clearing the keyboard -- a
    wrong match is now correctable, not silent (Decision 18's revision,
    2026-08-24). On no: nothing is written."""
    callback_query_id = callback_query.get("id", "")
    parsed = parse_linkfarmer_confirm_callback_data(callback_query.get("data", ""))
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    proxy_farmer_id, candidate_farmer_id, answer = parsed
    proxy_farmer = storage.get_farmer(proxy_farmer_id)
    cluster = storage.get_cluster(proxy_farmer.cluster_id) if proxy_farmer is not None else None
    if not _is_operator(callback_query, cluster):
        logger.warning(
            "linkfarmer_confirm callback proxy=%s candidate=%s from a non-operator -- refused",
            proxy_farmer_id, candidate_farmer_id,
        )
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    mod = notify._lang_module(cluster.operator_language)

    if not answer:
        message = callback_query.get("message") or {}
        msg_chat_id = (message.get("chat") or {}).get("id")
        message_id = message.get("message_id")
        if msg_chat_id is not None and message_id is not None:
            client.edit_message_reply_markup(msg_chat_id, message_id, reply_markup=None)
        client.answer_callback_query(callback_query_id, mod.linkfarmer_cancelled_toast())
        return

    success = proxy_registration.apply_link(storage, proxy_farmer_id, candidate_farmer_id)
    if not success:
        client.answer_callback_query(callback_query_id, mod.linkfarmer_cancelled_toast(), show_alert=True)
        return

    linked_at_epoch = str(int(datetime.now(timezone.utc).timestamp()))
    client.answer_callback_query(callback_query_id, mod.linkfarmer_linked_toast())
    _edit_message(
        client, callback_query,
        mod.linkfarmer_linked_with_undo_text(),
        proxy_registration.build_linkfarmer_undo_keyboard(
            proxy_farmer_id, candidate_farmer_id, linked_at_epoch, cluster.operator_language
        ),
    )


def parse_linkfarmer_undo_callback_data(data: str) -> tuple[str, str, str] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "linkfarmer_undo":
        return None
    _, proxy_farmer_id, candidate_farmer_id, linked_at_epoch = parts
    return proxy_farmer_id, candidate_farmer_id, linked_at_epoch


def handle_linkfarmer_undo_callback(
    client: TelegramClient, callback_query: dict, storage: Storage,
) -> None:
    """Reverses a /linkfarmer link within
    proxy_registration.LINKFARMER_UNDO_WINDOW of when it happened
    (Decision 18's revision, 2026-08-24) -- the one concession to "no
    merge primitive" this part makes, because the reversal itself needs
    no new machinery: retired_reason already exists and apply_link never
    deletes anything. Past the window, or if the linked state has
    already moved on, refused rather than guessed at."""
    callback_query_id = callback_query.get("id", "")
    parsed = parse_linkfarmer_undo_callback_data(callback_query.get("data", ""))
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    proxy_farmer_id, candidate_farmer_id, linked_at_epoch = parsed
    proxy_farmer = storage.get_farmer(proxy_farmer_id)
    cluster = storage.get_cluster(proxy_farmer.cluster_id) if proxy_farmer is not None else None
    if not _is_operator(callback_query, cluster):
        logger.warning(
            "linkfarmer_undo callback proxy=%s candidate=%s from a non-operator -- refused",
            proxy_farmer_id, candidate_farmer_id,
        )
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    mod = notify._lang_module(cluster.operator_language)

    message = callback_query.get("message") or {}
    msg_chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if msg_chat_id is not None and message_id is not None:
        client.edit_message_reply_markup(msg_chat_id, message_id, reply_markup=None)

    result = proxy_registration.undo_link(storage, proxy_farmer_id, candidate_farmer_id, linked_at_epoch)
    if result == "expired":
        client.answer_callback_query(callback_query_id, mod.linkfarmer_undo_expired(), show_alert=True)
        return
    if result != "ok":
        client.answer_callback_query(callback_query_id, mod.linkfarmer_undo_failed(), show_alert=True)
        return
    client.answer_callback_query(callback_query_id, mod.linkfarmer_undone_toast())


def parse_why_callback_data(data: str, expected_prefix: str) -> tuple[str, str, str] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != expected_prefix:
        return None
    _, plot_id, season_id, decision_date = parts
    return plot_id, season_id, decision_date


def _handle_why_callback(
    client: TelegramClient, callback_query: dict, storage: Storage, *, prefix: str, lost: bool,
) -> None:
    """ADR-013 Part 3: a farmer's one-tap "why" on a not_ready or
    escalation_resolved_lost message. Read-only -- nothing here mutates
    Storage, so a repeat tap on the same button (days later, twice in a
    row, whatever) just replays the same honest answer; the keyboard is
    deliberately never cleared after a tap, unlike every mutating
    callback in this module. See telegram/farmer_why.py for the actual
    lookup and rendering."""
    callback_query_id = callback_query.get("id", "")
    parsed = parse_why_callback_data(callback_query.get("data", ""), prefix)
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return

    plot_id, season_id, decision_date = parsed
    plot = storage.get_plot(plot_id)
    farmer = storage.get_farmer(plot.farmer_id) if plot is not None else None
    if plot is None or farmer is None:
        logger.error("%s callback for unknown plot=%s -- refused", prefix, plot_id)
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return

    if not _is_farmer(callback_query, farmer):
        # ADR-012: this plot's own farmer only, checked before any
        # lookup below -- the third farmer-owned callback class after
        # confirm:/rollover:, same check, same refusal wording.
        logger.warning(
            "%s callback for plot=%s from a chat_id that doesn't match "
            "the registered farmer -- refused",
            prefix, plot_id,
        )
        client.answer_callback_query(
            callback_query_id, notify._lang_module(farmer.language).unrecognized_action(), show_alert=True,
        )
        return

    client.answer_callback_query(callback_query_id)  # Telegram requires an answer either way
    build_text = farmer_why.why_lost_text if lost else farmer_why.why_not_ready_text
    client.send_message(
        farmer.telegram_chat_id,
        build_text(storage, plot_id, season_id, decision_date, language=farmer.language),
    )


def handle_why_notready_callback(client: TelegramClient, callback_query: dict, storage: Storage) -> None:
    _handle_why_callback(client, callback_query, storage, prefix="why_notready", lost=False)


def handle_why_lost_callback(client: TelegramClient, callback_query: dict, storage: Storage) -> None:
    _handle_why_callback(client, callback_query, storage, prefix="why_lost", lost=True)


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

    if not _is_operator(callback_query, cluster):
        # ADR-012: an escalation message resolves a scheduling conflict
        # and writes a real fairness-ledger entry -- anyone who obtained
        # or was forwarded it (not just the operator it was sent to)
        # could otherwise tap it. Same check, same refusal wording, as
        # the breakdown/machine_back callbacks (ADR-011 Part 2,
        # Decision 6) -- checked before any lookup or ledger write below.
        logger.warning(
            "escalation %s callback from a non-operator tapper -- refused", key,
        )
        client.answer_callback_query(callback_query_id, mod.unrecognized_action(), show_alert=True)
        return

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
            # ADR-013 "Resolved on review": a human resolving an
            # escalation is a human decision, same as an operator
            # override, even though it resolves a tie the agent's own
            # process asked for help on rather than reversing a confident
            # one -- see storage/interface.py:LedgerEntry.decided_by.
            decided_by="operator_escalation",
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
            decided_by="operator_escalation",
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

    # None (not mod.DEFAULT_WINNER_LABEL) when no farmer record was
    # found for the winning plot -- escalation_resolved_assigned picks
    # its own correctly-grammared fallback phrase for that case. See
    # messages_ta.py's comment beside DEFAULT_WINNER_LABEL: substituting
    # the bare fallback noun here and letting this function's own
    # spaced "{name} க்கு" template suffix it produced ungrammatical
    # Tamil (fixed 2026-08-24).
    winner_name = winner_result[0].name if winner_result else None
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

        if escalation.decision_date:
            _resolve_decision_record(
                storage, chosen_plot_id, season_id, escalation.decision_date, "escalated_won"
            )
            _resolve_decision_record(
                storage, loser_plot_id, season_id, escalation.decision_date, "escalated_lost"
            )
        else:
            logger.info(
                "escalation %s: pending payload has no decision_date -- "
                "cannot update its DecisionRecord (predates ADR-010 Part 0.5)",
                key,
            )
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
            season_id=season_id,
            # EscalationPayload.decision_date defaults to "" (a payload
            # built before ADR-010 Part 0.5, or hand-built without one) --
            # normalized to None here so notify.py's "was a date ever
            # known" check (Decision 26, Gap B) treats "" the same as
            # "no escalation found at all," not as a real, empty date.
            decision_date=(escalation.decision_date or None) if escalation is not None else None,
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

    Nine distinct callback_query shapes: a "lang:ta"/"lang:en"
    registration-language tap routes to
    registration.handle_language_callback (ADR-008 Decision 7); a
    "confirm:{plot_id}:{season_id}:{yes|no}" tap routes to
    handle_confirmation_callback (ADR-009 Part 2); a
    "rollover:{plot_id}:{new_season_id}:{yes|no}" tap routes to
    handle_rollover_callback (ADR-011 Part 1); "breakdown:...",
    "breakdown_followup:...", and "machine_back:..." route to their
    matching handlers (ADR-011 Part 2); "route_accept:...",
    "route_modify:...", "route_swap:...", "route_drop_confirm:...",
    "route_drop:...", and "route_done:..." route to the route-proposal
    handlers (ADR-013); "operator_lang:..." and "operator_replace:..."
    route to the operator-enrollment handlers (ADR-012 Part 2);
    "addfarmer_confirm:...", "linkfarmer_proxy:...", "linkfarmer_match:...",
    "linkfarmer_confirm:...", and "linkfarmer_undo:..." route to the
    proxy-registration/linking handlers (ADR-013 Part 2);
    "why_notready:..." and "why_lost:..." route to the farmer's own
    read-only "why" answer (ADR-013 Part 3); anything else goes through
    the existing handle_callback_query escalation flow.
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
        if data.startswith("rollover:"):
            handle_rollover_callback(client, callback_query, storage)
            return
        if data.startswith("breakdown_followup:"):
            handle_breakdown_followup_callback(client, callback_query, storage)
            return
        if data.startswith("breakdown:"):
            handle_breakdown_callback(client, callback_query, storage)
            return
        if data.startswith("machine_back:"):
            handle_machine_back_callback(client, callback_query, storage)
            return
        if data.startswith("route_accept:"):
            handle_route_accept_callback(client, callback_query, storage)
            return
        if data.startswith("route_modify:"):
            handle_route_modify_callback(client, callback_query, storage)
            return
        if data.startswith("route_swap:"):
            handle_route_swap_callback(client, callback_query, storage)
            return
        if data.startswith("route_drop_confirm:"):
            handle_route_drop_confirm_callback(client, callback_query, storage)
            return
        if data.startswith("route_drop:"):
            handle_route_drop_callback(client, callback_query, storage)
            return
        if data.startswith("route_done:"):
            handle_route_done_callback(client, callback_query, storage)
            return
        if data.startswith("operator_lang:"):
            handle_operator_lang_callback(client, callback_query, storage)
            return
        if data.startswith("operator_replace:"):
            handle_operator_replace_callback(client, callback_query, storage)
            return
        if data.startswith("addfarmer_confirm:"):
            handle_addfarmer_confirm_callback(client, callback_query, storage)
            return
        if data.startswith("linkfarmer_proxy:"):
            handle_linkfarmer_proxy_callback(client, callback_query, storage)
            return
        if data.startswith("linkfarmer_match:"):
            handle_linkfarmer_match_callback(client, callback_query, storage)
            return
        if data.startswith("linkfarmer_confirm:"):
            handle_linkfarmer_confirm_callback(client, callback_query, storage)
            return
        if data.startswith("linkfarmer_undo:"):
            handle_linkfarmer_undo_callback(client, callback_query, storage)
            return
        if data.startswith("why_notready:"):
            handle_why_notready_callback(client, callback_query, storage)
            return
        if data.startswith("why_lost:"):
            handle_why_lost_callback(client, callback_query, storage)
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

    # A one-time "/operator <code>" enrollment command -- checked before
    # anything else so it's never parsed as farmer registration text or
    # a rollover date reply. Matched on the bare command word (not just
    # parse_operator_command's success) so a malformed "/operator" with
    # no code still gets the usage message instead of falling through.
    # See ADR-012 Part 2.
    if (incoming.text or "").strip().lower().startswith(operator_enrollment.COMMAND):
        outbound = operator_enrollment.handle_operator_command(chat_id, incoming.text, storage)
        client.send_message(chat_id, outbound.text, reply_markup=outbound.reply_markup)
        return

    # "/addfarmer" and "/linkfarmer" -- ADR-013 Part 2, operator-only,
    # checked before anything else for the same reason "/operator <code>"
    # is: never parsed as farmer registration text or a rollover date
    # reply. Both commands check the sending chat_id against
    # Cluster.operator_chat_id directly (there is no code to validate --
    # identity is already established by the time an operator is
    # enrolled at all), refusing generically and starting nothing on a
    # mismatch.
    text_lower = (incoming.text or "").strip().lower()
    if text_lower.startswith(proxy_registration.ADD_FARMER_COMMAND) or text_lower.startswith(
        proxy_registration.LINK_FARMER_COMMAND
    ):
        cluster_id = os.environ.get("HARVEST_CONVOY_CLUSTER_ID")
        cluster = storage.get_cluster(cluster_id) if cluster_id else None
        if cluster is None or chat_id != cluster.operator_chat_id:
            logger.warning(
                "addfarmer/linkfarmer command from chat_id=%s -- not this "
                "cluster's operator, refused",
                chat_id,
            )
            client.send_message(chat_id, notify._lang_module("ta").operator_only_command())
            return
        if text_lower.startswith(proxy_registration.ADD_FARMER_COMMAND):
            outbound = proxy_registration.handle_addfarmer_command(chat_id, storage)
        else:
            outbound = proxy_registration.handle_linkfarmer_command(chat_id, storage)
        client.send_message(chat_id, outbound.text, reply_markup=outbound.reply_markup)
        return

    # An operator mid-/addfarmer-flow reply -- routed here, not into
    # registration.handle_incoming, so an operator's free-text answer
    # about someone else's plot is never mistaken for his own
    # registration attempt. See ADR-013 Part 2 Decision 13.
    addfarmer_outbound = proxy_registration.handle_incoming(chat_id, incoming, storage)
    if addfarmer_outbound is not None:
        client.send_message(chat_id, addfarmer_outbound.text, reply_markup=addfarmer_outbound.reply_markup)
        return

    # A farmer mid-reply to a "yes, tap and tell me the date" rollover
    # prompt is routed here, not into registration.handle_incoming --
    # their free-text date reply must not be parsed as a brand-new
    # registration attempt. See ADR-011 Part 1.
    pending_rollover = rollover.get_pending_state(chat_id)
    if pending_rollover is not None:
        resolved, outbound = rollover.advance_rollover_reply(pending_rollover, incoming, storage)
        if resolved:
            rollover.clear_state(chat_id)
        client.send_message(chat_id, outbound.text, reply_markup=outbound.reply_markup)
        return

    outbound = registration.handle_incoming(chat_id, incoming, storage)
    client.send_message(chat_id, outbound.text, reply_markup=outbound.reply_markup)
