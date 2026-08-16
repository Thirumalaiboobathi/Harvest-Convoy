# ADR-000: Stack and Architecture Decisions

- Status: Proposed (awaiting go-ahead)
- Date: 2026-08-16

## Context

Harvest Convoy is a submission to the AWS "Agents for Humans" hackathon (Devpost,
deadline 2026-09-14 17:00 PDT), track **Good Neighbor Agents**. The product
reschedules a village cluster's single shared combine harvester across multiple
farmers' plots as weather and crop maturity change, escalating to a human only
when two plots genuinely conflict for the same slot.

Two properties of the problem shape every decision below:

1. **The coordination is the product.** A single farmer with his own machine has
   no scheduling conflict to resolve. Any design that could be satisfied by one
   farmer and one agent talking to each other is off-thesis for this track.
2. **The farmer is silent for three months.** Registration is four messages in
   May; the agent should not need the farmer to answer questions to do its job
   between then and harvest. This rules out designs where plot state depends on
   the agent interviewing the farmer mid-season.

The hackathon rubric also imposes hard requirements: mandatory use of the
Strands Agents SDK (judged on depth of use, not presence), a public MIT-licensed
repo with an About-section license, a README sufficient to run the project cold,
a committed architecture diagram, a ≤5-minute demo video, and OpenTelemetry-style
observability. Amazon Bedrock AgentCore deployment is optional but explicitly
scored under Technical Implementation.

## Decision

We adopt the stack fixed in the project brief, §3:

| Area | Decision | Rationale |
|---|---|---|
| Language | Python 3.12 | Strands SDK and AWS tooling (boto3, AgentCore SDK) are Python-first; 3.12 is current stable with good typing support. |
| Agent framework | Strands Agents SDK | Mandatory per rules. Used for both the coordinator agent and the per-plot advocate agents, with structured tool contracts — not a single prompt call. |
| Model | Amazon Bedrock Nova Pro, `apac.amazon.nova-pro-v1:0`, region `ap-south-1` | Nova Pro balances cost and reasoning quality for a hackathon budget; `ap-south-1` (Mumbai) is the nearest Bedrock region to Theni, Tamil Nadu, minimizing latency for a scheduled daily job. **Open verification item below.** |
| Interface | Telegram Bot API | WhatsApp Business API requires Meta business verification, which routinely takes longer than the weeks we have. Telegram's Bot API needs only a token from BotFather and supports inline keyboards, which we need for the one-tap escalation. WhatsApp is noted in the README as the production path. |
| State | DynamoDB single table (plot/season state) + AgentCore Memory (per-farmer negotiation history) | DynamoDB fits the access pattern (lookup by plot/season, scan by cluster) with zero ops burden. AgentCore Memory is purpose-built for per-actor conversational/negotiation history and integrates natively with the Strands + AgentCore Runtime deployment path, avoiding a second bespoke store for the same kind of data. |
| Deployment | Bedrock AgentCore Runtime, scheduled daily invocation | Directly scores under the optional-but-weighted AgentCore criterion; a daily scheduled watcher matches the actual cadence of the problem (weather changes day to day, not minute to minute). |
| Observability | OpenTelemetry traces on every agent turn and tool call | Required to substantiate "the LLM did not compute the number" and to produce a real trace artifact for Phase 6's gate. |
| Package manager | `uv` | Fast, single lockfile, minimal ceremony for a solo hackathon build. |
| Tests | `pytest` | Standard; used to hand-verify agronomy/scheduling math (Phase 1–2 gates) as unit tests, not just agent-level demos. |

