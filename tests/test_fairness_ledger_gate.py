"""The Phase 5 gate. Two tests, both required, together the honest claim
that the coordinator is fairer than the phone calls it replaces:

1. test_bumped_farmer_wins_a_close_call_in_the_next_season -- fairness
   has real, measurable teeth: identical plots, identical weather,
   identical acreage; the only thing that differs between two seasons is
   ledger history, and that alone changes the outcome.

2. test_fairness_cannot_flip_a_maximally_urgent_plot -- fairness has a
   hard ceiling: no accumulation of bump history, however large, can move
   a shattering plot out of the way of a genuinely urgent one.

Read together, not separately -- either test alone would be a half-truth.
See ADR-005 Decision 4 for the arithmetic these two tests are built on.
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.agents.coordinator import (
    CLEAR_MARGIN,
    MAX_FAIRNESS_BONUS,
    build_plot_facts,
    negotiate_pair,
)
from harvest_convoy.agents.contracts import AdvocateClaim
from harvest_convoy.models import Plot
from harvest_convoy.scheduling.solver import PlotDecision, PlotOutcome
from harvest_convoy.storage.fairness import record_bump
from harvest_convoy.storage.file_storage import FileStorage


def _plot(plot_id: str, farmer_id: str) -> Plot:
    return Plot(
        plot_id=plot_id, farmer_id=farmer_id, cluster_id="c",
        lat=9.865, lon=77.454, crop="paddy", variety="ADT45",
        transplant_date=date(2026, 5, 1), area_acres=2.0,
    )


def _decision(plot_id: str, days_past_maturity: int, urgency: float) -> PlotDecision:
    return PlotDecision(
        plot_id=plot_id, outcome=PlotOutcome.CONTESTED, accumulated_gdd=2000.0,
        days_past_maturity=days_past_maturity, urgency=urgency, route_position=None,
    )


def _truthful_claim(facts, round_num, opponent_argument) -> AdvocateClaim:
    """No scripting, no rigging -- just echoes the facts, same as the
    Kamatchipuram gate's claim provider. Whatever happens, happens because
    of the facts and the ledger, not because the test told the claim what
    to say."""
    return AdvocateClaim.from_facts(facts, argument="claim", concedes=False)


def test_bumped_farmer_wins_a_close_call_in_the_next_season(tmp_path) -> None:
    """Same two plots, same weather-derived urgency, same acreage, in both
    seasons. The only variable is whether farmer_f's season-1 ledger entry
    exists yet.

    farmer_f's plot is at urgency 0.42, farmer_u's at 0.30 -- a genuine
    12-point gap, but under CLEAR_MARGIN (0.15), so on raw urgency alone
    this is a "close call": not decisive enough to resolve without a
    human. That's true in both seasons; what changes is what the system
    does about it.

    Season 1: neither farmer has ledger history. The call escalates --
    correctly, since 0.12 < CLEAR_MARGIN and nothing else exists yet to
    tilt it. (In this test's narrative, a human resolved that escalation
    against farmer_f -- recorded below as the season-1 ledger entry that
    sets up season 2. The mechanics of *how* a human resolution gets
    recorded are covered in test_webhook.py; here we start from the
    recorded fact.)

    Season 2: identical plots, identical urgency values. farmer_f now
    carries a season-1 bump. weighted_bump_days=4.0 (a single season,
    4 days bumped, no decay applied yet since it's the immediately
    preceding season) gives the maximum fairness bonus
    (min(MAX_FAIRNESS_BONUS, 4.0 * FAIRNESS_WEIGHT_PER_BUMPED_DAY) =
    MAX_FAIRNESS_BONUS). That's just enough to push farmer_f's already
    genuine (if marginal) urgency edge over CLEAR_MARGIN: 0.42 + 0.04 -
    0.30 = 0.16 > 0.15. farmer_f wins outright -- no escalation needed.

    This is "fairness tilts a close call," precisely: farmer_f was always
    somewhat ahead on the actual facts, not manufactured urgency. The
    ledger is what earns that edge the benefit of the doubt.
    """
    plot_f = _plot("season-plot-f", "farmer_f")
    plot_u = _plot("season-plot-u", "farmer_u")
    decision_f = _decision("season-plot-f", days_past_maturity=8, urgency=0.42)
    decision_u = _decision("season-plot-u", days_past_maturity=6, urgency=0.30)
    assert decision_f.urgency - decision_u.urgency < CLEAR_MARGIN  # a genuine close call

    # --- Season 1: no history for either farmer yet. ---
    storage_season_1 = FileStorage(tmp_path / "season_1.json")
    facts_f_s1 = build_plot_facts(plot_f, decision_f, storage_season_1)
    facts_u_s1 = build_plot_facts(plot_u, decision_u, storage_season_1)
    assert facts_f_s1.weighted_bump_days == 0.0
    assert facts_u_s1.weighted_bump_days == 0.0

    result_season_1 = negotiate_pair(facts_f_s1, facts_u_s1, _truthful_claim)
    assert result_season_1.escalated is True
    assert result_season_1.winner_plot_id is None

    # --- Between seasons: farmer_f's season-1 escalation loss is recorded. ---
    storage_season_2 = FileStorage(tmp_path / "season_2.json")
    bump_result = record_bump(
        "farmer_f", "season-1",
        days_bumped=4, outcome="bumped",
        cluster_id="c", plot_id="season-plot-f", opponent_plot_id="season-plot-u",
        storage=storage_season_2,
    )
    assert bump_result.success is True

    # --- Season 2: identical plots, identical urgency, only history differs. ---
    facts_f_s2 = build_plot_facts(plot_f, decision_f, storage_season_2)
    facts_u_s2 = build_plot_facts(plot_u, decision_u, storage_season_2)
    assert facts_f_s2.urgency == facts_f_s1.urgency  # same plot, same weather
    assert facts_u_s2.urgency == facts_u_s1.urgency  # same plot, same weather
    assert facts_f_s2.weighted_bump_days == 4.0
    assert facts_u_s2.weighted_bump_days == 0.0

    result_season_2 = negotiate_pair(facts_f_s2, facts_u_s2, _truthful_claim)

    assert result_season_2.escalated is False
    assert result_season_2.winner_plot_id == "season-plot-f"
    assert result_season_2.rounds_used == 1

    # The flip, stated explicitly: same facts, different history, different outcome.
    assert result_season_1.winner_plot_id != result_season_2.winner_plot_id


def test_fairness_cannot_flip_a_maximally_urgent_plot(tmp_path) -> None:
    """The inverse case, and the one that actually proves the cap works:
    a farmer with an extreme bump history and a genuinely low-urgency
    plot must still lose to a maximally urgent (fully decayed,
    "shattering") plot -- physics wins outright.

    farmer_h has been bumped in five straight prior seasons, 4 days each --
    about as extreme a history as this system can produce. Even summed
    with no decay at all (which overstates the real number, since
    storage.fairness.weighted_bump_days decays older seasons), that's
    20.0 weighted bump-days. The bonus function caps at MAX_FAIRNESS_BONUS
    regardless: min(MAX_FAIRNESS_BONUS, 20.0 * FAIRNESS_WEIGHT_PER_BUMPED_DAY)
    == MAX_FAIRNESS_BONUS. No history, however large, buys more than that.

    farmer_h's plot is barely past maturity (urgency 0.10). The opposing
    plot is at urgency 1.0 -- decay_fraction's ceiling, i.e. genuinely
    shattering. The gap (0.90) dwarfs CLEAR_MARGIN + MAX_FAIRNESS_BONUS
    (0.19) many times over: no plausible history could close it, let
    alone this test's deliberately extreme one.
    """
    plot_h = _plot("urgent-plot-h", "farmer_h")
    plot_m = _plot("urgent-plot-m", "farmer_m")
    decision_h = _decision("urgent-plot-h", days_past_maturity=2, urgency=0.10)
    decision_m = _decision("urgent-plot-m", days_past_maturity=30, urgency=1.0)
    assert decision_m.urgency == 1.0  # genuinely at the ceiling, not just "high"

    storage = FileStorage(tmp_path / "extreme_history.json")
    for seasons_ago, season_id in enumerate(
        ["season-5", "season-4", "season-3", "season-2", "season-1"]
    ):
        result = record_bump(
            "farmer_h", season_id,
            days_bumped=4, outcome="bumped",
            cluster_id="c", plot_id="urgent-plot-h", opponent_plot_id="urgent-plot-m",
            storage=storage,
        )
        assert result.success is True

    facts_h = build_plot_facts(plot_h, decision_h, storage)
    facts_m = build_plot_facts(plot_m, decision_m, storage)

    assert facts_h.weighted_bump_days > 0  # the history is real and non-trivial
    # Even the maximum bonus this system can ever grant is far short of
    # what would be needed to contest a maximally urgent plot.
    assert facts_h.urgency + MAX_FAIRNESS_BONUS < facts_m.urgency

    result = negotiate_pair(facts_h, facts_m, _truthful_claim)

    assert result.escalated is False
    assert result.winner_plot_id == "urgent-plot-m"
    assert result.rounds_used == 1
