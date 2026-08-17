# ADR-006: Deployment — AgentCore Runtime, Scheduled Watcher, DynamoDB, Tracing

- Status: Proposed (awaiting go-ahead — **nothing in this ADR has been provisioned**)
- Date: 2026-08-17

## Provisioning summary — read this first

Everything below is a proposal. Nothing has been created. Region for
everything is **ap-south-1** (matches Bedrock/AgentCore already in use).

| Resource | Purpose | Standing cost |
|---|---|---|
| S3 bucket | Holds the zipped code artifact for AgentCore Runtime's `codeConfiguration` deploy path | ~$0.0001/month (few MB) |
| IAM role (Runtime execution) | Lets AgentCore Runtime call Bedrock, DynamoDB, CloudWatch on the code's behalf | $0 |
| IAM role (Scheduler invoke) | Lets EventBridge Scheduler call `InvokeAgentRuntime` | $0 |
| AgentCore Runtime (1 resource) | Runs `app.py`, `PUBLIC` network mode (no VPC) | ~$0.05/month at daily-run scale (below) |
| EventBridge Schedule (1 rule) | Fires the daily watcher | ~$0 (14M free invocations/month; we use ~30) |
| DynamoDB table `harvest_convoy` | On-demand (`PAY_PER_REQUEST`) billing, schema from ADR-005 | ~$0.01/month at this scale |
| CloudWatch Logs / X-Ray traces | AgentCore Runtime's native ADOT auto-instrumentation | Traces likely within free tier; logs negligible (KB/day) |

**Nova Pro inference (Bedrock, already in use, not new)**: measured, not
estimated — see Decision 6. Real 8-plot run: **$0.0227**. Dominates every
other line above by 10-50x, and it's the one cost that exists regardless
of whether anything in this ADR gets built.

**Rough worst-case total: under $2/month.** Against a $50 credit balance,
running this unattended for the full hackathon window costs low single
digits of dollars even under pessimistic assumptions. Flagging the honest
uncertainty: this is the first time this account creates an AgentCore
Runtime resource, so there's residual risk of a first-time setup surprise
(quota request, a permission gap) — not a cost risk, an "it might not work
on the first try" risk, covered by the fallback in Decision 1.

## Decision 1: AgentCore Runtime — code deployment, not container, PUBLIC network

Verified live before proposing this: `list-agent-runtimes` and
`list-memories` both succeed (exit 0) in `ap-south-1` for this account,
and AWS's own release notes list Asia Pacific (Mumbai) among the regions
AgentCore Runtime shipped to as of November 2025. Control-plane access is
real, not assumed. **Actual `create-agent-runtime` has not been attempted**
— that's the provisioning action this ADR is asking permission for.

`create-agent-runtime` supports two artifact types: `containerConfiguration`
(build and push a Docker image to ECR) and `codeConfiguration` (zip source,
upload to S3, specify a Python runtime + entry point — no Docker, no ECR).
**Using `codeConfiguration`**: `runtime=PYTHON_3_12` (matches the project's
locked Python version exactly), `entryPoint` pointing at `app.py`. Simpler,
fewer resources (no ECR repo), and directly avoids needing Docker running
locally, which was already the reason FileStorage beat DynamoDB Local in
ADR-005.

`app.py` becomes a real entrypoint using the `bedrock_agentcore` SDK's
documented pattern:

```python
from bedrock_agentcore import BedrockAgentCoreApp
app = BedrockAgentCoreApp()

@app.entrypoint
async def handler(payload: dict) -> dict:
    # payload: {"cluster_id": "kamatchipuram", "season_id": "2026-kuruvai"}
    return await run_daily_watch(payload["cluster_id"], payload["season_id"])

app.run()
```

Network mode: **`PUBLIC`**, not `VPC` — avoids provisioning subnets/security
groups entirely, and the code only talks to public AWS APIs (Bedrock,
DynamoDB, Open-Meteo, Telegram) plus outbound HTTPS, none of which need
private networking.

