# ADR-010: Persisted Decision Records, Decision Replay, Equity Report, Machinery Gap Report, CHC Positioning

- Status: **Approved, with Part 0.5 added on review (2026-08-19).** Original
  proposal covered three read-only reporting scripts plus a README
  positioning section; review of that proposal found the reporting gap
  was a symptom of a deeper one — the system computes the reasoning
  behind every decision and then throws it away. Part 0.5 fixes that at
  the source before any report is built on top of it. Sequence: Part 0.5
  → Part 1 → Part 2 → Part 3 → Part 4 (draft only), commit and report at
  each boundary.
- Date: 2026-08-19

## Context

Four additions, requested together: two of them (Parts 1 and 2) are the
project's responsible-AI story — recovering, from what the system
actually recorded, why a scheduling decision happened and whether
allocation across the season was fair. Part 3 is an operational reporting
tool (where is machinery capacity short). Part 4 is a README positioning
section, writing rather than code. A fifth piece, Part 0.5, was added
during review of the original proposal and now comes first: it's not a
report, it's the fix that makes the reports honest.

**No `CLAUDE.md` exists in this repo** — same finding ADR-008 recorded
when it hit the same instruction. Read every ADR from ADR-000 through
ADR-009 before writing this, plus the source for `models.py`,
`storage/interface.py`, `storage/file_storage.py`, `storage/dynamo.py`,
`agents/contracts.py`, `agents/coordinator.py`, `telegram/webhook.py`,
`watcher.py`, `storage/fairness.py`, and `scheduling/solver.py`.

Scope discipline restated because it governs every design choice here:
Parts 1–3 are **read-only reports**. Nothing in Parts 1–3 writes to
`Storage`. Part 0.5 is different in kind and is called out as such
everywhere below — it's a small, deliberate write-path addition to the
scheduling core itself, not a reporting script, and it's the one place
in this ADR that touches `coordinator.py`/`webhook.py`/`watcher.py`. Part
4 is writing, not code. Nothing here touches the deployed AgentCore
artifact. No dashboard, no map, no web UI, no new farmer-facing message
type, no change to the scheduling *logic* — only to what the scheduling
core remembers about the decisions it already makes.

---

## Part 0.5: the finding, and the fix at the source

### The finding

Building Part 1 required first answering "what can `explain_decision.py`
actually read?" Reading `scheduling/solver.py`, `agents/coordinator.py`,
and `telegram/webhook.py` directly to answer that, rather than assuming
the request's illustrative narrative was buildable, surfaced this:

**Almost nothing about *why* a decision happened is ever written to
`Storage`.** Read directly from the code:

- `PlotDecision` (`scheduling/solver.py`) — `accumulated_gdd`,
  `days_past_maturity`, `urgency`, `outcome`, `route_position` — is built
  fresh inside `solve()` on every trigger day, consumed in the same
  function call by `coordinator.run_cluster_with_claims()` and
  `watcher._send_notifications()`, and discarded the instant that call
  returns. There is no `put_decision` anywhere in `Storage`. Which
  maturity threshold was in effect (the cluster's calibrated value, or
  the global Theni-reference fallback — a distinction the code itself
  treats as important enough to log a loud warning about,
  `MATURITY THRESHOLD FALLBACK`) is not recorded either; only the log
  line exists, and only for as long as whatever holds the process's
  stdout/CloudWatch Logs retains it.
- `AdvocateClaim` (`urgency_score`, `rain_vulnerability`, `argument`,
  `concedes`, `weighted_bump_days`) and `EscalationPayload` (`rounds_run`,
  both claims) — built inside `coordinator.negotiate_pair()`, held
  briefly in `webhook.py`'s `_PENDING_ESCALATIONS` (an in-memory dict,
  disclosed since ADR-004 as not surviving a process restart), and popped
  and discarded the moment an escalation resolves. **Nothing about a
  negotiation — the competing plot's claim, either side's argument, the
  round count, whether a side conceded — is ever written to `Storage`.**
  Only the terminal fact survives: one `LedgerEntry` per side
  (`days_bumped`, `outcome`, `opponent_plot_id`). Whether the fairness
  bonus (as opposed to raw urgency) was what actually separated the two
  sides' scores is computed inside `negotiate_pair()`'s comparison and
  never recorded at all, in any form.

