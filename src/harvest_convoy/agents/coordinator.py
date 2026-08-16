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
from dataclasses import dataclass
from typing import Callable

from harvest_convoy.agents.advocate import get_advocate_claim
from harvest_convoy.agents.contracts import (
    AdvocateClaim,
    EscalationPayload,
    PlotFacts,
    classify_rain_vulnerability,
)
from harvest_convoy.agents.fairness_stub import was_bumped_last_season
from harvest_convoy.models import Plot
from harvest_convoy.observability.otel import get_tracer
from harvest_convoy.scheduling.solver import PlotDecision, PlotOutcome

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)

MAX_NEGOTIATION_ROUNDS = 3
# Negotiation tuning, not an agronomy constant -- arbitrary, documented
# design choices for the score comparison, not sourced from anywhere.
FAIRNESS_WEIGHT = 0.3  # added to urgency_score when comparing bumped vs non-bumped
CLEAR_MARGIN = 0.15  # score gap above which a round resolves outright

ClaimProvider = Callable[[PlotFacts, int, str | None], AdvocateClaim]


def build_plot_facts(plot: Plot, decision: PlotDecision) -> PlotFacts:
    is_ready = decision.outcome != PlotOutcome.TOO_GREEN
    return PlotFacts(
        plot_id=plot.plot_id,
        farmer_id=plot.farmer_id,
        is_ready=is_ready,
        days_past_maturity=decision.days_past_maturity or 0,
        urgency=decision.urgency,
        rain_vulnerability=classify_rain_vulnerability(is_ready, decision.urgency),
        acres=plot.area_acres,
        bumped_last_season=was_bumped_last_season(plot.farmer_id),
    )


@dataclass(frozen=True)
class NegotiationResult:
    winner_plot_id: str | None  # None => escalated
    rounds_used: int
    claim_a: AdvocateClaim
    claim_b: AdvocateClaim
    escalated: bool


def _score(claim: AdvocateClaim) -> float:
    return claim.urgency_score + (FAIRNESS_WEIGHT if claim.bumped_last_season else 0.0)


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
    """
    claim_a = get_claim(facts_a, 1, None)
    claim_b = get_claim(facts_b, 1, None)

    for round_num in range(1, max_rounds + 1):
        if claim_a.concedes and not claim_b.concedes:
            return NegotiationResult(facts_b.plot_id, round_num, claim_a, claim_b, False)
        if claim_b.concedes and not claim_a.concedes:
            return NegotiationResult(facts_a.plot_id, round_num, claim_a, claim_b, False)
        if claim_a.concedes and claim_b.concedes:
            winner = facts_a.plot_id if facts_a.urgency >= facts_b.urgency else facts_b.plot_id
            return NegotiationResult(winner, round_num, claim_a, claim_b, False)

        score_a, score_b = _score(claim_a), _score(claim_b)
        if abs(score_a - score_b) > CLEAR_MARGIN:
            winner = facts_a.plot_id if score_a > score_b else facts_b.plot_id
            return NegotiationResult(winner, round_num, claim_a, claim_b, False)

        if round_num == max_rounds:
            break

        if score_a >= score_b:
            claim_b = get_claim(facts_b, round_num + 1, claim_a.argument)
        else:
            claim_a = get_claim(facts_a, round_num + 1, claim_b.argument)

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


def run_cluster(
    plots: list[Plot],
    decisions: list[PlotDecision],
    cluster_id: str,
    *,
    model=None,
) -> ClusterResult:
    """Production entrypoint: calls the real advocate agents."""

    def default_get_claim(facts, round_num, opponent_argument):
        return get_advocate_claim(
            facts, model=model, round_num=round_num, opponent_argument=opponent_argument
        )

    return run_cluster_with_claims(plots, decisions, cluster_id, default_get_claim)


def run_cluster_with_claims(
    plots: list[Plot],
    decisions: list[PlotDecision],
    cluster_id: str,
    get_claim: ClaimProvider,
) -> ClusterResult:
    """Same orchestration, with an injectable claim provider -- used for
    deterministic tests and by run_cluster() for the real agent path."""
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
            facts = build_plot_facts(plots_by_id[d.plot_id], d)
            claim = get_claim(facts, 1, None)
            outcomes.append(CoordinatorOutcome(d.plot_id, d.outcome, claim))

        for i in range(0, len(contested) - 1, 2):
            d_a, d_b = contested[i], contested[i + 1]
            facts_a = build_plot_facts(plots_by_id[d_a.plot_id], d_a)
            facts_b = build_plot_facts(plots_by_id[d_b.plot_id], d_b)
            result = negotiate_pair(facts_a, facts_b, get_claim)

            outcomes.append(CoordinatorOutcome(facts_a.plot_id, PlotOutcome.CONTESTED, result.claim_a))
            outcomes.append(CoordinatorOutcome(facts_b.plot_id, PlotOutcome.CONTESTED, result.claim_b))

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
                    )
                )
            else:
                loser_id = (
                    facts_b.plot_id
                    if result.winner_plot_id == facts_a.plot_id
                    else facts_a.plot_id
                )
                resolved_negotiations.append((result.winner_plot_id, loser_id))

        if len(contested) % 2 == 1:
            d = contested[-1]
            facts = build_plot_facts(plots_by_id[d.plot_id], d)
            claim = get_claim(facts, 1, None)
            outcomes.append(CoordinatorOutcome(d.plot_id, PlotOutcome.CONTESTED, claim))

        return ClusterResult(
            outcomes=sorted(outcomes, key=lambda o: o.plot_id),
            resolved_negotiations=resolved_negotiations,
            escalations=escalations,
        )
