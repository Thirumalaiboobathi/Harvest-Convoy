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

## Where this fits: Tamil Nadu's Custom Hiring Centres

Tamil Nadu runs a Custom Hiring Centre (CHC) scheme, funded through
central and state programs (SMAM, NADP), that places combine harvesters
and other farm machinery with cooperatives, FPOs, and private operators
so a farmer who can't afford to own one can still hire one. This isn't
inferred from the scheme being statewide — the state Agriculture
Department's own published dashboard, [Custom Hiring Centres formed in
Tamil Nadu from 2014-15 to 2020-21,
tnagrisnet.tn.gov.in](https://www.tnagrisnet.tn.gov.in/dashboard/report/10_09.pdf)
(fetched directly, not search-surfaced), reports 65 CHCs formed in Theni
district and 100 in Thanjavur district over that period. The table
breaks CHCs down by funding category, not by which implements each one
stocks, so this confirms the scheme's real presence in both districts —
not that a combine harvester specifically sits in either one today.

What doesn't exist is a way to decide whose field that one shared
machine visits first when the harvest window is short and more farmers
want it than it can reach in time. That's exactly the moment weather
forces every season, and exactly the moment informal or first-come
allocation is most likely to leave someone's crop standing too long
while a less urgent field gets cut first.

Harvest Convoy is a scheduling layer for that moment, not a new piece of
machinery and not a new institution. It sits in front of a CHC's
existing harvester and existing route, and decides — from real GDD
accumulation, real weather, and real capacity math, not guesswork —
which contested plot gets today's slot, escalating to a human operator
only when two farmers' claims are genuinely, defensibly tied.

**This project has no integration with any real CHC booking system,
with TN's AGRISNET portal, or with any other government system, and
does not claim to.** No API access and no data-sharing agreement exists
with either — building one anyway would mean inventing an interface
against a system nobody working on this project can actually see, which
would be worse than not building it: it would look real right up until
someone tried to call it. See
[ADR-010](docs/adr/ADR-010-reporting-decision-replay-equity-machinery.md)
for that decision recorded at the point it was made, not as an
afterthought.

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

**Farmer and operator onboarding are deliberately asymmetric, not an
oversight.** A farmer self-onboards entirely by messaging the bot
(`telegram/registration.py`) — no code, no external step. The operator
does not: they enroll with a one-time code (`/operator <code>`,
`telegram/operator_enrollment.py`) that a human — whoever provisions the
cluster — generates and hands them out-of-band (phone call, WhatsApp,
paper slip), the same way `Cluster.operator_chat_id` used to be set by
hand in `seed_cluster.py` before this existed. That asymmetry is by
design: an operator controls a whole cluster's route and can report a
machine breakdown or resolve a scheduling conflict, so letting anyone
self-onboard into that role the way a farmer does would hand out real
authority to whoever happened to message the bot first. A real
deployment would still need two things this project doesn't provide: an
actual secure process for a provisioner to generate and deliver codes at
more than a handful of clusters (this project has no opinion on SMS vs.
WhatsApp vs. paper vs. anything else), and a real answer for operator
turnover beyond what's here — a departing operator's replacement today
needs either a fresh code from the provisioner or the in-chat
replacement-confirmation flow (`/operator <code>` again, then a Yes/No
tap) that guards against a silent takeover.

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

## Backtest against the real 2025 Kuruvai season

Everything above is validated against synthetic 2026 fixture dates. This
section instead runs
[`scripts/backtest_2025_kuruvai.py`](scripts/backtest_2025_kuruvai.py):
replays both seeded clusters' exact plots (same coordinates, same area,
same month/day transplant dates) one year earlier, against real
Open-Meteo Archive weather for the real 2025 season, walking day by day
the way the real watcher does.

2025 is used specifically because it's the most recently completed
Kuruvai season — validating against real archived weather requires a
season that has actually finished, which is why the transplant dates in
the tables below are shifted one year earlier than `seed_cluster.py`'s
own dates and the live system's actual 2026 dates.

**What this does and does not prove — read this before the table below:**