**This is an architectural gap, not a reporting one.** A reporting script
can only be as honest as what it reads, and what it reads today would
have made Part 1 either misleadingly thin (nothing to say about almost
every decision) or, worse, tempted into recomputing a plausible-looking
substitute — the exact failure mode this project's core rule exists to
prevent, just committed with arithmetic instead of a language model. **A
replay must never reconstruct a plausible past; it must report the
recorded one, and today there mostly isn't one to report.** The system,
as built through ADR-009, is not auditable by design — it computes a
defensible answer every trigger day and then forgets it.

### What CloudWatch traces do and do not preserve — for a future maintainer

Worth being precise about this, since it's easy to assume "it's fine,
it's in the traces." OTel spans (`observability/otel.py`,
ADR-006 Decision 5) wrap every agent turn and tool call —
`app.daily_watch` → `coordinator.run_cluster` → `coordinator.negotiate` →
`negotiation.round` → `advocate.get_claim` → Strands' own
`invoke_agent`/`execute_event_loop_cycle` spans — and, per ADR-006
Decision 6, **span events include full message content**, which means a
trace genuinely does capture an advocate call's prompt and structured
response, including `argument`, at the moment it happens.

But traces are not a substitute for the persistence this ADR adds, for
three concrete reasons:

1. **They only exist where they're exported to.** Locally (the default,
   `FileStorage`-backed dev/test path this entire project promises works
   with zero AWS setup) traces go to the console exporter and vanish when
   the process exits. Only the deployed AgentCore Runtime path exports to
   CloudWatch/X-Ray at all.
2. **Even where they're exported, there's no recorded link from "plot p03,
   2026-09-09" to a trace ID.** Finding the right trace means searching
   CloudWatch Logs Insights / X-Ray by approximate timestamp and hoping the
   window is narrow enough — recoverable by someone with AWS console
   access and enough patience, not queryable by an auditor or an official
   handed a plot ID and a date, which is exactly the audience the request
   named.
3. **They're not cost-free to keep indefinitely**, and nothing in this
   project has ever set a retention policy for them — ADR-006 doesn't
   mention one. `Storage` (FileStorage or DynamoDB) is this project's
   actual system of record; CloudWatch is operational telemetry, not
   an audit trail, and treating it as one was never a decision anybody
   made, just an assumption nobody checked until now.

Net: traces held *some* of this data, in the one deployed environment,
for as long as CloudWatch happened to retain it, findable only by someone
with AWS access willing to search by timestamp. `Storage` held none of it,
anywhere, ever. Part 0.5 closes the `Storage` gap; it does not touch or
depend on the tracing pipeline.

### Decision A: a new `Storage` entity, `DecisionRecord` — one per plot per trigger day

