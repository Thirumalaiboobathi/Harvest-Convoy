import pytest
from pydantic import ValidationError

from harvest_convoy.agents.contracts import (
    AdvocateClaim,
    PlotFacts,
    classify_rain_vulnerability,
)


def _facts(**overrides) -> PlotFacts:
    base = dict(
        plot_id="p01",
        farmer_id="f01",
        is_ready=True,
        days_past_maturity=5,
        urgency=0.25,
        rain_vulnerability="low",
        acres=2.0,
        bumped_last_season=False,
    )
    base.update(overrides)
    return PlotFacts(**base)


def test_too_green_plot_has_no_rain_vulnerability() -> None:
    assert classify_rain_vulnerability(is_ready=False, urgency=0.0) == "none"


def test_freshly_matured_plot_has_no_rain_vulnerability() -> None:
    assert classify_rain_vulnerability(is_ready=True, urgency=0.0) == "none"


def test_rain_vulnerability_climbs_with_urgency() -> None:
    assert classify_rain_vulnerability(True, 0.1) == "low"
    assert classify_rain_vulnerability(True, 0.4) == "moderate"
    assert classify_rain_vulnerability(True, 0.9) == "severe"


def test_claim_from_facts_uses_ground_truth_not_model_input() -> None:
    facts = _facts(days_past_maturity=7, acres=3.25, bumped_last_season=True)
    claim = AdvocateClaim.from_facts(facts, argument="Ready now.", concedes=False)

    assert claim.plot_id == "p01"
    assert claim.days_past_maturity == 7
    assert claim.acres == 3.25
    assert claim.bumped_last_season is True
    assert claim.argument == "Ready now."
    assert claim.concedes is False


def test_argument_over_25_words_is_truncated_not_rejected() -> None:
    long_argument = " ".join(f"word{i}" for i in range(40))
    facts = _facts()
    claim = AdvocateClaim.from_facts(facts, argument=long_argument, concedes=False)
    assert len(claim.argument.split()) == 25


def test_invalid_rain_vulnerability_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AdvocateClaim(
            plot_id="p01",
            urgency_score=0.5,
            days_past_maturity=1,
            rain_vulnerability="catastrophic",  # not a valid literal
            acres=1.0,
            bumped_last_season=False,
            argument="x",
            concedes=False,
        )
