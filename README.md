# Harvest Convoy

A village cluster in Tamil Nadu shares one combine harvester across its
paddy plots. Harvest Convoy tracks each plot's crop maturity and the
incoming weather, and reschedules the shared machine's route automatically
— surfacing to a human only when two plots genuinely conflict for the same
slot, instead of burying farmers in decisions a machine can make for them.

Built for the AWS "Agents for Humans" hackathon, track **Good Neighbor
Agents**.

**Works anywhere in Tamil Nadu, not just one village.** Growing Degree
Days are computed from each plot's own coordinates and weather, not a
hardcoded location — nothing in the deterministic core assumes a
particular district. Two real, differently-climated demo clusters prove
this instead of asserting it: **Kamatchipuram** (Theni district, semi-arid
interior, 8 plots) and **Naducauvery** (Thanjavur district, Cauvery delta,
8 plots) — see `scripts/seed_cluster.py` and
`scripts/seed_cluster_naducauvery.py`. The one place climate *does* need
calibrating — the crop's maturity threshold — is covered in the Honesty
section below, including the real ~10.6% GDD-rate gap measured between
the two districts and how per-cluster calibration closes it.

**Crop scope stays paddy ADT 45 only — deliberately, not by oversight.**
Maize is the defensible second crop for a future season: combine-
harvested, moisture-critical at harvest, and backed by real published
GDD literature, unlike the derived paddy threshold above. Not attempted
here — widening the crop list now would compound the sourcing gap
rather than fix it.