```python
# storage/interface.py
@dataclass(frozen=True)
class DecisionRecord:
    """What the deterministic core and the coordinator actually computed
    for one plot on one trigger day -- written at the moment the decision
    is made (or, for an escalated pair, updated at the moment a human
    resolves it), so a later reader never has to recompute or guess. See
    ADR-010 Part 0.5.
    """

    plot_id: str
    farmer_id: str
    cluster_id: str
    season_id: str
    decision_date: str  # ISO date -- the trigger day this record is for

    # scheduling/solver.py:PlotDecision, copied at the moment it's known
    accumulated_gdd: float
    maturity_gdd_used: float
    threshold_source: str  # "calibrated" | "fallback"
    outcome: str  # PlotOutcome.value -- too_green | fits | contested | harvested
    days_past_maturity: int | None
    urgency: float
    route_position: int | None  # only set for fits

    # The weather/capacity context that produced this trigger day's
    # classification -- identical for every plot decided this run, stored
    # per-record anyway so a single plot's DecisionRecord is fully
    # self-contained and never needs a second lookup to make sense.
    rain_threshold_mm: float
    forecast_horizon_days: int
    usable_harvest_days: int
    machine_capacity_acres_per_day: float
    capacity_budget_acres: float

    # Negotiation -- populated only for a plot that went through
    # negotiate_pair() this trigger day; None for too_green/fits/harvested
    # and for a contested plot with no pairing partner this round.
    opponent_plot_id: str | None = None
    own_claim: dict | None = None       # AdvocateClaim.model_dump()
    opponent_claim: dict | None = None  # AdvocateClaim.model_dump()
    rounds_run: int | None = None
    resolution: str | None = None
    # "won" | "lost" (resolved this trigger, no escalation) |
    # "escalated" (pending human resolution) |
    # "escalated_won" | "escalated_lost" (webhook.py updates this after a
    # human taps) | "contested_no_partner_this_round" (odd plot out,
    # ADR-003 Decision 4's pairwise-only scope limit)
    fairness_decisive: bool | None = None
    # True/False only when a genuine automatic score comparison decided
    # the round (the CLEAR_MARGIN branch in negotiate_pair) -- derived
    # post-hoc, pure arithmetic over the two already-known claims, same
    # discipline as every other "derived, shown as derived" number in
    # this project (see the helper below). None means "not applicable":
    # resolved by concession (a model judgment, not a score comparison),
    # or resolved by a human tap (escalated_won/lost), or not yet
    # resolved at all (still "escalated").
    resolved_at: str | None = None  # ISO timestamp of the FINAL resolution
    # -- set at write time for won/lost/contested_no_partner_this_round,
    # left None for "escalated" until webhook.py's update sets it.
```

`fairness_decisive` is computed by a small, pure helper in
`coordinator.py`:

```python
def _fairness_was_decisive(
    claim_a: AdvocateClaim, claim_b: AdvocateClaim,
    plot_a_id: str, plot_b_id: str, actual_winner: str,
) -> bool | None:
    """None if the round was resolved by a concession (not a score
    comparison at all) -- otherwise: would the raw urgency_score alone
    (no fairness bonus) have picked a different winner than the actual
    (bonus-included) score did? Pure arithmetic over two already-computed
    claims, reusing this module's own _score()/_fairness_bonus() -- not a
    new source of truth, a post-hoc check of the one that already ran."""
    if claim_a.concedes or claim_b.concedes:
        return None
    raw_winner = plot_a_id if claim_a.urgency_score >= claim_b.urgency_score else plot_b_id
    scored_winner = plot_a_id if _score(claim_a) > _score(claim_b) else plot_b_id
    return raw_winner != scored_winner if actual_winner == scored_winner else None
```

(The trailing `if actual_winner == scored_winner else None` guards a case
that shouldn't arise given how `negotiate_pair` computes `actual_winner`
in the first place, but the check costs nothing and turns a would-be
silent inconsistency into an explicit `None` rather than a confidently
wrong `True`/`False`.)

### Decision B: `Storage` protocol additions — reads and one new write family

```python
def put_decision_record(self, record: DecisionRecord) -> StorageResult:
    """Overwrite semantics, like every put_* here except put_ledger_entry
    -- a retried write for the same (plot_id, season_id, decision_date)
    updates rather than errors, matching mark_plot_harvested's contract."""
    ...

def get_decision_record(
    self, plot_id: str, season_id: str, decision_date: str
) -> DecisionRecord | None: ...

def get_decision_records_for_plot(
    self, plot_id: str, season_id: str | None = None
) -> list[DecisionRecord]:
    """This plot's full decision history, optionally filtered to one
    season. season_id=None (the default) returns every recorded season --
    needed by explain_decision.py when --season isn't given."""
    ...

def get_decision_records_for_cluster(
    self, cluster_id: str, season_id: str
) -> list[DecisionRecord]:
    """Every decision record for this cluster/season -- equity_report.py's
    fairness-mechanism-activity aggregate reads this."""
    ...
```