| Claim | Does the backtest support it? |
|---|---|
| Maturity-date projections track real accumulated GDD for real 2025 weather at these coordinates | **Yes** — this is exactly what `agronomy/gdd.py` already computes, fed real Archive data instead of live/current data. |
| The capacity/route/contest classification (`scheduling/solver.py`) produces sensible splits against a real season's real weather, not just synthetic 2026 data | **Yes.** |
| The system would have correctly triggered on the real-time forecast a farmer would actually have seen | **No.** Open-Meteo has no archive of what a 16-day forecast said on a given past day — only what really happened. This backtest uses actual historical rainfall as a stand-in for "the forecast," which is optimistic by construction: hindsight is perfect, a real forecast issued in real time is not. A real deployment's trigger timing — and reliability — would likely have been somewhat different, and somewhat worse, than what this table shows. |
| These farmers' plots were actually harvested when the model says they should have been | **No.** There is no ground truth on what these (synthetic, disclosed) farmers actually did in 2025 — there were no real farmers in this fixture, in 2025 or any other year. |

In short: **this validates the model against real weather, not against
real harvest outcomes.** It converts "a simulated cluster on real
coordinates" into "a simulated cluster whose scheduling logic has been
run against a real season's real weather" — nothing stronger than that.

**Calibration note**: the maturity threshold used below was derived from
2020–2024 climatology only (`agronomy/calibration.py`, `today=2025-01-01`
passed explicitly), deliberately excluding 2025 itself — the threshold
that scores the 2025 season was never trained on the 2025 season. This
also doubles as a second independent proof point for per-cluster
calibration, on a different 5-year window than the one
[ADR-008](docs/adr/ADR-008-tn-generalization-and-tamil.md) originally used.

### Before Part 1.5: the FITS→CONTESTED regression bug

The backtest's first real run used `scheduling/solver.py:solve()` as it
existed at the time — no concept of a harvested plot. Kept here
unedited, not quietly replaced by the fixed run below, because **a bug
found by backtesting and fixed is stronger evidence than a clean run
ever would be.**

**Kamatchipuram** (172 trigger days, 2025-05-01 to 2025-12-05,
threshold 1637.0 GDD, 59 days with a CONTESTED plot):

| Plot | Farmer | Area (ac) | Transplanted | Projected Maturity | First Ready Trigger | Final Outcome |
|---|---|---|---|---|---|---|
| p01 | Muthu Pandian | 2.5 | 2025-05-01 | 2025-07-28 | 2025-07-28 (fits) | contested |
| p02 | Selvi Karuppiah | 0.75 | 2025-05-05 | 2025-08-05 | 2025-08-05 (contested) | contested |
| p03 | Kannan Raja | 3.0 | 2025-05-12 | 2025-08-13 | 2025-08-25 (fits) | contested |
| p04 | Meena Subramani | 1.25 | 2025-05-18 | 2025-08-19 | 2025-08-25 (fits) | contested |
| p05 | Raja Gounder | 2.0 | 2025-05-24 | 2025-08-21 | 2025-08-25 (fits) | contested |
| p06 | Lakshmi Nadar | 0.5 | 2025-05-30 | 2025-08-26 | 2025-08-26 (fits) | contested |
| p07 | Karthik Murugan | 1.5 | 2025-07-20 | 2025-10-19 | 2025-10-19 (contested) | contested |
| p08 | Valli Chinnasamy | 1.0 | 2025-07-28 | 2025-10-27 | 2025-10-27 (contested) | contested |

**Naducauvery** (172 trigger days, 2025-05-10 to 2025-12-07, threshold
1931.4 GDD — ~10.6% above Kamatchipuram's, consistent with ADR-008's
original comparison — 65 days with a CONTESTED plot):