**Farmer-facing messages are Tamil by default, English on request.**
`Farmer.language` (default `"ta"`) drives every scheduling-critical
message — harvest-scheduled, not-ready, and escalation-resolved — via
hand-authored templates in
[`telegram/messages_ta.py`](src/harvest_convoy/telegram/messages_ta.py)
and
[`telegram/messages_en.py`](src/harvest_convoy/telegram/messages_en.py),
not machine translation. Registration accepts Tamil script and
transliterated ("Tanglish") input, and both ஏக்கர் (acre) and சென்ட்
(cent) as area units, displayed back in whichever the farmer used. The
one generative string in the whole system — the advocate's argument,
shown to the operator in an escalation — is **templated from ground-truth
facts for Tamil, not model-generated**: live testing found Nova Pro
cannot reliably produce valid Tamil script (see "What we learned building
Tamil support" below for the actual samples). English farmers still get
the model's own generated argument text. See
[docs/adr/ADR-008-tn-generalization-and-tamil.md](docs/adr/ADR-008-tn-generalization-and-tamil.md)
Part 2 for the full design. Every Tamil string went through four rounds
of native-speaker review (Decisions 14–16) before this wording shipped.

**The core rule the whole system is built around: the LLM never computes a
number.** Crop maturity (Growing Degree Days), rain-day capacity, route
order, and fairness scores are all deterministic Python — the same inputs
always produce the same outputs, and you can unit-test them like any other
function. Bedrock Nova Pro, via Strands Agents, only does two things: argue
a plot's case when two plots are genuinely tied, and write the messages
farmers read. See [ARCHITECTURE.md](ARCHITECTURE.md) for why that split
matters and how it's enforced in code, not just convention.

## Quickstart — zero AWS required

No AWS account, no AWS credentials, no Docker.

```bash
uv sync --all-groups
uv run pytest
```

286 of 288 tests pass with genuinely nothing else set up — no `.env`, no
credentials of any kind. A handful are marked `network` (real Open-Meteo
calls, free tier, no key needed) or `bedrock` (real Bedrock calls, needs
AWS credentials) — see `pyproject.toml`; run `uv run pytest -m "not
bedrock"` to skip the two Bedrock-only ones if you don't have AWS
credentials configured. This is the fastest way to confirm the
deterministic core (GDD, calibration, capacity, route, fairness) actually
works.

To see the full scheduling pass end to end — including a plot-vs-plot
negotiation — `scripts/trigger_scenario.py` actually sends real Telegram
messages, so it needs one piece of free setup even in `--offline` mode
(it exits immediately without a token; `--offline` only skips the *Bedrock*
call, not the Telegram send, which is the whole point of the script):

```bash
cp .env.example .env
# message @BotFather on Telegram, /newbot, paste the token into .env — ~1 minute, no AWS involved
uv run python -m scripts.seed_cluster --write
uv run python -m scripts.trigger_scenario <your_telegram_chat_id> --offline
```

Message your own bot once first so it has a chat to reply to, then check
your phone — the four message types (harvest scheduled, not-ready, route
summary, escalation) arrive for real. `--offline` mode uses a canned
claim provider instead of live Bedrock, labeled `[scripted offline mode]`
in the arguments shown so it's never mistaken for live LLM judgment.

## Full setup — with AWS

### Local run against real Bedrock

```bash
cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN from @BotFather
# then set AWS credentials however you normally do: env vars, ~/.aws/credentials, SSO, etc.
uv run python -m scripts.seed_cluster --write
uv run python -m scripts.trigger_scenario <your_real_chat_id>
```

Without `--offline`, this makes real Bedrock calls (`apac.amazon.nova-pro-v1:0`,
`ap-south-1`) and reports whatever the negotiation actually decides — if a
plot's case is weak enough that the model concedes instead of escalating,
the script says so rather than forcing a scripted outcome.

### Storage backend

Default is `FileStorage` — a plain JSON file (`.data/harvest_convoy.json`,
created automatically), zero setup. Switch to real DynamoDB:

```bash
export HARVEST_CONVOY_STORAGE=dynamo
# DynamoDB Local instead of real AWS:
export DYNAMODB_ENDPOINT_URL=http://localhost:8000
uv run python -m scripts.seed_cluster --write
```

Both backends implement the same `Storage` Protocol
(`src/harvest_convoy/storage/interface.py`) against the same single-table
key scheme — see
[docs/adr/ADR-005-persistence-fairness.md](docs/adr/ADR-005-persistence-fairness.md).

### Deploying to AWS (AgentCore Runtime)

The full deployment is documented step-by-step in
[docs/adr/ADR-006-deploy.md](docs/adr/ADR-006-deploy.md) (provisioning
table, cost breakdown, and two real problems found only by deploying —
see "What we learned deploying" below). In short, what gets provisioned
in `ap-south-1`:

| Resource | Purpose |
|---|---|
| S3 bucket | Holds the zipped code artifact for AgentCore Runtime |
| AgentCore Runtime | Runs `app.py` (`codeConfiguration`, arm64, `PYTHON_3_12`, `PUBLIC` network) |
| DynamoDB table `harvest_convoy` | On-demand billing, single-table design |
| Lambda function `harvest-convoy-watcher-invoker` | Thin shim: EventBridge → this → `InvokeAgentRuntime` |
| EventBridge Schedule | Daily cron, `Asia/Kolkata` 06:00, fires the Lambda shim |
| 3 IAM roles | Runtime execution, Scheduler invoke, Lambda execution — each scoped to exactly what it needs |
| CloudWatch Transaction Search / X-Ray | Trace destination for OTel spans |

Environment variables the Runtime resource needs set (not in this repo —
set on the AWS resource itself):

```
HARVEST_CONVOY_STORAGE=dynamo
AGENT_OBSERVABILITY_ENABLED=true
OTEL_TRACES_EXPORTER=otlp
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://xray.ap-south-1.amazonaws.com/v1/traces
```

Health check after deploying (read-only, safe to run anytime):

```bash
uv run python -m scripts.check_watcher_health --cluster-id kamatchipuram
```

Tear everything down when you're done (after judging):

```bash
bash scripts/teardown.sh
```

## Architecture summary

```
Telegram Bot API          Open-Meteo API         EventBridge Schedule
       │                        │                        │ (daily, 06:00 IST)
       │                        ▼                         ▼
       │              ┌── AgentCore Runtime ──────────────────────┐
       │              │                                            │
       │              │  ┌─ DETERMINISTIC CORE ─┐  ┌─ LLM JUDGMENT ─┐
       │              │  │ GDD, capacity/route,  │─▶│ coordinator +  │
       │              │  │ fairness decay        │  │ advocates      │
       │              │  │ (pure Python)         │  │ (Strands+Nova) │
       │              │  └───────────────────────┘  └────────────────┘
       │              │           │                        │
       │              │      DynamoDB              CloudWatch / X-Ray
       └──────────────┴───────────┴────────────────────────┘
                    notify.py / webhook.py (escalation → human)
```

Full diagram: [docs/architecture.png](docs/architecture.png), detailed
walkthrough: [ARCHITECTURE.md](ARCHITECTURE.md).

The one-sentence version: `watcher.run_daily_watch()` checks whether rain
is coming within the forecast horizon; if so, it runs the deterministic
scheduler over all plots, and only the plots that come out `CONTESTED`
(the capacity budget couldn't fit them all) go to the LLM layer for
pairwise negotiation. Everything else — maturity, capacity, route order,
fairness — is math, not judgment.

## Honesty section

**This is a simulated cluster, not a live deployment with real farmers.**
Kamatchipuram, Chinnamanur block, Uthamapalayam taluk, Theni district,
Tamil Nadu (9.865°N 77.454°E) is a real place, and the weather data
(`weather/openmeteo.py`) is real, live Open-Meteo Archive/Forecast data
for those coordinates. The eight plots, farmer names, and transplant
dates in `scripts/seed_cluster.py` are synthetic fixture data — no real
farmer has registered, no real harvester operator uses this. Real field
validation would require: actual GPS coordinates and areas surveyed for
each plot (not estimated), a real operator with a real Telegram account
managing an actual route, at least one full season of comparing this
system's schedule against what the operator would have done manually,
and — most importantly — actual ADT45 GDD-to-maturity ground truth from
an agronomist or a season of paired measurements, replacing the derived
estimate below.

**`MATURITY_GDD_ESTIMATED` is derived, not sourced — the derivation is
shown in code, not hidden.** No published thermal-time (GDD) requirement
for the ADT45 variety was found despite targeted searches. What the code
uses instead (`src/harvest_convoy/agronomy/crop_params.py`):

- `T_BASE_C = 10.0` — the one number that *is* sourced: Sanwong et al.
  (2023), *Plants* 12(3):666, states directly "Tbase of rice = 10.0 °C."
- `ADT45_CROP_DURATION_DAYS = 110` — from TNAU AgriTech's published paddy
  variety guidance (90–120 day short-duration category).
- `ADT45_NURSERY_AGE_DAYS_ESTIMATED = 20` — the midpoint of TNAU's 18–22
  day nursery-age guidance for short-duration varieties; not measured for
  ADT45 specifically, a chosen point in a cited range.
- `ADT45_FIELD_DURATION_DAYS_ESTIMATED = 110 - 20 = 90` days from
  transplant to maturity.
- `KURUVAI_MEAN_GDD_PER_DAY_REFERENCE_ESTIMATED = 19.2133` — computed
  directly from Open-Meteo Archive history at Theni's coordinates, the
  90-day window (May 15–Aug 12) across the five most recent complete
  years (2021–2025): 450 days of real temperature data, mean 19.2133
  GDD/day. This replaced an earlier, unverified 17.5 figure caught
  during review.
- **`MATURITY_GDD_ESTIMATED = 90 × 19.2133 ≈ 1729.2`** — field-duration
  days times assumed mean daily GDD accrual. Estimated, not measured.
  **This number is now the documented fallback, not a TN-wide constant.**

**The maturity threshold is now derived per cluster, not applied
uniformly from Theni's climate.** Re-running the exact methodology above
against a second real district — Naducauvery, Thanjavur delta (Cauvery
delta, Tamil Nadu's principal rice belt) — measured **21.2509 GDD/day**,
~10.6% hotter than Theni. Applying Theni's number unmodified would have
misprojected maturity dates in Thanjavur by roughly 9 days. Each seeded
cluster's `Cluster.maturity_gdd_override` can now be calibrated against
its own coordinates (`agronomy/calibration.py`, `seed_cluster*.py
--write --calibrate` — live Open-Meteo calls, opt-in, not run by
`pytest`), and `scheduling/solver.py` uses that override when present.
**Calibration only fixes which climate the threshold is priced in — the
underlying `MATURITY_GDD_ESTIMATED = 1729.2` figure itself remains
DERIVED, not sourced,** for the reasons above (no published ADT45
thermal-time requirement exists at all); an uncalibrated cluster falls
back to it and logs a loud `MATURITY THRESHOLD FALLBACK` warning rather
than silently assuming Theni's climate applies. See
[docs/adr/ADR-008-tn-generalization-and-tamil.md](docs/adr/ADR-008-tn-generalization-and-tamil.md)
Decision 2 for the full measured comparison.

**The fairness decay curve is the same story.** No citable day-past-
physiological-maturity yield/quality loss curve for paddy was found.
`FAIRNESS_SEASON_DECAY = 0.5` (`storage/fairness.py`) is a tuning
constant, not a sourced figure — each season further back, a fairness
bump's weight is multiplied by 0.5 (geometric decay, asymptotic: old
bumps fade, never fully vanish). `DECAY_HORIZON_DAYS_ESTIMATED = 20`
(`agronomy/decay.py`) is a round number chosen past the recurring-but-
unquantified "harvest within 10–15 days of maturity" guidance found
during research, not derived from a curve.

Neither of these numbers is fabricated or presented as authoritative —
both are visibly labeled `_ESTIMATED` in the code, with the reasoning in
the docstrings, so a domain expert reviewing this can see exactly what
to correct and why, rather than trusting an unexplained constant.

## What we learned deploying

Two real problems, found only by deploying and watching it fail live —
not documented anywhere obvious beforehand:

1. **EventBridge Scheduler's "universal target" can't call
   `InvokeAgentRuntime` directly.** That API's request body is a raw
   blob (a payload-trait shape, not a JSON object), and Scheduler's
   universal-target JSON marshaller is built for standard JSON request
   bodies. It fails silently from the caller's point of view — the
   schedule fires, `AssumeRole` succeeds, but the target API call errors
   before ever reaching the runtime, and nothing shows up in the
   runtime's own logs. Root-caused by reading `InvokeAgentRuntimeRequest`'s
   botocore model directly. Fixed with a ~20-line Lambda shim that calls
   the same API via boto3 (which marshals blob bodies correctly) and
   gets targeted the normal, well-supported way.

2. **ADOT's span exporter defaults to a local collector at
   `localhost:4318` that doesn't exist in direct-code (non-container)
   AgentCore Runtime deployments.** `AGENT_OBSERVABILITY_ENABLED=true`
   alone isn't enough — every span export attempt failed with
   `Connection refused`, confirmed live with a one-line socket
   reachability check added to the deployed code. Root-caused by reading
   the installed `aws_opentelemetry_configurator.py` source: it only
   switches to a direct SigV4-signed X-Ray exporter when
   `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is set explicitly to
   `https://xray.<region>.amazonaws.com/v1/traces`. One env var, no code
   change, verified live afterward with real 9-level-deep span nesting
   landing in CloudWatch.

Full writeup with the exact CloudWatch metrics and error text that
confirmed each root cause: ADR-006, Decisions 7 and 8.

## What we learned building Tamil support

We tested whether Nova Pro could generate the advocate's `argument`
field directly in Tamil script, via a system-prompt instruction, before
shipping it — the same "verify live, don't assume" standard applied
everywhere else in this project. **It failed, and it failed in a more
specific way than "sounds like translated English."**

Five real `argument` values, generated live via a
`CACHED_SYSTEM_PROMPT_TA` variant that has since been removed, verbatim:

```
இந்ன்து புனக்கது தக்க தா஡ைல்தது.
திஂன்துத்தயாடுத்து
ஊமகட்த் மறந்தம் மறமிக்கம் மறஂடகடும்.
ஊண்ட கம்பத்டுககம்
ஆகிகணிகிச் மடுத்டல் உனிப்படு மட்தமட்டு மடுக்கப்படு.
```

Every character checks out as a valid Tamil Unicode code point (U+0B80–
U+0BFF) — this isn't an encoding bug or a font problem. But the
consonant/vowel-sign clusters are grammatically impossible (e.g. two
virama-joined nasals in a row, `ந்ன்`) and rare signs like anusvara
(`ஂ`, U+0B82) appear in positions no real Tamil word would use. This
isn't stilted or awkward Tamil — it isn't Tamil.

**The corruption never reached a scheduling decision.** The same facts,
sent through both an English-instructed and a Tamil-instructed call,
came back with identical, correct values for every *other* structured
field — `concedes`, `urgency_score`, `days_past_maturity`,
`rain_vulnerability`, `acres`, `bumped_last_season` all matched ground
truth exactly, in both languages. The failure was confined to the
free-text `argument` field; the model's actual judgment (whether to
concede the slot) was never degraded. This matters because it's the
difference between "one string was wrong" and "the agent layer can't be
trusted" — it's the former, verified, not assumed.

**The failure is script-level, not language-level.** A Tanglish
(romanized Tamil, Latin letters) variant of the same instruction
produced largely coherent output. Three real samples, verbatim:

```
Enga plot ready aayidichu, udambai vechu
Ithu ready aayila, action aanathe vendaam.
Enakku ready aayidichu, thungadi thunnai venum.
```

The second is a fully correct sentence ("This isn't ready yet, no
action needed."). The third shows the actual failure mode: a clean
opening ("Enakku ready aayidichu" — "I've become ready") that degrades
into a garbled tail ("thungadi thunnai venum" isn't a real phrase).
Nova Pro can produce valid Tamil *sounds* reliably; it's the Tamil
*script* specifically — the tokenization into Unicode code points —
where it breaks down. A native Tamil-instructed system prompt was not
attempted as a fix, on the reasoning that a model unable to reliably
emit Tamil script likely can't reliably read Tamil-script instructions
either.

**We templated the Tamil path instead of working around the model.**
`argument` for a Tamil-registered farmer's plot is now rendered
deterministically from the same ground-truth facts plus the model's own
`concedes` decision (`telegram/messages_ta.py:advocate_argument`) — not
requested from the model in any language. This string surfaces to an
operator making a real scheduling call between two farmers; text that
can't be verified for correctness doesn't belong on that path, no
matter how it reads. `concedes` itself is still a genuine live model
judgment for every plot regardless of language — only the prose
generation was removed from the model's job. English-registered farmers
are unaffected; the model's own generated argument text is used as
before.

Full writeup, including the other-field-corruption comparison table:
ADR-008, Decision 14 (the finding) and Decision 15 (the fix).

## Cost

Measured, not estimated, on the real 8-plot Kamatchipuram scenario
against live Bedrock, with a realistic 50/50 Tamil/English farmer mix
(not an all-one-language cluster) since that's what a single shared
system prompt now actually serves. Run three times, not once — live
model output varies call to call, and a single four-decimal figure from
one observation would overstate the precision we actually have:

| | |
|---|---|
| Per full scheduling run (Nova Pro, prompt-cached) | **≈$0.011** (measured $0.0106–$0.0118 across 3 live runs) |
| Same run, without prompt caching | $0.0227 (measured on an earlier, English-only run — still the right order of magnitude) |
| Standing AWS infrastructure | ~$2/month worst case |

An earlier measurement on an all-English 8-plot run reported $0.0081;
this range supersedes it — same single cached system prompt, ordinary
call-to-call token variance on live, non-deterministic model output
(each call's internal reasoning length varies a little), not a
regression. (A separate mixed-prompt configuration — a Tamil-specific
system-prompt variant alongside the English one — was tried and measured
around $0.0119 before being removed for the correctness reason above;
not carried forward.)

The prompt-caching win comes from an explicit `cachePoint` block on the
advocate's system prompt — see `agents/advocate.py` and ADR-006 Decision
6 for what was tried and rejected along the way (Strands'
`CacheConfig(strategy="auto")` caches at the wrong message boundary;
tool-config caching is rejected outright by Nova Pro).

## Development

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency
management and `pytest` for tests.

```bash
uv sync --all-groups
uv run pytest
```

Design decisions, in order, with the reasoning behind each: `docs/adr/`
(ADR-000 through ADR-006).

## License

MIT — see [LICENSE](LICENSE).
