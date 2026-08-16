"""Phase 3 gate: the Kamatchipuram scenario produces exactly one
escalation, and the too-green plots concede.

Builds on the Phase 2 gate scenario (test_kamatchipuram_gate.py) --
same real seed_cluster fixture, same synthetic constant-rate weather, same
tight rain window producing 2 fits / 2 contested / 4 too-green. This test
carries that through the coordinator with a "truthful" claim provider --
one that just echoes ground-truth facts (concedes iff not ready, no
artificial round-dependent scripting) rather than one rigged to force a
particular outcome. The escalation happens because the fairness stub
(f04 bumped last season) and p03's higher raw urgency genuinely tie once
FAIRNESS_WEIGHT is applied (see ADR-003 Decision 4) -- not because the test
manufactured a tie.

A separate live-Bedrock version of this isn't asserted here: real model
output isn't bit-reproducible, so exact-count assertions belong on the
deterministic path. test_advocate_live.py separately proves the real
integration produces sane behavior (concession for a too-green plot).
"""

from __future__ import annotations

from datetime import date, timedelta

from harvest_convoy.agents.contracts import AdvocateClaim
from harvest_convoy.agents.coordinator import run_cluster_with_claims
from harvest_convoy.agronomy import crop_params
from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.scheduling.solver import solve
from scripts import seed_cluster

REFERENCE_TODAY = date(2026, 8, 16)


def _synthetic_days(transplant_date: date, today: date) -> list[DailyTemperature]:
    rate = crop_params.KURUVAI_MEAN_GDD_PER_DAY_THENI_ESTIMATED
    mean_temp = crop_params.T_BASE_C + rate
    n = (today - transplant_date).days + 1
    return [
        DailyTemperature(
            date=(transplant_date + timedelta(days=i)).isoformat(),
            t_max_c=mean_temp,
            t_min_c=mean_temp,
        )
        for i in range(n)
    ]


def _truthful_claim(facts, round_num, opponent_argument) -> AdvocateClaim:
    return AdvocateClaim.from_facts(
        facts, argument="truthful claim", concedes=not facts.is_ready
    )


def _run_gate_scenario():
    plot_days = {
        p.plot_id: _synthetic_days(p.transplant_date, REFERENCE_TODAY)
        for p in seed_cluster.PLOTS
    }
    forecast = [ForecastDay("d0", 0.0), ForecastDay("d1", 20.0)]
    decisions = solve(
        seed_cluster.PLOTS,
        plot_days,
        seed_cluster.CLUSTER,
        forecast,
        rain_threshold_mm=5.0,
        today=REFERENCE_TODAY,
    )
    return run_cluster_with_claims(
        seed_cluster.PLOTS, decisions, "kamatchipuram", _truthful_claim
    )


def test_kamatchipuram_scenario_produces_exactly_one_escalation() -> None:
    result = _run_gate_scenario()
    assert len(result.escalations) == 1
    assert {result.escalations[0].plot_a_id, result.escalations[0].plot_b_id} == {
        "p03",
        "p04",
    }


def test_too_green_plots_concede() -> None:
    result = _run_gate_scenario()
    too_green_ids = {"p05", "p06", "p07", "p08"}
    too_green_outcomes = [o for o in result.outcomes if o.plot_id in too_green_ids]
    assert len(too_green_outcomes) == 4
    assert all(o.claim.concedes for o in too_green_outcomes)


def test_ready_plots_do_not_concede() -> None:
    result = _run_gate_scenario()
    ready_ids = {"p01", "p02", "p03", "p04"}
    ready_outcomes = [o for o in result.outcomes if o.plot_id in ready_ids]
    assert len(ready_outcomes) == 4
    assert not any(o.claim.concedes for o in ready_outcomes)