| Plot | Farmer | Area (ac) | Transplanted | Projected Maturity | First Ready Trigger | Final Outcome |
|---|---|---|---|---|---|---|
| nc-p01 | Marimuthu Iyer | 2.0 | 2025-05-10 | 2025-08-09 | 2025-08-09 (contested) | contested |
| nc-p02 | Kamala Pillai | 1.0 | 2025-05-16 | 2025-08-16 | 2025-08-22 (fits) | contested |
| nc-p03 | Rajendran Mudaliar | 2.5 | 2025-05-22 | 2025-08-22 | 2025-08-22 (fits) | contested |
| nc-p04 | Meenakshi Iyengar | 1.5 | 2025-05-30 | 2025-08-29 | 2025-08-29 (fits) | contested |
| nc-p05 | Sundaram Chettiar | 3.0 | 2025-06-08 | 2025-09-07 | 2025-09-07 (contested) | contested |
| nc-p06 | Pappathi Naidu | 0.75 | 2025-06-20 | 2025-09-20 | 2025-09-20 (fits) | contested |
| nc-p07 | Ganesan Pillai | 1.25 | 2025-07-25 | 2025-10-31 | 2025-10-31 (fits) | contested |
| nc-p08 | Valliammai Naidu | 1.0 | 2025-08-02 | 2025-11-08 | 2025-11-08 (fits) | contested |

**The finding: "Final Outcome" converges toward CONTESTED for most
plots by season's end.** 5 of 8 plots at Kamatchipuram and 6 of 8 at
Naducauvery were first classified FITS — ready, budget covered them —
but end the simulated season CONTESTED instead. The reason was a real,
previously undisclosed gap: **the system had no mechanism to remove a
plot from future scheduling once it was marked FITS.**
`scheduling/solver.py:solve()` recomputed from scratch on every trigger
day, reading every plot currently in storage — nothing marked a plot
"already harvested," so a plot that fit early kept re-competing for the
day's capacity budget against every later-maturing plot for the rest of
the season. This was a genuine property of the deployed system, not a
backtest artifact (the real watcher called `solve()` fresh every
trigger day too) — only visible here because the backtest ran ~170
trigger days in sequence instead of the single-run 8-plot demos this
project had exercised before. Two real production consequences: a
farmer whose crop was already in would get messaged again, and — more
seriously — the capacity budget could be consumed by plots that no
longer needed it, so a genuinely urgent unharvested plot could lose a
slot it should have won.

### After Part 1.5: plot harvest lifecycle

[ADR-009](docs/adr/ADR-009-harvest-lifecycle-and-validation.md), Part
1.5, fixes this in the core scheduling loop, not the backtest script:
`scheduling/solver.py` gains a fourth outcome, `PlotOutcome.HARVESTED`
— a plot the coordinator has already dispatched is excluded from
`solve()`'s classification entirely, the same way a `TOO_GREEN` plot is
excluded rather than ranked last, not coupled to the (separate, later,
sometimes-absent) farmer confirmation signal. Same script, same fixture
data, same real 2025 weather — only `solve()` changed:

**Kamatchipuram** (172 trigger days, threshold unchanged at 1637.0 GDD,
now only **3** days with a CONTESTED plot, down from 59):

| Plot | Farmer | Area (ac) | Transplanted | Projected Maturity | First Ready Trigger | Final Outcome |
|---|---|---|---|---|---|---|
| p01 | Muthu Pandian | 2.5 | 2025-05-01 | 2025-07-28 | 2025-07-28 (fits) | **harvested** |
| p02 | Selvi Karuppiah | 0.75 | 2025-05-05 | 2025-08-05 | 2025-08-05 (contested) | **harvested** |
| p03 | Kannan Raja | 3.0 | 2025-05-12 | 2025-08-13 | 2025-08-25 (fits) | **harvested** |
| p04 | Meena Subramani | 1.25 | 2025-05-18 | 2025-08-19 | 2025-08-25 (fits) | **harvested** |
| p05 | Raja Gounder | 2.0 | 2025-05-24 | 2025-08-21 | 2025-08-25 (fits) | **harvested** |
| p06 | Lakshmi Nadar | 0.5 | 2025-05-30 | 2025-08-26 | 2025-08-26 (fits) | **harvested** |
| p07 | Karthik Murugan | 1.5 | 2025-07-20 | 2025-10-19 | 2025-10-19 (contested) | **harvested** |
| p08 | Valli Chinnasamy | 1.0 | 2025-07-28 | 2025-10-27 | 2025-10-27 (contested) | **harvested** |