`DynamoStorage`: `PK=PLOT#{plot_id}`, `SK=DECISION#{season_id}#{decision_date}`
(colocated under the plot's own PK, same pattern as `HARVEST#`/`CONFIRM#`),
`GSI1PK=CLUSTER#{cluster_id}`, `GSI1SK=DECISION#{season_id}#{decision_date}#{plot_id}`
for the cluster-wide query, reusing the existing `_query_gsi1` machinery —
no schema change beyond one more `SK`/`GSI1SK` prefix. `FileStorage`: one
more top-level dict, `decisions[plot_id][season_id][decision_date] = asdict(record)`,
with `get_decision_records_for_cluster` doing a linear scan-and-filter
(same style `get_plots_for_cluster` already uses; this project's `FileStorage`
has never needed a secondary index, and this table stays small — see
retention, below).

### Decision C: where the writes actually happen

**Threading `TriggerContext` from `watcher.py` into the coordinator.** The
weather/capacity numbers a `DecisionRecord` needs
(`rain_threshold_mm`, `forecast_horizon_days`, `usable_harvest_days`,
`machine_capacity_acres_per_day`, `capacity_budget_acres`) and the
resolved maturity threshold (`maturity_gdd_used`, `threshold_source`) are
all things `watcher.py` already computes (or can compute with one already-
imported pure function) before it calls `solve()` — they're not new
data, just not currently passed to the coordinator. New small dataclass,
`agents/contracts.py` (alongside `PlotFacts`/`EscalationPayload` — this
is the same kind of watcher-to-coordinator structured handoff):

```python
@dataclass(frozen=True)
class TriggerContext:
    decision_date: str
    rain_threshold_mm: float
    forecast_horizon_days: int
    usable_harvest_days: int
    maturity_gdd_used: float
    threshold_source: str  # "calibrated" | "fallback"
    machine_capacity_acres_per_day: float
    capacity_budget_acres: float
```

`scheduling/solver.py` gains one small extraction, not a new computation
-- the maturity-threshold-resolution logic already inside `solve()` moves
to its own function so both `solve()` and `watcher.py` call the same
code instead of `watcher.py` duplicating the `None → fallback, log a
warning` rule:

```python
def resolve_maturity_gdd(cluster: Cluster) -> tuple[float, str]:
    """Returns (maturity_gdd, "calibrated" | "fallback"). Logs the same
    MATURITY THRESHOLD FALLBACK warning solve() already logs -- callers
    share one code path, one warning, not two independently-worded ones."""
```

`watcher.py`, right after computing `usable_days` and before calling
`solve()`, builds one `TriggerContext` for the whole cluster's trigger
run and passes it through `run_cluster`/`run_cluster_with_claims` (both
gain a required `trigger_context: TriggerContext` keyword parameter —
threading, not a new dependency, the same shape ADR-009 Part 1.5 already
used to add `season_id`/`today`).

**`coordinator.run_cluster_with_claims`** writes a `DecisionRecord` at
three points, one per branch that currently exists:

1. Inside the `for d in too_green + fits:` loop, right after the claim is
   built — covers `too_green` and `fits`, `opponent_plot_id`/negotiation
   fields left `None`, `resolved_at` set immediately (there's nothing
   pending; the classification *is* the resolution for these outcomes).
2. Right after `negotiate_pair()` returns for a paired contested plot —
   covers both plots in the pair, populates every negotiation field,
   `resolution` is `"won"`/`"lost"` with `resolved_at` set immediately if
   `negotiate_pair` resolved it this trigger, or `"escalated"` with
   `resolved_at=None` if it didn't (the `EscalationPayload` gains a
   `decision_date: str` field, `= trigger_context.decision_date`, so
   `webhook.py` can find the exact record later — see Decision D).
3. For the odd-one-out unpaired contested plot (the existing
   `if len(contested) % 2 == 1:` branch) — `resolution =
   "contested_no_partner_this_round"`, no negotiation fields, since this
   plot never actually negotiated this round.

**Never blocks or crashes the run** — same discipline ADR-009 Part 1.5
established for `mark_plot_harvested`: `storage.put_decision_record(...)`'s
`StorageResult` is checked, a failure logs
`"DECISION RECORD WRITE FAILED: plot=... : %s"` at `ERROR` and the loop
continues. A lost `DecisionRecord` degrades one plot's future
auditability, not the scheduling run that plot is part of.

