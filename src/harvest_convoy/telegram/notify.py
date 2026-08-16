"""Outbound message dispatch. Four distinct message shapes -- see
docs/adr/ADR-004-telegram.md Decision 3. `not_ready` is the one the brief
calls out as the actual product: a farmer told "not yet" is the system
working, not a non-event.

No raw plot IDs in any human-read text -- farmer name, acreage, and a
compass-direction/distance hint from the village center instead. plot_id
still appears inside callback_data (a machine-parsed field, never rendered
to a human), which is a different thing and stays as-is.
"""

from __future__ import annotations

import logging
import math

from harvest_convoy.agents.contracts import AdvocateClaim
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.route import haversine_km
from harvest_convoy.telegram.client import SendResult, TelegramClient

logger = logging.getLogger(__name__)

_COMPASS_POINTS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def location_hint(cluster: Cluster, plot: Plot) -> str:
    """Distance and compass direction from the village center -- lets an
    operator who knows the village confirm which physical plot this is,
    without exposing an internal plot_id."""
    distance_km = haversine_km(
        cluster.machine_start_lat, cluster.machine_start_lon, plot.lat, plot.lon
    )
    bearing = _bearing_deg(
        cluster.machine_start_lat, cluster.machine_start_lon, plot.lat, plot.lon
    )
    direction = _COMPASS_POINTS[round(bearing / 22.5) % 16]
    return f"{distance_km:.1f}km {direction} of village center"


def short_label(farmer: Farmer, plot: Plot) -> str:
    """e.g. "Muthu Pandian, 2.5ac" -- for buttons and inline mentions."""
    return f"{farmer.name}, {plot.area_acres}ac"


def full_label(farmer: Farmer, plot: Plot, cluster: Cluster) -> str:
    """e.g. "Muthu Pandian, 2.5ac, 0.8km NE of village center" -- for the
    route summary, where the operator needs enough to actually find it."""
    return f"{short_label(farmer, plot)}, {location_hint(cluster, plot)}"


def _overripe_phrase(days_past_maturity: int) -> str:
    if days_past_maturity <= 0:
        return "just reached ready today"
    unit = "day" if days_past_maturity == 1 else "days"
    return f"{days_past_maturity} {unit} overripe"


def build_harvest_scheduled_text(plot: Plot, route_position: int) -> str:
    return (
        f"Good news: the machine is coming to your {plot.area_acres} acre "
        f"plot today. You're stop #{route_position + 1} on the route."
    )


def build_not_ready_text(plot: Plot) -> str:
    return (
        f"Your {plot.area_acres} acre plot isn't ready to harvest yet -- "
        f"the grain is still filling, and it's safer standing than cut "
        f"early. No action needed, and no need to check in either: we're "
        f"tracking it every day on our side. You'll only hear from us "
        f"again when it's time to harvest or something changes."
    )


def build_escalation_resolved_text(
    plot: Plot,
    won: bool,
    *,
    other_farmer_name: str | None = None,
    reason: str | None = None,
) -> str:
    if won:
        return (
            f"Update: your {plot.area_acres} acre plot has been confirmed "
            f"for today's route."
        )
    who = other_farmer_name or "another farmer's plot"
    why = f" -- {reason}" if reason else ""
    return (
        f"Update: today's machine is going to {who}'s plot instead{why}. "
        f"Your {plot.area_acres} acre plot is not on today's route -- the "
        f"operator will send the machine as soon as it's free next. Every "
        f"extra day standing does raise the risk of grain loss to "
        f"shattering and moisture, so let us know if conditions on your "
        f"plot change."
    )


def build_operator_route_summary_text(
    cluster: Cluster, route: list[tuple[Farmer, Plot]]
) -> str:
    if not route:
        return f"{cluster.name}: no plots on today's route."
    stops = "\n".join(
        f"{i + 1}. {full_label(farmer, plot, cluster)}"
        for i, (farmer, plot) in enumerate(route)
    )
    return f"{cluster.name} route for today:\n{stops}"


def send_harvest_scheduled(
    client: TelegramClient, farmer: Farmer, plot: Plot, route_position: int
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id, build_harvest_scheduled_text(plot, route_position)
    )


def send_not_ready(client: TelegramClient, farmer: Farmer, plot: Plot) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(farmer.telegram_chat_id, build_not_ready_text(plot))


def send_escalation_resolved(
    client: TelegramClient,
    farmer: Farmer,
    plot: Plot,
    won: bool,
    *,
    other_farmer_name: str | None = None,
    reason: str | None = None,
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id,
        build_escalation_resolved_text(
            plot, won, other_farmer_name=other_farmer_name, reason=reason
        ),
    )


def send_operator_route_summary(
    client: TelegramClient,
    operator_chat_id: int | None,
    cluster: Cluster,
    route: list[tuple[Farmer, Plot]],
) -> SendResult:
    if operator_chat_id is None:
        logger.error(
            "cluster %s has no operator configured; route summary not sent",
            cluster.name,
        )
        return SendResult(success=False, error="no operator configured for cluster")
    return client.send_message(
        operator_chat_id, build_operator_route_summary_text(cluster, route)
    )


def build_escalation_keyboard(
    cluster_id: str,
    plot_a_id: str, farmer_a: Farmer, plot_a: Plot,
    plot_b_id: str, farmer_b: Farmer, plot_b: Plot,
) -> dict:
    def callback_data(chosen_plot_id: str) -> str:
        return f"resolve:{cluster_id}:{plot_a_id}:{plot_b_id}:{chosen_plot_id}"

    return {
        "inline_keyboard": [
            [
                {
                    "text": short_label(farmer_a, plot_a),
                    "callback_data": callback_data(plot_a_id),
                },
                {
                    "text": short_label(farmer_b, plot_b),
                    "callback_data": callback_data(plot_b_id),
                },
            ]
        ]
    }


def build_escalation_text(
    farmer_a: Farmer, plot_a: Plot, claim_a: AdvocateClaim,
    farmer_b: Farmer, plot_b: Plot, claim_b: AdvocateClaim,
) -> str:
    bumped_a = " (bumped last season)" if claim_a.bumped_last_season else ""
    bumped_b = " (bumped last season)" if claim_b.bumped_last_season else ""
    return (
        f"Only one plot can get today's machine.\n"
        f"- {short_label(farmer_a, plot_a)}: "
        f"{_overripe_phrase(claim_a.days_past_maturity)}{bumped_a}\n"
        f"- {short_label(farmer_b, plot_b)}: "
        f"{_overripe_phrase(claim_b.days_past_maturity)}{bumped_b}\n"
        f"Who should get it?"
    )


def send_escalation(
    client: TelegramClient,
    operator_chat_id: int | None,
    cluster_id: str,
    plot_a_id: str, farmer_a: Farmer, plot_a: Plot, claim_a: AdvocateClaim,
    plot_b_id: str, farmer_b: Farmer, plot_b: Plot, claim_b: AdvocateClaim,
) -> SendResult:
    if operator_chat_id is None:
        logger.error(
            "cluster %s has no operator configured; escalation %s vs %s "
            "could not be sent to a human",
            cluster_id, plot_a_id, plot_b_id,
        )
        return SendResult(success=False, error="no operator configured for cluster")
    return client.send_message(
        operator_chat_id,
        build_escalation_text(farmer_a, plot_a, claim_a, farmer_b, plot_b, claim_b),
        reply_markup=build_escalation_keyboard(
            cluster_id, plot_a_id, farmer_a, plot_a, plot_b_id, farmer_b, plot_b
        ),
    )
