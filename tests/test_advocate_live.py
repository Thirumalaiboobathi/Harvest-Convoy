"""Live proof the real Strands + Bedrock integration works end to end --
not just the orchestration shell around it. See ADR-003 Decision 5: this
asserts structure and the concession outcome, not exact wording or score,
since live model output isn't bit-reproducible.
"""

from __future__ import annotations

import pytest

from harvest_convoy.agents.advocate import get_advocate_claim
from harvest_convoy.agents.contracts import PlotFacts


@pytest.mark.network
@pytest.mark.bedrock
def test_live_advocate_concedes_for_a_too_green_plot() -> None:
    facts = PlotFacts(
        plot_id="p07",
        farmer_id="f07",
        is_ready=False,
        days_past_maturity=0,
        urgency=0.0,
        rain_vulnerability="none",
        acres=1.5,
        bumped_last_season=False,
    )

    claim = get_advocate_claim(facts)

    assert claim.plot_id == "p07"
    assert claim.concedes is True
    assert claim.days_past_maturity == 0
    assert claim.acres == 1.5
    assert isinstance(claim.argument, str) and len(claim.argument) > 0
    assert len(claim.argument.split()) <= 25


@pytest.mark.network
@pytest.mark.bedrock
def test_live_advocate_returns_valid_claim_for_a_ready_plot() -> None:
    facts = PlotFacts(
        plot_id="p01",
        farmer_id="f01",
        is_ready=True,
        days_past_maturity=17,
        urgency=0.85,
        rain_vulnerability="severe",
        acres=2.5,
        bumped_last_season=False,
    )

    claim = get_advocate_claim(facts)

    assert claim.plot_id == "p01"
    assert claim.days_past_maturity == 17
    assert claim.rain_vulnerability == "severe"
    assert isinstance(claim.concedes, bool)
    assert len(claim.argument.split()) <= 25