**Naducauvery** (172 trigger days, threshold unchanged at 1931.4 GDD,
now only **6** days with a CONTESTED plot, down from 65):

| Plot | Farmer | Area (ac) | Transplanted | Projected Maturity | First Ready Trigger | Final Outcome |
|---|---|---|---|---|---|---|
| nc-p01 | Marimuthu Iyer | 2.0 | 2025-05-10 | 2025-08-09 | 2025-08-09 (contested) | **harvested** |
| nc-p02 | Kamala Pillai | 1.0 | 2025-05-16 | 2025-08-16 | 2025-08-22 (fits) | **harvested** |
| nc-p03 | Rajendran Mudaliar | 2.5 | 2025-05-22 | 2025-08-22 | 2025-08-22 (fits) | **harvested** |
| nc-p04 | Meenakshi Iyengar | 1.5 | 2025-05-30 | 2025-08-29 | 2025-08-29 (fits) | **harvested** |
| nc-p05 | Sundaram Chettiar | 3.0 | 2025-06-08 | 2025-09-07 | 2025-09-07 (contested) | **harvested** |
| nc-p06 | Pappathi Naidu | 0.75 | 2025-06-20 | 2025-09-20 | 2025-09-20 (fits) | **harvested** |
| nc-p07 | Ganesan Pillai | 1.25 | 2025-07-25 | 2025-10-31 | 2025-10-31 (fits) | **harvested** |
| nc-p08 | Valliammai Naidu | 1.0 | 2025-08-02 | 2025-11-08 | 2025-11-08 (fits) | **harvested** |

**Every plot at both clusters now ends the season `harvested`, not
`contested` — 0 of 11 plots whose first ready trigger was FITS
regressed to CONTESTED later in the season, down from 11 of 11 (every
single one) before the fix.** The remaining
CONTESTED days (3 and 6, down from 59 and 65) are genuine — real
capacity contention on days when several plots were ready at once
before earlier ones had cleared the pool, not an artifact of the bug.
One more thing worth naming: some plots (p02, p07, p08 at Kamatchipuram;
nc-p01, nc-p05 at Naducauvery) were `CONTESTED` on their *first* ready
trigger, then later ended up `harvested` anyway — capacity freed up as
earlier plots left the pool for good, and they eventually won a slot.
That's the fairness/capacity mechanism working as designed, visible
now that plots correctly leave contention instead of piling up in it
forever.

**Real production consequences this fixes**: a farmer whose crop is
already in no longer gets `harvest_scheduled` messaged again on a later
trigger day, and the capacity budget is no longer spent on a plot that
doesn't need it — a genuinely urgent unharvested plot can no longer
lose a slot to one that's already done. Not coupled to
[ADR-009](docs/adr/ADR-009-harvest-lifecycle-and-validation.md) Part
2's harvest confirmation loop (still to be built): the coordinator marks
a plot harvested at dispatch time, because that's a scheduling fact it
already has — Part 2's farmer confirmation is a separate, later,
sometimes-absent signal that will eventually be able to *reverse* this
(a farmer reporting "it never came" returns the plot to the schedulable
pool), not one this fix waits on.

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

## Verification finds what tests don't

Eight separate defects turned up across this project's history, none of
them caught by the test suite going red:

1. **Registration never persisted a `Farmer`/`Plot` to storage at all.**
   Found while designing harvest confirmation (ADR-009), by reading
   every call site of `put_farmer`/`put_plot` in the repo and finding
   none in the registration path.
2. **The scheduler forgot harvested plots**, so a plot that fit early
   kept re-competing for capacity for the rest of the season
   (FITS→CONTESTED regression, ADR-009 Part 1.5). Found by running a
   ~170-day backtest against a real, completed season — invisible to
   the single-day, 8-plot demos the test suite already covered.
