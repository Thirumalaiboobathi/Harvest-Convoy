# ADR-005: Persistence, Fairness Ledger, AgentCore Memory

- Status: Proposed (awaiting go-ahead — **do not implement yet**)
- Date: 2026-08-17

## Context

Phases 1-4 ran entirely on fixture data and two in-memory placeholder
stores (`registration.py`'s state dict, `webhook.py`'s resolved-escalation
set), both explicitly disclosed as not surviving a restart. Phase 5 makes
that real: DynamoDB-backed storage, a fairness ledger that actually
changes negotiation outcomes across seasons, and an honest verification
status for AgentCore Memory.

## Decision 0: AgentCore Memory — skipped this phase, by deliberate choice

Ran the same live-verification standard as Bedrock: `aws
bedrock-agentcore-control list-memories --region ap-south-1` →
**exit 0, `{"memories": []}`**. The service is real, reachable, and
authorized for this account/region — control-plane access is confirmed.
No Memory resource was created (that provisions real, persistent,
potentially-billable AWS infrastructure — not something to do just to
answer a verification question).

**Decision: skip AgentCore Memory entirely this phase.** Not because it's
unreachable — it's reachable — but because right now it would be
decoration, not architecture. DynamoDB (Decisions 2 and 4 below) is
already the real, queryable store for everything AgentCore Memory would
hold: per-farmer negotiation/fairness history. Standing up a second store
that duplicates the same facts, with no consumer that needs Memory's
specific capabilities (semantic recall across unstructured conversation,
extraction pipelines) over a plain structured query, would be surface area
added for the sake of using the service, not because the product needs it.

Revisit in Phase 6: once the AgentCore Runtime deployment exists, Memory
has a real job to do — session/actor-scoped state for the running agent
across invocations, which is what it's actually designed for, rather than
a parallel copy of ledger rows DynamoDB already answers directly.

Separately, for the record: `strands-agents` 1.52.0 ships no AgentCore
Memory integration (`strands.vended_memory_stores` has
`bedrock_knowledge_base` and `test_memory_store`, no AgentCore one) — any
future wiring would be direct `boto3` calls against the `bedrock-agentcore`
data-plane API, not through Strands' `MemoryManager` abstraction.

## Decision 1: local dev backend is a file adapter, not DynamoDB Local

Checked what's actually on this machine: Docker CLI is installed but the
Docker Desktop engine isn't running (`docker ps` fails to connect). DynamoDB
Local needs either Docker or a JRE. Requiring either for "a judge clones
the repo and runs it" adds a real setup step a judge might not have ready —
worse than the honest bar in the brief ("run without an AWS account").

**Decision: a small `FileStorage` adapter (plain JSON on disk, stdlib
only) is the default local backend — zero extra installs, works anywhere
Python does.** `DynamoStorage` (real `boto3`) is the second implementation,
usable against real AWS *or* DynamoDB Local via `endpoint_url` override
for anyone who wants closer-to-prod fidelity — same code path either way,
just configuration. Both implement one shared `Storage` protocol
(`storage/interface.py`), so the single-table key scheme below is
validated by both backends using the literal same keys, not two designs
that happen to agree.

README will state plainly: default `uv run` uses `FileStorage`, no AWS
account needed; set `HARVEST_CONVOY_STORAGE=dynamo` (+ AWS credentials, or
`+ DYNAMODB_ENDPOINT_URL` for DynamoDB Local if you have it running) to
switch backends.

## Decision 2: single-table design

One table, `harvest_convoy`. PK/SK scheme, and why:

| Entity | PK | SK | Notes |
|---|---|---|---|
| Cluster | `CLUSTER#{cluster_id}` | `METADATA` | |
| Farmer | `FARMER#{farmer_id}` | `METADATA` | |
| Plot | `PLOT#{plot_id}` | `METADATA` | |
| Season record (per plot) | `PLOT#{plot_id}` | `SEASON#{season_id}` | colocated under the plot's PK |
| Fairness ledger entry (per farmer, per season) | `FARMER#{farmer_id}` | `LEDGER#{season_id}` | colocated under the farmer's PK |

GSI1 ("ClusterIndex"): `GSI1PK = CLUSTER#{cluster_id}`, `GSI1SK = PLOT#{plot_id}`
or `FARMER#{farmer_id}` — one index serves both "all plots in this
cluster" and "all farmers in this cluster" queries.

**Access patterns this covers, and why each key was chosen:**

