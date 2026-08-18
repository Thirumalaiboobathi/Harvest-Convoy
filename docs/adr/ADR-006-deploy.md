# ADR-006: Deployment — AgentCore Runtime, Scheduled Watcher, DynamoDB, Tracing

- Status: Implemented — all resources below are live in `ap-south-1`.
  Runtime redeployed 2026-08-18 with ADR-008's code (Tamil support,
  per-cluster GDD, multi-cluster-capable watcher), version 6 → 11 across
  two rounds that day — see Decision 9 (versions 7–10: the redeploy
  itself, a DynamoDB int/float bug, an unhandled Open-Meteo forecast gap,
  a never-set `TELEGRAM_BOT_TOKEN`) and Decision 10 (version 11: two
  Tamil half-translation bugs found by live Telegram verification, not
  string review — see ADR-008 Decision 17). Deviations from the original
  proposal, all found live and fixed the same session they were found:
  Decision 7 (Scheduler can't call `InvokeAgentRuntime` directly),
  Decision 8 (ADOT needs one more env var than Decision 5 assumed).
- Date: 2026-08-17; redeploy rounds 2026-08-18

## Provisioning summary — read this first

Everything below is a proposal. Nothing has been created. Region for
everything is **ap-south-1** (matches Bedrock/AgentCore already in use).

| Resource | Purpose | Standing cost |
|---|---|---|
| S3 bucket | Holds the zipped code artifact for AgentCore Runtime's `codeConfiguration` deploy path | ~$0.0001/month (few MB) |
| IAM role (Runtime execution) | Lets AgentCore Runtime call Bedrock, DynamoDB, CloudWatch, X-Ray on the code's behalf | $0 |
| IAM role (Scheduler invoke) | Lets EventBridge Scheduler call `lambda:InvokeFunction` on the shim (Decision 7 — not a direct `InvokeAgentRuntime` call as originally planned) | $0 |
| Lambda function (`harvest-convoy-watcher-invoker`) | Thin shim: Scheduler → this → boto3 `invoke_agent_runtime()` → Runtime (Decision 7) | ~$0 (one ~0.1-25s invocation/day, free tier) |
| IAM role (Lambda execution) | Lets the shim call `bedrock-agentcore:InvokeAgentRuntime` + basic CloudWatch Logs | $0 |
| AgentCore Runtime (1 resource) | Runs `app.py`, `PUBLIC` network mode (no VPC) | ~$0.05/month at daily-run scale (below) |
| EventBridge Schedule (1 rule) | Fires the daily watcher via the Lambda shim | ~$0 (14M free invocations/month; we use ~30) |
| DynamoDB table `harvest_convoy` | On-demand (`PAY_PER_REQUEST`) billing, schema from ADR-005 | ~$0.01/month at this scale |
| CloudWatch Logs / X-Ray traces | AgentCore Runtime's ADOT integration, direct-to-X-Ray SigV4 export (Decision 8) | Traces likely within free tier; logs negligible (KB/day) |

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

**One concrete tightening, implemented this phase:** Bedrock prompt
caching via an explicit `cachePoint` block on the system prompt
(`CACHED_SYSTEM_PROMPT = [{"text": SYSTEM_PROMPT}, {"cachePoint":
{"type": "default"}}]`), not Strands' `CacheConfig(strategy="auto")` —
that caches at the wrong message boundary and measurably didn't help;
tool-config caching was tried and rejected outright by Nova Pro (a real
`ValidationException`, not a guess). Measured before/after on the same
8-call scenario: **$0.0227 → $0.0081, a 64% reduction** — the cached
system prompt is reused across all 8 advocate calls in a run instead of
re-sent in full every time.

## Decision 7: EventBridge Scheduler can't call `InvokeAgentRuntime` directly — Lambda shim in between

The original plan (Decision 1's fallback aside) was a direct Scheduler
**universal target** calling `bedrock-agentcore:InvokeAgentRuntime`. Built
it, and it fails live: `AssumeRole` on the Scheduler's invoke role
succeeds, but the actual API call errors before reaching the runtime —
confirmed via CloudWatch (`AWS/Scheduler` namespace, dimensioned by
`ScheduleGroup`): `InvocationAttemptCount=1`, `TargetErrorCount=1`,
`InvocationDroppedCount=1`, and zero new lines in the runtime's own log
group for that window.

Root cause, found by reading `InvokeAgentRuntimeRequest`'s botocore model
directly: its request body is a `payload` **payload-trait blob** (the
entire HTTP body, not a JSON object), with `contentType`/`accept` as
header-located fields, not body fields. Universal targets marshal a plain
JSON object into a standard JSON request body — that shape doesn't fit an
API whose body *is* a blob. (Confirmed the resource ARN and IAM action
name were right — `create-schedule` validated the JSON shape and even
told me the exact required field names, `AgentRuntimeArn`/`Payload` in
PascalCase, once I got the service identifier — `bedrockagentcore`, no
hyphen, differs from the boto3 client name — right. The failure is
specifically the blob body, not a naming mistake.)

**Fix: a ~20-line Lambda (`harvest-convoy-watcher-invoker`) as a thin
shim.** It calls `bedrock_agentcore.invoke_agent_runtime()` via boto3
(handles the blob-body marshalling correctly, since that's what the SDK
is for) and returns the response. EventBridge Scheduler targets the
Lambda instead, using the ordinary, well-supported Lambda target type —
no universal-target ambiguity. The daily schedule
(`harvest-convoy-daily-watch`, `cron(0 6 * * ? *)`, `Asia/Kolkata`) now
points at this Lambda; `harvest-convoy-scheduler-invoke`'s permissions
changed from (unused) `bedrock-agentcore:InvokeAgentRuntime` to
`lambda:InvokeFunction` scoped to the shim.

One more real finding along the way, not a guess: IAM resource-level
authorization for `InvokeAgentRuntime` is checked against
`.../runtime/<id>/runtime-endpoint/DEFAULT`, not the bare runtime ARN —
the Lambda's execution role needed both ARNs in its policy `Resource`
list before a direct boto3 call succeeded (verified via a live
`AccessDeniedException` naming the exact resource it checked).

`app.py`'s payload contract gained one optional field, `force: bool`
(default `false`) — bypasses the rain-trigger gate so a real invocation
can exercise the full pipeline (including negotiation) on demand, for
verification and for `scripts/check_watcher_health.py --invoke`, without
waiting for an actual rainy forecast. Every scheduled run still uses the
real trigger condition; nothing about production behavior changed.

## Decision 8: ADOT needs `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` set explicitly — Decision 5's env var list was incomplete

Decision 5 assumed `AGENT_OBSERVABILITY_ENABLED=true` plus the standard
`OTEL_TRACES_EXPORTER=otlp`/`OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`
pair would be enough for AWS's ADOT distro to export straight to
CloudWatch. Live, it wasn't: every span export attempt failed with
`Connection refused` to `localhost:4318` — confirmed with a one-line
socket-reachability diagnostic added to `observability/otel.py` and
deployed, which reported both `4318` and `4317` unreachable inside the
running container. No local collector is present in this
`codeConfiguration` (direct-code, non-container) deployment mode.

Root cause, found by reading the installed
`aws_opentelemetry_configurator.py` source directly rather than guessing
further: `_customize_span_exporter()` only swaps in AWS's SigV4-signed
direct-to-X-Ray exporter when `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` (or
`OTEL_EXPORTER_OTLP_ENDPOINT`) is set **and** matches the pattern
`https://xray.<region>.amazonaws.com/v1/traces`. Without it, the SDK
falls through to a plain `OTLPSpanExporter` pointed at the OTel-standard
default (`localhost:4318`), which nothing in this deployment mode is
listening on. This isn't documented as a required variable anywhere in
AWS's AgentCore observability guide for the "hosted inside AgentCore
Runtime" path — found by reading the library, not the docs.

**Fix: added `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://xray.ap-south-1.amazonaws.com/v1/traces`**
to the runtime's environment variables. No code or dependency change.
Verified live: the `Connection refused` spam is gone, replaced by
successful span export, and CloudWatch Logs (`aws/spans`) shows real
records with correct `traceId`/`spanId`/`parentSpanId` nesting — 9 levels
deep for a real negotiation run: `app.daily_watch` → `coordinator.run_cluster`
→ `advocate.get_claim` → Strands' own `invoke_agent Strands Agents` →
`execute_event_loop_cycle` → `chat`/`execute_tool AdvocateClaim`.

**One open item, disclosed rather than papered over:** the specific
`coordinator.negotiate`/`negotiation.round` spans (added earlier this
phase specifically for round-by-round visibility) weren't exercised
during this verification. `run_cluster()` only calls `negotiate_pair()`
for plots the full cluster-wide `solve()` classifies as `CONTESTED`
against each other — the live seeded data doesn't currently produce that
(confirmed by temporarily reproducing `trigger_scenario.py`'s known
deadlock dates for p03/p04 directly in the live DynamoDB table, then
reverting them: still zero pairing, meaning the full-cluster capacity
solve treats them differently than the isolated two-plot harness
`trigger_scenario.py` uses). What *is* verified: (a) the span code itself
nests `coordinator.negotiate`/`negotiation.round` correctly — confirmed
earlier this phase with a local custom `SpanExporter` capturing real
parent/child span IDs; (b) the export pipeline faithfully preserves deep,
correct nesting for whatever spans a run actually produces, proven live
just above. Nothing about the export mechanism is span-name-specific, so
these two facts together are strong evidence this pair would export
correctly too — but it has not been directly, individually observed
landing in CloudWatch, and I'm not claiming it has.

## Gate: runs unattended 24h, trace is showable — verification harness

Since 24h outlives this session, `scripts/check_watcher_health.py` (new)
is what you run tomorrow. Read-only by default, does not require me to be
present:

```bash
uv run python -m scripts.check_watcher_health --cluster-id kamatchipuram
uv run python -m scripts.check_watcher_health --invoke   # also makes one real call
```

Checks, in order, each printed `[OK]`/`[WARN]`/`[FAIL]`:
1. Reads the `WATCHER#RUN` marker from DynamoDB for the cluster (forces
   `HARVEST_CONVOY_STORAGE=dynamo` regardless of local `.env` — this
   script only ever checks deployed state) — confirms `last_run_date` is
   today, proving the scheduled invocation actually fired and completed,
   not just that EventBridge attempted it.
2. Checks the Lambda shim's CloudWatch metrics/logs for the last 25h:
   at least one invocation recorded, zero `ERROR`-level log lines.
3. Filters the AgentCore Runtime's own log group for the last 25h for
   `ERROR`/`Traceback` lines.
4. With `--invoke`: makes one real (non-forced) call through the same
   Lambda shim the schedule uses, and reports the returned status.

Trace inspection itself is manual (CloudWatch console → Logs Insights →
`aws/spans`, or `aws logs filter-log-events` on that log group) — the
health-check script confirms the pipeline ran and didn't error, not the
trace shape specifically; Decision 8 documents how that was verified
live during this session.

## Decision 9: redeploying with ADR-008's code (2026-08-18) — two more live-found bugs, and a build process that didn't exist

ADR-008 (Tamil Nadu generalization, Tamil-language interface) changed
`harvest_convoy`'s source but the deployed AgentCore Runtime still ran
the pre-ADR-008 code from Decision 1's original deployment (version 6) —
the standing instruction throughout ADR-008 was explicitly to leave the
deployed artifact alone until asked. Asked, here: redeploy with current
code, verify the schedule/shim survive, prove real messages land.

**No committed build process existed for the deployment package.** The
zip AgentCore Runtime actually runs (`main.py` + vendored `arm64`
dependencies + the `harvest_convoy` source tree, ~36MB) was built once,
ad hoc, in the original Decision 1 session, and never turned into a
script. Reconstructed the exact recipe by downloading and inspecting the
live zip directly rather than guessing: `uv export --no-emit-project
--no-dev --no-hashes --frozen` for the pinned dependency list (confirmed
byte-identical package/version set to what was already deployed, since
`uv.lock` hadn't changed), `uv pip install --target <dir>
--python-platform aarch64-unknown-linux-gnu --python 3.12 -r
<that list>`, then the current `src/harvest_convoy/` tree and an
unchanged `main.py` wrapper copied in alongside it, zipped with
permission bits preserved. Verified before uploading anything: identical
top-level package set to the currently-deployed zip (`old - new` and
`new - old` both empty), and every copied `.py` file compiles cleanly.

**Redeploy mechanics, verified rather than assumed**: `update-agent-runtime`
against the existing `agentRuntimeId` creates a new numbered version
(6→7) without changing the runtime's ARN; the `DEFAULT` runtime endpoint
tracks the latest version automatically (`list-agent-runtime-endpoints`
showed `liveVersion` advance on its own after each update, no separate
endpoint-repoint call needed). The Lambda shim invokes by the stable
runtime ARN, not a version-pinned one, so it needed no changes. Confirmed
directly, not inferred: the Lambda's `LastModified` and the EventBridge
Schedule's `LastModificationDate` were both still their original
Decision 7 values after every update in this round — the schedule and
shim were never touched, only the runtime's code artifact.

**Two more real bugs, found by actually trying to receive a message, not
by reading code:**

1. **`DynamoStorage._from_decimal` always cast to `float`.** DynamoDB's
   Number type doesn't distinguish int from float, so every numeric
   field — including `Farmer.telegram_chat_id`, typed `int` — round-tripped
   through a real table as `Decimal` and got unconditionally converted to
   `float`. A live farmer's chat ID, wired up for this verification, came
   back as `1276258406.0`. `telegram/client.py` puts that value straight
   into the JSON request body; a JSON float is not a valid Telegram
   `chat_id`. Fixed: `_from_decimal` now returns `int(value)` when the
   `Decimal` has no fractional part, `float(value)` otherwise — a
   genuinely fractional value (acreage, GDD figures) is unaffected.
   Pre-existing bug, not introduced by ADR-008; never caught before
   because no prior session had written a real int through this path and
   then read it back through a real table.
2. **The forecast fetch had no tolerance for Open-Meteo's own rolling
   compute schedule.** Requesting the full `FORECAST_MAX_HORIZON_DAYS`
   (16 days) window returned `precipitation_sum: null` for day 16,
   reproducibly, confirmed with a direct API call outside this project's
   code — Open-Meteo computes near-term days first, and the far edge of
   a 16-day request isn't always populated yet. `get_precipitation_forecast`
   raised `WeatherError` on any null value, which meant the watcher
   failed before reaching the scheduling logic at all — and `force: true`
   does not bypass this, since the forecast fetch happens before the
   force check. Fixed: a trailing run of null days is now trimmed (a
   shorter, still-real usable window), while a null anywhere else in the
   series still raises — that distinction matters, since a null in the
   *middle* of the series is a genuine gap, not "not computed yet," and
   silently dropping it would be exactly the kind of data fabrication
   this project's weather client is built to refuse.

**A third, independent finding, not a code bug**: the deployed runtime's
`TELEGRAM_BOT_TOKEN` environment variable had never been set, at any
point since the original Decision 1 deployment. Every Telegram call
failed with `404 Not Found` against `https://api.telegram.org/botNone/sendMessage`,
visible directly in the runtime's own CloudWatch logs. This means no
scheduled 06:00 run has ever actually delivered a message to anyone,
silently, since this was first deployed — the fire-and-forget degrade
path (Decision 2's design) did exactly what it was built to do, log and
move on, which is also exactly why this went unnoticed until someone
checked for a received message instead of a "did it run" status. Fixed
by adding the token (read from local `.env`, never printed to any log or
transcript) to the runtime's environment variables.

**Verification, in order, each a real live check**:
1. Redeployed with current code (version 7) — `READY`, `DEFAULT`
   endpoint's `liveVersion` advanced automatically.
2. Wired a real `operator_chat_id` and four (of eight) farmers'
   `telegram_chat_id` on the live Kamatchipuram cluster — deliberately
   left four unset, to prove the missing-chat-ID path degrades cleanly
   (confirmed in code: `notify.py`'s `send_*` functions log and return
   early, no exception; `watcher.py` doesn't inspect the return value
   either, so a missing chat ID can never crash the run).
3. Seeded Naducauvery into the same live table — confirmed independent
   from Kamatchipuram (`nc-` prefix), 8 farmers/8 plots, no collisions.
4. First real invoke through the Lambda shim (the same path EventBridge
   uses, not a shortcut) surfaced the forecast-horizon bug live; fixed,
   redeployed (version 8, then 9 after the horizon fix).
5. Second invoke surfaced the `float` chat-ID bug and the missing-token
   gap via the runtime's own logs (`telegram sendMessage ... 404`); fixed
   both, redeployed (version 10).
6. Reset the per-cluster daily watcher marker via the project's own
   `Storage.set_watcher_last_run` (not a raw DynamoDB delete — same
   effect, uses the code's own path) and re-invoked. Response body:
   `{"status": "triggered", "usable_days": 15, "escalations": 0}`. Runtime
   logs for this invoke show zero Telegram failures and exactly four
   `no chat_id for farmer ...` lines — matching the four farmers
   deliberately left unwired, nothing else.
7. `escalations: 0` is a genuine result, not a shortfall: today's real
   weather and the cluster's real 3.5 acres/day capacity classify every
   non-too-green plot as `FITS`, none `CONTESTED` — re-confirms, live,
   the same thing Decision 8 already found (the full 8-plot cluster
   doesn't naturally reproduce a tie the way the standalone
   `trigger_scenario.py` two-plot harness deliberately engineers one).
   Not treated as a problem to fix under time pressure — see
   `docs/DEMO.md`'s Beat 4/Beat 5 split for how the demo accounts for
   this honestly instead of forcing an outcome.

**One open item, disclosed rather than assumed resolved**: distributed
trace visibility. CloudWatch Application Signals shows the
`app.daily_watch` operation as live (`Service: harvest-convoy-watcher`),
which confirms the ADOT pipeline is running and processing spans. A
fresh individual trace was **not** independently reproducible via
`aws logs filter-log-events` against `aws/spans` (`storedBytes: 0`,
zero events across a 24h window) or via `aws xray get-trace-summaries`
(zero results, including across the window Decision 8's originally-
captured trace claims to be from). The account-level X-Ray trace
destination is confirmed correctly set (`get-trace-segment-destination`
→ `CloudWatchLogs`/`ACTIVE`), so this isn't the account-level
misconfiguration Decision 8 already fixed reappearing. Root cause not
found in the time available — not pursued further live, to avoid
destabilizing a working deployment chasing an observability nice-to-have
minutes before a filming deadline. `docs/DEMO.md` was written to not
depend on this being resolved.

## Decision 10: redeploying again after live Telegram verification found two Tamil half-translation bugs (2026-08-18)

You checked your own phone against the version-10 deploy from Decision 9
and found real problems live delivery alone could surface: the operator
route summary's location hint was half-translated (Tamil acreage,
English "...NNW of village center" tail), and the route-position ordinal
used "1வது," not a real Tamil word. Full account, including the
in-context compass-bearing comparison you picked between, and two more
half-translation instances found by auditing every farmer/operator-facing
string for the same pattern: ADR-008 Decision 17.

Redeployed the same way as Decision 9 (rebuild from current
`src/harvest_convoy/`, upload to a new S3 key, `update-agent-runtime`) —
version 10 → 11. Reset the watcher marker
(`Storage.set_watcher_last_run`) and re-invoked through the real Lambda
shim: `{"status": "triggered", "usable_days": 15, "escalations": 0}`,
same honest result as Decision 9's verification (today's real
weather/capacity still doesn't produce a tie — unrelated to this fix),
zero Telegram send failures in the runtime logs, the same four expected
`no chat_id for farmer ...` skips. The fix is live on the path being
filmed against, not just in the source tree.

**One incidental exposure, disclosed rather than hidden**: while
preparing this round's `update-agent-runtime` payload, a `grep` over the
JSON input file printed the full file — including the `TELEGRAM_BOT_TOKEN`
value — to this session's own output. The token was never logged by
application code and this isn't a public leak, but it is now present in
this session's transcript. Recommended the token be rotated via
@BotFather as a precaution; that decision was left to you rather than
rotated unilaterally, since doing so would invalidate the token
everywhere it's already configured.

## Consequences

- AgentCore Runtime deployment succeeded — `codeConfiguration`, arm64,
  `PYTHON_3_12`, `READY`. One earlier `create-agent-runtime` attempt
  failed first (`CREATE_FAILED`, the `opentelemetry-instrument` launcher
  issue fixed in `observability/otel.py`); disclosed, not hidden. The
  Lambda-fallback contingency in Decision 1 was **not** needed for the
  runtime itself — only for the narrower Scheduler→API link (Decision 7).
- DynamoDB is real for this project for the first time; FileStorage
  remains the code default. Live round trip against the seeded
  Kamatchipuram scenario confirmed.
- Tracing lands in CloudWatch with correct, deep span nesting — but
  getting there needed one more explicit env var than Decision 5
  predicted (Decision 8), found by reading the ADOT source rather than
  by further guessing.
- One extra resource beyond the original plan: a small Lambda shim
  (Decision 7), because EventBridge Scheduler's universal target can't
  marshal `InvokeAgentRuntime`'s blob request body. Still no hand-rolled
  SigV4 code of our own — the shim just calls boto3, which already
  handles that correctly.
- AgentCore Memory stays out, now on a re-examined basis specific to
  deployment, not a copy-pasted Phase 5 answer.
- Real, measured token/dollar cost on record before any scaling decision,
  plus prompt caching implemented and measured (Decision 6): $0.0227 →
  $0.0081, a 64% reduction.