### Decision D: `webhook.py` updates the record when a human resolves an escalation

`handle_callback_query`, at the point it already pops
`_PENDING_ESCALATIONS[key]` to build the loser's resolution reason: uses
`escalation.decision_date` (Decision C's new field) to look up both
plots' `DecisionRecord`s via `storage.get_decision_record(plot_id,
season_id, escalation.decision_date)`, and — if found —
`storage.put_decision_record(replace(record, resolution="escalated_won" |
"escalated_lost", resolved_at=<now>))` for each side. `fairness_decisive`
stays `None`: a human tap is not a score comparison, and recording one as
if it were would misattribute a human's judgment to the algorithm.

**If `escalation is None`** (process restarted since the escalation was
sent — the same disclosed case that already makes the loser's resolution
message omit its specific reason, ADR-005/ADR-009): the `DecisionRecord`
update is skipped too, for the identical reason (no `decision_date` to
look it up by), logged at `INFO`. The original `"escalated"` record
written by the coordinator still exists — a reader sees "this escalated,
and was never confirmed resolved in storage," which is true, not a
fabricated resolution. Same never-crash discipline: the update attempt
never blocks notifying the two farmers.

### Decision E: retention — kept indefinitely, no season-rollover clearing, deliberately

Unlike the harvest marker (which exists *only* to control future
scheduling — Part 1.5's `season_id`-scoping means it correctly has no
life beyond the season it governs), a `DecisionRecord` has no functional
role in any future scheduling decision. Nothing in `solve()`,
`negotiate_pair()`, or the fairness ledger ever reads one back. Its only
job is to be read by a human, or a report, later — which means clearing
it on any schedule would defeat the entire point of building it: an
audit trail that quietly expires is not an audit trail, and this ADR
exists because the system's *previous* audit trail (discard-after-use)
already failed exactly that test.

**Decision: no automatic deletion, no season-rollover clearing, ever.**
At this project's actual scale, that's not a meaningful cost concern:
`solve()` already only calls `get_claim` once per plot per trigger day
it's actually evaluated, so this adds one record per plot per trigger
day the plot was already being evaluated — no new computation, no new
call frequency, just persistence of a result that already existed
in-memory. A season that runs ~170 trigger days (the backtest's real
figure) against an 8-16 plot cluster, where most plots clear to
`fits`/`harvested` within the first few trigger days they're ready (per
ADR-009's own re-run results), produces on the order of a few dozen to a
few hundred small records per cluster per season — the same order of
magnitude ADR-006 already priced DynamoDB at (~$0.01/month for this
project's whole table). No purge script is built now, for the same
reason `clear_plot_harvest.py` exists but no automated sweep does: build
the tool when something actually needs it, not speculatively.

### Tests (`tests/test_coordinator.py`, `tests/test_webhook.py`, `tests/test_file_storage.py`)

A `fits` outcome writes a complete `DecisionRecord` with the right
`threshold_source`/context fields (fixture with both a calibrated and an
uncalibrated cluster, asserting `"calibrated"`/`"fallback"` land
correctly); a `too_green` outcome writes one too, with `route_position`
and negotiation fields all `None`; a resolved (non-escalated) contested
pair writes two records, `own_claim`/`opponent_claim` correctly
cross-referenced (plot A's `opponent_claim` is claim B and vice versa,
not both copies of the same claim); `fairness_decisive` is `True` for a
constructed case where the fairness bonus provably flips the raw-urgency
winner, `False` for a case where it doesn't, `None` for a
concession-resolved case; an escalated pair writes `resolution="escalated"`,
`resolved_at=None`; `webhook.handle_callback_query` updates both plots'
records to `escalated_won`/`escalated_lost` with `fairness_decisive`
still `None`; the `escalation is None` (process-restart) path leaves the
original `"escalated"` record untouched rather than crashing; a
`FileStorage` subclass whose `put_decision_record` always fails logs and
doesn't raise, and the scheduling run still completes and still notifies
farmers (matching the existing `mark_plot_harvested`-failure test
pattern); `get_decision_records_for_plot`/`_for_cluster` round-trip
against `FileStorage`, including the `season_id=None` "all seasons" case.

**Manual verification against live `DynamoStorage`** happens once, folded
into Part 0.5's own completion report (a real trigger run against the
live table, then reading the written `DecisionRecord`s back through
`get_decision_records_for_cluster` and confirming they match what the
run actually did) — not automated as a `pytest` test, consistent with
this project's existing, disclosed practice of not mock-testing
`DynamoStorage`'s CRUD methods.

---

## Cross-cutting design for Parts 1–3, shared by all three scripts

### New package: `harvest_convoy/reporting/`

```python
# harvest_convoy/reporting/provenance.py
@dataclass(frozen=True)
class Provenance:
    storage_backend: str      # type(storage).__name__
    generated_at: str         # ISO 8601 UTC
    git_commit: str           # `git rev-parse HEAD`, or "unknown (...)"
    cluster_ids: list[str]
    season_ids: list[str]

