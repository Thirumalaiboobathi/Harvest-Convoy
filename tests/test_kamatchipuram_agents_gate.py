"""Phase 3 gate, corrected in Phase 5: too-green plots concede, and p03
correctly beats p04 -- WITHOUT an escalation.

This test's assertions changed in Phase 5 and that change is deliberate,
not a regression. Under Phase 3's flat FAIRNESS_WEIGHT=0.3 bonus, p03
(urgency 0.3) and p04 (urgency 0.0, "bumped_last_season" from the static
stub) scored as an exact tie and escalated. ADR-005's analysis found that
wrong: p03 and p04's raw urgency gap is 0.3, far larger than
CLEAR_MARGIN (0.15) -- that's a genuine, decisive urgency difference, not
a close call, and no fairness bonus should have been able to paper over
it. Phase 5 caps the fairness bonus below the urgency scale's granularity
(agents/coordinator.py:MAX_FAIRNESS_BONUS) specifically so this can't
happen: p03 now wins outright, in round 1, with no escalation. See
ADR-005 Decision 4 and test_coordinator.py's invariant tests for the
general case; this is that correction validated against the real
Kamatchipuram scenario instead of synthetic values.

Builds on the Phase 2 gate scenario (test_kamatchipuram_gate.py) -- same
real seed_cluster fixture, same synthetic constant-rate weather, same
tight rain window producing 2 fits / 2 contested / 4 too-green. Storage is
a fresh, empty FileStorage per test (tmp_path) -- no ledger history, so
every farmer starts at the neutral weighted_bump_days=0.0 baseline. The
season-over-season case where ledger history actually changes the outcome
is test_fairness_ledger_gate.py, not this file.

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
from harvest_convoy.storage.file_storage import FileStorage
from scripts import seed_cluster

REFERENCE_TODAY = date(2026, 8, 16)


def _synthetic_days(transplant_date: date, today: date) -> list[DailyTemperature]:
    rate = crop_params.KURUVAI_MEAN_GDD_PER_DAY_REFERENCE_ESTIMATED
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


def _run_gate_scenario(storage):
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
        seed_cluster.PLOTS, decisions, "kamatchipuram", storage,
        "2026-kuruvai", REFERENCE_TODAY, _truthful_claim,
    )


def test_p03_beats_p04_outright_with_no_escalation(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    result = _run_gate_scenario(storage)

    assert result.escalations == []
    assert result.resolved_negotiations == [("p03", "p04")]


def test_too_green_plots_concede(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    result = _run_gate_scenario(storage)
    too_green_ids = {"p05", "p06", "p07", "p08"}
    too_green_outcomes = [o for o in result.outcomes if o.plot_id in too_green_ids]
    assert len(too_green_outcomes) == 4
    assert all(o.claim.concedes for o in too_green_outcomes)


def test_ready_plots_do_not_concede(tmp_path) -> None:
    storage = FileStorage(tmp_path / "storage.json")
    result = _run_gate_scenario(storage)
    ready_ids = {"p01", "p02", "p03", "p04"}
    ready_outcomes = [o for o in result.outcomes if o.plot_id in ready_ids]
    assert len(ready_outcomes) == 4
    assert not any(o.claim.concedes for o in ready_outcomes)
