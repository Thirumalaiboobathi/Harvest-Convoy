"""One advocate agent per plot. Structured claims only, never prose.

Never throws from the agent loop: any failure (model error, throttling, a
response that fails to validate) is logged and degrades to a safe fallback
claim built entirely from ground-truth facts, so one broken advocate call
can never take down the coordinator's negotiation. See ADR-003 Decision 3.
"""

from __future__ import annotations

import logging

from strands import Agent, tool
from strands.models.bedrock import BedrockModel
from strands.models.model import Model

from harvest_convoy.agents.contracts import AdvocateClaim, PlotFacts
from harvest_convoy.observability.otel import get_tracer
from harvest_convoy.storage import Storage, get_storage
from harvest_convoy.storage.fairness import get_ledger_history

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

# Prompt caching: verified live against apac.amazon.nova-pro-v1:0 in
# ap-south-1 before building this. Three findings, not one:
# (1) System-prompt caching works, via an explicit cachePoint content
#     block -- a raw Converse call with one after the system text showed
#     cacheWriteInputTokens then cacheReadInputTokens on a repeat call.
# (2) Strands' cache_config=CacheConfig(strategy="auto") does NOT help
#     this workload: reading its source, "auto" places the cache
#     boundary at the last USER message, not the system prompt -- useful
#     for growing multi-turn conversations, useless here since every
#     advocate call is a fresh single-turn Agent with different plot
#     facts as the user message each time (nothing repeats there to
#     cache). Measured: zero cache_read/cache_write tokens across a real
#     8-call run with "auto" enabled.
# (3) Tool-config caching (Strands' cache_tools=...) does not work for
#     this model at all: Bedrock rejects it outright --
#     "Malformed input request: #/toolConfig/tools/1: extraneous key
#     [cachePoint] is not permitted" -- confirmed by trying it and
#     reading the real error, not assumed from docs.
# So: the system prompt is passed as an explicit SystemContentBlock list
# with a trailing cachePoint (the modern, non-deprecated API -- the
# alternative BedrockModel(cache_prompt=...) kwarg works identically but
# is deprecated in favor of this). Tool schemas are not cached; see (3).
CACHED_SYSTEM_PROMPT = [
    {"text": SYSTEM_PROMPT},
    {"cachePoint": {"type": "default"}},
]


def _build_model() -> Model:
    return BedrockModel(model_id=BEDROCK_MODEL_ID, region_name=BEDROCK_REGION)


def _make_fairness_tool(storage: Storage):
    """Real, storage-backed replacement for the Phase 3 fairness_stub.
    Bound to a specific Storage instance via closure so the @tool-decorated
    function the LLM calls still takes only the arguments it should see
    (farmer_id) -- the storage backend is a wiring detail, not something
    the model should reason about.
    """

    @tool
    def fairness_lookup(farmer_id: str) -> dict:
        """Look up this farmer's bump history across past seasons. Use
        this before deciding how hard to argue -- a farmer bumped
        repeatedly has a stronger fairness claim than one bumped once or
        never."""
        history = get_ledger_history(farmer_id, storage)
        return {
            "farmer_id": farmer_id,
            "seasons_recorded": len(history),
            "bumped_last_season": bool(history and history[0].days_bumped > 0),
            "history": [
                {
                    "season_id": e.season_id,
                    "days_bumped": e.days_bumped,
                    "outcome": e.outcome,
                }
                for e in history
            ],
        }

    return fairness_lookup


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
    storage: Storage | None = None,
    round_num: int = 1,
    opponent_argument: str | None = None,
) -> AdvocateClaim:
    """Get one plot's structured advocacy claim for this negotiation round.

    Builds a fresh single-turn Agent per call rather than a long-lived
    conversational one -- each round is an independent judgment given the
    current facts (and, from round 2 on, the opponent's prior argument),
    not a multi-turn conversation with memory.
    """
    storage = storage or get_storage()
    with tracer.start_as_current_span(
        "advocate.get_claim",
        attributes={"plot_id": facts.plot_id, "round": round_num},
    ) as span:
        try:
            agent = Agent(
                model=model or _build_model(),
                tools=[_make_fairness_tool(storage)],
                system_prompt=CACHED_SYSTEM_PROMPT,
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
