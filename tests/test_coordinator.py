"""Coordinator orchestration tests, using an injected deterministic claim
provider (same dependency-injection pattern as the Phase 1 weather-bridging
tests) rather than faking Strands' Model protocol -- Bedrock already works
live (see ADR-003), so there's no need for a hand-rolled stub to stand in
as "the real path." This tests our own negotiation/escalation logic fast
and deterministically.
"""

from __future__ import annotations

from harvest_convoy.agents.contracts import AdvocateClaim, PlotFacts
from harvest_convoy.agents.coordinator import negotiate_pair, run_cluster_with_claims
from harvest_convoy.models import Plot
from harvest_convoy.scheduling.solver import PlotDecision, PlotOutcome


def _facts(plot_id: str, **overrides) -> PlotFacts:
    base = dict(
        plot_id=plot_id,
        farmer_id=f"farmer-{plot_id}",
        is_ready=True,
        days_past_maturity=5,
        urgency=0.25,
        rain_vulnerability="low",
        acres=2.0,
        bumped_last_season=False,
    )
    base.update(overrides)
    return PlotFacts(**base)


def _claim(facts: PlotFacts, *, argument: str, concedes: bool, **overrides) -> AdvocateClaim:
    data = dict(
        plot_id=facts.plot_id,
        urgency_score=facts.urgency,
        days_past_maturity=facts.days_past_maturity,
        rain_vulnerability=facts.rain_vulnerability,
        acres=facts.acres,
        bumped_last_season=facts.bumped_last_season,
        argument=argument,
        concedes=concedes,
    )
    data.update(overrides)
    return AdvocateClaim(**data)


def test_negotiate_pair_resolves_immediately_when_one_side_concedes() -> None:
    a = _facts("a", urgency=0.1)
    b = _facts("b", urgency=0.8)

    def get_claim(facts, round_num, opponent_argument):
        concedes = facts.plot_id == "a"
        return _claim(facts, argument="claim", concedes=concedes)

    result = negotiate_pair(a, b, get_claim)

    assert result.escalated is False
    assert result.winner_plot_id == "b"
    assert result.rounds_used == 1


def test_negotiate_pair_resolves_on_clear_score_margin_without_full_rounds() -> None:
    a = _facts("a", urgency=0.1)
    b = _facts("b", urgency=0.9)

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="claim", concedes=False)

    result = negotiate_pair(a, b, get_claim)

    assert result.escalated is False
    assert result.winner_plot_id == "b"
    assert result.rounds_used == 1


def test_negotiate_pair_escalates_after_max_rounds_when_genuinely_tied() -> None:
    a = _facts("a", urgency=0.5)
    b = _facts("b", urgency=0.5)

    def get_claim(facts, round_num, opponent_argument):
        # Perpetually tied, neither concedes -- the genuine-ambiguity case.
        return _claim(facts, argument=f"round {round_num}", concedes=False)

    result = negotiate_pair(a, b, get_claim, max_rounds=3)

    assert result.escalated is True
    assert result.winner_plot_id is None
    assert result.rounds_used == 3


def test_negotiate_pair_reargue_uses_fairness_to_break_a_tie() -> None:
    a = _facts("a", urgency=0.5, bumped_last_season=False)
    b = _facts("b", urgency=0.5, bumped_last_season=True)

    calls: list[tuple[str, int]] = []

    def get_claim(facts, round_num, opponent_argument):
        calls.append((facts.plot_id, round_num))
        return _claim(
            facts,
            argument="claim",
            concedes=False,
            bumped_last_season=facts.bumped_last_season,
        )

    result = negotiate_pair(a, b, get_claim, max_rounds=3)

    # b's fairness weight (bumped_last_season) breaks the urgency tie.
    assert result.escalated is False
    assert result.winner_plot_id == "b"


def _plot(plot_id: str, area_acres: float = 1.0) -> Plot:
    from datetime import date

    return Plot(
        plot_id=plot_id,
        farmer_id=f"farmer-{plot_id}",
        cluster_id="c",
        lat=10.0,
        lon=77.5,
        crop="paddy",
        variety="ADT45",
        transplant_date=date(2026, 5, 1),
        area_acres=area_acres,
    )


def _decision(plot_id: str, outcome: PlotOutcome, days_past_maturity=None, urgency=0.0):
    return PlotDecision(
        plot_id=plot_id,
        outcome=outcome,
        accumulated_gdd=2000.0,
        days_past_maturity=days_past_maturity,
        urgency=urgency,
        route_position=0 if outcome == PlotOutcome.FITS else None,
    )


def test_run_cluster_too_green_plots_all_concede() -> None:
    plots = [_plot("p05"), _plot("p06")]
    decisions = [
        _decision("p05", PlotOutcome.TOO_GREEN),
        _decision("p06", PlotOutcome.TOO_GREEN),
    ]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="not ready", concedes=not facts.is_ready)

    result = run_cluster_with_claims(plots, decisions, "c", get_claim)

    assert len(result.outcomes) == 2
    assert all(o.claim.concedes for o in result.outcomes)
    assert result.escalations == []


def test_run_cluster_produces_exactly_one_escalation_for_a_tied_contested_pair() -> None:
    plots = [_plot("p01"), _plot("p05"), _plot("p03"), _plot("p04")]
    decisions = [
        _decision("p01", PlotOutcome.FITS, days_past_maturity=17, urgency=0.85),
        _decision("p05", PlotOutcome.TOO_GREEN),
        _decision("p03", PlotOutcome.CONTESTED, days_past_maturity=6, urgency=0.3),
        _decision("p04", PlotOutcome.CONTESTED, days_past_maturity=0, urgency=0.0),
    ]

    def get_claim(facts, round_num, opponent_argument):
        if not facts.is_ready:
            return _claim(facts, argument="not ready", concedes=True)
        if facts.plot_id in ("p03", "p04"):
            # Tied regardless of round -- forces escalation.
            return _claim(facts, argument=f"round {round_num}", concedes=False, urgency_score=0.5)
        return _claim(facts, argument="fits", concedes=False)

    result = run_cluster_with_claims(plots, decisions, "kamatchipuram", get_claim)

    assert len(result.escalations) == 1
    escalation = result.escalations[0]
    assert {escalation.plot_a_id, escalation.plot_b_id} == {"p03", "p04"}
    assert escalation.rounds_run == 3

    too_green_outcomes = [o for o in result.outcomes if o.plot_id == "p05"]
    assert too_green_outcomes[0].claim.concedes is True
