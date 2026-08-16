"""advocate.py tests.

Degrade-and-log paths are tested by monkeypatching the Agent construction
inside the module (same pattern as the Phase 1 weather-bridging tests) --
no live Bedrock call needed to prove a broken agent call doesn't propagate.
A separate live test (marked `network`) proves the real integration works.
"""

from __future__ import annotations

import harvest_convoy.agents.advocate as advocate_module
from harvest_convoy.agents.contracts import PlotFacts


def _facts(**overrides) -> PlotFacts:
    base = dict(
        plot_id="p07",
        farmer_id="f07",
        is_ready=False,
        days_past_maturity=0,
        urgency=0.0,
        rain_vulnerability="none",
        acres=1.5,
        bumped_last_season=False,
    )
    base.update(overrides)
    return PlotFacts(**base)


def test_agent_construction_failure_degrades_to_fallback(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("simulated Bedrock throttling")

    monkeypatch.setattr(advocate_module, "Agent", boom)

    facts = _facts(is_ready=False)
    claim = advocate_module.get_advocate_claim(facts, model=object())

    assert claim.plot_id == "p07"
    assert claim.concedes is True  # fallback concedes for not-ready plots
    assert claim.days_past_maturity == 0


def test_fallback_does_not_concede_for_ready_plots(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(advocate_module, "Agent", boom)

    facts = _facts(plot_id="p01", is_ready=True, days_past_maturity=5, urgency=0.25)
    claim = advocate_module.get_advocate_claim(facts, model=object())
    assert claim.concedes is False
    assert claim.plot_id == "p01"


def test_none_structured_output_degrades_to_fallback(monkeypatch) -> None:
    class FakeResult:
        structured_output = None

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, prompt):
            return FakeResult()

    monkeypatch.setattr(advocate_module, "Agent", FakeAgent)

    facts = _facts(is_ready=False)
    claim = advocate_module.get_advocate_claim(facts, model=object())

    assert claim.concedes is True
    assert claim.plot_id == "p07"


def test_successful_call_overrides_model_echoed_facts_with_ground_truth(monkeypatch) -> None:
    from harvest_convoy.agents.contracts import AdvocateClaim

    class FakeResult:
        def __init__(self, claim):
            self.structured_output = claim

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, prompt):
            # Model "hallucinates" wrong facts -- these must be discarded.
            hallucinated = AdvocateClaim(
                plot_id="WRONG",
                urgency_score=0.99,
                days_past_maturity=999,
                rain_vulnerability="severe",
                acres=1000.0,
                bumped_last_season=True,
                argument="I am very urgent.",
                concedes=False,
            )
            return FakeResult(hallucinated)

    monkeypatch.setattr(advocate_module, "Agent", FakeAgent)

    facts = _facts(
        plot_id="p03", is_ready=True, days_past_maturity=6, urgency=0.3,
        rain_vulnerability="low", acres=3.0, bumped_last_season=False,
    )
    claim = advocate_module.get_advocate_claim(facts, model=object())

    assert claim.plot_id == "p03"
    assert claim.days_past_maturity == 6
    assert claim.acres == 3.0
    assert claim.bumped_last_season is False
    assert claim.rain_vulnerability == "low"
    # argument/concedes are the only things allowed through from the model
    assert claim.argument == "I am very urgent."
    assert claim.concedes is False
