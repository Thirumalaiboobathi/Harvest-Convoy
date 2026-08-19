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
from harvest_convoy.agronomy import market_params
from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.scheduling.route import haversine_km
from harvest_convoy.telegram import messages_en, messages_ta
from harvest_convoy.telegram.client import SendResult, TelegramClient

logger = logging.getLogger(__name__)

_LANGUAGE_MODULES = {"ta": messages_ta, "en": messages_en}


def _lang_module(language: str):
    """Unknown/legacy language codes default to Tamil -- see
    Farmer.language's dataclass default (ADR-008 Decision 6). Used for
    both the farmer-facing message shapes (keyed by Farmer.language) and
    the operator-facing ones (keyed by Cluster.operator_language) -- see
    ADR-008 Decision 8's revision note: operator-facing text was
    English-only by disclosed decision, reversed on request."""
    return _LANGUAGE_MODULES.get(language, messages_ta)

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


def location_hint(cluster: Cluster, plot: Plot, *, language: str = "ta") -> str:
    """Distance and compass direction from the village center -- lets an
    operator who knows the village confirm which physical plot this is,
    without exposing an internal plot_id. The bearing calculation itself
    (which of 16 sectors) is language-agnostic geometry; only the
    rendered direction word and the surrounding sentence are localized,
    same dispatch pattern as every other message shape. Was a single
    hardcoded English sentence regardless of language until caught live
    on the deployed path -- a Tamil-registered farmer's route summary
    read "...NNW of village center" verbatim, mid-Tamil-sentence. See
    ADR-008 follow-up."""
    distance_km = haversine_km(
        cluster.machine_start_lat, cluster.machine_start_lon, plot.lat, plot.lon
    )
    bearing = _bearing_deg(
        cluster.machine_start_lat, cluster.machine_start_lon, plot.lat, plot.lon
    )
    direction_key = _COMPASS_POINTS[round(bearing / 22.5) % 16]
    mod = _lang_module(language)
    return mod.location_hint(distance_km, mod.format_direction(direction_key))


def short_label(farmer: Farmer, plot: Plot, *, language: str = "ta") -> str:
    """e.g. "Muthu Pandian, 2.5 acres" -- for buttons and inline
    mentions. `language` is whoever is READING the label (the operator
    for route-summary/escalation uses, matching Cluster.operator_language)
    -- the farmer's name itself is never translated."""
    area = _lang_module(language).format_area(plot.area_acres, plot.area_unit)
    return f"{farmer.name}, {area}"


def full_label(farmer: Farmer, plot: Plot, cluster: Cluster, *, language: str = "ta") -> str:
    """e.g. "Muthu Pandian, 2.5 acres, 0.8km NE of village center" -- for
    the route summary, where the operator needs enough to actually find
    it."""
    return (
        f"{short_label(farmer, plot, language=language)}, "
        f"{location_hint(cluster, plot, language=language)}"
    )


def build_harvest_scheduled_text(plot: Plot, route_position: int, *, language: str = "ta") -> str:
    return _lang_module(language).harvest_scheduled(plot.area_acres, plot.area_unit, route_position)


def build_not_ready_text(plot: Plot, *, language: str = "ta") -> str:
    return _lang_module(language).not_ready(plot.area_acres, plot.area_unit)


def build_drying_window_alert_text(*, language: str = "ta") -> str:
    """Reads agronomy/market_params.py directly at call time (not passed
    in) -- both figures are manually-set config, not per-message data,
    same reasoning as crop_params.py constants being imported directly
    rather than threaded through every caller. See ADR-009 Part 4."""
    return _lang_module(language).drying_window_alert(
        moisture=market_params.DPC_MOISTURE_THRESHOLD_PERCENT,
        msp=market_params.MSP_PADDY_COMMON_PER_QUINTAL,
    )


def send_drying_window_alert(client: TelegramClient, farmer: Farmer) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot send drying alert", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id, build_drying_window_alert_text(language=farmer.language)
    )


def build_escalation_resolved_text(
    plot: Plot,
    won: bool,
    *,
    language: str = "ta",
    other_farmer_name: str | None = None,
    reason: str | None = None,
) -> str:
    mod = _lang_module(language)
    if won:
        return mod.escalation_resolved_won(plot.area_acres, plot.area_unit)
    return mod.escalation_resolved_lost(
        plot.area_acres, plot.area_unit,
        other_farmer_name=other_farmer_name, reason=reason,
    )


def resolution_reason_text(
    loser_language: str, winner_claim: AdvocateClaim, loser_claim: AdvocateClaim
) -> str:
    """A concrete, honest, human reason for the losing side, rendered in
    *their* language -- never mentions negotiation rounds or internal
    scoring mechanics. Moved here from webhook.py's old (English-only,
    hand-authored) _resolution_reason -- found while wiring up Tamil
    support that it had no language awareness at all, which would have
    produced a mixed-language sentence for a Tamil-registered farmer. See
    ADR-008 Part 2."""
    mod = _lang_module(loser_language)
    return mod.resolution_reason(
        bumped_winner=winner_claim.bumped_last_season,
        bumped_loser=loser_claim.bumped_last_season,
        winner_days_past_maturity=winner_claim.days_past_maturity,
        loser_days_past_maturity=loser_claim.days_past_maturity,
    )


def build_operator_route_summary_text(
    cluster: Cluster, route: list[tuple[Farmer, Plot]], *, language: str = "ta"
) -> str:
    mod = _lang_module(language)
    if not route:
        return mod.route_summary_empty(cluster.name)
    stops = "\n".join(
        mod.route_stop_line(i + 1, full_label(farmer, plot, cluster, language=language))
        for i, (farmer, plot) in enumerate(route)
    )
    return f"{mod.route_summary_header(cluster.name)}\n{stops}"


