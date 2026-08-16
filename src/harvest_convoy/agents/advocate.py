"""One advocate agent per plot. Structured claims only, never prose.

Never throws from the agent loop: any failure (model error, throttling, a
response that fails to validate) is logged and degrades to a safe fallback
claim built entirely from ground-truth facts, so one broken advocate call
can never take down the coordinator's negotiation. See ADR-003 Decision 3.
"""

from __future__ import annotations

import logging

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.models.model import Model

from harvest_convoy.agents.contracts import AdvocateClaim, PlotFacts
from harvest_convoy.agents.fairness_stub import fairness_lookup
from harvest_convoy.observability.otel import get_tracer

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)

BEDROCK_MODEL_ID = "apac.amazon.nova-pro-v1:0"
BEDROCK_REGION = "ap-south-1"

SYSTEM_PROMPT = """\
You are a plot advocate in a farming cooperative's harvest scheduling \
system. You represent exactly one farmer's plot and argue for it to get \
the shared combine harvester's next available slot.

You are given ground-truth facts about the plot -- you cannot change them, \
only interpret them. Use the fairness_lookup tool to check whether this \
farmer was bumped (delayed) last season before deciding how hard to argue.

Argue honestly, not maximally. A plot that is not yet ready (is_ready is \
false) has grain too immature and too light to lodge in rain -- harvesting \
it early produces poor quality and no safety benefit. If the facts do not \
support urgency, say so and concede (concedes=true). Conceding when the \
facts warrant it is a correct, expected outcome, not a failure -- a farmer \
told "your plot is fine standing, no action needed" is the system working \
as intended.

If you are in a later negotiation round after losing an earlier round, you \
will be told the opponent's argument and reminded of the fairness ledger. \
Only escalate your case if the fairness ledger or the facts genuinely \
support it -- do not simply repeat your previous argument.

Return your answer as the structured claim schema. `argument` must be one \
sentence, at most 25 words.
"""


def _build_model() -> Model:
    return BedrockModel(model_id=BEDROCK_MODEL_ID, region_name=BEDROCK_REGION)


def _build_prompt(
    facts: PlotFacts, round_num: int, opponent_argument: str | None
) -> str:
    lines = [
        f"Plot {facts.plot_id} (farmer {facts.farmer_id}):",
        f"- ready to harvest: {facts.is_ready}",
        f"- days past projected maturity: {facts.days_past_maturity}",
        f"- urgency (0=just matured/not ready, 1=fully decayed): {facts.urgency:.2f}",
        f"- rain vulnerability at current stage: {facts.rain_vulnerability}",
        f"- acreage: {facts.acres}",
    ]
    if round_num > 1:
        lines.append(
            f"This is negotiation round {round_num}. You did not get the slot "
            f"in the previous round."
        )
        if opponent_argument:
            lines.append(f'The competing plot argued: "{opponent_argument}"')
        lines.append(
            "Check the fairness ledger before re-arguing or conceding."
        )
    return "\n".join(lines)


def _fallback_claim(facts: PlotFacts, reason: str) -> AdvocateClaim:
    logger.warning(
        "advocate fallback for plot %s: %s", facts.plot_id, reason
    )
    return AdvocateClaim.from_facts(
        facts,
        argument=(
            "Not ready to harvest; standing safely." if not facts.is_ready
            else "Ready and awaiting the machine; no special claim."
        ),
        concedes=not facts.is_ready,
    )


def get_advocate_claim(
    facts: PlotFacts,
    *,
    model: Model | None = None,
    round_num: int = 1,
    opponent_argument: str | None = None,
) -> AdvocateClaim:
    """Get one plot's structured advocacy claim for this negotiation round.

    Builds a fresh single-turn Agent per call rather than a long-lived
    conversational one -- each round is an independent judgment given the
    current facts (and, from round 2 on, the opponent's prior argument),
    not a multi-turn conversation with memory.
    """
    with tracer.start_as_current_span(
        "advocate.get_claim",
        attributes={"plot_id": facts.plot_id, "round": round_num},
    ) as span:
        try:
            agent = Agent(
                model=model or _build_model(),
                tools=[fairness_lookup],
                system_prompt=SYSTEM_PROMPT,
                structured_output_model=AdvocateClaim,
                callback_handler=None,
            )
            prompt = _build_prompt(facts, round_num, opponent_argument)
            result = agent(prompt)
            claim = result.structured_output
            if claim is None:
                span.set_attribute("degraded", True)
                return _fallback_claim(facts, "no structured_output returned")
        except Exception as exc:  # noqa: BLE001 -- degrade, never throw from the agent loop
            span.set_attribute("degraded", True)
            span.record_exception(exc)
            logger.exception(
                "advocate call failed for plot %s round %d", facts.plot_id, round_num
            )
            return _fallback_claim(facts, f"{type(exc).__name__}: {exc}")

        # Ground-truth override: whatever the model echoed for factual
        # fields is discarded. Only argument/concedes are the model's own.
        return AdvocateClaim.from_facts(
            facts, argument=claim.argument, concedes=claim.concedes
        )
