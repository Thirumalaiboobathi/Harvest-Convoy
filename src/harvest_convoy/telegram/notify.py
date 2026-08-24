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
from datetime import date

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


def build_not_ready_text(
    plot: Plot, *, language: str = "ta", rain_event_classification: str = "none"
) -> str:
    """rain_event_classification (ADR-011 Part 3): "none"/"brief" render
    byte-identical to the pre-Part-3 wording; only "sustained" appends a
    clause -- reflected in the message, without making the common case
    any longer."""
    return _lang_module(language).not_ready(
        plot.area_acres, plot.area_unit, rain_event_classification=rain_event_classification,
    )


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


def build_advance_harvest_notice_text(
    plot: Plot, projected_maturity_date: date, *, language: str = "ta",
) -> str:
    mod = _lang_module(language)
    return mod.advance_harvest_notice(
        plot.area_acres, plot.area_unit, mod.format_date(projected_maturity_date),
    )


def send_advance_harvest_notice(
    client: TelegramClient, farmer: Farmer, plot: Plot, projected_maturity_date: date,
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error(
            "no chat_id for farmer %s, cannot send advance harvest notice", farmer.farmer_id
        )
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id,
        build_advance_harvest_notice_text(
            plot, projected_maturity_date, language=farmer.language,
        ),
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


def build_why_keyboard(
    prefix: str, plot_id: str, season_id: str, decision_date: str, *, language: str = "ta"
) -> dict:
    """The farmer's own one-tap "why" button (ADR-013 Part 3) -- attached
    to not_ready (prefix="why_notready") and to the loser's copy of
    escalation_resolved_lost (prefix="why_lost") only. plot_id/season_id/
    decision_date are the exact DecisionRecord key already known at send
    time; carried whole in callback_data so nothing needs to be
    re-resolved (or guessed) at tap time -- same "no in-memory state"
    shape as every other bounded reply keyboard in this module. None of
    the three fields can contain a colon, so this doesn't risk the
    colon-splitting bug ADR-013 Part 2's Undo button hit."""
    mod = _lang_module(language)
    return {
        "inline_keyboard": [[{
            "text": mod.WHY_BUTTON_LABEL,
            "callback_data": f"{prefix}:{plot_id}:{season_id}:{decision_date}",
        }]]
    }


def send_not_ready(
    client: TelegramClient,
    farmer: Farmer,
    plot: Plot,
    *,
    season_id: str,
    decision_date: str,
    rain_event_classification: str = "none",
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id,
        build_not_ready_text(
            plot, language=farmer.language, rain_event_classification=rain_event_classification,
        ),
        reply_markup=build_why_keyboard(
            "why_notready", plot.plot_id, season_id, decision_date, language=farmer.language,
        ),
    )


def send_escalation_resolved(
    client: TelegramClient,
    farmer: Farmer,
    plot: Plot,
    won: bool,
    *,
    other_farmer_name: str | None = None,
    reason: str | None = None,
    season_id: str | None = None,
    decision_date: str | None = None,
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    reply_markup = None
    if not won and season_id is not None and decision_date is not None:
        # ADR-013 Part 3, Decision 26, Gap B: decision_date is None
        # whenever the escalation's in-memory payload was lost to a
        # process restart before it was resolved -- the same case that
        # already makes this message omit its specific reason clause. No
        # button is attached rather than one that would deterministically
        # answer "not recorded" every time it's tapped.
        reply_markup = build_why_keyboard(
            "why_lost", plot.plot_id, season_id, decision_date, language=farmer.language,
        )
    return client.send_message(
        farmer.telegram_chat_id,
        build_escalation_resolved_text(
            plot, won, language=farmer.language,
            other_farmer_name=other_farmer_name, reason=reason,
        ),
        reply_markup=reply_markup,
    )


def build_route_dropped_notice_text(plot: Plot, *, language: str = "ta") -> str:
    return _lang_module(language).route_dropped_notice(plot.area_acres, plot.area_unit)


def send_route_dropped_notice(client: TelegramClient, farmer: Farmer, plot: Plot) -> SendResult:
    """Sent immediately when a confirmed drop removes this plot from
    today's route (ADR-013 Decision 5) -- the farmer's copy of a
    harvest_scheduled message is now false, and the sooner he knows the
    more time he has to adjust. Never batched behind a Done tap, unlike
    a pure reorder's position-change notice."""
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot notify of route drop", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id,
        build_route_dropped_notice_text(plot, language=farmer.language),
    )


def build_breakdown_keyboard(cluster_id: str, season_id: str, report_date: str, *, language: str = "ta") -> dict:
    """One inline button on the operator's route summary, always present
    regardless of whether the route is empty -- a stale button from an
    earlier message may still be the one tapped, and that degrade path
    (Decision 10, "a breakdown reported for a day with no route") is
    handled at the callback, not by hiding the button. See ADR-011 Part 2."""
    mod = _lang_module(language)
    return {
        "inline_keyboard": [
            [{
                "text": mod.BREAKDOWN_BUTTON_LABEL,
                "callback_data": f"breakdown:{cluster_id}:{season_id}:{report_date}",
            }]
        ]
    }


def build_breakdown_followup_keyboard(cluster_id: str, *, language: str = "ta") -> dict:
    """Optional second tap, sent as a follow-up after the first tap's
    recompute already completed -- purely additive context, never a
    prerequisite for the recompute itself. See ADR-011 Part 2."""
    mod = _lang_module(language)
    return {
        "inline_keyboard": [
            [
                {
                    "text": mod.BREAKDOWN_FOLLOWUP_TOMORROW_LABEL,
                    "callback_data": f"breakdown_followup:{cluster_id}:tomorrow",
                },
                {
                    "text": mod.BREAKDOWN_FOLLOWUP_INDEFINITE_LABEL,
                    "callback_data": f"breakdown_followup:{cluster_id}:indefinite",
                },
            ]
        ]
    }


def build_machine_back_keyboard(cluster_id: str, *, language: str = "ta") -> dict:
    """The symmetric clearing action for "down indefinitely" -- sent
    alongside the acknowledgment of that tap, so the operator has an
    obvious, persistent place to report the machine is running again."""
    mod = _lang_module(language)
    return {
        "inline_keyboard": [
            [{"text": mod.MACHINE_BACK_BUTTON_LABEL, "callback_data": f"machine_back:{cluster_id}"}]
        ]
    }


def build_route_proposal_keyboard(
    cluster_id: str, season_id: str, report_date: str, *, language: str = "ta"
) -> dict:
    """Accept/Modify row -- ADR-013. Only ever attached when the route is
    non-empty (nothing to accept or modify against an empty route); see
    send_operator_route_summary below."""
    mod = _lang_module(language)
    return {
        "inline_keyboard": [
            [
                {
                    "text": mod.ROUTE_ACCEPT_BUTTON_LABEL,
                    "callback_data": f"route_accept:{cluster_id}:{season_id}:{report_date}",
                },
                {
                    "text": mod.ROUTE_MODIFY_BUTTON_LABEL,
                    "callback_data": f"route_modify:{cluster_id}:{season_id}:{report_date}",
                },
            ]
        ]
    }


def build_route_edit_keyboard(
    cluster_id: str, season_id: str, report_date: str, route: list[tuple[Farmer, Plot]],
    *, language: str = "ta",
) -> dict:
    """The per-stop editing view opened by a Modify tap -- one row per
    stop (an up-swap button on every row but the first, a drop button on
    every row), plus a trailing Done row. Each button carries the exact
    key (cluster_id, season_id, report_date, plus a position or plot_id)
    needed to apply one atomic mutation and re-render this same keyboard
    -- no client-side state, the handler always reads the current
    RouteOverride fresh. See ADR-013 Decision 4."""
    mod = _lang_module(language)
    rows = []
    for i, (farmer, plot) in enumerate(route):
        position = i + 1
        row = []
        if position > 1:
            row.append({
                "text": mod.ROUTE_SWAP_UP_BUTTON_LABEL,
                "callback_data": f"route_swap:{cluster_id}:{season_id}:{report_date}:{position}",
            })
        row.append({
            "text": f"{position}. {short_label(farmer, plot, language=language)} {mod.ROUTE_DROP_BUTTON_LABEL}",
            "callback_data": f"route_drop:{cluster_id}:{season_id}:{report_date}:{plot.plot_id}",
        })
        rows.append(row)
    rows.append([{
        "text": mod.ROUTE_DONE_BUTTON_LABEL,
        "callback_data": f"route_done:{cluster_id}:{season_id}:{report_date}",
    }])
    return {"inline_keyboard": rows}


def build_route_drop_confirm_keyboard(
    cluster_id: str, season_id: str, report_date: str, plot_id: str, *, language: str = "ta",
) -> dict:
    mod = _lang_module(language)

    def callback_data(answer: str) -> str:
        return f"route_drop_confirm:{cluster_id}:{season_id}:{report_date}:{plot_id}:{answer}"

    return {
        "inline_keyboard": [
            [
                {"text": mod.ROUTE_DROP_CONFIRM_YES_LABEL, "callback_data": callback_data("yes")},
                {"text": mod.ROUTE_DROP_CONFIRM_NO_LABEL, "callback_data": callback_data("no")},
            ]
        ]
    }


def build_route_edit_text(
    cluster: Cluster, route: list[tuple[Farmer, Plot]], *, language: str = "ta"
) -> str:
    mod = _lang_module(language)
    stops = "\n".join(
        mod.route_stop_line(i + 1, short_label(farmer, plot, language=language))
        for i, (farmer, plot) in enumerate(route)
    )
    return f"{mod.route_edit_header(cluster.name)}\n{stops}"


def build_route_summary_keyboard(
    cluster_id: str, season_id: str, report_date: str, route: list[tuple[Farmer, Plot]],
    *, language: str = "ta",
) -> dict:
    """The full keyboard for the summary-view state -- Accept/Modify
    (only for a non-empty route) plus the "machine down today" button
    (always present, per build_breakdown_keyboard's own docstring).
    Shared by send_operator_route_summary (a fresh send) and
    webhook.handle_route_done_callback (editing back from the per-stop
    view), so the two states render identically either way."""
    rows: list[list[dict]] = []
    if route:
        rows.extend(
            build_route_proposal_keyboard(
                cluster_id, season_id, report_date, language=language
            )["inline_keyboard"]
        )
    rows.extend(
        build_breakdown_keyboard(
            cluster_id, season_id, report_date, language=language
        )["inline_keyboard"]
    )
    return {"inline_keyboard": rows}


def send_operator_route_summary(
    client: TelegramClient,
    operator_chat_id: int | None,
    cluster: Cluster,
    route: list[tuple[Farmer, Plot]],
    *,
    season_id: str | None = None,
    report_date: str | None = None,
) -> SendResult:
    """season_id/report_date are optional so every existing call site
    (and every existing test) keeps working unchanged -- when both are
    given, the message gets the "machine down today" button (ADR-011
    Part 2) plus, for a non-empty route, Accept/Modify (ADR-013); when
    either is omitted, it renders exactly as before, no keyboard at
    all."""
    if operator_chat_id is None:
        logger.error(
            "cluster %s has no operator configured; route summary not sent",
            cluster.name,
        )
        return SendResult(success=False, error="no operator configured for cluster")
    reply_markup = None
    if season_id is not None and report_date is not None:
        reply_markup = build_route_summary_keyboard(
            cluster.cluster_id, season_id, report_date, route, language=cluster.operator_language
        )
    return client.send_message(
        operator_chat_id,
        build_operator_route_summary_text(cluster, route, language=cluster.operator_language),
        reply_markup=reply_markup,
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


def build_season_rollover_keyboard(plot_id: str, new_season_id: str, *, language: str = "ta") -> dict:
    """Same two-buttons-one-tap shape as build_confirmation_keyboard,
    reusing the identical Yes/No labels -- callback_data carries
    plot_id/new_season_id directly so a tap always resolves against the
    exact rollover it was sent for. See ADR-011 Part 1."""
    mod = _lang_module(language)

    def callback_data(answer: str) -> str:
        return f"rollover:{plot_id}:{new_season_id}:{answer}"

    return {
        "inline_keyboard": [
            [
                {"text": mod.CONFIRMATION_YES_LABEL, "callback_data": callback_data("yes")},
                {"text": mod.CONFIRMATION_NO_LABEL, "callback_data": callback_data("no")},
            ]
        ]
    }


def build_season_rollover_prompt_text(*, language: str = "ta") -> str:
    return _lang_module(language).season_rollover_prompt()


def send_season_rollover_prompt(
    client: TelegramClient, farmer: Farmer, plot: Plot, new_season_id: str
) -> SendResult:
    if farmer.telegram_chat_id is None:
        logger.error("no chat_id for farmer %s, cannot ask about rollover", farmer.farmer_id)
        return SendResult(success=False, error="farmer has no telegram_chat_id")
    return client.send_message(
        farmer.telegram_chat_id,
        build_season_rollover_prompt_text(language=farmer.language),
        reply_markup=build_season_rollover_keyboard(
            plot.plot_id, new_season_id, language=farmer.language
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
