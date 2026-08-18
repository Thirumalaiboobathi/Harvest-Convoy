"""Coordinator orchestration tests, using an injected deterministic claim
provider (same dependency-injection pattern as the Phase 1 weather-bridging
tests) rather than faking Strands' Model protocol -- Bedrock already works
live (see ADR-003), so there's no need for a hand-rolled stub to stand in
as "the real path." This tests our own negotiation/escalation logic fast
and deterministically.
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.agents.contracts import AdvocateClaim, PlotFacts
from harvest_convoy.agents.coordinator import (
    CLEAR_MARGIN,
    MAX_FAIRNESS_BONUS,
    build_plot_facts,
    negotiate_pair,
    run_cluster_with_claims,
)
from harvest_convoy.models import Farmer, Plot
from harvest_convoy.scheduling.solver import PlotDecision, PlotOutcome
from harvest_convoy.storage.file_storage import FileStorage
from harvest_convoy.storage.interface import StorageResult


TEST_SEASON_ID = "2026-kuruvai"
TEST_TODAY = date(2026, 8, 16)


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


def _language_test_plot(plot_id: str, farmer_id: str) -> Plot:
    return Plot(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id="c",
        lat=9.865, lon=77.454, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=2.0,
    )


def _language_test_decision(plot_id: str) -> PlotDecision:
    return PlotDecision(
        plot_id=plot_id, outcome=PlotOutcome.FITS, accumulated_gdd=2000.0,
        days_past_maturity=5, urgency=0.25, route_position=None,
    )


def test_build_plot_facts_pulls_the_farmers_registered_language(tmp_path) -> None:
    """ADR-008 Decision 12: PlotFacts.language comes from the plot's own
    farmer's stored registration, so advocate.py can generate `argument`
    natively in that language."""
    storage = FileStorage(tmp_path / "s.json")
    storage.put_farmer(Farmer(farmer_id="f-en", name="X", cluster_id="c", language="en"))
    storage.put_farmer(Farmer(farmer_id="f-ta", name="Y", cluster_id="c", language="ta"))

    facts_en = build_plot_facts(
        _language_test_plot("p-en", "f-en"), _language_test_decision("p-en"), storage
    )
    facts_ta = build_plot_facts(
        _language_test_plot("p-ta", "f-ta"), _language_test_decision("p-ta"), storage
    )

    assert facts_en.language == "en"
    assert facts_ta.language == "ta"


def test_build_plot_facts_defaults_to_tamil_when_farmer_not_found(tmp_path) -> None:
    storage = FileStorage(tmp_path / "s.json")  # farmer never seeded
    facts = build_plot_facts(
        _language_test_plot("p1", "no-such-farmer"), _language_test_decision("p1"), storage
    )
    assert facts.language == "ta"


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


def test_negotiate_pair_fairness_tilts_a_close_call_but_only_a_close_one() -> None:
    # a is raw-urgency ahead of b by 0.12 -- under CLEAR_MARGIN (0.15) on
    # its own, so without fairness this would need extra rounds rather
    # than resolve in round 1. b's weighted_bump_days=4.0 gives it the
    # maximum fairness bonus (MAX_FAIRNESS_BONUS, capped at 4*0.01=0.04),
    # which pushes the effective gap to 0.16 -- just over CLEAR_MARGIN --
    # flipping the round-1 winner from a to b. This is "tilts a close
    # call," not "fairness alone decides": see the companion invariant
    # test below for the case fairness must NOT be able to flip.
    a = _facts("a", urgency=0.30)
    b = _facts("b", urgency=0.42, weighted_bump_days=4.0)
    assert 0.42 - 0.30 < CLEAR_MARGIN  # raw gap alone would not resolve decisively
    assert 0.42 + MAX_FAIRNESS_BONUS - 0.30 > CLEAR_MARGIN  # boosted gap does

    def get_claim(facts, round_num, opponent_argument):
        return _claim(
            facts, argument="claim", concedes=False,
            weighted_bump_days=facts.weighted_bump_days,
        )

    result = negotiate_pair(a, b, get_claim, max_rounds=3)

    assert result.escalated is False
    assert result.winner_plot_id == "b"
    assert result.rounds_used == 1


def test_negotiate_pair_fairness_cannot_flip_an_already_clear_margin() -> None:
    # a leads by more than CLEAR_MARGIN + MAX_FAIRNESS_BONUS -- no amount
    # of fairness bonus available to b can flip this. Companion to the
    # test above: fairness tilts close calls, it does not override a
    # decisive urgency gap.
    a = _facts("a", urgency=0.80)
    b = _facts("b", urgency=0.30, weighted_bump_days=4.0)  # max bonus
    assert 0.80 - (0.30 + MAX_FAIRNESS_BONUS) > CLEAR_MARGIN

    def get_claim(facts, round_num, opponent_argument):
        return _claim(
            facts, argument="claim", concedes=False,
            weighted_bump_days=facts.weighted_bump_days,
        )

    result = negotiate_pair(a, b, get_claim, max_rounds=3)

    assert result.escalated is False
    assert result.winner_plot_id == "a"


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


def test_run_cluster_too_green_plots_all_concede(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    plots = [_plot("p05"), _plot("p06")]
    decisions = [
        _decision("p05", PlotOutcome.TOO_GREEN),
        _decision("p06", PlotOutcome.TOO_GREEN),
    ]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="not ready", concedes=not facts.is_ready)

    result = run_cluster_with_claims(
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim
    )

    assert len(result.outcomes) == 2
    assert all(o.claim.concedes for o in result.outcomes)
    assert result.escalations == []


def test_run_cluster_produces_exactly_one_escalation_for_a_tied_contested_pair(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
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

    result = run_cluster_with_claims(
        plots, decisions, "kamatchipuram", storage, TEST_SEASON_ID, TEST_TODAY, get_claim
    )

    assert len(result.escalations) == 1
    escalation = result.escalations[0]
    assert {escalation.plot_a_id, escalation.plot_b_id} == {"p03", "p04"}
    assert escalation.rounds_run == 3

    too_green_outcomes = [o for o in result.outcomes if o.plot_id == "p05"]
    assert too_green_outcomes[0].claim.concedes is True


# --- ADR-009 Part 1.5: plot harvest lifecycle -- coordinator marks the
# plot harvested at dispatch, deliberately not coupled to Part 2's
# (not-yet-built) farmer confirmation. ---

class _FailingMarkStorage(FileStorage):
    """Wraps a real FileStorage but makes every mark_plot_harvested call
    fail, to test the coordinator's degrade-and-log path without needing
    a hand-rolled fake for the rest of the Storage surface."""

    def mark_plot_harvested(self, plot_id, cluster_id, season_id, dispatched_at):
        return StorageResult(success=False, error="simulated write failure")


def test_run_cluster_marks_a_fits_plot_harvested(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    plots = [_plot("p01")]
    decisions = [_decision("p01", PlotOutcome.FITS, days_past_maturity=5, urgency=0.5)]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="fits", concedes=False)

    run_cluster_with_claims(
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim
    )

    assert storage.get_harvested_plot_ids("c", TEST_SEASON_ID) == {"p01"}


def test_run_cluster_does_not_mark_too_green_plots_harvested(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    plots = [_plot("p05")]
    decisions = [_decision("p05", PlotOutcome.TOO_GREEN)]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="not ready", concedes=True)

    run_cluster_with_claims(
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim
    )

    assert storage.get_harvested_plot_ids("c", TEST_SEASON_ID) == set()


def test_run_cluster_does_not_mark_a_contested_negotiation_winner_harvested(tmp_path) -> None:
    """Winning a negotiation only sets priority for a future capacity
    opening -- it does not manufacture capacity, so the winner must stay
    CONTESTED and unmarked, exactly as before Part 1.5."""
    storage = FileStorage(tmp_path / "storage.json")
    plots = [_plot("p03"), _plot("p04")]
    decisions = [
        _decision("p03", PlotOutcome.CONTESTED, days_past_maturity=10, urgency=0.6),
        _decision("p04", PlotOutcome.CONTESTED, days_past_maturity=2, urgency=0.1),
    ]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="contesting", concedes=False)

    result = run_cluster_with_claims(
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim
    )

    assert result.resolved_negotiations == [("p03", "p04")]
    assert storage.get_harvested_plot_ids("c", TEST_SEASON_ID) == set()


def test_run_cluster_logs_and_continues_when_the_harvest_write_fails(tmp_path, caplog) -> None:
    import logging

    storage = _FailingMarkStorage(tmp_path / "storage.json")
    plots = [_plot("p01")]
    decisions = [_decision("p01", PlotOutcome.FITS, days_past_maturity=5, urgency=0.5)]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="fits", concedes=False)

    with caplog.at_level(logging.ERROR):
        result = run_cluster_with_claims(
            plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim
        )

    # Never raises -- the trigger run completes, the FITS outcome still
    # reaches the farmer, only the harvest-state write was lost.
    assert len(result.outcomes) == 1
    assert result.outcomes[0].outcome == PlotOutcome.FITS
    assert any("HARVEST STATE WRITE FAILED" in r.message for r in caplog.records)
