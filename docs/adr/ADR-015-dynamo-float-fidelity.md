# ADR-015: `_from_decimal`'s float/int coercion — fix with type information, not a better heuristic

- Status: **Implemented and live-verified (2026-08-25).** Approved as
  written — option 3 (per-type deserializers) with the reflection-based
  completeness test, no changes requested.
- Date: 2026-08-25

## Context

Live verification against the real `harvest_convoy` table (this session,
prior turn) found that `DecisionRecord` does not round-trip cleanly: every
`float`-typed field holding a whole number comes back from a real
`DynamoStorage` read as Python `int`, not `float` — `rain_urgency_boost`'s
own dataclass default (`0.0`) included. Confirmed live, not guessed:

```
accumulated_gdd: wrote float 2450.0, read back int 2450
rain_urgency_boost: wrote float 0.0, read back int 0   <- the dataclass default
opponent_claim.acres: wrote float 3.0, read back int 3
```

**Root cause**: `dynamo.py`'s `_from_decimal` decides the target type from
the *value* alone —

```python
def _from_decimal(value):
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if as_int == value else float(value)
    ...
```

— which is exactly right for `Farmer.telegram_chat_id` (ADR-006 Decision
9: an integral chat ID must come back as `int`, not `1276258406.0`) and
exactly wrong for a `float` field that happens to hold a whole number.
DynamoDB's Number type doesn't preserve int/float; a stored `Decimal("3")`
is genuinely ambiguous without knowing what the *field* is declared as.
No heuristic over the value can resolve that — `3` is correct for
`telegram_chat_id` and wrong for `area_acres`. This has to be fixed with
type information, not a better guess.

**This is not new-code-only.** Per your instruction, I checked every other
dataclass for `float` fields that would hit the same path before designing
the fix, not after:

| Dataclass | `float` fields | Live today? |
|---|---|---|
| `Plot` (`models.py`) | `lat`, `lon`, `area_acres` | **Yes** |
| `Cluster` (`models.py`) | `machine_capacity_acres_per_day`, `machine_start_lat`, `machine_start_lon`, `maturity_gdd_override` | **Yes** |
| `DecisionRecord` | `accumulated_gdd`, `maturity_gdd_used`, `urgency`, `rain_threshold_mm`, `machine_capacity_acres_per_day`, `capacity_budget_acres`, `rain_urgency_boost`, plus `urgency_score`/`acres`/`weighted_bump_days` nested inside `own_claim`/`opponent_claim` | Not yet deployed |
| `Farmer` | none (`telegram_chat_id` is `int`) | n/a |
| `HarvestConfirmation`, `SeasonRolloverPrompt`, `BreakdownDisplacement`, `MachineStatus`, `AdvanceNoticeRecord`, `OperatorEnrollmentCode`, `OperatorAuditEvent`, `RouteOverride` | none | n/a |

**`Plot` is live and affected right now**, not hypothetically — I read
the real Kamatchipuram cluster (read-only `get_plots_for_cluster`, no
write) to check, since `area_acres=3.0`/`2.0`/`1.0` are real values in
`scripts/seed_cluster.py`:

```
p03 3 int    <- area_acres=3.0 in the seed script
p05 2 int    <- area_acres=2.0
p08 1 int    <- area_acres=1.0
p01 2.5 float, p02 0.75 float, p04 1.25 float, p06 0.5 float, p07 1.5 float
```

Three of eight real farmers' plots are reconstructed with the wrong
Python type on every read, today, on the deployed system. Practical
impact found so far: none visible to a farmer — `messages_en/ta.py`'s
`format_area()` uses `f"{value:g}"`, which renders identically for `3`
and `3.0`. `area_acres` also only ever reaches deterministic arithmetic
(`scheduling/capacity.py`, `solver.py`), which doesn't care about int vs
float for correctness. I have not audited every consumer, so I'm not
claiming zero risk anywhere — only that nothing checked so far breaks
visibly. This is a data-fidelity bug, not a currently-observed functional
one, which is exactly the kind of thing that stays invisible until
something downstream *does* care about the type, same as `telegram_chat_id`
did.

**One structurally convenient fact**: because the wrong type is
reconstructed at *read* time, not written into the stored bytes (a
DynamoDB `Number` for `3` and `3.0` is the identical `Decimal("3")` either
way — there is no way to store a "wrong" Decimal for a whole number, only
a wrong Python type on the way back out), fixing the read path alone
retroactively fixes every already-stored item for free. No migration, no
backfill, no touching the live table's data — the three Kamatchipuram
plots above will simply come back as `float` the next time they're read
through the fixed code, redeployed or not.

## Decision: per-type reconstruction, explicit float-field lists, guarded by a reflection-based completeness test

Three type-information-based approaches were considered, per your
instruction, not a fourth "smarter heuristic":