- Get a cluster: `GetItem(PK=CLUSTER#id, SK=METADATA)`.
- Get all plots in a cluster (the scheduling entrypoint's main read):
  `Query(GSI1, GSI1PK=CLUSTER#id, GSI1SK begins_with PLOT#)`.
- Get all farmers in a cluster: same GSI, `GSI1SK begins_with FARMER#`.
- Get one plot: `GetItem(PK=PLOT#id, SK=METADATA)`.
- Get a plot's full season history in one call:
  `Query(PK=PLOT#id, SK begins_with SEASON#)` — colocating season records
  under the plot's own PK means "this plot's metadata + every season it's
  had" comes back from a single Query, not a join.
- Get one farmer: `GetItem(PK=FARMER#id, SK=METADATA)`.
- **Get a farmer's full fairness history in one call:**
  `Query(PK=FARMER#id, SK begins_with LEDGER#)` — same colocation trick.
  This is the query the gate test and `fairness.py` actually run. Each
  item *is* one season's record — `days_bumped`, `outcome`,
  `resolved_at`, `cluster_id`, `plot_id`, `opponent_plot_id` — satisfying
  "bump count, which seasons, by how many days, and the outcome each
  time" directly: bump count is `len([e for e in entries if
  e.days_bumped > 0])`, derived from the query result, not a separately
  maintained counter that could drift.

Not built this phase: a chat_id → farmer GSI (for recognizing an
already-registered farmer messaging again next season). `registration.py`
doesn't need it yet — its in-memory dict is already chat_id-keyed, it's
just not persisted. Flagging this as a real, disclosed gap for whenever
season-over-season farmer recognition actually gets built, not silently
deferring it without saying so.

## Decision 3: `seed_cluster.py` migration keeps the fixtures, adds seeding

`scripts/seed_cluster.py`'s `PLOTS`/`FARMERS`/`CLUSTER` module-level data
stays exactly as-is — every test built in Phases 2-4 imports these
directly and must keep working unmodified. A new function,
`seed_into_storage(storage: Storage) -> None`, writes that same fixture
data into whichever `Storage` backend is configured. Running
`uv run python -m scripts.seed_cluster --write` seeds real storage (file
or DynamoDB); running it with no flag just prints the summary, as today.

## Decision 4: fairness ledger — accumulated, decayed, capped below the urgency scale's granularity

Phase 3's `bumped_last_season: bool` stub becomes a real numeric signal.
Two defects in the first draft of this decision, both fixed before writing
any code:

**Defect 1 — only counting the most recent season under-weights a repeat
pattern.** A farmer bumped three seasons running is the precise injustice
the ledger exists to correct, and "last season only" would score them
identically to a farmer bumped once. Fixed by accumulating across *all*
recorded seasons with geometric decay, so repeated bumps compound and old
ones fade without disappearing:

```python
# storage/fairness.py
FAIRNESS_SEASON_DECAY = 0.5  # DERIVED, tuning constant, not sourced: each
                              # season further back, a bump's contribution
                              # is multiplied by this factor. Geometric
                              # decay is asymptotic -- old bumps fade,
                              # never fully vanish.

def weighted_bump_days(farmer_id: str, storage: Storage) -> float:
    history = get_ledger_history(farmer_id, storage)  # most-recent-season first
    return sum(
        entry.days_bumped * (FAIRNESS_SEASON_DECAY ** seasons_ago)
        for seasons_ago, entry in enumerate(history)
    )
```

A farmer bumped 2 days in each of the last 3 seasons:
`2×0.5⁰ + 2×0.5¹ + 2×0.5² = 2 + 1 + 0.5 = 3.5`. A farmer bumped 2 days
once: `2.0`. The repeat pattern now visibly outweighs the one-off, which
it didn't under "last season only."

`agents/contracts.py`'s `PlotFacts` and `AdvocateClaim` gain
`weighted_bump_days: float = 0.0` alongside the existing
`bumped_last_season: bool` (kept as-is — it's in the brief's locked
schema, and stays useful as the plain fact surfaced in farmer-facing copy;
`weighted_bump_days` is the new field that actually drives scoring).

**Defect 2 — the cap needs to be justified against the urgency scale, not
picked as a round number.** Stating the scale explicitly:
`AdvocateClaim.urgency_score` ∈ `[0.0, 1.0]`
(`agents/contracts.py:AdvocateClaim`, `pydantic.Field(ge=0.0, le=1.0)`),
produced by `agronomy/decay.py:decay_fraction()` —
`min(1.0, days_past_maturity / DECAY_HORIZON_DAYS_ESTIMATED)` for an
**integer** `days_past_maturity`. That makes it a discrete step function,
not a true continuum: with `DECAY_HORIZON_DAYS_ESTIMATED = 20` today, the
only values it can take are `0, 1/20, 2/20, ..., 19/20, 1.0` — a fixed
granularity of `1/DECAY_HORIZON_DAYS_ESTIMATED = 0.05`. The largest
possible gap between "maximally urgent" (`1.0`, fully decayed —
"shattering") and the next value below it is exactly that `0.05`, not
some infinitesimal amount.

**Invariant:** fairness must be able to tilt a close call, but a
less-than-maximally-urgent plot must never outscore a maximally urgent one
purely on fairness. That holds if and only if `MAX_FAIRNESS_BONUS` stays
strictly below the granularity — so it's *derived* from
`DECAY_HORIZON_DAYS_ESTIMATED`, not chosen independently, and stays
correct if that constant is ever changed later:

```python
# agents/coordinator.py
from harvest_convoy.agronomy import crop_params

