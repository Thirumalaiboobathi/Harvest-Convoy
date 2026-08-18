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
    # ADR-008 Decision 12: fallback claims are marked degraded so
    # notify.py's escalation rendering knows not to present this
    # placeholder text as if it were real model reasoning.
    assert claim.degraded is True


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


def test_single_system_prompt_used_regardless_of_language(monkeypatch) -> None:
    """ADR-008 Decision 14: a Tamil-instructed system-prompt variant was
    tried and removed after live verification showed it produces
    grammatically invalid Tamil (real Unicode code points, not real
    words) -- see messages_ta.advocate_argument's docstring for the full
    account. There is exactly one system prompt again, used for every
    call regardless of facts.language."""
    captured_prompts = []

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured_prompts.append(kwargs["system_prompt"])

        def __call__(self, prompt):
            raise RuntimeError("not needed -- this test only checks prompt selection")

    monkeypatch.setattr(advocate_module, "Agent", FakeAgent)

    advocate_module.get_advocate_claim(_facts(language="ta"), model=object())
    advocate_module.get_advocate_claim(_facts(language="en"), model=object())

    assert captured_prompts[0] == advocate_module.CACHED_SYSTEM_PROMPT
    assert captured_prompts[1] == advocate_module.CACHED_SYSTEM_PROMPT
    assert not hasattr(advocate_module, "CACHED_SYSTEM_PROMPT_TA")
    assert not hasattr(advocate_module, "SYSTEM_PROMPT_TA")


def test_tamil_registered_plot_gets_templated_argument_not_model_text(monkeypatch) -> None:
    """ADR-008 Decision 14: `argument` for a Tamil-registered farmer is
    rendered deterministically from ground-truth facts + the model's own
    `concedes` decision -- never the model's own (English-only, since the
    Tamil instruction is gone) prose."""
    from harvest_convoy.agents.contracts import AdvocateClaim

    class FakeResult:
        def __init__(self, claim):
            self.structured_output = claim

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, prompt):
            return FakeResult(AdvocateClaim(
                plot_id="p07", urgency_score=0.0, days_past_maturity=0,
                rain_vulnerability="none", acres=1.5, bumped_last_season=False,
                argument="The model's own English sentence.", concedes=True,
            ))

    monkeypatch.setattr(advocate_module, "Agent", FakeAgent)

    facts = _facts(language="ta", is_ready=False)
    claim = advocate_module.get_advocate_claim(facts, model=object())

    assert claim.concedes is True  # the model's genuine decision, kept
    assert claim.argument != "The model's own English sentence."
    assert "தயார" in claim.argument or "அவசரம்" in claim.argument  # real Tamil template output


def test_english_registered_plot_keeps_model_generated_argument(monkeypatch) -> None:
    from harvest_convoy.agents.contracts import AdvocateClaim

    class FakeResult:
        def __init__(self, claim):
            self.structured_output = claim

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, prompt):
            return FakeResult(AdvocateClaim(
                plot_id="p07", urgency_score=0.0, days_past_maturity=0,
                rain_vulnerability="none", acres=1.5, bumped_last_season=False,
                argument="The model's own English sentence.", concedes=True,
            ))

    monkeypatch.setattr(advocate_module, "Agent", FakeAgent)

    facts = _facts(language="en", is_ready=False)
    claim = advocate_module.get_advocate_claim(facts, model=object())

    assert claim.argument == "The model's own English sentence."


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
        language="en",  # this test checks the model's own argument passes through
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
    assert claim.degraded is False  # a genuine, successful model call