def build_provenance(storage: Storage, *, cluster_ids: list[str], season_ids: list[str]) -> Provenance: ...
def render_provenance_text(p: Provenance) -> str: ...  # first block of every text output
```

`git_commit` via `subprocess.run(["git", "rev-parse", "HEAD"], ...)`
wrapped in a bare `except Exception` — must still produce a complete
report from a non-git checkout, never crash on this.

Every result dataclass across all three scripts carries `provenance:
Provenance` first and a `data_gaps: list[str]` field for the standard
"not recorded, and why" notes.

### One result object, two renderers, never formatted twice

```python
def render_text(result: ...) -> str: ...          # stdout
def to_json_dict(result: ...) -> dict: ...         # --out path, includes raw stored records
```

`to_json_dict` includes the actual stored dataclasses (`asdict(...)`) the
text was built from under a `raw_records` key.

### Storage backend, testing, missing-data, CLI, language

Unchanged from the original proposal: `get_storage()` (respects
`HARVEST_CONVOY_STORAGE`, no script-specific override); hermetic
`FileStorage`-backed tests for every script, one script's read paths
additionally verified by hand against live `DynamoDB` (Part 2, since it
reads the widest slice of entity types in one run) and reported, not
automated; empty results render as explicit stated absences, never
errors; a cluster/season/plot that doesn't resolve is a clear stderr
message and exit 1, never a traceback; `argparse` with a documented
`--help` and one example invocation per script; a per-script test
patching the model provider to raise, proving no Bedrock call is possible
in any of the three read paths; English only, for the reasons already
recorded (operator/auditor/official audience, a fourth Tamil-review round
isn't feasible in the time available for text nobody asked to read in
Tamil).

---

## Part 1: `scripts/explain_decision.py`

```
usage: explain_decision.py --plot-id PLOT_ID --date YYYY-MM-DD
                            [--season SEASON_ID] [--out PATH]
