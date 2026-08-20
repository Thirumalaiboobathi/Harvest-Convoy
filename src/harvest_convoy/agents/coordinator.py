"""Coordinator: owns the machine calendar for one weather trigger.

Calls one advocate per plot (every plot gets a claim, including TOO_GREEN
ones -- their concession is a first-class outcome, not skipped), then
negotiates Phase 2's CONTESTED plots pairwise, capped at MAX_NEGOTIATION_ROUNDS.
A pair that doesn't resolve within the cap escalates -- that's the intended
outcome for a genuine conflict, not a failure mode. See ADR-003.

Negotiation determines *priority* between two closely-matched contested
plots (who gets the next slot if capacity frees up) -- it does not
manufacture capacity Phase 2's budget didn't allocate. FITS/CONTESTED/
TOO_GREEN outcomes from the solver are never rewritten here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Callable

from harvest_convoy.agents.advocate import get_advocate_claim
from harvest_convoy.agents.contracts import (
    AdvocateClaim,
    EscalationPayload,
    PlotFacts,
    TriggerContext,
    classify_rain_vulnerability,
)
from harvest_convoy.agronomy import crop_params
from harvest_convoy.models import Plot
from harvest_convoy.observability.otel import get_tracer
from harvest_convoy.scheduling.solver import PlotDecision, PlotOutcome
from harvest_convoy.storage import Storage
from harvest_convoy.storage.fairness import was_bumped_last_season, weighted_bump_days
from harvest_convoy.storage.interface import DecisionRecord

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)

MAX_NEGOTIATION_ROUNDS = 3
CLEAR_MARGIN = 0.15  # score gap above which a round resolves outright.
# Negotiation tuning, not an agronomy constant -- arbitrary, documented
# design choice, not sourced from anywhere.

# Fairness weighting -- see ADR-005 Decision 4 for the full derivation.
#
# Urgency scale: AdvocateClaim.urgency_score is in [0.0, 1.0], produced by
# agronomy/decay.py's decay_fraction() -- min(1.0, days_past_maturity /
# DECAY_HORIZON_DAYS_ESTIMATED) for an INTEGER days_past_maturity. That's a
# discrete step function, not a continuum: its granularity (the smallest
# gap between "maximally urgent" and the next value below it) is
# 1 / DECAY_HORIZON_DAYS_ESTIMATED.
#
# INVARIANT: fairness must be able to tilt a close call, but a
# less-than-maximally-urgent plot must never outscore a maximally urgent
# one on fairness alone. MAX_FAIRNESS_BONUS is therefore derived from that
# granularity, not picked independently -- so it stays correct even if
# DECAY_HORIZON_DAYS_ESTIMATED changes later. See test_coordinator.py for
# the behavioral assertion of this invariant, not just this assert.
_URGENCY_GRANULARITY = 1.0 / crop_params.DECAY_HORIZON_DAYS_ESTIMATED  # 0.05 today
MAX_FAIRNESS_BONUS = _URGENCY_GRANULARITY * 0.8  # comfortable margin below it
assert MAX_FAIRNESS_BONUS < _URGENCY_GRANULARITY, (
    "MAX_FAIRNESS_BONUS must stay below the urgency scale's granularity, "
    "or a maximally urgent plot could lose a negotiation to fairness alone"
)

FAIRNESS_WEIGHT_PER_BUMPED_DAY = 0.01  # DERIVED, tuning constant, not sourced

ClaimProvider = Callable[[PlotFacts, int, str | None], AdvocateClaim]


def build_plot_facts(plot: Plot, decision: PlotDecision, storage: Storage) -> PlotFacts:
    is_ready = decision.outcome != PlotOutcome.TOO_GREEN
    farmer = storage.get_farmer(plot.farmer_id)
    language = farmer.language if farmer is not None else "ta"
    return PlotFacts(
        plot_id=plot.plot_id,
        farmer_id=plot.farmer_id,
        is_ready=is_ready,
        days_past_maturity=decision.days_past_maturity or 0,
        urgency=decision.urgency,
        rain_vulnerability=classify_rain_vulnerability(is_ready, decision.urgency),
        acres=plot.area_acres,
        bumped_last_season=was_bumped_last_season(plot.farmer_id, storage),
        weighted_bump_days=weighted_bump_days(plot.farmer_id, storage),
        language=language,
    )


@dataclass(frozen=True)
class NegotiationResult:
    winner_plot_id: str | None  # None => escalated
    rounds_used: int
    claim_a: AdvocateClaim
    claim_b: AdvocateClaim
    escalated: bool


def _fairness_bonus(claim: AdvocateClaim) -> float:
    return min(MAX_FAIRNESS_BONUS, claim.weighted_bump_days * FAIRNESS_WEIGHT_PER_BUMPED_DAY)


def _score(claim: AdvocateClaim) -> float:
    return claim.urgency_score + _fairness_bonus(claim)


def _fairness_was_decisive(
    claim_a: AdvocateClaim, claim_b: AdvocateClaim,
    plot_a_id: str, plot_b_id: str, actual_winner: str,
) -> bool | None:
    """None if the round was resolved by a concession (a model judgment,
    not a score comparison at all) -- otherwise: would the raw
    urgency_score alone (no fairness bonus) have picked a different
    winner than the actual (bonus-included) score did? Pure arithmetic
    over two already-computed claims, reusing this module's own
    _score()/_fairness_bonus() -- a post-hoc check of the comparison that
    already ran, not a new source of truth. See ADR-010 Part 0.5."""
    if claim_a.concedes or claim_b.concedes:
        return None
    scored_winner = plot_a_id if _score(claim_a) > _score(claim_b) else plot_b_id
    if actual_winner != scored_winner:
        # Shouldn't arise given how negotiate_pair() derives its winner --
        # an explicit None instead of a confidently wrong True/False if it
        # ever does.
        return None
    raw_winner = plot_a_id if claim_a.urgency_score >= claim_b.urgency_score else plot_b_id
    return raw_winner != scored_winner


def negotiate_pair(
    facts_a: PlotFacts,
    facts_b: PlotFacts,
    get_claim: ClaimProvider,
    max_rounds: int = MAX_NEGOTIATION_ROUNDS,
) -> NegotiationResult:
    """Pairwise negotiation between two contested plots. Resolves as soon
    as either side concedes or the scores clearly separate; otherwise the
    losing side (by raw score) gets one re-argue per round with the
    fairness ledger surfaced. No resolution after max_rounds -> escalate.

    Traced explicitly, not left to fall out as flat advocate.get_claim
    siblings under coordinator.run_cluster: one span for the whole
    negotiation, one nested span per round, so a trigger -> negotiation ->
    escalation run reads as a real nested story rather than an
    undifferentiated list of calls. Checked this by inspecting real
    exported spans before adding it -- without this, all of a run's
    advocate calls land as siblings with no round or pairing structure
    visible.
    """
    with tracer.start_as_current_span(
        "coordinator.negotiate",
        attributes={"plot_a": facts_a.plot_id, "plot_b": facts_b.plot_id},
    ) as negotiation_span:
        with tracer.start_as_current_span("negotiation.round", attributes={"round": 1}):
            claim_a = get_claim(facts_a, 1, None)
            claim_b = get_claim(facts_b, 1, None)

        for round_num in range(1, max_rounds + 1):
            if claim_a.concedes and not claim_b.concedes:
                negotiation_span.set_attribute("outcome", "resolved")
                negotiation_span.set_attribute("winner", facts_b.plot_id)
                return NegotiationResult(facts_b.plot_id, round_num, claim_a, claim_b, False)
            if claim_b.concedes and not claim_a.concedes:
                negotiation_span.set_attribute("outcome", "resolved")
                negotiation_span.set_attribute("winner", facts_a.plot_id)
                return NegotiationResult(facts_a.plot_id, round_num, claim_a, claim_b, False)
            if claim_a.concedes and claim_b.concedes:
                winner = facts_a.plot_id if facts_a.urgency >= facts_b.urgency else facts_b.plot_id
                negotiation_span.set_attribute("outcome", "resolved")
                negotiation_span.set_attribute("winner", winner)
                return NegotiationResult(winner, round_num, claim_a, claim_b, False)

            score_a, score_b = _score(claim_a), _score(claim_b)
            if abs(score_a - score_b) > CLEAR_MARGIN:
                winner = facts_a.plot_id if score_a > score_b else facts_b.plot_id
                negotiation_span.set_attribute("outcome", "resolved")
                negotiation_span.set_attribute("winner", winner)
                return NegotiationResult(winner, round_num, claim_a, claim_b, False)

            if round_num == max_rounds:
                break

            with tracer.start_as_current_span(
                "negotiation.round", attributes={"round": round_num + 1}
            ):
                if score_a >= score_b:
                    claim_b = get_claim(facts_b, round_num + 1, claim_a.argument)
                else:
                    claim_a = get_claim(facts_a, round_num + 1, claim_b.argument)

        negotiation_span.set_attribute("outcome", "escalated")
        return NegotiationResult(None, max_rounds, claim_a, claim_b, True)


@dataclass(frozen=True)
class CoordinatorOutcome:
    plot_id: str
    outcome: PlotOutcome  # unchanged from Phase 2's solver
    claim: AdvocateClaim


@dataclass(frozen=True)
class ClusterResult:
    outcomes: list[CoordinatorOutcome]
    resolved_negotiations: list[tuple[str, str]]  # (winner_plot_id, loser_plot_id)
    escalations: list[EscalationPayload]


def _base_decision_record(
    d: PlotDecision, farmer_id: str, cluster_id: str, season_id: str,
    trigger_context: TriggerContext,
) -> DecisionRecord:
    """The deterministic-core fields every DecisionRecord shares,
    regardless of outcome -- negotiation fields are added by the caller
    for a contested plot. See ADR-010 Part 0.5."""
    return DecisionRecord(
        plot_id=d.plot_id, farmer_id=farmer_id, cluster_id=cluster_id,
        season_id=season_id, decision_date=trigger_context.decision_date,
        accumulated_gdd=d.accumulated_gdd,
        maturity_gdd_used=trigger_context.maturity_gdd_used,
        threshold_source=trigger_context.threshold_source,
        outcome=d.outcome.value,
        days_past_maturity=d.days_past_maturity,
        urgency=d.urgency,
        route_position=d.route_position,
        rain_threshold_mm=trigger_context.rain_threshold_mm,
        forecast_horizon_days=trigger_context.forecast_horizon_days,
        usable_harvest_days=trigger_context.usable_harvest_days,
        machine_capacity_acres_per_day=trigger_context.machine_capacity_acres_per_day,
        capacity_budget_acres=trigger_context.capacity_budget_acres,
        trigger_reason=trigger_context.trigger_reason,
    )


def _write_decision_record(storage: Storage, record: DecisionRecord) -> None:
    """Never blocks or crashes the run -- same discipline ADR-009 Part 1.5
    established for mark_plot_harvested. A lost DecisionRecord degrades
    one plot's future auditability, not the scheduling run it's part of."""
    result = storage.put_decision_record(record)
    if not result.success:
        logger.error(
            "DECISION RECORD WRITE FAILED: plot=%s cluster=%s season=%s "
            "decision_date=%s -- not persisted, this decision will not be "
            "replayable later: %s",
            record.plot_id, record.cluster_id, record.season_id,
            record.decision_date, result.error,
        )


def run_cluster(
    plots: list[Plot],
    decisions: list[PlotDecision],
    cluster_id: str,
    storage: Storage,
    season_id: str,
    today: date,
    trigger_context: TriggerContext,
    *,
    model=None,
) -> ClusterResult:
    """Production entrypoint: calls the real advocate agents."""

    def default_get_claim(facts, round_num, opponent_argument):
        return get_advocate_claim(
            facts, model=model, storage=storage,
            round_num=round_num, opponent_argument=opponent_argument,
        )

    return run_cluster_with_claims(
        plots, decisions, cluster_id, storage, season_id, today,
        default_get_claim, trigger_context,
    )


def run_cluster_with_claims(
    plots: list[Plot],
    decisions: list[PlotDecision],
    cluster_id: str,
    storage: Storage,
    season_id: str,
    today: date,
    get_claim: ClaimProvider,
    trigger_context: TriggerContext,
) -> ClusterResult:
    """Same orchestration, with an injectable claim provider -- used for
    deterministic tests and by run_cluster() for the real agent path.

    season_id/today (ADR-009 Part 1.5): needed to mark a FITS plot
    harvested at the point it's dispatched -- see the loop below.
    trigger_context (ADR-010 Part 0.5): the weather/capacity/threshold
    context this trigger day resolved, needed to persist a DecisionRecord
    per plot. All three are already resolved by watcher.py before it
    calls in here, so this is a threading change, not a new dependency.
    """
    plots_by_id = {p.plot_id: p for p in plots}

    with tracer.start_as_current_span(
        "coordinator.run_cluster", attributes={"cluster_id": cluster_id}
    ):
        outcomes: list[CoordinatorOutcome] = []
        resolved_negotiations: list[tuple[str, str]] = []
        escalations: list[EscalationPayload] = []

        too_green = [d for d in decisions if d.outcome == PlotOutcome.TOO_GREEN]
        fits = [d for d in decisions if d.outcome == PlotOutcome.FITS]
        contested = sorted(
            (d for d in decisions if d.outcome == PlotOutcome.CONTESTED),
            key=lambda d: d.plot_id,
        )

        for d in too_green + fits:
            facts = build_plot_facts(plots_by_id[d.plot_id], d, storage)
            claim = get_claim(facts, 1, None)
            outcomes.append(CoordinatorOutcome(d.plot_id, d.outcome, claim))

            now_iso = datetime.now(timezone.utc).isoformat()
            _write_decision_record(
                storage,
                replace(
                    _base_decision_record(d, facts.farmer_id, cluster_id, season_id, trigger_context),
                    resolved_at=now_iso,
                ),
            )

            if d.outcome == PlotOutcome.FITS:
                # The coordinator dispatching a plot IS the harvested
                # signal -- ADR-009 Part 1.5. Deliberately not coupled to
                # Part 2's farmer confirmation, which is a separate,
                # sometimes-absent signal that arrives later, if at all.
                mark_result = storage.mark_plot_harvested(
                    d.plot_id, cluster_id, season_id, dispatched_at=today.isoformat()
                )
                if not mark_result.success:
                    logger.error(
                        "HARVEST STATE WRITE FAILED: plot=%s cluster=%s "
                        "season=%s -- not persisted, so this plot remains "
                        "eligible to be reassessed and re-notified on the "
                        "next trigger day: %s",
                        d.plot_id, cluster_id, season_id, mark_result.error,
                    )

        for i in range(0, len(contested) - 1, 2):
            d_a, d_b = contested[i], contested[i + 1]
            facts_a = build_plot_facts(plots_by_id[d_a.plot_id], d_a, storage)
            facts_b = build_plot_facts(plots_by_id[d_b.plot_id], d_b, storage)
            result = negotiate_pair(facts_a, facts_b, get_claim)

            outcomes.append(CoordinatorOutcome(facts_a.plot_id, PlotOutcome.CONTESTED, result.claim_a))
            outcomes.append(CoordinatorOutcome(facts_b.plot_id, PlotOutcome.CONTESTED, result.claim_b))

            record_a = replace(
                _base_decision_record(d_a, facts_a.farmer_id, cluster_id, season_id, trigger_context),
                opponent_plot_id=facts_b.plot_id,
                own_claim=result.claim_a.model_dump(),
                opponent_claim=result.claim_b.model_dump(),
                rounds_run=result.rounds_used,
            )
            record_b = replace(
                _base_decision_record(d_b, facts_b.farmer_id, cluster_id, season_id, trigger_context),
                opponent_plot_id=facts_a.plot_id,
                own_claim=result.claim_b.model_dump(),
                opponent_claim=result.claim_a.model_dump(),
                rounds_run=result.rounds_used,
            )

            if result.escalated:
                logger.info(
                    "escalating %s vs %s after %d rounds",
                    facts_a.plot_id, facts_b.plot_id, result.rounds_used,
                )
                escalations.append(
                    EscalationPayload(
                        cluster_id=cluster_id,
                        plot_a_id=facts_a.plot_id,
                        plot_b_id=facts_b.plot_id,
                        claim_a=result.claim_a,
                        claim_b=result.claim_b,
                        rounds_run=result.rounds_used,
                        reason="No clear resolution after max negotiation rounds.",
                        decision_date=trigger_context.decision_date,
                    )
                )
                # resolution="escalated", resolved_at=None -- webhook.py
                # updates both records once a human resolves it. See
                # ADR-010 Part 0.5 Decision D.
                _write_decision_record(storage, replace(record_a, resolution="escalated"))
                _write_decision_record(storage, replace(record_b, resolution="escalated"))
            else:
                loser_id = (
                    facts_b.plot_id
                    if result.winner_plot_id == facts_a.plot_id
                    else facts_a.plot_id
                )
                resolved_negotiations.append((result.winner_plot_id, loser_id))

                fairness_decisive = _fairness_was_decisive(
                    result.claim_a, result.claim_b,
                    facts_a.plot_id, facts_b.plot_id, result.winner_plot_id,
                )
                now_iso = datetime.now(timezone.utc).isoformat()
                _write_decision_record(
                    storage,
                    replace(
                        record_a,
                        resolution="won" if result.winner_plot_id == facts_a.plot_id else "lost",
                        fairness_decisive=fairness_decisive,
                        resolved_at=now_iso,
                    ),
                )
                _write_decision_record(
                    storage,
                    replace(
                        record_b,
                        resolution="won" if result.winner_plot_id == facts_b.plot_id else "lost",
                        fairness_decisive=fairness_decisive,
                        resolved_at=now_iso,
                    ),
                )

        if len(contested) % 2 == 1:
            d = contested[-1]
            facts = build_plot_facts(plots_by_id[d.plot_id], d, storage)
            claim = get_claim(facts, 1, None)
            outcomes.append(CoordinatorOutcome(d.plot_id, PlotOutcome.CONTESTED, claim))

            now_iso = datetime.now(timezone.utc).isoformat()
            _write_decision_record(
                storage,
                replace(
                    _base_decision_record(d, facts.farmer_id, cluster_id, season_id, trigger_context),
                    resolution="contested_no_partner_this_round",
                    resolved_at=now_iso,
                ),
            )

        return ClusterResult(
            outcomes=sorted(outcomes, key=lambda o: o.plot_id),
            resolved_negotiations=resolved_negotiations,
            escalations=escalations,
        )
