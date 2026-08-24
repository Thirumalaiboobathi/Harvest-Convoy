"""Farmer-facing "why?" answers -- ADR-013 Part 3. Assembled entirely
from a DecisionRecord already written at decision time (ADR-010 Part
0.5) -- never a live call, never a model, never a recomputed projection.
Not scripts/explain_decision.py's audience or its output shape: that
script is the full audit trail for an auditor with repo/AWS access;
this module returns exactly one short, already-localized answer for the
farmer who asked, reusing the same underlying data and, for the
lost-escalation case, the literal same messages_*.resolution_reason()
call the original loser message used -- not a re-derived narrative.
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.storage import Storage
from harvest_convoy.telegram import notify


def _record_or_none(storage: Storage, plot_id: str, season_id: str, decision_date: str):
    if not decision_date:
        # Gap B (ADR-013 Part 3, Decision 26): no date was ever known at
        # send time, so no button was attached -- this branch only fires
        # from a direct/defensive call, never a real Telegram tap.
        return None
    return storage.get_decision_record(plot_id, season_id, decision_date)


def _formatted_date(mod, decision_date: str) -> str | None:
    if not decision_date:
        return None
    try:
        return mod.format_date(date.fromisoformat(decision_date))
    except ValueError:
        return None


def why_not_ready_text(
    storage: Storage, plot_id: str, season_id: str, decision_date: str, *, language: str = "ta",
) -> str:
    mod = notify._lang_module(language)
    formatted_date = _formatted_date(mod, decision_date)
    record = _record_or_none(storage, plot_id, season_id, decision_date)
    if record is None or formatted_date is None:
        return mod.why_not_recorded(formatted_date)
    # Capped at 99: a TOO_GREEN record's accumulated_gdd is always below
    # maturity_gdd_used by construction (scheduling/solver.py), but a
    # rounding artifact that happens to land on 100 would read as "fully
    # grown -- but not ready," a real self-contradiction for a farmer to
    # notice. Presentation-only cap, not a change to the stored numbers.
    pct_grown = min(99, round(record.accumulated_gdd / record.maturity_gdd_used * 100))
    return mod.why_not_ready_answer(
        formatted_date, pct_grown, record.capacity_budget_acres, record.usable_harvest_days,
    )


def why_lost_text(
    storage: Storage, plot_id: str, season_id: str, decision_date: str, *, language: str = "ta",
) -> str:
    mod = notify._lang_module(language)
    formatted_date = _formatted_date(mod, decision_date)
    record = _record_or_none(storage, plot_id, season_id, decision_date)
    if (
        record is None
        or formatted_date is None
        or record.own_claim is None
        or record.opponent_claim is None
    ):
        return mod.why_not_recorded(formatted_date)
    winner_plot = storage.get_plot(record.opponent_plot_id) if record.opponent_plot_id else None
    winner_farmer = storage.get_farmer(winner_plot.farmer_id) if winner_plot is not None else None
    # None (not mod.DEFAULT_WINNER_LABEL) when no farmer record was
    # found for the winning plot -- why_lost_answer picks its own
    # correctly-formed fallback phrase for that case, same fix shape as
    # escalation_resolved_assigned/route_drop_confirm_prompt (2026-08-24).
    winner_name = winner_farmer.name if winner_farmer is not None else None
    reason = mod.resolution_reason(
        bumped_winner=record.opponent_claim["bumped_last_season"],
        bumped_loser=record.own_claim["bumped_last_season"],
        winner_days_past_maturity=record.opponent_claim["days_past_maturity"],
        loser_days_past_maturity=record.own_claim["days_past_maturity"],
    )
    return mod.why_lost_answer(formatted_date, winner_name, reason)