```

### Behavior

1. Resolve `Plot`/`Farmer`/`Cluster` (current record; any missing one
   renders as a stated absence, doesn't block the rest).
2. **Season resolution**, if `--season` omitted: try
   `storage.get_decision_records_for_plot(plot_id, season_id=None)` first
   (every season, unfiltered) and look for a record whose `decision_date`
   matches `--date` — if found, its `season_id` is used for everything
   else. If not found there, fall back to the same ledger-entry-date-match
   heuristic the original proposal used (a `LedgerEntry` for this
   farmer/plot whose `resolved_at` date matches `--date`) — covers a
   decision predating Part 0.5. Zero matches either way: proceed without
   a season, later sections state plainly that a season-scoped lookup
   wasn't possible.
3. **The decision itself.** `storage.get_decision_record(plot_id,
   season_id, date)` (once a season is known). **If found: this is the
   real thing** — GDD, threshold used and its source, classification,
   weather/capacity context, and, if this plot was contested, the full
   negotiation (opponent, both claims verbatim, rounds run, resolution,
   and whether the fairness bonus was decisive) — reported in full, no
   gap notes needed for any of it. **If not found**, the report states,
   explicitly and by name: *"No decision record exists for this plot on
   this date. This project began persisting per-decision records on
   {Part 0.5's ship date} (ADR-010); a decision made before that date was
   never recorded, and there is no way to recover it — recomputing it now
   would not reproduce what was actually decided, only a plausible
   substitute, which this project's own rule refuses to present as fact.
   A decision made after that date and still missing here indicates
   either this plot was never evaluated on this date, or a write
   failure (see the DECISION RECORD WRITE FAILED log line for this plot/
   date, if any)."* This is the load-bearing sentence in the whole
   script: it makes "never recorded" and "did not happen" impossible to
   confuse, exactly as required.
4. **Ledger and confirmation sections**, as in the original proposal —
   `LedgerEntry`s referencing this plot (either side) for the season,
   `HarvestConfirmation` (dispatch date via `scheduled_date`, ask/confirm
   status via `confirmation_status()`), current `get_harvested_plot_ids`
   membership. These remain independently useful even when a full
   `DecisionRecord` exists, since they're the terminal-outcome view a
   reader may want cross-checked against the decision-time view.
5. Provenance header first, both outputs.

### Narrative rendering

When a `DecisionRecord` exists, the narrative now looks like the
request's original illustrative example — real numbers, not placeholders
— because the data is now real:

> On 2026-09-09, Kannan Raja's 3.0 acre plot (p03, Kamatchipuram) was
> classified CONTESTED. The crop had accumulated 1,681.4 GDD against the
> cluster's calibrated maturity threshold of 1,637.0 (calibrated, not the
> global fallback) — 6 days past maturity. That day's usable-harvest-day
> window was 3 days (rain threshold 5.0mm, 16-day forecast horizon),
> giving a capacity budget of 10.5 acres against this cluster's 3.5
> acres/day.
>
> This plot was paired against p04 (Meena Subramani). Three rounds ran.
> p03's final claim: urgency_score 0.30, rain_vulnerability moderate,
> weighted_bump_days 0.0, concedes False, argument: "...". p04's final
> claim: urgency_score 0.05, weighted_bump_days 3.5, concedes False,
> argument: "...". No clear score margin resolved it after 3 rounds — the
> pair escalated. Recorded resolution: escalated_won, resolved 2026-09-09T18:04:00Z.
> Fairness bonus decisive: not applicable (resolved by a human, not an
> automatic score comparison).

When no `DecisionRecord` exists, the script falls back to the original
proposal's narrower report (ledger/confirmation-only, with the explicit
"never recorded vs. did not happen" sentence from step 3).

### Tests (`tests/test_explain_decision.py`)

Full happy path with a real `DecisionRecord` present (both a resolved-
without-escalation case and an escalated-then-human-resolved case,
checking `own_claim`/`opponent_claim` render correctly and
`fairness_decisive` renders correctly in all three states —
`True`/`False`/`None`); the pre-Part-0.5 case (no `DecisionRecord`, but a
`LedgerEntry` exists) — asserts the exact "never recorded, not did not
happen" sentence appears, not a silently thinner report; plot/farmer/
cluster-not-found cases; season-inference success and failure paths; the
no-Bedrock assertion; text/JSON built from one result object.

---

## Part 2: `scripts/equity_report.py`

```
usage: equity_report.py --cluster CLUSTER_ID (--season SEASON_ID | --all-seasons)
                         [--out PATH]