**Fallback, per your instruction:** if `create-agent-runtime` fails for
this account/region (quota, preview allowlist, anything not visible from
read-only calls), the watcher logic itself (Decision 2) is written against
a plain `async def handler(payload) -> dict` function signature with no
AgentCore-specific code inside it — swapping the deployment shell to a
Lambda function on an EventBridge (Events, not Scheduler) rule is a
different entrypoint wrapper around the identical function, not a rewrite.
I will not claim AgentCore Runtime deployment succeeded without showing
the actual `create-agent-runtime` response.

New dependency: `bedrock-agentcore` (the SDK providing `BedrockAgentCoreApp`
— confirmed real on PyPI, current version 1.21.0).

## Decision 2: scheduled daily watcher — trigger condition and idempotency

**Trigger condition**, stated precisely: reuses `scheduling/capacity.py`'s
existing `usable_harvest_days()` — if it returns the *full* forecast length
(no day breaches the rain threshold), there's nothing to react to: **no
message, no DynamoDB write beyond the idempotency marker, silence.** If
it returns less than the full length, rain is coming within the horizon —
that's the trigger, and the full `solve()` → `run_cluster()` pipeline runs.

**Idempotency (the "fires twice for the same day" failure path), a real
persisted guard, not in-memory:** one item per cluster,
`PK=CLUSTER#{cluster_id}`, `SK=WATCHER#RUN`, holding `last_run_date`
(IST calendar date, matching the weather timezone from ADR-001). On
invocation: read this item; if `last_run_date == today`, log a no-op
("already ran today") and exit — this is what makes a duplicate
EventBridge fire (or a manual re-invoke) a safe no-op instead of a second
round of farmer messages.

**The marker is only written on a *completed* check**, whether that check
found a trigger or not — "checked, no rain, did nothing" and "checked,
triggered, ran the pipeline" both count as done for the day.
**A failed check (Open-Meteo down, an unhandled exception) does not write
the marker** — so tomorrow's scheduled run isn't silently skipped over a
day that never actually got evaluated. This is a per-cluster, all-or-nothing
marker, not per-plot — a genuine, disclosed simplification: a crash
mid-negotiation (Decision-required failure path below) means the whole
cluster's check is retried next time, not resumed plot-by-plot. Precise
partial-run recovery is a real future improvement, not built now.

**Failure paths, explicit:**
- **Open-Meteo down**: `WeatherError` propagates up through `solve()`;
  the watcher's top-level handler catches it, logs
  `"weather fetch failed, cluster={id}, will retry next scheduled run"`
  at ERROR, does not write the marker, sends nothing. No fabricated data.
- **Partial run dies mid-negotiation**: the whole per-cluster body runs
  inside one top-level `try/except`. Anything that escapes the
  per-advocate-call and per-negotiation degrade-and-log layers already
  built in Phase 3 is caught here too, logged with cluster_id and
  whatever plot was being processed when it happened, and — critically —
  the marker is still not written, so it's retried rather than silently
  marked complete with some farmers unnotified.
- **Fires twice same day**: covered above — the marker check is the first
  thing the handler does.

## Decision 3: DynamoDB live round trip

Ask, per your rule: create table `harvest_convoy` in `ap-south-1`,
**on-demand billing (`PAY_PER_REQUEST`)** — no provisioned capacity to
size or leave idle. Schema exactly as documented in ADR-005: `PK`/`SK`
both `String`, one GSI (`GSI1PK`/`GSI1SK`, both `String`) for the
cluster-to-children queries.

```bash
aws dynamodb create-table --region ap-south-1 \
  --table-name harvest_convoy \
  --attribute-definitions \
      AttributeName=PK,AttributeType=S AttributeName=SK,AttributeType=S \
      AttributeName=GSI1PK,AttributeType=S AttributeName=GSI1SK,AttributeType=S \
  --key-schema AttributeName=PK,KeyType=HASH AttributeName=SK,KeyType=RANGE \
  --global-secondary-indexes '[{"IndexName":"GSI1","KeySchema":[{"AttributeName":"GSI1PK","KeyType":"HASH"},{"AttributeName":"GSI1SK","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}]' \
  --billing-mode PAY_PER_REQUEST
```