**Architectural split (the load-bearing decision of this ADR):** all agronomy
math (GDD accumulation, maturity projection, overripe decay) and all scheduling
math (harvest-day budget, capacity solving, route ordering) live in deterministic
Python modules with unit tests and zero LLM involvement. The Strands agents
(coordinator + per-plot advocates) consume the *outputs* of that math as
structured tool results and are restricted to: ranking under ambiguity,
negotiation dialogue, concession judgment, and message generation. No agent
is ever given the ability to produce a GDD value, a maturity date, or an acreage
figure — those are tool calls into `agronomy/` and `scheduling/`, not model
completions. This is what makes "did the LLM compute that number?" answerable
as "no" in the demo.

## Alternatives Considered

- **WhatsApp instead of Telegram.** Rejected: Meta business verification timeline
  is incompatible with a 4-week build. Documented as the production path in the
  README rather than silently dropped.
- **Single agent instead of coordinator + per-plot advocates.** Rejected: a
  single agent reasoning over all plots internally would satisfy the Strands
  "depth of use" criterion poorly (one prompt, dressed up) and would make the
  fairness-ledger-weighted negotiation and explicit `concedes: true` outcome
  harder to make legible in traces and in the demo. Multiple agents with a
  structured contract (§4 of the brief) is both more faithful to the real
  multi-party conflict and better demo material.
- **LLM-computed agronomy/scheduling.** Rejected outright per the brief's
  critical architectural rule — a model-produced GDD or capacity number is
  unverifiable and undermines the entire submission's credibility with a judge
  who checks.
- **Self-hosted agent runtime (e.g., plain Lambda + Strands, no AgentCore).**
  Considered as a lower-risk fallback if AgentCore proves immature or its
  Memory API doesn't fit. Not chosen now because AgentCore deployment is
  explicitly weighted in scoring, but flagged as the fallback if Phase 6
  reveals a blocking problem.
- **RDS/Postgres instead of DynamoDB.** Rejected: access patterns are simple
  key/scan lookups per cluster and season; DynamoDB avoids provisioning a
  relational instance for a hackathon-scale demo cluster (8 plots).

## Consequences

**Positive:**
- Clear separation lets us unit-test the parts a judge is most likely to
  distrust (the numbers) independently of the parts that are inherently
  non-deterministic (the negotiation).
- Telegram + AgentCore + Strands together cover three of the rubric's explicit
  scoring hooks (SDK depth, AgentCore, a working live demo channel) without
  extra services.
- Single DynamoDB table + AgentCore Memory keeps infra surface small enough to
  actually finish in four weeks.

**Negative / risks, carried forward:**
- **Nova Pro model ID and region availability is unverified as of writing.**
  I have not confirmed against the live AWS Bedrock console/docs that
  `apac.amazon.nova-pro-v1:0` is a valid cross-region inference profile ID
  callable from `ap-south-1`, or that Nova Pro is enabled for it in this
  account. This must be checked in Phase 0 setup (`aws bedrock list-inference-profiles`
  or equivalent, plus a smoke-test invocation) before Phase 3 depends on it.
  I'm flagging this now rather than assuming it's correct.
- **AgentCore Memory is a newer, less battle-tested API than DynamoDB.** If
  Phase 5 reveals its negotiation-history model doesn't fit the fairness-ledger
  use case cleanly, the fallback is to fold that history into the same
  DynamoDB table and drop AgentCore Memory specifically (keeping AgentCore
  Runtime for deployment).
- **Telegram's audience reach is worse than WhatsApp's in rural Tamil Nadu in
  practice.** This is a known, disclosed limitation, not a technical one — it
  belongs in the README's honesty section (§9 of the brief), not in this ADR's
  scope to solve.
- Single shared table + optional Memory store means schema changes later in
  the build (Phase 5) touch code written in Phases 1–4; mitigated by keeping
  `storage/` as the only module that knows the table shape.

## Open items before Phase 0 scaffolding proceeds

1. Confirm Nova Pro model ID / region access in the target AWS account.
2. Confirm a Telegram bot token can be created (BotFather) under the account
   that will run the live demo.

Neither blocks writing code, but both should be confirmed before Phase 3/4
depend on them working.