```

Unchanged from the original proposal for Coverage, By-holding-size,
Repeat-bumps, Never-served, and Operator-follow-through (see the
`SMALLHOLDER_THRESHOLD_ACRES` policy-parameter design, the
`Plot`-has-no-season-dimension caveat stated in the header, and the
simulated-cluster caveat prepended to both outputs — all as originally
proposed, none of it depended on Part 0.5).

**Fairness mechanism activity — no longer `not recorded`.**
`storage.get_decision_records_for_cluster(cluster_id, season_id)`,
filtered to records with `fairness_decisive is not None` (i.e., an
actual automatic score comparison happened, not a concession or a human
resolution): count of `True` ("decisions the fairness bonus actually
influenced") vs. `False` ("applied but did not change the outcome").
Records from before Part 0.5 shipped simply don't exist for this query,
so a season entirely predating Part 0.5 correctly reports zero of both —
**the header states the count of decision records found for this season
and, if zero, says explicitly that this season predates persisted
decision records rather than implying fairness was never a factor.**

### Tests

As originally proposed, plus: fairness-mechanism-activity counts against
a hand-built set of `DecisionRecord`s (mix of `True`/`False`/`None`,
asserting the `None` ones are excluded from both buckets, not folded into
either); the "season predates Part 0.5" zero-records case renders its
explicit note rather than a bare `0/0`.

---

## Part 3: `scripts/machinery_gap.py`

Unchanged from the original proposal — forward-looking (current plot
roster, current cluster config, live weather), the same kind of live
computation `watcher.py` and `backtest_2025_kuruvai.py` already do, no
`Storage`-fidelity question the way Parts 1–2 have, and no dependency on
Part 0.5. See window resolution, per-cluster computation, assumptions
block, and headline-sentence design as originally specified; tests as
originally specified (hand-computed arithmetic, no live calls in the test
suite, no-Bedrock assertion).

---

## Part 4: README positioning section

Not implemented in this ADR — drafted and shown for approval after Part 3
ships. One scope decision recorded here, unchanged from the original
proposal: **no CHC or AGRISNET integration is built or implied.** No API
access, no data-sharing agreement with either system exists; any
integration code would be written against an interface nobody can see,
and unlike this project's other `_ESTIMATED` constants (visibly labeled,
correctable), a fabricated integration surface looks real until someone
tries to call it. The README section is positioning language only.

---

## Consequences

- **The system now remembers why it decided what it decided, from Part
  0.5 forward.** A `DecisionRecord` per plot per trigger day, written by
  the coordinator at the moment of decision and updated by `webhook.py`
  at the moment a human resolves an escalation — GDD, threshold and its
  source, classification, weather/capacity context, both sides' full
  claims, round count, resolution, and whether the fairness bonus was
  decisive. This is a genuine architecture change, not a reporting
  add-on, and it's the honest headline for this ADR: an audit tool
  surfaced that the system it audits wasn't recording enough to be
  auditable, and got fixed at the source instead of worked around in the
  reporting layer.
- **`explain_decision.py` now reports the real thing for every decision
  made from Part 0.5 onward**, and an explicit, unambiguous "never
  recorded, not did not happen" statement — never a silently thinner
  report — for anything before it.
- **`equity_report.py`'s fairness-mechanism-activity section is real**,
  reading `DecisionRecord.fairness_decisive` directly, with the same
  explicit predates-Part-0.5 handling for older seasons.
- No new farmer-facing behavior, no change to any scheduling *outcome* —
  `solve()`'s classification, `negotiate_pair()`'s resolution logic, and
  every existing test's expected outcomes are unaffected; the change is
  additive persistence of results that already existed in memory, plus
  one small, backward-compatible refactor
  (`scheduling.solver.resolve_maturity_gdd`) and one small, additive
  dataclass field (`EscalationPayload.decision_date`).
- Retention is a deliberate "forever," recorded and justified, not left
  implicit — this project's other durable records (the ledger,
  confirmations) already work this way; `DecisionRecord` joining them is
  consistency, not a new policy.
- CloudWatch traces remain what they always were — rich, but
  environment-limited, unindexed by plot/date, and retention-unmanaged —
  documented here so a future maintainer doesn't rediscover the same gap
  by assuming traces already covered this.
- Parts 1–3 remain strictly read-only over `Storage`; Part 0.5 is the one
  write-path change in this ADR, called out everywhere above as such.

## Sequence

Part 0.5 (storage entity, protocol methods, both backends, the
coordinator/webhook/watcher wiring, the solver refactor, full test
coverage, one manual live-`DynamoStorage` verification) — commit, stop,
report. Then Part 1 — commit, stop, report. Then Part 2 — commit, stop,
report. Then Part 3 — commit, stop, report. Then Part 4's README section
drafted and shown before it's committed.