Then: `seed_cluster.py --write` with `HARVEST_CONVOY_STORAGE=dynamo`, and
re-run the exact Phase 2/3/5 gate scenarios against `DynamoStorage` instead
of `FileStorage` — this closes the honest gap Phase 5 disclosed (encode/
decode helpers and the degrade path were unit tested; a live round trip
never was). `FileStorage` stays the default in code — this only proves the
`DynamoStorage` path for real, it doesn't flip the default.

## Decision 4: AgentCore Memory — reconsidered, still skipped

You asked me to actually re-examine this, not repeat Phase 5's answer.
What would have changed the calculus: if deployment meant advocate agents
needed to recall their *own prior phrasing* across negotiation rounds or
seasons — "I argued X last time, let me try Y" — that's a genuine
semantic/conversational memory shape AgentCore Memory is built for, and
DynamoDB genuinely serves it worse.

That's not what this system does, deployed or not. Advocate agents are
still deliberately single-turn per call (ADR-003 Decision 3) — each round
gets fresh facts plus the opponent's argument text passed explicitly as
a string, not recalled from memory. The fairness ledger's actual shape —
`season_id`, `days_bumped`, `outcome` — is structured data queried by
exact key, the case DynamoDB is built for, not semantic retrieval over
unstructured history. Deployment changes *where* the code executes; it
doesn't change what data the advocate needs or in what shape. Nothing
about running on AgentCore Runtime creates a new need for Memory's
specific capability (semantic extraction/retrieval) over a keyed query.

**Still skipped, same reasoning, now re-verified rather than assumed.**

## Decision 5: OTel tracing — rely on AgentCore Runtime's native ADOT integration, not a hand-rolled SigV4 exporter

Found something worth designing around: **Strands already emits rich
OpenTelemetry spans automatically**, using GenAI semantic conventions
(`gen_ai.usage.input_tokens`, `gen_ai.request.model`, `execute_tool`,
`execute_event_loop_cycle`, `invoke_agent` spans, full message content as
span events) — confirmed directly while measuring token usage for
Decision 6, not assumed. This means "every agent turn and tool call
traced" is already true today, locally, with the console exporter — the
open question is only *where the traces land*, not whether they exist.

