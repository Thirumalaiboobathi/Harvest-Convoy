"""Outbound message dispatch. Four distinct message shapes -- see
docs/adr/ADR-004-telegram.md Decision 3. `not_ready` is the one the brief
calls out as the actual product: a farmer told "not yet" is the system
working, not a non-event.
"""

from __future__ import annotations

import logging

from harvest_convoy.agents.contracts import EscalationPayload
from harvest_convoy.models import Farmer, Plot
from harvest_convoy.telegram.client import SendResult, TelegramClient

logger = logging.getLogger(__name__)


def build_harvest_scheduled_text(plot: Plot, route_position: int) -> str:
    return (
        f"Good news: the machine is coming to your {plot.area_acres} acre "
        f"plot today. You're stop #{route_position + 1} on the route."
    )


def build_not_ready_text(plot: Plot, next_check_days: int = 1) -> str:
    return (
        f"Your {plot.area_acres} acre plot isn't ready to harvest yet -- "
        f"the grain is still filling and is safer standing than cut early. "
        f"No action needed. We'll check again in {next_check_days} day"
        f"{'s' if next_check_days != 1 else ''}."
    )


def build_escalation_resolved_text(plot: Plot, won: bool) -> str:
    if won:
        return (
            f"Update: your {plot.area_acres} acre plot has been confirmed "
            f"for today's route after a scheduling conflict was resolved."
        )
    return (
        f"Update: the machine is going to a nearby plot first today due to "
        f"a closer conflict. Your {plot.area_acres} acre plot is still on "
        f"the list -- we'll be in touch with your new slot."
    )


def build_operator_route_summary_text(
    cluster_name: str, ordered_plot_ids: list[str]
) -> str:
    if not ordered_plot_ids:
        return f"{cluster_name}: no plots on today's route."
    stops = "\n".join(f"{i + 1}. {pid}" for i, pid in enumerate(ordered_plot_ids))
    return f"{cluster_name} route for today:\n{stops}"


def send_harvest_scheduled(
    client: TelegramClient, farmer: Farmer, plot: Plot, route_position: int
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id, build_harvest_scheduled_text(plot, route_position)
    )


def send_not_ready(
    client: TelegramClient, farmer: Farmer, plot: Plot, next_check_days: int = 1
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id, build_not_ready_text(plot, next_check_days)
    )


def send_escalation_resolved(
    client: TelegramClient, farmer: Farmer, plot: Plot, won: bool
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id, build_escalation_resolved_text(plot, won)
    )


def send_operator_route_summary(
    client: TelegramClient,
    operator_chat_id: int | None,
    cluster_name: str,
    ordered_plot_ids: list[str],
) -> SendResult:
    if operator_chat_id is None:
        logger.error(
            "cluster %s has no operator configured; route summary not sent",
            cluster_name,
        )
        return SendResult(success=False, error="no operator configured for cluster")
    return client.send_message(
        operator_chat_id, build_operator_route_summary_text(cluster_name, ordered_plot_ids)
    )


def build_escalation_keyboard(escalation: EscalationPayload) -> dict:
    def callback_data(chosen_plot_id: str) -> str:
        return (
            f"resolve:{escalation.cluster_id}:{escalation.plot_a_id}:"
            f"{escalation.plot_b_id}:{chosen_plot_id}"
        )

    return {
        "inline_keyboard": [
            [
                {
                    "text": f"Send machine to {escalation.plot_a_id}",
                    "callback_data": callback_data(escalation.plot_a_id),
                },
                {
                    "text": f"Send machine to {escalation.plot_b_id}",
                    "callback_data": callback_data(escalation.plot_b_id),
                },
            ]
        ]
    }


def build_escalation_text(escalation: EscalationPayload) -> str:
    return (
        f"Scheduling conflict after {escalation.rounds_run} rounds of "
        f"negotiation:\n"
        f"- {escalation.plot_a_id}: {escalation.claim_a.argument}\n"
        f"- {escalation.plot_b_id}: {escalation.claim_b.argument}\n"
        f"Which plot should the machine go to?"
    )


def send_escalation(
    client: TelegramClient,
    operator_chat_id: int | None,
    escalation: EscalationPayload,
) -> SendResult:
    if operator_chat_id is None:
        logger.error(
            "cluster %s has no operator configured; escalation %s vs %s "
            "could not be sent to a human",
            escalation.cluster_id, escalation.plot_a_id, escalation.plot_b_id,
        )
        return SendResult(success=False, error="no operator configured for cluster")
    return client.send_message(
        operator_chat_id,
        build_escalation_text(escalation),
        reply_markup=build_escalation_keyboard(escalation),
    )