def send_harvest_scheduled(
    client: TelegramClient, farmer: Farmer, plot: Plot, route_position: int
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id,
        build_harvest_scheduled_text(plot, route_position, language=farmer.language),
    )


def send_not_ready(client: TelegramClient, farmer: Farmer, plot: Plot) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id, build_not_ready_text(plot, language=farmer.language)
    )


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
            plot, won, language=farmer.language,
            other_farmer_name=other_farmer_name, reason=reason,
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
        operator_chat_id,
        build_operator_route_summary_text(cluster, route, language=cluster.operator_language),
    )


def build_confirmation_keyboard(plot_id: str, season_id: str, *, language: str = "ta") -> dict:
    """Two buttons, one tap either way -- the entire farmer-facing
    interaction for ADR-009 Part 2's confirmation loop. callback_data
    carries plot_id/season_id directly (not "today's" or "current"
    anything), so a tap on a stale message always resolves against the
    exact season it was sent for, even if the season has since rolled
    over -- see webhook.handle_confirmation_callback."""
    mod = _lang_module(language)

    def callback_data(answer: str) -> str:
        return f"confirm:{plot_id}:{season_id}:{answer}"

    return {
        "inline_keyboard": [
            [
                {"text": mod.CONFIRMATION_YES_LABEL, "callback_data": callback_data("yes")},
                {"text": mod.CONFIRMATION_NO_LABEL, "callback_data": callback_data("no")},
            ]
        ]
    }


def build_harvest_confirmation_prompt_text(plot: Plot, *, language: str = "ta") -> str:
    return _lang_module(language).harvest_confirmation_prompt(plot.area_acres, plot.area_unit)


def send_harvest_confirmation_prompt(
    client: TelegramClient, farmer: Farmer, plot: Plot, season_id: str
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot ask for confirmation", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id,
        build_harvest_confirmation_prompt_text(plot, language=farmer.language),
        reply_markup=build_confirmation_keyboard(
            plot.plot_id, season_id, language=farmer.language
        ),
    )


def build_escalation_keyboard(
    cluster_id: str,
    plot_a_id: str, farmer_a: Farmer, plot_a: Plot,
    plot_b_id: str, farmer_b: Farmer, plot_b: Plot,
    *,
    language: str = "ta",
) -> dict:
    def callback_data(chosen_plot_id: str) -> str:
        return f"resolve:{cluster_id}:{plot_a_id}:{plot_b_id}:{chosen_plot_id}"

    return {
        "inline_keyboard": [
            [
                {
                    "text": short_label(farmer_a, plot_a, language=language),
                    "callback_data": callback_data(plot_a_id),
                },
                {
                    "text": short_label(farmer_b, plot_b, language=language),
                    "callback_data": callback_data(plot_b_id),
                },
            ]
        ]
    }


def _argument_line(claim: AdvocateClaim, *, language: str) -> str | None:
    """The advocate's generated reasoning, labeled as such, one line below
    the plot's facts -- never blocks the escalation from rendering. See
    ADR-008 Decision 12: presentational only, so a claim from the
    fallback path (claim.degraded) or an empty/whitespace argument omits
    this line entirely rather than showing generic placeholder text as if
    it were real judgment. `language` selects the LABEL's language (the
    operator's, i.e. Cluster.operator_language) -- claim.argument itself
    stays in whatever language it was generated in (the owning farmer's),
    so this message is deliberately mixed-language when farmers differ."""
    if claim.degraded:
        return None
    text = claim.argument.strip()
    if not text:
        return None
    label = _lang_module(language).escalation_argument_label()
    return f'    {label} "{text}"'


def build_escalation_text(
    farmer_a: Farmer, plot_a: Plot, claim_a: AdvocateClaim,
    farmer_b: Farmer, plot_b: Plot, claim_b: AdvocateClaim,
    *,
    language: str = "ta",
) -> str:
    mod = _lang_module(language)
    bumped_a = mod.bumped_suffix() if claim_a.bumped_last_season else ""
    bumped_b = mod.bumped_suffix() if claim_b.bumped_last_season else ""

    lines = [mod.escalation_intro()]
    lines.append(
        f"- {short_label(farmer_a, plot_a, language=language)}: "
        f"{mod.overripe_phrase(claim_a.days_past_maturity)}{bumped_a}"
    )
    arg_a = _argument_line(claim_a, language=language)
    if arg_a:
        lines.append(arg_a)
    lines.append(
        f"- {short_label(farmer_b, plot_b, language=language)}: "
        f"{mod.overripe_phrase(claim_b.days_past_maturity)}{bumped_b}"
    )
    arg_b = _argument_line(claim_b, language=language)
    if arg_b:
        lines.append(arg_b)
    lines.append(mod.escalation_question())
    return "\n".join(lines)


def send_escalation(
    client: TelegramClient,
    operator_chat_id: int | None,
    cluster_id: str,
    plot_a_id: str, farmer_a: Farmer, plot_a: Plot, claim_a: AdvocateClaim,
    plot_b_id: str, farmer_b: Farmer, plot_b: Plot, claim_b: AdvocateClaim,
    *,
    operator_language: str = "ta",
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
        build_escalation_text(
            farmer_a, plot_a, claim_a, farmer_b, plot_b, claim_b,
            language=operator_language,
        ),
        reply_markup=build_escalation_keyboard(
            cluster_id, plot_a_id, farmer_a, plot_a, plot_b_id, farmer_b, plot_b,
            language=operator_language,
        ),
    )