3. **Scheduling decisions were never recorded anywhere** — negotiation
   claims, arguments, round counts, which maturity threshold applied.
   Found while attempting to build a decision-replay report (ADR-010)
   and discovering there was nothing left to replay by the time it ran.
4. **The season-rollover prompt had no exclusion filter behind it.**
   Found while tracing what actually reads a `SeasonRolloverPrompt` and
   discovering `watcher.py` didn't (ADR-011 Part 1) — a farmer could
   answer "no" and be scheduled anyway.
5. **The escalation-resolve tap had no authorization check.** Found by
   grepping for every call site of `_is_operator` and finding the
   escalation handler wasn't one of them (ADR-012).
6. **The harvest-confirmation callback had no farmer-identity check.**
   Found in the same audit, by asking *whose* authorization each
   callback requires rather than whether it had any (ADR-012).
7. **The rollover callback had no farmer-identity check** — the more
   dangerous of the two, since an unauthorized "yes" could redirect
   another farmer's date-reply flow to the imposter's own chat. Same
   audit, same question (ADR-012).
8. **A Tamil fallback string doubled a noun in both languages** —
   "the selected plot's plot instead" — in a farmer-facing "why did I
   lose" answer, shipped two turns before it was found, with a test
   that stayed green the whole time: the test asserted
   `DEFAULT_WINNER_LABEL in text`, which is true of the broken string
   too. A substring assertion passes on gibberish that happens to
   contain the substring; it was never checking the sentence was
   grammatical, only that a fragment of it was present. Found by
   grepping every remaining use of the fallback constant across the
   codebase and rendering both paths by hand to actually read the
   output, not by re-reading the passing test (ADR-013 Part 3).

Every test touching these paths was green the whole time, because a
test only exercises what someone already thought to check when they
wrote it. None of these eight were found that way. What found them
instead was the same handful of methods, repeated across five ADRs: run
the system against something real instead of a demo, try to build the
thing that reads data the system is supposed to be producing and see
what's actually there, read every call site of a function that's
supposed to matter and confirm it's called from where it needs to be,
or — for #8 — grep every call site of a *value* rather than a function
and actually render each one instead of trusting that a passing
assertion meant the output was correct. Tests confirm what you thought
to check. Verification — actually using, replaying, rendering, or
auditing the system end-to-end — finds what you didn't.

**A ninth finding, different in kind from the eight above: the
verification harness itself was lying.** `scripts/print_tamil_strings.py`
exists specifically so every new Tamil string gets read by a human
before it ships. Its demo of `escalation_resolved_assigned`'s no-farmer
fallback called the function as
`escalation_resolved_assigned(messages_ta.DEFAULT_WINNER_LABEL)` —
passing the fallback *label* as the argument. After
`escalation_resolved_assigned` was fixed to take `winner_name: str |
None`, a non-`None` string (even the fallback label itself) still
re-enters the real-farmer-name branch — so this call kept rendering the
exact broken "வயல் க்கு" text the fix was supposed to eliminate, right
there in the review dump meant to catch it. Production calls it as
`escalation_resolved_assigned(None)`, which is the only call that ever
exercises the fixed branch. Every prior native-speaker review round
that looked at this fallback was reading a sentence the running code
could never actually produce; the one path a human was supposed to
catch a mistake in was never correctly shown to them at all. This is
not "another defect the harness happened to miss" — the seven above and
#8 were things the harness (or a test) could have caught but didn't
design for; this is the harness actively calling production code the
wrong way and reporting a clean result. A verification tool that
doesn't call the way production calls doesn't just fail to find bugs —
it manufactures confidence that a review happened when it didn't, which
is worse than having no harness at all: "we checked" now means
something false. Fixed by making the harness call exactly what
`webhook.py` calls (`None`, not the label), and by adding the fallback
demo that had never existed for `route_drop_confirm_prompt` either.
Every harness in this project (`print_tamil_strings.py`,
`explain_decision.py`, `equity_report.py`, `machinery_gap.py`) is now
worth the same question: does its demo/test data actually match how the
real call site invokes it, or only resemble it.

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