_URGENCY_GRANULARITY = 1.0 / crop_params.DECAY_HORIZON_DAYS_ESTIMATED  # 0.05 today
MAX_FAIRNESS_BONUS = _URGENCY_GRANULARITY * 0.8  # comfortable margin below it
assert MAX_FAIRNESS_BONUS < _URGENCY_GRANULARITY, (
    "MAX_FAIRNESS_BONUS must stay below the urgency scale's granularity, "
    "or a maximally urgent plot could lose a negotiation to fairness alone"
)

FAIRNESS_WEIGHT_PER_BUMPED_DAY = 0.01  # DERIVED, tuning constant, not sourced

def _fairness_bonus(claim: AdvocateClaim) -> float:
    return min(MAX_FAIRNESS_BONUS, claim.weighted_bump_days * FAIRNESS_WEIGHT_PER_BUMPED_DAY)

def _score(claim: AdvocateClaim) -> float:
    return claim.urgency_score + _fairness_bonus(claim)
```

This is the line you can point at — the module-level `assert` makes the
invariant self-checking (fails loudly at import time if a future edit ever
breaks the relationship between the two constants), and a test asserts it
behaviorally too (see Gate, below): the maximum possible fairness bonus,
applied to a claim just below the ceiling, must not exceed a maximally
urgent claim with zero bonus. `MAX_FAIRNESS_BONUS = 0.04` today (`0.05 ×
0.8`) — comfortably below the `0.05` granularity. "Measurably harder" is
this arithmetic; the LLM never sees or sets any of these numbers, same
ground-truth-override discipline as every other fact in `AdvocateClaim`
(ADR-003 Decision 1).

**Recording a bump** happens in `webhook.py`'s escalation resolution
(`handle_callback_query`, which already retains the full
`EscalationPayload` since the copy-fix commit): on resolution, write one
`LEDGER#{current_season_id}` entry for the losing farmer
(`days_bumped` = a value supplied by whatever triggered the escalation —
for now, 1 per escalation loss; refining this to actual calendar days
lost is a reasonable future improvement, not blocking this phase) and,
for symmetry and a complete record, a `days_bumped=0, outcome="won"` entry
for the winner. **Idempotent via a conditional write**
(`ConditionExpression="attribute_not_exists(SK)"`): a concurrent duplicate
resolution attempt for the same farmer+season fails the condition, is
caught, logged, and treated as already-recorded — the same idempotency
guarantee Phase 4's in-memory set gave within one process, now real across
concurrent invocations.

## Decision 5: failure paths

- **DynamoDB unavailable**: `DynamoStorage` methods catch `botocore`
  exceptions, log, and degrade — reads return `None`/empty (already the
  "not found" shape every caller handles), writes return a `StorageResult`
  (`success`/`error`, same shape as Telegram's `SendResult`) rather than
  raising. Never throws into the agent loop.
- **Farmer with no ledger history**: `get_ledger_history` returns `[]`;
  `weighted_bump_days` defaults to `0.0`. Not an error, not a special
  case — the neutral/never-bumped baseline.
- **Ledger entry referencing a season that no longer exists**: fairness
  weighting never joins a ledger entry against a live `Season` or
  `Cluster` record — each entry is self-contained (`days_bumped`,
  `outcome` stored directly on it). A dangling `season_id` reference
  can't break the weighting because nothing dereferences it.
- **Concurrent writes to the same plot/farmer**: conditional writes
  (`DynamoStorage`) as above. `FileStorage` does **not** provide real
  concurrency safety — documented explicitly as a single-process local-dev
  convenience, not a concurrency-correct backend. If that's not
  acceptable for how you want to demo this, say so and I'll add file
  locking; wasn't going to silently claim safety it doesn't have.

## Gate

Two tests, in one file, both required — together they're the honest claim,
neither alone is:

1. **Fairness tilts close calls.** Plots, weather, and acreage held
   constant; only ledger history differs between two seeded seasons.
   Season 2's outcome must flip purely because of `weighted_bump_days` —
   same plots, same urgency gap, different history, different winner.
2. **Physics wins outright.** The inverse: a heavily bumped farmer
   (maximum plausible `weighted_bump_days`) with a genuinely low-urgency
   plot must still lose to a maximally urgent (fully decayed) plot. This
   is the invariant from Decision 4 written as a test, not just an
   `assert` — a judge should be able to open this one file and see both
   halves of the claim: fairness matters, but it cannot outvote a
   shattering plot.

## Consequences

- `FileStorage` is the default; `DynamoStorage` is one env var away, same
  interface, same key scheme, works against real AWS or DynamoDB Local.
- AgentCore Memory is deliberately not built this phase — reachable,
  documented, revisited when Phase 6's runtime gives it actual work to do.
- The fairness weighting is arithmetic in `coordinator.py`, not prompt
  text — inspectable, testable, the same discipline as every other number
  in this system, with a self-checking invariant (`assert` at import time
  plus a behavioral test) that fairness can never outrank genuine
  agronomic urgency.
