# ADR-003: Strands Agents — Advocates, Coordinator, Negotiation

- Status: Proposed (awaiting go-ahead)
- Date: 2026-08-16

## Context

Phase 3 adds the only LLM-touching layer in the system: per-plot advocate
agents and a coordinator that ranks, negotiates, and resolves. Per the
project's architectural rule, the LLM is restricted to advocacy, judgment
under genuine ambiguity, and message generation — every number it could
touch (GDD, maturity, acreage, capacity) is already computed by Phases 1-2
and must not be re-derived or trusted from model output.

## Decision 0: Bedrock and Strands verified live, not assumed

Before writing any agent code, ran the exact verification requested:

```
aws bedrock-runtime invoke-model --region ap-south-1 \
  --model-id apac.amazon.nova-pro-v1:0 \
  --body '{"messages":[{"role":"user","content":[{"text":"ping"}]}]}' \
  --cli-binary-format raw-in-base64-out <file>
```

Exit code 0, real Nova Pro response returned. **The locked model ID and
region from ADR-000 are confirmed live and accessible from this AWS
account** — resolves the open verification item ADR-000 flagged.

`strands-agents` PyPI metadata confirms `requires_python >= 3.10` with an
explicit `Programming Language :: Python :: 3.12` classifier (checked
directly against PyPI's JSON API, not assumed).

Went further than a bare ping before writing advocate.py/coordinator.py:
built a throwaway `strands.Agent` with `BedrockModel(model_id="apac.amazon.nova-pro-v1:0",
region_name="ap-south-1")`, a pydantic `structured_output_model`, and a
`@tool`-decorated function, and ran both against live Bedrock. Both
returned correctly-typed, correctly-populated results. This means the
stub-model fallback the brief asked for (in case Bedrock failed) is not
needed as the primary path — **Bedrock is the live, verified path** for
this phase. It's mentioned here explicitly so it's clear this isn't being
silently skipped: it wasn't needed because the real thing worked.

## Decision 1: facts are computed, never trusted from the model

`agents/contracts.py` defines `PlotFacts` — a plain dataclass built entirely
from Phase 1/2 output (days_past_maturity, urgency/decay, acres,
rain_vulnerability, bumped_last_season) — and `AdvocateClaim`, the pydantic
schema from the brief, returned by the LLM.

The claim schema *echoes* the facts (days_past_maturity, acres, etc.)
alongside the LLM's actual contribution (`argument`, `concedes`). To make
"the LLM never produces a number that matters" true in code, not just in
prompting: **`get_advocate_claim()` overwrites every factual field on the
returned claim with the ground-truth `PlotFacts` values before returning
it.** If the model hallucinates a different number, it's discarded, not
merged. Only `argument` and `concedes` are genuinely the model's output. A
lenient validator caps `argument` at 25 words by truncation rather than
raising, since a wordiness violation shouldn't be treated the same as a
structurally broken response.

**`rain_vulnerability` is a deterministic classification, not a model
judgment.** DERIVED, not independently sourced (same honesty treatment as
`decay.py`): bucketed directly off the already-DERIVED `urgency` (decay
fraction) value computed in Phase 2 — `none` if too-green-or-fresh,
`low`/`moderate`/`severe` at urgency thresholds of 0.25/0.6. Reusing the
existing decay proxy rather than inventing a second independent day-count
threshold keeps the honesty-flagged assumptions to one, not two stacked
ones.

## Decision 2: fairness ledger is a read-only stub tool this phase

**Superseded the next day by ADR-005 Decision 4.** `agents/fairness_stub.py`
was deleted and replaced with the real, storage-backed ledger
(`storage/fairness.py` — `weighted_bump_days`, `record_bump`,
`get_ledger_history`) described there, exposed to the advocate through
the same `@tool`-decorated lookup signature this phase built. Nothing in
the shipped codebase imports `fairness_stub` any more; the module itself
no longer exists as source. The paragraph below is kept as the original
Phase 3 design record, not silently edited out — see "Resolved on
review" at the end of this document for when and why this note was
added.

`agents/fairness_stub.py` holds an in-memory dict (`{farmer_id: bumped_last_season}`)
seeded so the Kamatchipuram scenario has a genuine fairness-vs-urgency
tension to negotiate over (see Decision 4), exposed to the advocate as a
`@tool`-decorated lookup — real Strands tool-calling, not just prompt
stuffing, and it demonstrates the SDK being used for more than structured
output alone. Phase 5 replaces the dict with DynamoDB behind the same
lookup signature.

## Decision 3: advocate agents are real Strands Agents, one construction per call

`agents/advocate.py`:

```python
def get_advocate_claim(
    facts: PlotFacts,
    *,
    model: Model | None = None,
    round_num: int = 1,
    opponent_argument: str | None = None,
) -> AdvocateClaim:
```

Builds a fresh `Agent(model=..., tools=[fairness_lookup], structured_output_model=AdvocateClaim, ...)`
per call rather than a long-lived conversational agent — each plot's
advocacy is a single-turn judgment given the current facts (and, in later
negotiation rounds, the opponent's prior argument), not a multi-turn
conversation with memory. This matches the brief's "structured claim, not
prose" framing and keeps each call independently retryable.

**Never throw from the agent loop:** wrapped in try/except. On any failure
(API error, throttling, a structured-output response that fails pydantic
validation entirely), logs the exception and the plot_id, and returns a
safe fallback claim: facts filled from ground truth, `concedes=False`,
a generic argument, `urgency_score` set from the real decay value. A
malformed or failed advocate call degrades to "argue normally, don't
concede" rather than taking down the coordinator's negotiation loop —
exactly the brief's requirement.

## Decision 4: negotiation is pairwise, capped at 3 rounds, escalates on no resolution

The coordinator only runs negotiation over Phase 2's `CONTESTED` plots —
`TOO_GREEN` plots get exactly one advocate call each (to produce their
concession) and never enter negotiation; `FITS` plots already have their
capacity slot and don't need one either.

**Scope simplification, disclosed rather than silently assumed:** contested
plots are paired up sequentially by rank (1st vs 2nd, 3rd vs 4th, ...) for
pairwise negotiation, not a general N-way auction. This is sufficient for
an 8-plot demo cluster where the Kamatchipuram scenario produces exactly
two contested plots (one pair), but a cluster with 3+ simultaneous
contested plots would need a real multi-party mechanism this doesn't
attempt. Flagging this now so it doesn't quietly become a load-bearing
assumption later.

Per pair, each round:
1. Both advocates give a claim (round 1: independent; later rounds: the
   side with the lower `urgency_score` from the previous round gets a
   chance to re-argue, this time with the fairness ledger explicitly
   surfaced in its prompt — "you were not selected last round; you were
   bumped N times previously; make your case again").
2. Resolve if: either side concedes (the other wins outright), or the two
   sides' scores (`urgency_score`, plus a fairness weight if
   `bumped_last_season`) are separated by more than a clear margin.
3. If still ambiguous after 3 rounds: **escalate.** This is the design
   intent, not a failure mode — a genuine, comparably-matched conflict
   between two farmers' claims is exactly what should reach a human, and
   the cap exists so the negotiation can't run indefinitely instead of
   escalating.

**The Kamatchipuram seed data was extended (not re-designed) to make this
a real test, not a trivial one:** the fairness stub marks `f04` (p04,
smaller/newer-transplanted, lower raw urgency) as bumped last season,
while `f03` (p03) has strictly higher raw urgency but no prior bump. That's
a genuine tension — more decayed crop vs. previously-shortchanged farmer —
that a pure score comparison shouldn't resolve cleanly, which is the point:
it's supposed to need the 3 rounds and then escalate, not get short-circuited
by round 1.

## Decision 5: testing — deterministic orchestration tests + one live proof

The coordinator's negotiation/escalation *logic* (round counting, score
comparison, concession handling, escalation payload construction) is
tested with an injected fake claim-producer (same `monkeypatch` pattern
already used for the weather bridging tests in Phase 1) rather than by
faking Strands' internal `Model` streaming protocol — implementing that
protocol correctly from scratch is a real undertaking with its own failure
surface, and Bedrock already verified working live, so there's no need for
a hand-rolled stub to stand in as the "real" path. This keeps the
Phase 3 gate ("exactly one escalation, too-green plots concede")
deterministic and fast in CI, independent of live model variance.

Separately, a `network`-marked test builds one real advocate `Agent` against
live Bedrock for a single too-green plot and asserts it produces
`concedes=True` with a valid structured claim — proof the real integration
works end to end, not just the orchestration shell around it. This test
asserts structure and the concession outcome, not an exact wording or
score, since live model output isn't bit-reproducible the way Phase 1/2's
deterministic paths are.

## Consequences

- Every number in an `AdvocateClaim` that reaches the coordinator's
  decision logic is ground-truth, not model output — verifiable by reading
  `get_advocate_claim()`, not just by trusting the prompt.
- The pairwise-only negotiation is a real scope limit for clusters larger
  than this demo; noted for Phase 5+ if the cluster size assumption changes.
- Bedrock is confirmed live for this AWS account/region/model combination
  as of 2026-08-16 — no stub fallback shipped, since none was needed.

## Resolved on review (2026-09-12)

A later code-and-docs audit (`docs/FEATURES.md`) found this document still
describing `agents/fairness_stub.py` as the current fairness mechanism,
with the DynamoDB migration framed as future "Phase 5" work — a file that
no longer exists in source, and a migration that in fact shipped the very
next day, in ADR-005 Decision 4. A forward-pointer has been added to
Decision 2 above recording this. No behavior changed: the real ledger in
`storage/fairness.py` has been the shipped mechanism since ADR-005, this
document's Decision 2 was simply never updated to say so.
