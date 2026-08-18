# ADR-009: Historical Backtest, Plot Harvest Lifecycle, Harvest Confirmation, Maturity Projection, Post-Harvest Drying Alert

- Status: **Approved, with adjustments (2026-08-18). Part 1 implemented
  and committed. Part 1.5 (plot harvest lifecycle) added after Part 1's
  backtest surfaced a real correctness bug in `solve()` — see below —
  and implemented next, before Part 2. Nothing touches the deployed
  AgentCore artifact unless and until separately approved.**
- Date: 2026-08-18 (Part 1.5 addendum same day, after Part 1's real run)

## Context

Four additions, requested together, implemented in order:

1. Historical backtest against the real 2025 Kuruvai season (validation,
   no new surface).
2. Harvest confirmation loop (closes the "did it actually happen" gap in
   the fairness ledger).
3. Projected maturity date appended to registration's confirmation
   message (farmer-facing signal that the system is working).
4. Post-harvest drying-window rain alert (the single highest-value
   proactive message this project's own scope rule allows).

Scope discipline restated because it governs every design choice below:
**the agent may speak first only about what it already knows.** It knows
transplant date and location (so it can project maturity). It knows when
the machine visited and whether the farmer confirmed that (so it can
watch the drying window). It does not know which DPC a farmer sells to,
what the mandi price is, or anything about pests, soil, or credit — none
of that appears anywhere below, and none of it should be added later
without revisiting this rule explicitly.

## Prerequisite, found during design research — registration doesn't persist anything yet

Before Part 3 (or 2, or 4) can do anything real, one gap has to close
that none of your four parts named directly, because it's below the
level you'd normally need to think about:

**`registration.py`'s four-message flow never writes a `Farmer` or
`Plot` to `Storage`.** Confirmed by reading every call site in the repo:
`put_farmer`/`put_plot` are only ever invoked from `seed_cluster.py`'s
fixture scripts (`--write`) and from the storage backends' own method
bodies. `advance_registration` is a pure function by explicit design
(ADR-004 Decision 2 — testable without Telegram or storage), and
`registration.handle_incoming`/`webhook.handle_update` never call
`storage.put_farmer`/`put_plot` either. A real farmer who completes the
real four-message conversation today ends up with a `RegistrationState`
in an in-memory dict (`_STATE_STORE`, explicitly disclosed as
"does not survive a process restart") and nothing else — no `Farmer`,
no `Plot`, invisible to the watcher, the fairness ledger, and every
seeded-cluster gate test that only ever exercises `seed_cluster.py`'s
fixture data.

A second, related gap: `RegistrationState` has no `cluster_id`.
`village` is free text the farmer types, never matched against a real
`Cluster`. There's no fuzzy-matching logic anywhere, and building one is
a real NLP problem — explicitly not something this ADR proposes solving.

**Proposed fix, minimal, and consistent with how the deployed system
already works**: the EventBridge Schedule's payload already hardcodes
`cluster_id: "kamatchipuram"` per Lambda invocation (ADR-006) — one bot
deployment already means one cluster, in practice, today. Extend that
same assumption to registration: a `HARVEST_CONVOY_CLUSTER_ID` config
value (env var, matching `HARVEST_CONVOY_STORAGE`'s existing pattern)
names the cluster every farmer talking to this bot instance registers
into. At the `COMPLETE` transition, `registration.handle_incoming`
(signature gains a `storage: Storage` parameter — `webhook.handle_update`
already has `storage` in scope, so this is a threading change, not a new
dependency) constructs and persists a `Farmer` and a `Plot` from the now-
complete `RegistrationState` plus a generated ID (`f"farmer-{chat_id}"`
/ `f"plot-{chat_id}"` — deterministic and collision-free per chat,
matching this project's existing plain-string-ID convention). `village`
stays free text, stored on... nowhere yet, actually — it isn't a field
on `Plot` or `Farmer` today. Proposing it's simply not persisted (it
only ever existed to make the bilingual greeting conversational); if you
want it kept for operator reference later, say so and I'll add a field.

**What this does not solve**: multi-cluster bot deployments (one bot
serving several villages, farmer-chosen). Out of scope here — flagging
it as a real limitation, not silently designing around it. If Naducauvery
ever gets its own real farmers, it needs its own bot token/deployment,
matching how the two clusters are already two separate demo fixtures,
never one shared bot.

**Known limitation, flagged per your instruction — `HARVEST_CONVOY_CLUSTER_ID`
sits in tension with the multi-cluster watcher ADR-008 already built.**
`watcher.py`'s `run_daily_watch(cluster_id: str | list[str], ...)` was
built specifically so one deployment *can* serve several clusters at
once (it's exercised that way in tests today). A per-deployment
`HARVEST_CONVOY_CLUSTER_ID` env var for registration pulls in the
opposite direction: it assumes one bot instance == one cluster, which
is true of every real thing currently deployed (one Telegram bot token,
one Lambda payload hardcoding `cluster_id: "kamatchipuram"`) but is a
narrower assumption than the watcher code already supports. This isn't
a new constraint introduced here — it's the same one-bot-one-cluster
reality ADR-006's Lambda payload already has — but naming it in
registration too makes it a second place that would need to change
together if that ever stops being true. **Not solving it now; the
eventual fix, sketched and left for a future ADR if this becomes real**:
either (a) infer the cluster from the plot's registered coordinates
(nearest seeded `Cluster` by distance, since every cluster already has
`lat`/`lon` — no free-text village-matching needed) at the `COMPLETE`
transition instead of trusting a bot-level config value, which would
let one bot serve farmers across clusters; or (b) keep one-bot-one-
cluster but make it explicit and operator-scoped (one bot token issued
per operator/cluster, matching how MSP/moisture in Part 4 are already
scoped to be operator-verified per deployment). Both are real designs,
neither is needed for the two-cluster demo this project ships with
today.

**Re-registration: a second complete run from the same `chat_id`
updates the existing `Farmer`/`Plot`, not a duplicate, not a rejection.**
Justification: the ID scheme is deliberately deterministic
(`farmer-{chat_id}`/`plot-{chat_id}`) precisely because one Telegram
chat represents one farmer's one active plot in this system's current
model — there's no mechanism anywhere (registration, storage, solver)
for a single farmer to have two concurrent plots, so "duplicate" isn't
a coherent option without a larger redesign this ADR isn't proposing.
Rejecting a second attempt outright would trap a farmer who fat-
fingered their area or transplant date with no way to fix it through
the bot itself — the only channel they have. Update is also the
behavior a farmer needs every new season anyway (new transplant date,
possibly a different area), so treating "register again" as "these are
now my current details" is the same operation whether the reason is a
typo five minutes later or a new season five months later: `put_farmer`/
`put_plot` overwrite by ID, which is already how both storage backends'
`put_*` methods behave (upsert, not append) — this decision is really
"don't special-case it," not new code. `HarvestConfirmation` and ledger
records stay correctly keyed regardless (both key off `season_id`
alongside `plot_id`, never off a farmer's current field values), so an
update doesn't corrupt history from a prior season. Tested by
`test_second_registration_from_same_chat_id_updates_existing_plot`:
complete registration once, complete it again with different area/
transplant date, assert `get_plots_for_cluster` still returns exactly
one plot for that farmer, with the second run's values.

**Where this lands**: shipped as part of Part 3, since that's the first
part that needs a persisted `Plot` to compute anything against. Parts 2
and 4 depend on it transitively (both need a real, storage-backed `Plot`
tied to a `season_id` to schedule against). Part 1 does not — the
backtest runs entirely off existing seeded fixture data, never touches
live registration.

---

## Part 1: Historical backtest

### Decision 1: reuse the seeded fixtures' transplant-date pattern, replayed in 2025

`seed_cluster.py`/`seed_cluster_naducauvery.py` already define 16 plots
(8 per cluster) with real coordinates and a disclosed-synthetic spread of
transplant dates chosen to exercise TOO_GREEN/FITS/CONTESTED. Rather than
inventing a third dataset, the backtest script replays the exact same
month/day transplant dates against **2025** instead of 2026 — e.g.
Kamatchipuram's `p01` transplanted "2026-05-01" in the fixture becomes
"2025-05-01" for the backtest. Same plots, same coordinates, same area,
one year earlier. This keeps the backtest traceable to data already
disclosed as synthetic-on-real-coordinates, rather than asking you to
evaluate a second invented dataset's realism.

### Decision 2: what real data this actually pulls, and the one thing it can't

Confirmed live before proposing this: Open-Meteo's Archive API returns
both `temperature_2m_max`/`temperature_2m_min` **and**
`precipitation_sum` for a historical date range in one call — e.g.
`2025-06-01..2025-06-05` at Kamatchipuram's coordinates returned real
daily highs/lows and real daily rainfall (0.0, 0.0, 0.0, 0.9, 1.3mm)
against a live request made today. `weather/openmeteo.py`'s
`_fetch_archive` currently only requests `DAILY_FIELDS` (temperature
only) — the backtest needs a small new function requesting
`precipitation_sum` alongside, reusing `_fetch`/`_fetch_archive`'s
caching and gap-bridging machinery rather than duplicating it.

**What this validates, stated plainly, because overclaiming it would be
worse than not building it**: this replays *actual* historical daily
rainfall as the stand-in for what the watcher's rain-forecast trigger
would have seen. That is not the same claim as "the forecast the system
would have received in real time in 2025" — Open-Meteo's Forecast API
serves near-term forecasts only; there is no archive of *what the
16-day forecast said on a given past day*, and this project has no way
to reconstruct one. Using real historical rainfall as a proxy for "the
forecast" is optimistic by construction (hindsight is perfect;
real-time forecasts aren't) — it can only tell you the system's
GDD/capacity/routing logic reacted sensibly to weather that genuinely
happened, not that a real-time forecast-driven trigger would have fired
at exactly the moment this backtest says it would.

**What it validates, and what it doesn't, side by side:**

| Claim | Backtest supports it? |
|---|---|
| Maturity-date projections track real accumulated GDD for real 2025 weather at these coordinates | Yes — this is exactly what `agronomy/gdd.py` already computes, fed real Archive data instead of live/current data |
| The capacity/route/contest classification (`scheduling/solver.py`) produces sensible splits against a real season's real weather, not just synthetic 2026 data | Yes |
| The system would have correctly triggered on the real-time forecast a farmer would have seen | **No** — no historical forecast archive exists to test against; this uses actual rainfall as an optimistic stand-in, disclosed as such |
| These farmers' plots were actually harvested when the model says they should have been | **No** — no ground truth on what these (synthetic, disclosed) farmers actually did; there were no real farmers in 2025 for this specific fixture |

The README section states this table's second and third rows explicitly,
not just the strong first row — "validated against real weather, not
against real outcomes" is your framing and it goes in verbatim.

### Decision 3: script shape and output

New `scripts/backtest_2025_kuruvai.py`, read-only (no `--write`, no
storage mutation — this never touches DynamoDB or the deployed table
either):

1. For each seeded cluster, for each plot (dates shifted to 2025 per
   Decision 1): fetch real daily temperatures + precipitation for the
   full season window (transplant date through ~4 months out — enough
   to guarantee maturity is reached for even the latest-transplanted
   plot) in one Archive call per plot, cached like every other
   `weather/openmeteo.py` call.
2. Walk the season day by day *as if* the watcher had run every real
   day: for each simulated "today," slice each plot's pre-fetched daily
   series up to that date, run `agronomy.gdd.project_maturity_date`
   (already exists, unmodified) against the cluster's real derived
   threshold (`agronomy.calibration.derive_cluster_maturity_gdd`,
   already exists — this backtest is also a second real proof point for
   per-cluster calibration, on a different year's data than ADR-008
   used), and run `scheduling.solver.solve()` with the real historical
   rainfall standing in for the forecast (Decision 2's caveat applies).
   Slicing pre-fetched data instead of one Archive call per simulated
   day keeps this to ~16 live calls total (one per plot, historical
   temp+precip together), not thousands.
3. Record: per plot, the real projected maturity date, and every
   simulated day the trigger condition would have fired, with the
   outcome (`TOO_GREEN`/`FITS`/`CONTESTED`) on each triggering day.
4. Output: a markdown table (one row per plot: transplant date,
   projected maturity date, first trigger date, final outcome) printed
   to stdout, plus a short narrative — how many trigger days per
   cluster, how the two clusters' calibrated thresholds compared against
   this real season, any contested pairs and why.

No Telegram, no Bedrock, no LLM calls anywhere in this script — this is
pure verification of the deterministic core (`agronomy`/`scheduling`)
against real data, which is also the whole point: the LLM's job
(advocacy) isn't what needs backtesting, the math is.

**README addition**: new section, "Backtest against the real 2025
Kuruvai season," placed near the existing "Honesty section" — the table
from Decision 2 goes here verbatim, plus a link to the script and a
one-line summary of the narrative output from a real run (not
hypothetical numbers — the actual run's results, once it's been run for
real).

### Implemented — real run results, and one finding surfaced by it

Built as `scripts/backtest_2025_kuruvai.py` and `get_historical_daily()`
(new function, `weather/openmeteo.py`), both with unit tests (7 new
tests for the script's pure helpers, 4 new tests for the weather
function — 329 total, all passing, all hermetic). One refinement beyond
what Decision 3 specified: `agronomy.calibration.derive_cluster_maturity_gdd`
is called with `today=2025-01-01`, not the real current date — its 5-year
lookback then covers 2020–2024, deliberately excluding 2025 itself, so
the threshold that scores the 2025 season was never trained on the 2025
season. Real run, both clusters:

- **Kamatchipuram**: calibrated threshold 1637.0 GDD (2020–2024
  climatology). 172 trigger days across the simulated window
  (2025-05-01 to 2025-12-05); 59 of those days had at least one
  CONTESTED plot.
- **Naducauvery**: calibrated threshold 1931.4 GDD — ~10.6% above
  Kamatchipuram's, consistent with ADR-008's original comparison and now
  confirmed on a second, non-overlapping 5-year window. 172 trigger days
  (2025-05-10 to 2025-12-07); 65 contested days.

**A real finding, not something this ADR anticipated**: 5 of 8 plots at
Kamatchipuram and 6 of 8 at Naducauvery were first classified FITS, then
end the simulated season CONTESTED instead. Root cause:
`scheduling/solver.py:solve()` recomputes from scratch on every trigger
day, reading every plot currently in storage — there is no mechanism
anywhere in the system today that marks a plot "already harvested" and
removes it from future scheduling, so a plot that fit early keeps
re-competing for capacity against every later-maturing plot for the rest
of the season. This is a real property of the deployed system, not a
backtest artifact (the real watcher calls `solve()` fresh every trigger
day too) — it was only surfaced now because this is the first time the
solver has been run across ~170 sequential trigger days instead of a
single-run demo. **Not a validation-script problem to shrug off — see
Part 1.5 immediately below, which fixes this in the core scheduling
loop itself, before any of Parts 2–4 are built.** Full tables and
narrative (both the buggy and the fixed run) are in the README's
backtest section.

---

## Part 1.5: plot harvest lifecycle

**Added after Part 1's real run, before Part 2 — a correctness bug in
`solve()`, not a stylistic addition, and it blocks trusting Parts 2–4's
design on top of an unfixed scheduling core.**

`solve()` has no concept of "this plot was already harvested" — it
recomputes every plot's classification from scratch on every trigger
day, reading whatever `Storage.get_plots_for_cluster()` returns. Every
demo and every gate test so far has been a single trigger on a single
day, so this never surfaced. Part 1's backtest ran ~170 sequential
trigger days per cluster and found it immediately: 5/8 Kamatchipuram
plots and 6/8 Naducauvery plots were first classified FITS, then
regressed to CONTESTED later in the same season, because a plot that
fit early keeps re-entering capacity contention against every
later-maturing plot for the rest of the season.

Two real production consequences, not just a backtest artifact:

1. **A farmer whose crop is already in gets messaged again** —
   `notify.send_harvest_scheduled` has no memory between trigger days.
2. **More seriously: the capacity budget is consumed by plots that no
   longer need it.** `scheduling/solver.py:solve()`'s greedy allocation
   (`ready_ranked`, sorted by urgency) ranks an already-harvested plot
   as if it still needed a slot, which means it can out-rank — and take
   budget away from — a genuinely urgent unharvested plot that should
   have won that slot instead. This is not a cosmetic bug; it can
   produce a wrong schedule.

### Decision A: new persisted state, keyed per plot per season

No new dataclass entity — this is a boolean-per-key marker, the same
shape as `Storage.get/set_watcher_last_run`'s idempotency marker, not a
rich record like `LedgerEntry`. Three new `Storage` Protocol methods
(`storage/interface.py`):

```python
def mark_plot_harvested(
    self, plot_id: str, cluster_id: str, season_id: str, dispatched_at: str,
) -> StorageResult:
    """Overwrite semantics, like every put_* here except put_ledger_entry
    -- a plot legitimately marked twice (e.g. a retried write) should
    just update dispatched_at, not error."""

def clear_plot_harvest(self, plot_id: str, cluster_id: str, season_id: str) -> StorageResult:
    """Removes the marker -- returns the plot to the schedulable pool on
    solve()'s next call. Safe on a plot never marked (no-op, not an
    error). See Decision E for who calls this today, and the Part 2
    reversal hook this is designed for."""

def get_harvested_plot_ids(self, cluster_id: str, season_id: str) -> set[str]:
    """Every plot_id marked harvested for this cluster/season. Empty set
    for a season with no records -- see Decision D."""
```

`DynamoStorage` stores these as `PK=PLOT#{plot_id}, SK=HARVEST#{season_id}`,
`GSI1PK=CLUSTER#{cluster_id}, GSI1SK=HARVEST#{season_id}#{plot_id}` —
reusing the exact `GSI1`/`_query_gsi1` machinery `Farmer`/`Plot` already
use for their own `CLUSTER#{cluster_id}` lookups, no schema change.
`FileStorage` adds one more top-level dict, `harvest[cluster_id][season_id][plot_id] = dispatched_at`.

### Decision B: `solve()` gains a fourth outcome, `HARVESTED` — excluded, not ranked

```python
class PlotOutcome(str, Enum):
    TOO_GREEN = "too_green"
    FITS = "fits"
    CONTESTED = "contested"
    HARVESTED = "harvested"  # already dispatched this season
```

`solve()` gains a keyword-only `harvested_plot_ids: frozenset[str] = frozenset()`
parameter. Every plot in it gets a `PlotDecision(outcome=HARVESTED, ...)`
built directly — no `assess_plot()` call, no GDD/urgency computed, no
`plot_days` lookup needed for it at all — and is removed from the set
that `assess_plot`/ranking/capacity allocation ever sees. This is
structurally identical to how `TOO_GREEN` already works (a plot excluded
from contention before ranking runs, not a plot ranked last), which is
exactly the shape you asked for. `solve()` still returns one
`PlotDecision` per input plot (the existing contract), so a caller can
always see a harvested plot's status in the result — it just never
competes for a route position or a budget acre.

Because harvested plots need no weather data, `watcher.py` fetches
`harvested_plot_ids` from storage *before* calling
`get_daily_temperatures`, and skips the weather fetch for them entirely
— one fewer Open-Meteo call per already-harvested plot, every day, for
the rest of the season it's excluded.

### Decision C: the coordinator marks the plot harvested at dispatch — not Part 2's confirmation

Per your instruction, this is deliberately not coupled to Part 2.
`agents/coordinator.py:run_cluster`/`run_cluster_with_claims` gain two
new required parameters, `season_id: str` and `today: date` (both
already resolved by `watcher.py` before it calls in — a threading
change, not a new dependency). Inside the existing
`for d in too_green + fits:` loop, immediately after a `FITS` outcome is
processed, the coordinator calls
`storage.mark_plot_harvested(d.plot_id, cluster_id, season_id, dispatched_at=today.isoformat())`.
This is the exact scheduling fact the coordinator already has in hand —
"solve() says this plot fits, so the machine is going there this
run" — nothing about Part 2's farmer confirmation is involved, and
nothing here waits on it. A `CONTESTED` plot that wins a negotiation
round is *not* marked harvested — negotiation only sets priority for a
future capacity opening (per this file's own docstring: "it does not
manufacture capacity Phase 2's budget didn't allocate"), so it correctly
stays `CONTESTED`, still fully in future contention, exactly as before.

### Decision D: season boundary — there is no separate `Season` entity, and that's fine

This codebase has no `Season` object with an explicit start/end date;
`season_id` is an opaque string threaded through every call
(`LedgerEntry`, and now this). "Season boundary resets the state" is
therefore automatic by construction: `get_harvested_plot_ids(cluster_id,
"2026-samba")` for a season with zero prior records simply returns an
empty set — nothing to reset, nothing to migrate, no special-cased
"first run of a new season" branch anywhere. A plot harvested under
`season_id="2026-kuruvai"` is unconditionally schedulable again under
`season_id="2026-samba"` because the two season_ids never share a
storage key. Documented here explicitly so "no boundary defined" reads
as "handled by construction," not "unhandled."

### Decision E: failure handling, and the reversal hook for Part 2

**A plot marked harvested that shouldn't have been** (operator
correction, e.g. a false trigger or a data-entry mistake upstream):
`clear_plot_harvest` is the fix, callable today. No Telegram surface for
it — building a farmer/operator-facing command wasn't asked for and
isn't needed for this to be a real, usable fix; a minimal
`scripts/clear_plot_harvest.py` (same `--cluster-id`/`--season-id`/
`--plot-id` shape as this project's other maintenance scripts) exposes
it without anyone touching DynamoDB directly.

**A plot whose state write fails mid-run**: `mark_plot_harvested`
returns the same `StorageResult` every other write does. On failure,
the coordinator logs a loud, explicit warning (matching
`solve()`'s existing `MATURITY THRESHOLD FALLBACK` convention) and
*continues* — it does not abort the trigger run over one failed write.
Consequence, stated plainly: that one plot's harvested status isn't
persisted, so it's eligible to be reassessed and re-notified on the next
trigger day — the same double-notification Part 1.5 exists to reduce,
recurring for just that one plot, not a crash or data corruption. Safe
degrade, not silent data loss (the failure is logged, not swallowed).

**The Part 2 reversal hook, designed now, not implemented**: when Part 2
lands, a farmer reporting "the machine never came" should call
`clear_plot_harvest` (returning the plot to the schedulable pool) *and*
credit the fairness ledger — both already-designed operations, not new
ones. Nothing in Part 2's design is built yet; this ADR only confirms
the interface Part 2 will call is already clean and already tested.

### Decision F: re-running the backtest without Storage or the coordinator

Part 1's script deliberately never touches `Storage` and never calls the
coordinator (no Bedrock, no LLM calls — see Part 1's Decision 3). It
can't fetch `harvested_plot_ids` from a real backend the way `watcher.py`
now does. Instead it keeps a local, in-memory `set[str]` per
cluster/season backtest run (never persisted, discarded at the end of
each cluster's simulation — the backtest's own stand-in for "one
season's worth of storage state"): after each trigger day's `solve()`
call, every plot decided `FITS` that day is added to the set, and the
set is passed as `solve()`'s `harvested_plot_ids` on every subsequent
day. This exercises the exact same `solve()` code path production now
uses, without needing a real `Storage` backend or any LLM calls — the
backtest stays exactly as "pure verification of the deterministic core"
as Part 1 first described it.

### Tests

`tests/test_solver.py` (or wherever solver tests already live): a
harvested plot is excluded from `ready`/ranking entirely, never appears
in `fits`/`contested`, still appears exactly once in the returned list
with outcome `HARVESTED`; a harvested plot never consumes capacity
budget that an unharvested urgent plot needed (the actual bug, tested
directly: construct a case where, without the fix, a harvested plot
would have out-ranked a genuinely urgent one for the last budget slot).
`tests/test_coordinator.py`: a `FITS` decision triggers
`mark_plot_harvested`; a `CONTESTED`-negotiation winner does not;
a failed write logs and doesn't raise (a `FileStorage` subclass whose
`mark_plot_harvested` always fails, same "no hand-rolled Storage fake
needed" approach the rest of this file already uses).
`tests/test_file_storage.py`: mark/clear/get round-trip, a fresh
season_id returns an empty set, `clear_plot_harvest` on an unmarked plot
is a no-op success, state survives a reload. **Not** adding
`DynamoStorage`-specific round-trip tests for these methods — this
project's existing, disclosed practice
(`tests/test_dynamo_storage.py`'s own docstring: "No real AWS table is
created or used by anything in this file... FileStorage is what every
other test in this suite actually exercises") already doesn't test
`get_cluster`/`put_cluster`/etc. against a mocked table, and `moto`
isn't a project dependency. Adding table-mocked tests for only the new
methods, while every existing `DynamoStorage` CRUD method stays
untested that way, would be inconsistent scope creep, not a fix — this
is a pre-existing, already-disclosed gap, not one Part 1.5 introduces.

### Re-run results

See the README's backtest section for the full before/after tables —
both runs are kept side by side there, not replaced, per your
instruction. Summary of the real re-run, same fixture data, same real
2025 weather, only `solve()` changed:

- **Kamatchipuram**: CONTESTED days 59 → **3**. All 5 plots whose first
  ready trigger was FITS regressed to CONTESTED before the fix (5 of 5);
  0 of 5 regress after.
- **Naducauvery**: CONTESTED days 65 → **6**. All 6 plots whose first
  ready trigger was FITS regressed before the fix (6 of 6); 0 of 6
  regress after.
- Across both clusters: 11 of 11 plots that ever reached FITS regressed
  to CONTESTED before the fix — this was not an edge case, it was what
  happened to every plot that got far enough to matter. 0 of 11 regress
  now; every plot that fits ends the season `harvested` instead.
- The remaining CONTESTED days (3 and 6) are genuine capacity
  contention — several plots ready at once before earlier ones cleared
  the pool — not a residual bug. Some plots that were CONTESTED on
  their *first* ready trigger (p02/p07/p08, nc-p01/nc-p05) later became
  `harvested` anyway once capacity freed up — the fairness/priority
  mechanism working as intended, only visible now that plots correctly
  leave contention for good instead of piling back up in it.

---

## Part 2: Harvest confirmation loop

### Decision 4: new Storage entity — `HarvestConfirmation`

One record per (plot_id, season_id), matching `LedgerEntry`'s existing
per-(farmer_id, season_id) shape:

```python
@dataclass(frozen=True)
class HarvestConfirmation:
    plot_id: str
    farmer_id: str
    cluster_id: str
    season_id: str
    scheduled_date: str       # ISO date -- the day the route said "today"
    asked_at: str | None      # ISO timestamp the evening prompt was sent; None if not yet asked
    confirmed: bool | None    # None = unknown/no reply yet -- never assumed either way
    confirmed_at: str | None  # ISO timestamp of the farmer's reply, if any
```

`confirmed: bool | None` is the load-bearing type choice: `None` is a
real, distinct third state (never asked, or asked and no reply yet),
not defaulted to `False`. Your instruction — "if unconfirmed, the farmer
was effectively bumped regardless of what the system decided" — applies
specifically to **no reply after a defined window**, not to "not yet
asked" (those are different states and the fairness write in Decision 6
only fires for the former).

`Storage` gains `get_harvest_confirmation(plot_id, season_id)`,
`put_harvest_confirmation(...)`, and
`get_pending_confirmations(cluster_id, older_than_days: int)` (for the
"unconfirmed after N days" sweep in Decision 6) — same
Protocol-then-both-backends shape as every other entity.

### Decision 5: the evening prompt — one message, two taps, new trigger, not a new schedule change yet

A plot gets a `HarvestConfirmation` record created the moment
`_send_notifications` sends it a `harvest_scheduled` message (today's
watcher run already knows exactly which plots that is — `fits_route` in
`watcher.py:_send_notifications`). The evening prompt itself needs a
**separate scheduled invocation**, since "evening" is a different time
of day than the existing 06:00 trigger — proposing a second entrypoint,
`watcher.run_evening_confirmations(cluster_id, season_id)`, code-only
for now: queries `HarvestConfirmation` rows for this cluster with
`scheduled_date == today` and `asked_at is None`, sends one message per
farmer with a two-button inline keyboard (yes/no, same
`callback_query` mechanism the escalation resolution already uses —
no new message-shape category), sets `asked_at`.

**This needs a second EventBridge Schedule to actually run on a real
evening, and per your instruction that's a deployment change requiring
separate sign-off** — the code path is real and tested either way;
wiring a live schedule to it is explicitly held back until you say so,
same discipline as ADR-008's multi-cluster watcher (shipped in code,
deployed schedule left untouched until asked).

The farmer's yes/no tap is a `callback_query`, handled the same way
`webhook.py`'s escalation-resolution taps already are: a new
`webhook.handle_confirmation_callback` (routed by a
`confirm:{plot_id}:{season_id}:{yes|no}` callback-data prefix, parallel
to the existing `resolve:...`/`lang:...` prefixes) sets `confirmed` and
`confirmed_at`, answers with a short toast (translated, same pattern as
every other operator/farmer toast fixed in the last round).

**One message, two taps — verified this doesn't grow into a chat flow**:
the farmer's only interaction is one button tap. No free-text parsing,
no follow-up question, no new `RegistrationStep`-style state machine.
If a later ask tries to add "why didn't it come" as a free-text
follow-up, that's the chat-flow drift you told me to flag — noting it
here so it's on record what the line is.

### Decision 6: fairness ledger integration — no reply after N days = treated as bumped

A farmer with `confirmed is None` and `asked_at` more than
`UNCONFIRMED_HARVEST_WINDOW_DAYS` old gets a `record_bump(...,
days_bumped=DEFAULT_DAYS_BUMPED, outcome="unconfirmed")` write, same
function Decision-4-in-ADR-005's ledger already uses for a lost
escalation. Per your instruction, the window is **2 days**, and it's
declared as a config constant in `watcher.py` alongside
`RAIN_THRESHOLD_MM`, with the same source-discipline comment style as
`agronomy/crop_params.py` — except honestly labeled as a guess, not a
citation:

```python
# How long to wait for a farmer's harvest-confirmation reply before
# treating a scheduled-but-unconfirmed plot as a no-show for fairness
# purposes. This is an unsourced judgment call, not a derived value --
# there is no data behind "2," only the assumption that a farmer
# plausibly doesn't open Telegram every single day. Change freely; it
# has no dependency elsewhere in the codebase. See ADR-009 Decision 6.
UNCONFIRMED_HARVEST_WINDOW_DAYS = 2
```

This is the literal implementation of "the ledger currently records
what the system decided, not what happened" — an unconfirmed scheduled
harvest now costs the farmer real fairness weight next season, same as
a real bump would, because from the ledger's point of view they're
indistinguishable: the farmer didn't get harvested on the day the
system said they would.

This sweep runs as part of `run_evening_confirmations`'s *next* daily
invocation (checking for records past the window), not a separate job —
one more scheduled entrypoint would be one too many for what this
needs.

**A farmer with no `telegram_chat_id`** never gets an `asked_at` at
all — `HarvestConfirmation` is still created (so the fact remains on
record: this plot was scheduled and nobody could be asked), but the
evening job skips the send exactly the way `notify.py`'s existing
`send_*` functions already skip a `None` chat_id, logs it, and the
window sweep still fires against it (a farmer who can't be reached is,
functionally, a farmer who wasn't confirmed — same fairness
consequence, disclosed as the same case, not a special exemption).

**A reply days later, after the ledger write already fired**: the
`confirmed`/`confirmed_at` fields still get set (truth on record stays
accurate), but the fairness write is **not** reversed — per ADR-005's
existing idempotency discipline (`put_ledger_entry` fails cleanly on a
duplicate key, doesn't overwrite), a late "yes it came" doesn't retract
an already-recorded bump. Flagging this as a real, disclosed edge case
rather than building reversal logic for what should be a rare event —
say if you want it handled differently.

### Decision 7: operator follow-through rate — a derived stat, not a stored one

`storage.fairness`-style module gains
`operator_follow_through_rate(cluster_id, season_id, storage) -> float`:
confirmed-yes count over (confirmed-yes + treated-as-bumped) count for
that cluster/season, computed on read from `HarvestConfirmation`
records — not written anywhere, the same "derived, not persisted"
pattern `weighted_bump_days` already uses. Surfaced via
`scripts/check_watcher_health.py` (operator-facing CLI, already exists)
as an additional line, not a new farmer-facing message — this is
diagnostic information for whoever's running the cluster, not something
a farmer needs to see.

### Decision 8: tests

New `tests/test_harvest_confirmation.py`, same shape as the existing
ledger-gate tests in `tests/test_fairness*.py`: a farmer who reports
`confirmed=False` (or never replies past the window) shows up with
non-zero `weighted_bump_days` in the *following* season, proven the
same way the existing bump tests prove it — construct two seasons'
worth of ledger state, assert the second season's coordinator scoring
reflects the first season's no-show. Plus: no-chat-id degrade test,
late-reply-doesn't-reverse test, follow-through-rate arithmetic test.

### Implemented — design revised from Decision 6's original draft, per your direct instruction

**Decision 6 as originally drafted above was wrong, and you corrected it
before implementation started — recorded here rather than silently
edited out.** The draft said unconfirmed-after-the-window gets
`record_bump(..., outcome="unconfirmed")` — silence credited the ledger
the same as an explicit "no". Your actual instruction for Part 2 reversed
that: **silence records as unknown, never as a no-show; the ledger is
credited, and the reversal hook fires, only on an explicit "no" tap.**
"An unanswered message is not evidence the machine failed to come" — a
real, correct distinction the original draft missed by treating "no
reply" and "no" as the same signal.

Consequences of the correction, as actually built:

- **No sweep exists.** The original design needed a scheduled sweep
  (`run_evening_confirmations`'s *next* invocation checking for
  window-expired records) to write the ledger credit. Since nothing is
  ever written for silence, there's nothing to sweep — `confirmed`
  simply stays `None` forever for a plot nobody answers about, which
  *is* the correctly-recorded "unknown" state, not a placeholder for one.
  `watcher.confirmation_status(confirmation, today)` computes
  `"pending"` vs. `"unknown"` (using `UNCONFIRMED_HARVEST_WINDOW_DAYS`)
  purely at read time, for reporting — it writes nothing.
- **The late-reply edge case simplifies to "not a special case."** The
  original draft worried about a late "yes" arriving after a ledger
  write had already fired for the same key and needing to not reverse
  it. Since nothing is written on silence anymore, a late reply —
  whether "yes" or "no", whether one day or one month late — is
  processed by `webhook.handle_confirmation_callback` exactly the same
  way an on-time one is. No window-awareness in the handler at all.
- **A self-contradicting double-tap** ("no" then "yes", changing their
  mind) does update the stored `confirmed` value (truth on record,
  last-write-wins), but does not re-run the irreversible side effects —
  the plot is not re-marked harvested, the ledger credit is not
  reversed. Same discipline as "we don't reverse ledger writes",
  extended to this case explicitly, tested
  (`test_no_then_yes_does_not_reverse_the_already_recorded_bump`).
- **`operator_follow_through_rate`** (Decision 7) now excludes pending/
  unknown records from *both* halves of the ratio, not just the
  numerator — silence must not move the number in either direction,
  matching the same asymmetry. Returns `None`, not `0.0`, when nobody
  has answered anything yet.

Built: `HarvestConfirmation` (storage/interface.py, both backends,
`GSI1PK=CLUSTER#{cluster_id}` keyed like Part 1.5's harvest marker);
created at dispatch time inside `watcher._send_notifications`;
`watcher.run_evening_confirmations` (code-only, undeployed);
`webhook.handle_confirmation_callback` +
`webhook.parse_confirmation_callback_data`; confirmation prompt +
Yes/No keyboard in `notify.py`; new strings in both message modules
(drafts, sent for review below); `operator_follow_through_rate` in
`storage/fairness.py`; a diagnostic line in
`scripts/check_watcher_health.py`. 19 new tests (362 total), including
both fairness-gate tests side by side
(`test_a_reported_no_show_gains_fairness_weight_the_following_season`
and `test_silence_does_not_gain_fairness_weight_the_following_season`)
— the asymmetry you asked to have pinned down.

---

## Part 3: Projected maturity date at registration

### Decision 9: depends on the Prerequisite above

This is the part that actually needs a persisted, cluster-linked `Plot`
to compute anything — see the Prerequisite section. Implementation order
inside Part 3: ship the persistence fix first (nothing works without
it), then the projection sentence.

### Decision 10: real elapsed GDD (the existing engine) plus the existing rate, for the remainder — not a new estimation method

The literal "existing GDD engine, all the way to maturity" isn't
possible at registration time: a farmer registers right around
transplant, and maturity is ~90-110 real field days out, while
Open-Meteo's forecast horizon is 16 days — `project_maturity_date`
walking only real+forecast data would almost always return `None`
("hasn't reached threshold yet") for a projection requested this early.
Proposing a composed answer instead, using only pieces the codebase
already has:

1. Fetch real data from `transplant_date` through `today`
   (`weather.openmeteo.get_daily_temperatures` — the same function
   every other GDD computation in this project already calls). This is
   usually a handful of days (registration happens near transplant), and
   it's a genuine live call with a genuine failure mode.
2. `accumulate_gdd` (existing, unmodified) over that real span →
   `accumulated_so_far`.
3. `remaining_gdd = maturity_gdd - accumulated_so_far`, where
   `maturity_gdd` is the cluster's own `maturity_gdd_override` if
   calibrated, else `crop_params.MATURITY_GDD_ESTIMATED` — same
   fallback `scheduling/solver.py` already uses, same source of truth,
   no new number invented.
4. `rate` = the cluster's *implied* calibrated rate
   (`maturity_gdd_override / crop_params.ADT45_FIELD_DURATION_DAYS_ESTIMATED`,
   recoverable algebra, no second live call) if calibrated, else
   `crop_params.KURUVAI_MEAN_GDD_PER_DAY_REFERENCE_ESTIMATED` directly.
5. `projected_maturity_date = today + timedelta(days=round(remaining_gdd / rate))`.

This is real GDD-engine math for the part that can be real (what's
actually accumulated so far), and the same rate-based estimation the
threshold constants themselves are already built from for the part that
can't be (the ~100 days no forecast can reach) — not a third,
independently-invented methodology needing its own justification.

**Why this has a genuine "weather unavailable" path to test, as you
asked for**: step 1 is a real network call (Archive/Forecast bridge for
a just-elapsed date range) and can genuinely raise `WeatherError` —
Open-Meteo unreachable at the exact moment a farmer finishes
registering, or (rarer, near-real-time) a not-yet-settled data gap that
can't bridge. On that failure: skip the sentence, send the unmodified
`COMPLETE_MESSAGE`, never block registration — the farmer still gets
registered either way, they just don't get the maturity line this one
time. This differs from `scheduling/solver.py`'s fallback (which is
silent-with-a-loud-log because it's an internal scheduling decision,
never wrong to a human) — here, the fallback is "say nothing extra,"
because a wrong or stale-feeling number said directly to a farmer is
worse than no number.

### Decision 11: wording — one sentence, "around," both languages, drafts only

Appended to `COMPLETE_MESSAGE`, both `messages_ta.py`/`messages_en.py`
gain a `projected_maturity_sentence(date_str)` function (not a new
top-level message type — this is a suffix, composed at the call site
the same way `transplant_info_missing_prefix` already composes with a
prompt).

**English draft**: *"Based on today's weather, your plot should be
ready around {date}."*

**Tamil draft** (hand-drafted by me, not model-generated, per your
standing instruction — but I am not a native speaker, so this is a
first draft that goes through the same `print_tamil_strings.py` review
pass as every other string before it ships, exactly like ADR-008's
process): *"இன்றைய வானிலையின் அடிப்படையில், உங்கள் வயல் {date} அளவில்
தயாராக இருக்கக்கூடும்."* — literally "based on today's weather, your
plot may become ready around {date}"; "இருக்கக்கூடும்" (may be/could
be) carries the non-guaranteed, projected nature the way "should" does
in the English draft, on top of "around" already doing that work in
both.

Neither draft is final. Once you approve this ADR, implementation ends
with the usual dump-and-review round before anything ships, same as
every prior Tamil string in this project.

### Decision 12: tests

`test_registration_completion_appends_maturity_sentence_when_weather_available`
(mocked weather, deterministic dates — same `monkeypatch.setattr(om,
"_fetch", fake_fetch)` pattern already used in
`test_weather_bridging.py`), and the required negative:
`test_registration_completion_falls_back_to_plain_complete_message_on_weather_error`
— asserts the unmodified `COMPLETE_MESSAGE` is sent and registration
still transitions to `COMPLETE` normally when the weather call raises.

---

## Part 4: Post-harvest drying-window rain alert

### Decision 13: trigger — chained off Part 2's confirmation, checked in the existing daily pass

A plot becomes "in its drying window" the moment
`HarvestConfirmation.confirmed` is set to `True` (Part 2) — no new
trigger event needed, this reuses that write directly. The **existing**
06:00 daily watcher run (no new schedule, unlike Part 2's evening prompt)
gains one more pass, after its normal per-cluster work: for every plot
with a `confirmed=True` `HarvestConfirmation` where `confirmed_at` is
within the last 4 days, check that cluster's **already-fetched**
precipitation forecast (the same `get_precipitation_forecast` call
`run_daily_watch` already makes for the rain-trigger check — no second
weather fetch, no new data source, exactly as you specified) for rain
above the same `RAIN_THRESHOLD_MM` within the near-term days. If found
and no alert has been sent for this plot's current drying window yet
(new field, `HarvestConfirmation.drying_alert_sent: bool`, defaulting
`False`), send the alert and set it `True`.

**"One message per drying window, not per rain event or daily" —
confirmed as the correct reading.** `drying_alert_sent` is a single flag
per confirmation record, not per day and not per rain event — once set,
the daily pass skips this plot for the rest of its 4-day window
regardless of how many more days show rain in the forecast, and
regardless of whether there's a dry gap mid-window before more rain
risk appears. No event-boundary detection is built, on purpose — one
flag for the whole window is the entire mechanism. Because a single
message has to stay accurate across a window that might not be one
continuous rain event, the wording (Decision 15) covers the *period*,
not a moment: "rain expected over the next few days," not "it's about
to rain" — true whether the alert fires on day 1 for rain arriving day
3, or there's a dry day between the alert and the rain, or it rains
again after a dry patch the alert already covered.

### Decision 14: MSP/moisture config — a new module, same sourcing discipline as `crop_params.py`, both figures user-verified

New `agronomy/market_params.py`, mirroring `crop_params.py`'s exact
discipline (every constant carries a source URL and, where applicable,
an announcement date) — but unlike every constant in `crop_params.py`,
**neither figure here gets a value from me**:

```python
# MSP (Minimum Support Price) for paddy, Common grade, per quintal.
# Source: <TNCSC/CACP/Ministry of Agriculture notification URL — you set this>
# Announced: <date>
# None until you set a real, source-cited value -- never scraped, never
# estimated, never model-generated. See ADR-009 Part 4.
MSP_PADDY_COMMON_PER_QUINTAL: int | None = None

# Moisture content ceiling for full-price DPC acceptance, percent.
# Source: <FCI/TNCSC procurement specification URL — you set this>
# Same rule as MSP above -- not something I estimate from the "roughly
# 14%" mentioned in conversation; that's a discussion point, not a cited
# figure.
DPC_MOISTURE_THRESHOLD_PERCENT: int | None = None
```

Confirmed: the same manually-set/source-cited/you-verify treatment
applies to both figures, not just MSP — per your review, the moisture
threshold is arguably the more consequential of the two (a wrong MSP
costs a farmer an expectation; a wrong moisture figure costs him a
rejected load at the DPC), so it gets no lighter treatment than MSP,
not less scrutiny for being the "context" figure rather than the
headline number.

**If `MSP_PADDY_COMMON_PER_QUINTAL` is `None`**: the alert sends with
the rain warning only, MSP sentence omitted entirely — matches your
"send the rain alert alone and omit the price" instruction exactly. Same
independent handling for the moisture figure, since the two are
logically separate claims even though they'll likely ship together.

### Decision 15: wording — rain alert plus context, never a payment promise

**English draft**: *"Rain is expected over the next few days. Cover your
harvested grain — paddy needs to dry to about {moisture}% moisture
before a DPC will accept it at full price, and rain during drying can
cause discolouration and sprouting. MSP for this grade is Rs {msp} per
quintal."* (MSP sentence dropped entirely if unset, per Decision 14.
Phrased as a period, not a moment, per Decision 13 — accurate whether
the window turns out to be one storm or an unsettled several days.)

**Tamil draft** (same first-draft-pending-review status as Part 3):
*"அடுத்த சில நாட்களில் மழை வரக்கூடும். அறுவடை செய்த நெல்லை
மூடி வையுங்கள் -- DPC முழு விலைக்கு ஏற்க நெல் ஈரப்பதம் சுமார்
{moisture}% அளவுக்குக் குறையவேண்டும்; உலர்த்தும் போது மழை நிறம்
மாறுவதற்கும் முளைப்பதற்கும் காரணமாகலாம். இந்த தரத்திற்கான குறைந்தபட்ச
ஆதரவு விலை குயின்டால் ஒன்றுக்கு ரூ.{msp}."*

Wording check against your hard constraint: "MSP for this grade is Rs
X," never "you will receive Rs X" — both drafts state the MSP as a
published figure ("இந்த தரத்திற்கான குறைந்தபட்ச ஆதரவு விலை" = "the
minimum support price for this grade"), never a personal payment claim.
Actual grade/moisture/DPC assessment stays entirely the DPC's call, not
stated or implied otherwise anywhere in either draft.

### Decision 16: tests

Rain-in-window-sends-alert, no-rain-in-window-sends-nothing, already-
alerted-this-window-doesn't-resend, MSP-unset-omits-price-sentence-but-
still-sends-rain-warning, moisture-unset-same. All against a fixed
`HarvestConfirmation` fixture and a mocked forecast, same
`monkeypatch`-the-fetch-layer pattern as everywhere else in this
project's weather-dependent tests.

---

## Explicitly out of scope (restated for the record)

DPC/godown location lookup, mandi prices, scheme information, loan help,
pest/disease diagnosis, soil/fertiliser guidance, irrigation advice, any
query interface. The test stays: does the agent already know enough to
speak first? If asked for any of the above later, this paragraph is the
answer.

## Resolved on review (2026-08-18)

1. Prerequisite approved, with the multi-cluster tension and
   re-registration behavior both now specified above (previously
   unexamined gaps in the original draft).
2. Unconfirmed-harvest window: **2 days**, `UNCONFIRMED_HARVEST_WINDOW_DAYS`
   in `watcher.py`, commented as an unsourced judgment call — same
   discipline as the crop constants, not presented as derived.
3. "Not daily" confirmed to mean one alert flag per whole drying window;
   wording adjusted to cover a period, not a moment.
4. MSP's manually-set/source-cited/user-verified treatment confirmed to
   extend to the moisture threshold — both `None` until sourced, neither
   ships as a guess.
5. Both draft Tamil sentences (Decisions 11, 15) go through the same
   `print_tamil_strings.py` dump-and-review round as every prior Tamil
   string before either ships — not final by virtue of appearing here.

Implementation proceeds Part 1 → 2 → 3 → 4, stop-and-report after each,
commit at each boundary. Starting Part 1 now.