**1. Read the target dataclass's field annotations at deserialization
(reflection at the call site).** Resolve `typing.get_type_hints()` on the
target class, walk the decoded dict, cast any key whose resolved
annotation is `float`/`float | None`. Automatically covers any *future*
top-level float field with zero code change in `dynamo.py` — genuinely
attractive, since that's exactly how this bug keeps getting reintroduced
(a new field added to `models.py`/`interface.py`, `dynamo.py` never told).
**Cost**: `own_claim`/`opponent_claim` are declared as plain `dict | None`
in `interface.py` — there is no dataclass field type to reflect on for
`weighted_bump_days` three levels down, so the nested-claim case needs a
hardcoded exception regardless. Also needs `get_type_hints()` (not raw
`__annotations__`, which are unresolved strings under
`from __future__ import annotations`) called per read, cacheable but real
added complexity in a file that currently has zero reflection anywhere.

**2. Store a type marker alongside the value (tag at write time).** Have
`_to_decimal` wrap every `float` it sees (top-level or nested — it
already recurses) in something like `{"$f": Decimal(...)}`; `_from_decimal`
trusts the tag over the value. This is the only option that fixes the
nested-claim case with the *same* mechanism as everything else, since the
tag is applied per-value at encode time, when Python still knows the real
type, before DynamoDB's Number type erases it.
**Cost**: this is a genuine schema change, and not a free one — every
float value written from now on changes shape on disk (a human reading a
raw item in the console sees a nested map instead of a plain number), and
every *already-stored* float (all of `Plot`/`Cluster`'s live data) has no
tag, so the reader needs the old value-based heuristic as a permanent
fallback path forever, not a transitional one — there is no migration
step that retires it, because nothing rewrites old items. Two decode
paths living side by side indefinitely is a worse ongoing cost than the
bug it fixes.

**3. Per-type deserializers that know their own shapes.** Replace the
inline `EntityClass(**_decode(_strip_keys(item)))` pattern with a private
`_item_to_<type>(item)` function per entity that does the decode *and*
casts its own known float fields (top-level and, for `DecisionRecord`'s
claims, nested) by an explicit list. No reflection, no wire-format
change, nothing to migrate, and it matches this file's existing style
exactly — `dynamo.py` already hand-writes one method per entity with its
own PK/SK construction (`Plot`'s `transplant_date` parsing already does
exactly this kind of per-type special-casing inline, just not factored
out yet).
**Cost**: the float-field list is manual. Forget to add a new float field
to the right type's list and the bug is back for that one field, silently
— reflection (option 1) would have caught this for free, at the cost of
not fully solving the nested case anyway.

**Decision: option 3, with option 1's automatic-detection benefit added
back as a test, not as runtime logic.** A new hermetic test
(`test_dynamo_storage.py`, no AWS needed — matches that file's existing
convention) uses `typing.get_type_hints()` to walk every dataclass in
`interface.py` plus `Cluster`/`Farmer`/`Plot` from `models.py`, collects
every field resolving to `float`/`float | None`, and asserts each one
appears in `dynamo.py`'s corresponding explicit float-field list —
plus a matching check of `AdvocateClaim.model_fields` (Pydantic's own
reflection) against the claim-nested list. This is exactly the gap
option 1 exists to close, applied where it costs the least: once, in CI,
not on every read. Production code stays fully explicit and
reflection-free, matching the rest of the file; the maintenance risk
option 3 alone would carry is converted into a test failure instead of a
silent runtime bug.

**Fix, concretely**: `_item_to_plot`, `_item_to_cluster`,
`_item_to_decision_record` (the latter also handling `own_claim`/
`opponent_claim` via a small `_cast_claim_floats` helper) replace the
inline construction in `get_plot`, `get_plots_for_cluster`, `get_cluster`,
`get_decision_record`, `get_decision_records_for_plot`, and
`get_decision_records_for_cluster`. `_from_decimal` itself is unchanged —
its int-for-integral-Decimal behavior is still exactly correct for every
`int`-typed field (`telegram_chat_id`, `route_position`, chat-ID fields on
`OperatorEnrollmentCode`/`OperatorAuditEvent`, etc.); this fix adds a
targeted float-cast *after* decode for the specific fields that need it,
it doesn't change the general-purpose decoder's behavior for anything
else. No other entity type needs any change — confirmed by the field
audit above, not assumed.

## Failure paths

- **A future float field gets added and forgotten in the cast list**: the
  new reflection-based test fails in CI, naming the exact field, before
  it ever reaches a live table. This is the whole reason the test exists.
- **Old data already stored as a bare `Decimal`**: not a failure path —
  see "one structurally convenient fact" above. The fix is retroactive by
  construction; there is nothing to migrate.
- **`own_claim`/`opponent_claim` missing a key the cast list expects** (a
  claim dict from before some future `AdvocateClaim` field existed):
  `_cast_claim_floats` only casts keys that are present, same
  none-is-a-no-op discipline as every other optional-field handling in
  this codebase — it doesn't KeyError on a narrower historical shape.

## Test plan

1. Full suite (`uv run pytest`, FileStorage-backed — the default, and
   what nearly the whole suite already runs against) to confirm nothing
   elsewhere regresses.
2. New hermetic tests in `test_dynamo_storage.py` (no AWS, exercising the
   new `_item_to_*` helpers and `_from_decimal` directly):
   - `test_telegram_chat_id_style_int_field_stays_int` — explicit
     `type(x) is int`, proving ADR-006 Decision 9's fix doesn't regress.
   - `test_decision_record_float_fields_stay_float` — every top-level
     float field plus both nested claim dicts, each asserted with
     `type(x) is float`, not `==` (`int(0) == float(0.0)` is `True`,
     which is exactly why the original bug passed unnoticed).
   - `test_plot_and_cluster_float_fields_stay_float` — same pattern for
     `area_acres`/`lat`/`lon`/`machine_capacity_acres_per_day`/etc.,
     specifically with whole-number inputs (`3.0`, not `3.5`), since
     that's the only input shape that ever exercised the bug.
   - The reflection-based completeness test described above.
3. Re-run the exact live verification script from the prior turn against
   the real table (throwaway `zzverify-` items, same cleanup discipline,
   table restored to its starting count) — this time expecting `PASS` on
   the two `DecisionRecord` cases that failed before.
4. A read-only, zero-write re-check of the real Kamatchipuram plots
   (`get_plots_for_cluster("kamatchipuram")`) confirming `p03`/`p05`/`p08`
   now come back as `float`, proving the fix actually corrects live data,
   not just a synthetic test case. **No redeploy** — this reads through
   the fixed local code against the real table, the same non-destructive
   check used to find the bug in the first place.

## README

Add a new, explicitly-named pattern to "Verification finds what tests
don't", distinct from the existing eight numbered defects: this finding
and the `why_lost_text` doubled-noun finding are two *separate* instances
of the same underlying lesson — an equality check that is technically
true while validating the wrong thing (`int(0) == float(0.0)`, and
separately, `DEFAULT_WINNER_LABEL in text` being true of a broken string
that merely contains it). Name it as its own pattern, cross-referencing
both instances, not folded into either's original numbered entry.

## Verification, in order, each a real check

1. `uv run pytest tests/test_dynamo_storage.py -m "not slow"` — 5 new
   tests, all `type(x) is float`/`is int`, not `==`: the chat_id
   regression guard (`Farmer`, `OperatorEnrollmentCode`,
   `OperatorAuditEvent`), `DecisionRecord`'s float fields including
   `rain_urgency_boost=0.0`, `Plot`/`Cluster`'s whole-number float
   fields including the `maturity_gdd_override: None` case, and the
   reflection-based completeness test (`typing.get_type_hints` across
   every dataclass in `interface.py` plus `Cluster`/`Farmer`/`Plot`,
   `AdvocateClaim.model_fields` for the nested claim case). All 17
   tests in the file pass; the 5 new ones pass on first run.
2. Full suite: `659 passed` (up from 654 by exactly the 5 new tests) —
   FileStorage-backed, confirming nothing else regressed.
3. Live, against the real table, same throwaway-`zzverify015-`-namespace
   and cleanup discipline as the audit that found this bug: both
   `DecisionRecord` cases that failed pre-fix now `PASS`, plus a
   `Cluster`/`Plot` case with whole-number floats. Baseline item count
   35, final item count 35, all 4 written items confirmed deleted by a
   follow-up `get_item`, not just a successful delete call.
4. **Read-only** (zero writes) re-check of the real, already-affected
   Kamatchipuram cluster: `p03`/`p05`/`p08` — whose seeded `area_acres`
   are `3.0`/`2.0`/`1.0` — now come back as `float` through
   `get_plots_for_cluster`, confirmed directly, not assumed from the
   synthetic test cases alone.

No redeploy. This is a local code fix verified against a real table's
read path only — the deployed AgentCore artifact is still running the
2026-08-18 code and has never seen this fix.

## Consequences

- `dynamo.py` gains three small `_item_to_*` helpers and one nested-claim
  helper; `_from_decimal`/`_to_decimal` are unchanged.
- Every `Plot`/`Cluster`/`DecisionRecord` read through `DynamoStorage`
  after this lands gets the correct Python type, including the three
  real Kamatchipuram plots already affected — with no migration, because
  nothing was ever wrong in storage, only in reconstruction.
- A new CI-only reflection check exists specifically to keep this class
  of bug from recurring silently for the next float field someone adds.
- Still no redeploy. This ADR is a code fix verified locally and against
  the real table's read path only.
