"""Coordinator orchestration tests, using an injected deterministic claim
provider (same dependency-injection pattern as the Phase 1 weather-bridging
tests) rather than faking Strands' Model protocol -- Bedrock already works
live (see ADR-003), so there's no need for a hand-rolled stub to stand in
as "the real path." This tests our own negotiation/escalation logic fast
and deterministically.
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.agents.contracts import AdvocateClaim, PlotFacts, TriggerContext
from harvest_convoy.agents.coordinator import (
    CLEAR_MARGIN,
    MAX_FAIRNESS_BONUS,
    _fairness_was_decisive,
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

TEST_TRIGGER_CONTEXT = TriggerContext(
    decision_date=TEST_TODAY.isoformat(),
    rain_threshold_mm=5.0,
    forecast_horizon_days=16,
    usable_harvest_days=3,
    maturity_gdd_used=1729.2,
    threshold_source="fallback",
    machine_capacity_acres_per_day=3.5,
    capacity_budget_acres=10.5,
)


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


def test_rain_urgency_boost_can_tie_but_never_invert_urgency_ordering() -> None:
    """ADR-011 Part 3, Decision 13: the monotonicity proof in
    agents/coordinator.py's comment above FAIRNESS_WEIGHT_PER_BUMPED_DAY --
    min(1, x+b) is monotonic non-decreasing in x for any fixed boost b, so
    the rain urgency boost can compress a genuine urgency gap toward zero
    (at worst an exact tie once both sides hit the 1.0 cap) but can never
    invert which of two plots is more urgent. Checked against the actual
    RAIN_URGENCY_BOOST_SUSTAINED_TUNING value, including the exact
    worst-case pair the algebra identifies: the more-urgent plot already
    at the 1.0 cap pre-boost, the less-urgent plot close behind it.
    """
    from harvest_convoy.scheduling.rain_event import RAIN_URGENCY_BOOST_SUSTAINED_TUNING

    def boosted(raw_urgency: float, boost: float) -> float:
        return min(1.0, raw_urgency + boost)

    high_raw = 1.0
    low_raw = 1.0 - RAIN_URGENCY_BOOST_SUSTAINED_TUNING / 2
    assert high_raw > low_raw

    high_boosted = boosted(high_raw, RAIN_URGENCY_BOOST_SUSTAINED_TUNING)
    low_boosted = boosted(low_raw, RAIN_URGENCY_BOOST_SUSTAINED_TUNING)
    assert high_boosted >= low_boosted  # never inverted -- at most tied
    assert high_boosted == 1.0 and low_boosted == 1.0  # the actual tie case

    # General sweep across raw urgency pairs and boost magnitudes: ordering
    # is always preserved or collapses to a tie, never inverts.
    for boost in (0.0, 0.05, RAIN_URGENCY_BOOST_SUSTAINED_TUNING, 0.5, 1.0):
        for x in (0.0, 0.2, 0.5, 0.79, 0.8, 0.95, 1.0):
            for y in (0.0, 0.2, 0.5, 0.79, 0.8, 0.95, 1.0):
                if x >= y:
                    assert boosted(x, boost) >= boosted(y, boost)


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
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
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
        plots, decisions, "kamatchipuram", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
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
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
    )

    assert storage.get_harvested_plot_ids("c", TEST_SEASON_ID) == {"p01"}


def test_run_cluster_does_not_mark_too_green_plots_harvested(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    plots = [_plot("p05")]
    decisions = [_decision("p05", PlotOutcome.TOO_GREEN)]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="not ready", concedes=True)

    run_cluster_with_claims(
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
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
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
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
            plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
        )

    # Never raises -- the trigger run completes, the FITS outcome still
    # reaches the farmer, only the harvest-state write was lost.
    assert len(result.outcomes) == 1
    assert result.outcomes[0].outcome == PlotOutcome.FITS
    assert any("HARVEST STATE WRITE FAILED" in r.message for r in caplog.records)


# --- ADR-010 Part 0.5: persisted decision records ---

def test_fairness_was_decisive_is_none_when_either_side_conceded() -> None:
    winner = _claim(_facts("a", urgency=0.1), argument="x", concedes=False)
    loser = _claim(_facts("b", urgency=0.05), argument="x", concedes=True)
    assert _fairness_was_decisive(winner, loser, "a", "b", "a") is None


def test_fairness_was_decisive_true_when_the_bonus_flips_the_raw_winner() -> None:
    # A hypothetical the real negotiate_pair() resolution path can never
    # actually produce (CLEAR_MARGIN is wider than 2x MAX_FAIRNESS_BONUS,
    # so a flip can never also clear the resolution margin -- see the
    # completion report) -- tested directly against the pure helper
    # itself, which makes no assumption about how its inputs arose.
    claim_a = _claim(_facts("a", urgency=0.35), argument="x", concedes=False, urgency_score=0.35)
    claim_b = _claim(
        _facts("b", urgency=0.32), argument="x", concedes=False,
        urgency_score=0.32, weighted_bump_days=4.0,  # max fairness bonus
    )
    # raw: a ahead (0.35 > 0.32); scored: b ahead (0.32+0.04=0.36 > 0.35)
    assert _fairness_was_decisive(claim_a, claim_b, "a", "b", "b") is True


def test_fairness_was_decisive_false_when_the_bonus_does_not_flip_the_winner() -> None:
    claim_a = _claim(_facts("a", urgency=0.6), argument="x", concedes=False, urgency_score=0.6)
    claim_b = _claim(
        _facts("b", urgency=0.1), argument="x", concedes=False,
        urgency_score=0.1, weighted_bump_days=4.0,
    )
    assert _fairness_was_decisive(claim_a, claim_b, "a", "b", "a") is False


def test_run_cluster_writes_a_decision_record_for_a_fits_plot(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    plots = [_plot("p01")]
    decisions = [_decision("p01", PlotOutcome.FITS, days_past_maturity=5, urgency=0.5)]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="fits", concedes=False)

    run_cluster_with_claims(
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
    )

    record = storage.get_decision_record("p01", TEST_SEASON_ID, TEST_TRIGGER_CONTEXT.decision_date)
    assert record is not None
    assert record.outcome == "fits"
    assert record.accumulated_gdd == 2000.0
    assert record.maturity_gdd_used == TEST_TRIGGER_CONTEXT.maturity_gdd_used
    assert record.threshold_source == TEST_TRIGGER_CONTEXT.threshold_source
    assert record.route_position == 0
    assert record.opponent_plot_id is None
    assert record.own_claim is None
    assert record.resolution is None
    assert record.resolved_at is not None


def test_run_cluster_writes_a_decision_record_for_a_too_green_plot(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    plots = [_plot("p05")]
    decisions = [_decision("p05", PlotOutcome.TOO_GREEN)]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="not ready", concedes=True)

    run_cluster_with_claims(
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
    )

    record = storage.get_decision_record("p05", TEST_SEASON_ID, TEST_TRIGGER_CONTEXT.decision_date)
    assert record is not None
    assert record.outcome == "too_green"
    assert record.route_position is None
    assert record.rounds_run is None


def test_run_cluster_writes_cross_referenced_decision_records_for_a_resolved_pair(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    plots = [_plot("p03"), _plot("p04")]
    decisions = [
        _decision("p03", PlotOutcome.CONTESTED, days_past_maturity=10, urgency=0.6),
        _decision("p04", PlotOutcome.CONTESTED, days_past_maturity=2, urgency=0.1),
    ]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument=f"contesting {facts.plot_id}", concedes=False)

    run_cluster_with_claims(
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
    )

    record_a = storage.get_decision_record("p03", TEST_SEASON_ID, TEST_TRIGGER_CONTEXT.decision_date)
    record_b = storage.get_decision_record("p04", TEST_SEASON_ID, TEST_TRIGGER_CONTEXT.decision_date)

    assert record_a.resolution == "won"
    assert record_b.resolution == "lost"
    assert record_a.opponent_plot_id == "p04"
    assert record_b.opponent_plot_id == "p03"
    # Cross-referenced, not both copies of the same claim.
    assert record_a.own_claim["plot_id"] == "p03"
    assert record_a.opponent_claim["plot_id"] == "p04"
    assert record_b.own_claim["plot_id"] == "p04"
    assert record_b.opponent_claim["plot_id"] == "p03"
    assert record_a.rounds_run == 1
    # No fairness weight on either side -- the resolution came from raw
    # urgency alone, so the bonus (zero either way) could not have been
    # decisive.
    assert record_a.fairness_decisive is False
    assert record_a.resolved_at is not None


def test_run_cluster_writes_escalated_decision_records_pending_human_resolution(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    plots = [_plot("p03"), _plot("p04")]
    decisions = [
        _decision("p03", PlotOutcome.CONTESTED, days_past_maturity=6, urgency=0.3),
        _decision("p04", PlotOutcome.CONTESTED, days_past_maturity=0, urgency=0.0),
    ]

    def get_claim(facts, round_num, opponent_argument):
        # Tied regardless of round -- forces escalation, same trick
        # test_run_cluster_produces_exactly_one_escalation_for_a_tied_contested_pair
        # uses.
        return _claim(facts, argument=f"round {round_num}", concedes=False, urgency_score=0.5)

    result = run_cluster_with_claims(
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
    )

    assert len(result.escalations) == 1
    assert result.escalations[0].decision_date == TEST_TRIGGER_CONTEXT.decision_date

    for plot_id in ("p03", "p04"):
        record = storage.get_decision_record(plot_id, TEST_SEASON_ID, TEST_TRIGGER_CONTEXT.decision_date)
        assert record.resolution == "escalated"
        assert record.resolved_at is None
        assert record.rounds_run == 3


def test_run_cluster_writes_a_decision_record_for_the_odd_contested_plot(tmp_path) -> None:
    """Three contested plots, no even pairing -- the last one never enters
    negotiate_pair() at all (ADR-003 Decision 4's pairwise-only scope
    limit), but still gets a DecisionRecord so it isn't invisible to a
    later reader."""
    storage = FileStorage(tmp_path / "storage.json")
    plots = [_plot("p03"), _plot("p04"), _plot("p07")]
    decisions = [
        _decision("p03", PlotOutcome.CONTESTED, days_past_maturity=10, urgency=0.6),
        _decision("p04", PlotOutcome.CONTESTED, days_past_maturity=2, urgency=0.1),
        _decision("p07", PlotOutcome.CONTESTED, days_past_maturity=3, urgency=0.2),
    ]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="contesting", concedes=False)

    run_cluster_with_claims(
        plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
    )

    record = storage.get_decision_record("p07", TEST_SEASON_ID, TEST_TRIGGER_CONTEXT.decision_date)
    assert record is not None
    assert record.resolution == "contested_no_partner_this_round"
    assert record.opponent_plot_id is None
    assert record.resolved_at is not None


class _FailingDecisionStorage(FileStorage):
    """Every put_decision_record call fails -- tests the coordinator's
    degrade-and-log path for this write the same way _FailingMarkStorage
    already does for mark_plot_harvested."""

    def put_decision_record(self, record):
        return StorageResult(success=False, error="simulated write failure")


def test_run_cluster_logs_and_continues_when_the_decision_record_write_fails(tmp_path, caplog) -> None:
    import logging

    storage = _FailingDecisionStorage(tmp_path / "storage.json")
    plots = [_plot("p01")]
    decisions = [_decision("p01", PlotOutcome.FITS, days_past_maturity=5, urgency=0.5)]

    def get_claim(facts, round_num, opponent_argument):
        return _claim(facts, argument="fits", concedes=False)

    with caplog.at_level(logging.ERROR):
        result = run_cluster_with_claims(
            plots, decisions, "c", storage, TEST_SEASON_ID, TEST_TODAY, get_claim, TEST_TRIGGER_CONTEXT
        )

    # Never raises -- the trigger run completes and the plot is still
    # marked harvested; only the audit record was lost.
    assert len(result.outcomes) == 1
    assert storage.get_harvested_plot_ids("c", TEST_SEASON_ID) == {"p01"}
    assert any("DECISION RECORD WRITE FAILED" in r.message for r in caplog.records)