AgentCore Runtime has a native answer for that: when the agent runs on
Runtime, setting `AGENT_OBSERVABILITY_ENABLED=true` plus a small set of
`OTEL_*` environment variables (`OTEL_PYTHON_DISTRO=aws_distro`,
`OTEL_PYTHON_CONFIGURATOR=aws_configurator`,
`OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, `OTEL_TRACES_EXPORTER=otlp`)
on the Runtime resource makes AWS's ADOT distribution auto-instrument the
process and export straight to CloudWatch (viewable via CloudWatch
Transaction Search / GenAI Observability) — no custom exporter code, no
manual SigV4 signing.

**Decision: use that, not a hand-built SigV4-authenticated OTLP HTTP
exporter.** The X-Ray/CloudWatch OTLP traces endpoint requires SigV4
auth, which the standard `opentelemetry-exporter-otlp-proto-http` package
doesn't do natively — the normal way to bridge that outside AgentCore
Runtime is a local ADOT Collector sidecar with the `sigv4authextension`,
which is more infrastructure than this project needs when the actual
deployment target already does this natively. Building a custom signer
would be new, fragile, untested code solving a problem AWS's own runtime
already solves for the path this is actually deploying to.

`observability/otel.py` gets one change, not a rewrite:
`configure_tracing()` currently assumes it always owns the global
`TracerProvider`. It needs to check whether one is already configured
(by ADOT's auto-instrumentation, which runs before application code) and
defer to it rather than silently overwrite it — otherwise running on
AgentCore Runtime would clobber ADOT's exporter with the local console
one. New dependency: `aws-opentelemetry-distro` (provides the
`aws_distro`/`aws_configurator` entry points those env vars reference;
confirmed real on PyPI, 0.19.0).

Local runs (not deployed) keep the console exporter exactly as today —
this doesn't change how `trigger_scenario.py` or the test suite behave.

## Decision 6: token cost — measured, not estimated

Ran the real 8-plot Kamatchipuram scenario end to end against live
Bedrock (`run_cluster`, real `AdvocateClaim`s, real `fairness_lookup` tool
calls), wrapping each call to capture `result.metrics.accumulated_usage`.

| | value |
|---|---|
| Advocate calls made | 8 (the p03/p04 negotiation resolved in round 1 — p04 conceded live, so this run never reached round 2 or 3) |
| Total input tokens | 20,947 |
| Total output tokens | 1,855 |
| Total cost (Nova Pro: $0.80/M in, $3.20/M out) | **$0.0227** |
| Average per call | ~2,618 in / ~232 out |

**Not "high" in absolute terms** — 2.3 cents for a full scheduling pass;
30 days of daily runs at this rate is under $0.70/month. But the
per-call average is worth explaining, since you asked specifically about
tightening: each call is actually *two* LLM round-trips under the hood
(reason-and-call-`fairness_lookup`, then reason-and-emit-`AdvocateClaim`),
and the full system prompt (~500 tokens) plus both tool schemas
(`fairness_lookup` and the `AdvocateClaim` structured-output schema,
which Strands auto-generates with a `description` on every field) are
resent in full on *both* turns — that's the dominant cost, not the
few lines of actual plot facts.

**Worst case, calculated (not measured) from the same per-call average:**
a full 3-round negotiation that never resolves early costs 4 calls for
the contested pair instead of 2 (round 1 both sides, rounds 2-3 the
losing side only) — roughly 6 + 4 = 10 calls instead of 8, ≈ **$0.028**
per scenario. Still trivial.

**One concrete tightening, worth doing regardless of the dollar amount
because it's structural, not cosmetic:** enable Bedrock prompt caching
(`BedrockModel(..., cache_config=CacheConfig(strategy="auto"))`) for the
system prompt and tool configuration, which are byte-identical across
every one of the 8+ calls in a run. This is the actual lever — trimming
prose in the system prompt saves tens of tokens; caching the ~2,400
repeated tokens (system prompt + both tool schemas) across calls is the
one that matters. Proposing to implement this as part of Phase 6, not
defer it.

## Gate: runs unattended 24h, trace is showable — verification harness

Since 24h outlives this session, `scripts/check_watcher_health.py` (new)
is what you run tomorrow. It does not require me to be present:

```bash
uv run python -m scripts.check_watcher_health --cluster kamatchipuram
```

Checks, in order, each printed pass/fail:
1. Reads the `WATCHER#RUN` marker from DynamoDB for the cluster — confirms
   `last_run_date` is today (proves the scheduled invocation actually
   fired and completed, not just that EventBridge attempted it).
2. Prints the CloudWatch Logs group/stream ARN and the last invocation's
   log lines, so you can see the actual watcher output.
3. Prints a direct CloudWatch console URL, pre-filtered to this
   invocation's trace ID, for the visual "show the trace" ask —
   `invoke_agent` → `execute_event_loop_cycle` → `chat`/`execute_tool`
   spans nested exactly as observed locally in Decision 6, now in
   CloudWatch instead of console output.
4. If a trigger fired: confirms the escalation (if any) and the Telegram
   sends succeeded, cross-checked against the DynamoDB ledger if a bump
   was recorded.
5. If no trigger: confirms the no-op was logged (the "silence is the
   product" case), not just silent absence of evidence.

## Consequences

- First AgentCore Runtime resource this account has created — genuine,
  disclosed uncertainty about a clean first attempt, with a stated
  fallback (Lambda + EventBridge) that doesn't require rewriting the
  watcher logic.
- DynamoDB becomes real for this project for the first time; FileStorage
  remains the code default.
- No custom OTLP/SigV4 code — deliberately deferring to AgentCore
  Runtime's native ADOT integration rather than building a second,
  untested path to the same destination.
- AgentCore Memory stays out, now on a re-examined basis specific to
  deployment, not a copy-pasted Phase 5 answer.
- Real, measured token/dollar cost on record before any scaling decision,
  plus one concrete structural optimization (prompt caching) queued for
  implementation.
