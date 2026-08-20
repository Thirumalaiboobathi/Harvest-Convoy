# ADR-011: Seasonal Rollover, Machine Breakdown, Rain Event Classification, Advance Harvest Notice

- Status: Proposed (awaiting go-ahead — **do not implement yet**)
- Date: 2026-08-20

## Context

Four additions, implemented in the order given because Part 1 touches the
data model Parts 2–4 build on. **No `CLAUDE.md` exists in this repo** —
the same finding ADR-008 and ADR-010 both recorded. Read ADR-000 through
ADR-010 in full before writing this, plus the current source for
`watcher.py`, `agents/coordinator.py`, `telegram/webhook.py`,
`telegram/registration.py`, `telegram/notify.py`,
`telegram/messages_ta.py`/`messages_en.py`, `storage/interface.py`,
`storage/fairness.py`, `scheduling/capacity.py`, and
`agronomy/calibration.py` — the modules every part below either extends
or reuses.

**No contradiction with ADR-000–ADR-010 was found.** Three places where
this ADR could plausibly have contradicted a prior decision, checked
explicitly:

- **Part 1 does not reverse ADR-009's "no Season entity, boundaries
  handled by construction" finding — it reaffirms it, narrower.** ADR-009
  said a new `season_id` string has no prior records, full stop; nothing
  in this ADR adds a `Season` entity with start/end dates. What's added
  is a much narrower record (`SeasonRolloverPrompt`) that tracks a
  farmer's reply to one specific prompt, not a calendar concept.
- **Part 3 does not touch ADR-002 Decision 3's capacity rule** ("the
  window does not reopen after the first breach"). Classification is a
  parallel signal read off the same forecast, used for urgency and
  wording only — `usable_harvest_days()`'s math is unchanged.
- **Part 3's urgency boost does not weaken ADR-005 Decision 4's fairness
  invariant** (`MAX_FAIRNESS_BONUS < _URGENCY_GRANULARITY`, so fairness
  can tilt a close call but never outrank genuine urgency). The boost is
  applied uniformly to every ready plot's base urgency *before* the
  fairness bonus is added, capped at 1.0 same as today; `MAX_FAIRNESS_BONUS`
  itself is untouched. Worked through in Part 3 below, not just asserted.

Nothing here calls Bedrock for anything new, nothing writes a fabricated
number, and every new failure path degrades and logs rather than raising
— the standing constraints from the brief are treated as literal
acceptance criteria for each part below, not restated once and forgotten.

---

## Part 1: Seasonal rollover

### Decision 1: no `Season` entity with dates — reasoned through, not assumed

The brief asks explicitly: does rollover need a `Season` entity (ID
convention, start/end, which cluster, how "current" is determined)? Going
through each question against what the four required behaviors (rollover
prompt, carry-forward table, the four real cases) actually need:

- **Season ID convention**: stays a plain string, `{year}-{kuruvai|samba}`
  — already the de facto convention every fixture and test uses. Not
  enforced or validated anywhere; formalized here as documentation, not
  a schema.
- **Start and end dates**: **not tracked.** Nothing in any of the four
  required behaviors needs a stored calendar boundary. Kuruvai and Samba
  windows genuinely shift by district and by monsoon year — there is no
  single verified TN-wide calendar to encode, and inventing one would be
  exactly the kind of unsourced universal assumption this project's
  honesty culture exists to refuse (the same reasoning that kept
  `crop_params.py` from asserting a single national maturity threshold
  without calibration).
- **Which cluster it belongs to**: a season_id is always paired with a
  `cluster_id` at the call site, exactly as it already is everywhere in
  this codebase (`run_daily_watch(cluster_id, season_id, ...)`,
  `equity_report.py --cluster --season`, ...). Two clusters can be in
  different seasons simultaneously today with zero code changes — nothing
  about a bare string prevents that.
- **How the current season is determined**: **not automatically.**
  `season_id` has been an externally-supplied, human-configured string
  since Phase 0 — the deployed EventBridge/Lambda payload hardcodes it
  (ADR-006), `scripts/*.py` take it as a CLI arg, `app.py`'s handler
  reads it from the invocation payload. Rollover keeps that same
  discipline: **it is a deliberate, explicitly-triggered operator action,
  not a calendar-detected event.** An operator who knows the local season
  has turned runs it; nothing watches a clock for them. This is
  consistent with, not a departure from, how `cluster_id` and `season_id`
  already work everywhere else in this system.

**Conclusion: no `Season` entity.** What's actually new is a much
narrower thing — a record of one prompt sent to one farmer and whether
they answered it, which is the same shape `HarvestConfirmation` already
is for a different question.

### Decision 2: `SeasonRolloverPrompt` — one record per (plot, new season)

```python
@dataclass(frozen=True)
class SeasonRolloverPrompt:
    """Whether a farmer confirmed participation in a new season after a
    rollover prompt. replied=None (never answered) and replied=False
    (explicit "no") both exclude the plot from the new season's
    scheduling pool -- only replied=True includes it. Mirrors
    HarvestConfirmation's confirmed: bool | None shape and the same
    silence-is-not-consent discipline (ADR-009 Part 2), applied to the
    opposite question (should this plot be scheduled at all, not did the
    machine come).
    """
    plot_id: str
    farmer_id: str
    cluster_id: str
    old_season_id: str
    new_season_id: str
    asked_at: str
    replied: bool | None = None
    replied_at: str | None = None
```

`Storage` gains `put_season_rollover_prompt`, `get_season_rollover_prompt(plot_id,
season_id)`, `get_season_rollover_prompts_for_cluster(cluster_id, season_id)`
— same Protocol-then-both-backends shape as every other entity,
`PK=PLOT#{plot_id}`/`SK=ROLLOVER#{new_season_id}` in DynamoDB, colocated
under the plot like `HARVEST#`/`CONFIRM#`/`DECISION#` already are.

**The exclusion mechanism — the real gap this closes.** Today,
`watcher.py` reads `storage.get_plots_for_cluster(cluster_id)`
unconditionally; nothing lets a plot opt out of a season's scheduling
pool short of being `TOO_GREEN` or already harvested. `_run_daily_watch_one`
gains one more filter, applied at the same point `harvested_plot_ids` is:

```python
rollover_prompts = storage.get_season_rollover_prompts_for_cluster(cluster_id, season_id)
excluded_plot_ids = {p.plot_id for p in rollover_prompts if p.replied is not True}
```

A plot with **no** `SeasonRolloverPrompt` record for the current
`season_id` at all is **included by default** — this covers a brand-new
registration this season (the farmer just gave a real transplant date
directly; there's no ambiguity to resolve) and every plot before the
first rollover this mechanism ever runs for a cluster. Only a plot that
was *asked* and didn't say yes is excluded.

### Decision 3: the rollover flow — one new entrypoint, one new tiny FSM

**Trigger**: `watcher.run_season_rollover(cluster_id, old_season_id,
new_season_id, *, storage=None, today=None, telegram_client=None) -> dict`
— code-only, operator-invoked, same shape and same "needs a human to run
it, not wired to a schedule" status as `run_evening_confirmations`.
Idempotent by construction: for every plot in `get_plots_for_cluster(cluster_id)`,
skip if a `SeasonRolloverPrompt` already exists for `(plot_id, new_season_id)`
— re-running the same call after a partial failure or an
interrupted previous attempt just resumes, never re-prompts a farmer who
already has a record. This is the direct answer to "a rollover triggered
when the previous season never completed": re-running is always safe,
because every write is idempotent per exact key, the same discipline as
every other entity in this codebase.

For a farmer with no `telegram_chat_id`: still write the
`SeasonRolloverPrompt` (`asked_at` set, matching `HarvestConfirmation`'s
same "the fact stays on record even if nobody could be asked" rule from
ADR-009 Part 2), skip the send, log it. That plot is excluded from the
new season by the same rule as unanswered — correctly: nobody could ask,
so nobody said yes.

**Message 1 (agent speaks first)**: two-button inline keyboard,
`rollover:{plot_id}:{new_season_id}:{yes|no}`.

> Transplanting again this season? Tap yes and tell me the date.
> [Yes] [No]

- **Tap No**: `SeasonRolloverPrompt.replied=False`, `replied_at` set,
  one short acknowledgment sent, done. One tap, terminal.
- **Tap Yes**: a second, short message asking *only* for the date — not
  village, location, crop, or area, per the explicit instruction. This
  needs one small new piece of per-chat state (which plot/season this
  farmer is mid-reply for), so a new, narrow module,
  `telegram/rollover.py`, mirrors `registration.py`'s exact shape at a
  fraction of the size: a `RolloverState` dataclass (`chat_id, plot_id,
  new_season_id`), a pure `advance_rollover_reply(state, incoming) ->
  (state, OutboundMessage)` function reusing `registration._parse_date`
  (not `_parse_area` — area is never asked here), and the same disclosed
  in-memory-placeholder store `registration.py`'s `_STATE_STORE` already
  is (same limitation, stated once, not re-litigated: doesn't survive a
  process restart).
- **The date reply**: on a valid parse, `storage.put_plot(replace(existing_plot,
  transplant_date=new_date))` — an upsert on the *same* `plot_id`, not a
  new plot, matching ADR-009's "update, not duplicate" re-registration
  rule exactly, just narrower in scope (only `transplant_date` changes).
  `SeasonRolloverPrompt.replied=True`, `replied_at` set. One short
  acknowledgment sent. On an unparseable reply: re-prompt with the same
  date-only question, same re-prompt discipline every other free-text
  step in this codebase already uses — never silently guesses, never
  drops into a longer back-and-forth beyond "try that date again."

**Routing**: `webhook.handle_update` gains a `rollover:` callback-data
prefix check (alongside `lang:`/`confirm:`), and, for incoming free-text
messages, a check of whether `chat_id` has a pending `RolloverState`
*before* falling through to `registration.handle_incoming` — a farmer
mid-rollover-reply must not have their date accidentally parsed as a
brand-new registration attempt.

### Decision 4: the carry-forward table

| Data | Survives rollover? | Why |
|---|---|---|
| Fairness ledger (`LedgerEntry`, `weighted_bump_days`) | **Yes, unconditionally.** | Keyed `(farmer_id, season_id)`; a new `season_id` is just a new row. `weighted_bump_days` already reads across *every* recorded season with geometric decay (ADR-005 Decision 4) — nothing about rollover changes that read path. This is the entire point of Part 1: make a second real row possible in production, not fixture-seeded. |
| Harvest state (`mark_plot_harvested`/`get_harvested_plot_ids`) | **No — resets, by construction.** | Keyed `(cluster_id, season_id, plot_id)`; a new `season_id` has an empty set with no migration needed (ADR-009 Part 1.5 Decision D). **Verified, not just asserted** — Part 1's test plan (below) drives a real rollover and confirms a plot harvested in the old season is schedulable again under the new one. |
| Accumulated GDD | **No — resets.** | Never stored as a standalone value in the first place (recomputed live from `Plot.transplant_date` every trigger, per ADR-010's own Prerequisite finding); resets automatically the moment the rollover reply updates `transplant_date`. |
| `HarvestConfirmation` | **No — resets, by construction.** | Same `(plot_id, season_id)` keying as the harvest marker; not explicitly asked about in the brief, included here for completeness since it follows the identical rule. |
| `DecisionRecord` (ADR-010 Part 0.5) | **Yes — retained indefinitely, never cleared.** | Same reasoning as ADR-010 Decision E: an audit trail that expires on a season boundary isn't an audit trail. Naturally partitions by `season_id` already; there's nothing to clear, only a new season's records accumulating alongside, not replacing, the old ones. |
| `SeasonRolloverPrompt` | **Yes — retained.** | Same audit-trail reasoning; also the record `equity_report.py` would read to answer "how many farmers actually rolled over" if that's wanted later (not built in this ADR). |

### Decision 5: the four required cases

1. **Never replies**: `replied` stays `None` forever for that plot —
   excluded from scheduling (Decision 2).
2. **Replies "no"**: excluded, ledger untouched — no bump, no credit,
   nothing recorded against fairness. The farmer chose not to plant this
   season; that's not a fairness event.
3. **New farmer joining mid-season**: registers through the existing
   four-message flow exactly as today — no `SeasonRolloverPrompt` record
   exists for them, so Decision 2's default-include rule covers this
   without any special case.
4. **Rollover triggered when the previous season never completed**:
   covered in Decision 3 — every write is idempotent per exact key, so a
   re-run resumes rather than re-prompting or duplicating.

**"Declined" and "did not respond" are different facts about a person
and must not collapse into one state** — both exclude the plot from
scheduling (Decision 2's filter treats them identically, correctly,
since both mean "do not schedule this"), but they stay distinguishable
everywhere the record is read. A new `rollover_status()` helper, same
shape as `watcher.confirmation_status()`, returns one of four distinct
values — `"confirmed"` (`replied=True`), `"declined"` (`replied=False`),
`"unknown"` (`replied=None`, never answered), and (implicitly, no record
at all) not-applicable/default-included — never folding declined and
unknown into a shared "excluded" bucket anywhere in code, storage, or
output.

**Surfaced in `equity_report.py`, not just in raw storage.** A new
"Season participation" section reads
`get_season_rollover_prompts_for_cluster(cluster_id, season_id)` and
reports three separate counts — confirmed, declined, unknown — plus
which plots fall in each. An equity report that only said "N plots
excluded" would let a reader assume every exclusion was the same kind of
fact; a farmer who said no and a farmer nobody could reach are different
findings for different audiences (an official reading this report needs
to know the difference between "chose not to" and "the system lost
contact with").

### Tests

The proof this ADR asks for explicitly: a real rollover, not two
fixture-seeded seasons. `tests/test_season_rollover.py`, one end-to-end
scenario — season 1 runs (`run_daily_watch`) and produces a real
escalation that resolves against one farmer (a real `LedgerEntry`
written); `run_season_rollover` is called; that farmer taps Yes and
replies with a new date (`advance_rollover_reply`, twice); season 2's
`run_daily_watch` runs against a close-call scenario between that farmer
and a fresh opponent, and `weighted_bump_days` measurably tilts the
outcome — the exact assertion shape `test_negotiate_pair_fairness_tilts_a_close_call`
already uses, but reached by walking the real rollover path instead of
hand-seeding two `LedgerEntry`s. Plus: harvest-state-resets-across-rollover
(a plot `FITS` in season 1, confirm it's schedulable again after
rollover with a fresh transplant date); never-replies excludes from
scheduling; explicit-no excludes and doesn't touch the ledger; idempotent
re-run of `run_season_rollover` doesn't double-prompt or double-write; a
farmer mid-rollover-reply whose free text is routed to
`advance_rollover_reply`, not `registration.handle_incoming`.

**The asymmetry test, named explicitly per your instruction**:
`test_declined_and_unresponsive_are_distinguishable_not_collapsed` — two
plots in the same cluster/season, one `replied=False` (declined), one
with no reply at all (`replied=None`, unknown). Asserts both are excluded
from `solve()`'s schedulable pool identically (same behavior), *and*
`rollover_status()` returns `"declined"` for one and `"unknown"` for the
other (different classification), *and* `equity_report.py`'s new Season
participation section lists them in separate counts/lists, not a shared
"excluded" bucket — three assertions, because a test that only checked
the first would pass even if the second and third silently collapsed.

### New Tamil strings (drafts, for your review before this part ships)

English: *"Transplanting again this season? Tap yes and tell me the
date."* / No-tap ack: *"Understood — hope to see you next season."* /
Yes-tap date prompt: *"What date did you transplant?"* / Date-accepted
ack: *"Got it — you're on the list for this season."*

Tamil (first-pass, not final — same `print_tamil_strings.py` dump-and-review
discipline as every prior string in this project):

- *"இந்த பருவத்திலும் நடவு செய்தீர்களா? 'ஆம்' எனத் தட்டி தேதியைச்
  சொல்லுங்கள்."*
- No-tap ack: *"புரிந்தது — அடுத்த பருவத்தில் உங்களைச் சந்திக்க
  ஆவலாக உள்ளோம்."*
- Yes-tap date prompt: *"எந்த தேதி நடவு செய்தீர்கள்?"*
- Date-accepted ack: *"பதிவு செய்யப்பட்டது — இந்த பருவத்திற்கான
  பட்டியலில் நீங்கள் உள்ளீர்கள்."*

---

## Part 2: Machine breakdown

### Decision 6: the tap — attached to the message the operator already has

`notify.send_operator_route_summary`'s message gains one inline-keyboard
button, `breakdown:{cluster_id}:{season_id}:{today}` — no new command, no
new inbound surface, just one more button on a message the operator
already receives every triggered day. The first tap alone is sufficient;
a follow-up keyboard (`back tomorrow` / `down indefinitely`) is offered
after, purely additive.

**Authorization check**: the tapping user's own Telegram identity
(`callback_query["from"]["id"]`, who actually tapped — not
`message.chat.id`, which only says which chat the message lives in)
must equal `cluster.operator_chat_id`. A mismatch answers with a generic
refusal toast and does nothing else — logged, not raised.

### Decision 7: immediate recompute — reuses two things already built, invents nothing new

On a valid first tap:

1. **Find today's dispatched plots.** `HarvestConfirmation.scheduled_date
   == today` for this cluster/season (`get_confirmations_for_cluster`,
   filtered) — created at dispatch time unconditionally (ADR-009 Part 2
   Decision 4), so this is already exactly the right list; no new lookup
   needed.
2. **Un-harvest them.** `storage.clear_plot_harvest(plot_id, cluster_id,
   season_id)` for each — the exact reversal hook ADR-009 Part 1.5
   designed and left unused for this.
3. **Recompute against the remaining days.** Re-fetch the forecast, then
   call the same `solve()`/`run_cluster_with_claims()` pair
   `watcher.py`'s normal trigger uses — but with `forecast[1:]` (today
   dropped) instead of the full fetch. `usable_harvest_days()` then
   naturally reads "how many usable days remain starting tomorrow,"
   which *is* "recompute against the remaining harvest days before the
   rain window closes" — no new capacity parameter, no change to
   `scheduling/capacity.py` at all.
4. **Re-notify.** Every plot that comes out of this second `solve()` call
   gets its normal message (`harvest_scheduled` with a new route
   position, or `not_ready` if it no longer fits) — the existing
   `_send_notifications` path, called a second time for this recompute.
   A plot that genuinely has no slot before the rain closes gets the
   honest `not_ready` wording, not a fabricated promise.
5. **Notify the operator** with the revised route via the same
   `send_operator_route_summary` call.

**`TriggerContext`/`DecisionRecord` gain one field**, `trigger_reason:
str = "scheduled"` (`"breakdown_recompute"` for this path) — so
`explain_decision.py` can tell a reader *why* a plot's numbers on a given
day look the way they do (zero capacity because of a breakdown, not
because of rain). A small, additive extension of ADR-010 Part 0.5, not a
new mechanism.

### Decision 8: the fairness distinction — the subtlest call in this part, stated plainly

**A farmer displaced by a breakdown was not bumped by another farmer, and
must never be recorded as if they were.** `record_bump`/`LedgerEntry`
exist to detect one farmer losing to another farmer's genuinely stronger
claim — crediting a mechanical failure into that same ledger would make
it measure equipment reliability instead of the thing it's actually for,
silently, in a way nobody reviewing `equity_report.py` later could
distinguish from a real bump.

New entity, deliberately separate from `LedgerEntry`:

```python
@dataclass(frozen=True)
class BreakdownDisplacement:
    plot_id: str
    farmer_id: str
    cluster_id: str
    season_id: str
    original_scheduled_date: str
    reported_at: str
    reason: str = "machine_breakdown"
```

`Storage` gains `put_breakdown_displacement`,
`get_breakdown_displacements_for_cluster(cluster_id, season_id)`. Every
plot un-harvested in Decision 7 gets one of these, written *instead of*
any ledger entry — nothing in the breakdown path ever calls `record_bump`.

**`equity_report.py` gains a new section**, "Breakdown displacements"
(count, plots, farmers, total acres), read from
`get_breakdown_displacements_for_cluster` and shown clearly separated
from repeat bumps and fairness-mechanism activity — visible, per the
brief's explicit ask, without being counted as bumps anywhere in the
existing sections' arithmetic.

### Decision 9: suppressing the evening confirmation for a plot that's already known to have not come

If the evening confirmation prompt asks a farmer whose plot was actually
displaced by a breakdown "did the machine come?", a truthful "no" tap
would run straight into `webhook.handle_confirmation_callback`'s existing
reversal-and-ledger-credit logic — `record_bump(..., outcome="harvest_no_show")`
— the exact fairness pollution Decision 8 exists to prevent, reached
through a second door.

`HarvestConfirmation` gains one field, `cancelled: bool = False`. Decision
7's recompute sets it `True` on any confirmation record it invalidates.
Two call sites change:

- `run_evening_confirmations`'s `to_ask` filter becomes `asked_at is None
  and not c.cancelled` — a cancelled plot is never asked in the first
  place, covering the common case (breakdown reported before the evening
  pass runs).
- `webhook.handle_confirmation_callback` checks `cancelled` before
  processing *any* reply, yes or no — covering the race case named
  explicitly in the brief ("a breakdown reported after the day's
  confirmations have already gone out"): the prompt is already out,
  possibly already answered, and a truthful "no" tap must still not
  reach `record_bump`. On a cancelled confirmation, the tap is
  acknowledged (`confirmed`/`confirmed_at` still recorded — the truth
  stays on record) but the reversal/ledger-credit branch is skipped
  entirely, with a distinct log line so this is visible, not silently
  absorbed.

`confirmation_status()` gains a fifth return value, `"cancelled"` —
distinct from `"unknown"` on purpose: unknown means *we don't know what
happened*; cancelled means *we know, and it wasn't the farmer's or
another farmer's doing*. Folding the two together would blur exactly the
distinction Decision 8 is built to preserve.

### Decision 10: the remaining failure paths

- **No route that day**: `get_confirmations_for_cluster` filtered to
  today returns empty — nothing to clear, one no-op acknowledgment, no
  displacement records written, logged.
- **Tapped twice**: `get_breakdown_displacements_for_cluster` checked
  first for any record with `original_scheduled_date == today` before
  processing anything — if found, the second tap answers "already
  reported today" and does nothing further. Same idempotency posture as
  escalation resolution's `_RESOLVED_ESCALATIONS` check, applied here.
- **Not the registered operator**: refused per Decision 6, logged with
  the tapping user's ID for the record.

### "Down indefinitely" — the symmetric follow-up, scoped narrowly

The optional second tap's "down indefinitely" branch sets a new,
minimal `MachineStatus` record (`cluster_id`, `status: "down"`,
`reported_at`). `_run_daily_watch_one` checks this before computing
capacity on every subsequent day: if down, capacity is treated as zero
(no plots dispatched, everything ready stays `CONTESTED` rather than
silently `FITS` against a machine that isn't there) — and, critically,
**a symmetric one-tap "machine is back" action is built alongside it**,
because a status that can only be set and never cleared would leave a
cluster permanently stuck, which is a worse failure mode than not
building the "indefinitely" branch at all. "Back tomorrow" (the other
second-tap option, and the implicit default if no second tap is given)
sets nothing — tomorrow's trigger runs at full capacity automatically,
which is already what happens today with no code change.

### Tests

`tests/test_machine_breakdown.py`: a full recompute end to end (dispatched
plots un-harvested, re-notified with a revised or honest `not_ready`
outcome, operator gets the revised route); breakdown displacement written,
never a `LedgerEntry`; `equity_report.py`'s new section renders the
displacement separately from bumps; cancelled confirmation is skipped by
`run_evening_confirmations`; a late "no" tap on a cancelled confirmation
is acknowledged but does not call `record_bump` (the race case, tested
directly, not just reasoned about); no-route/double-tap/wrong-tapper
degrade correctly; "down indefinitely" suppresses capacity on the next
trigger day until "machine is back" clears it.

**The invariant test, per your instruction — not "no `LedgerEntry` was
written," but "the fairness weight is provably unmoved next season."**
`test_breakdown_displacement_produces_zero_change_in_next_seasons_weighted_bump_days`:
a farmer's plot is displaced by a breakdown in season 1 (no ledger entry,
per Decision 8), a second, unrelated farmer in the same cluster genuinely
loses a real escalation in season 1 (a real `LedgerEntry`, real bump);
`run_season_rollover` runs season 1 → season 2 for both farmers; season
2's `weighted_bump_days` is computed for both. Assertion: the
breakdown-displaced farmer's `weighted_bump_days` is exactly `0.0` in
season 2 — not merely "no entry was found," but the actual number the
coordinator's scoring function reads, walked through the same
`storage/fairness.py:weighted_bump_days()` call site `agents/coordinator.py`
itself uses — while the genuinely-bumped farmer's is measurably nonzero
and decayed per ADR-005 Decision 4's formula, in the same test, so the
two cases are contrasted directly rather than checked in isolation. This
is the regression pin: a future change that accidentally started
crediting breakdown displacements (e.g., someone "simplifying" Decision
8's separate code path back into `record_bump`) would fail this test
immediately, not silently drift.

### New Tamil strings

No new farmer-facing message shapes beyond the existing `harvest_scheduled`/
`not_ready` (reused as-is, since Decision 7's recompute produces the same
outcomes those already render). One new operator-facing toast/button set
— operator-only, per ADR-008 Decision 8's convention these are dispatched
by `Cluster.operator_language`, so still need both languages:

- Button label: *"🚜 இன்று இயந்திரம் பழுதடைந்தது"* / *"🚜 Machine down
  today"*.
- Acknowledgment toast: *"பதிவு செய்யப்பட்டது; பாதை மறு கணக்கீடு
  செய்யப்படுகிறது."* / *"Recorded; recalculating the route."*
- Already-reported toast: *"இன்று ஏற்கனவே பதிவு செய்யப்பட்டுள்ளது."*
  / *"Already reported today."*
- "Back tomorrow" / "Down indefinitely" button labels: *"நாளை
  திரும்பும்"* / *"Back tomorrow"*, *"காலவரையின்றி பழுதடைந்தது"* /
  *"Down indefinitely"*.
- "Machine is back" button/ack: *"🚜 இயந்திரம் மீண்டும் இயங்குகிறது"*
  / *"🚜 Machine is back"*.

---

## Part 3: Rain event classification

### Decision 11: what was searched, and what was and wasn't found

Per the brief's explicit instruction, a targeted search was run before
writing any threshold: paddy field trafficability/workability after
rainfall, rice lodging recovery time, combine-harvester field
re-entry timing. **No citable, directly-fetched, primary-source figure
quantifying "how many days (or how much rain) separates a brief shower
from a sustained event" for paddy specifically was found**, the same
outcome ADR-002 Decision 2 reached for the decay curve. What *was* found,
stated as directional corroboration only, not a citation for a numeric
constant (same discipline ADR-002 applied to the Wang et al. figures):

- Agricultural-engineering field-workability literature treats
  post-rainfall trafficability as governed by soil moisture return to
  field capacity, commonly referenced around 24 hours after a soaking
  rain in compaction-study methodology — one data point suggesting "about
  a day" is a real, recurring order of magnitude for short-recovery
  conditions, not one this project invented.
- Rice-lodging literature is unanimous that sustained wet/windy periods
  measurably worsen lodging and extend real-world harvest duration (one
  cited case: a normal ~2-week harvest stretched to 33 days under
  sustained wet weather) but reports outcomes in days-of-harvest-lost,
  not a rainfall-duration/accumulation threshold that predicts them in
  advance — the same "genuine relationship exists, no quantified
  predictive threshold was found" gap ADR-001/002 already hit for the
  maturity threshold and the decay curve.

**Every constant below is `# DERIVED:`, a tuning parameter, not a
sourced figure — and, per your instruction, named so a future
maintainer can't mistake it for a measured agronomy constant sitting
nearby. `crop_params.py`'s `_ESTIMATED` suffix already means something
specific in this codebase — "derived through a real, shown calculation
chain from at least one measured input" (e.g. `MATURITY_GDD_ESTIMATED`,
built from a sourced `T_BASE_C` and a measured GDD/day rate). These
three constants have no such chain — they're closer in kind to
`agents/coordinator.py`'s `CLEAR_MARGIN` (an arbitrary, documented
negotiation-tuning choice) than to anything in `crop_params.py`, so they
get their own, differently-named suffix rather than borrowing
`_ESTIMATED`'s implied provenance:**

```python
# scheduling/rain_event.py
# TUNING PARAMETERS -- not measured agronomy thresholds. Do not read the
# _TUNING suffix as _ESTIMATED's cousin: crop_params.py's _ESTIMATED
# constants are derived through a shown calculation chain from at least
# one sourced input; these have no such chain. No citable paddy-specific
# source distinguishing a brief shower from a sustained/monsoon-onset
# event was found despite a targeted search (ADR-011 Decision 11); the
# ~24h post-rain field-workability reference point from general
# agricultural engineering literature is directional corroboration for
# the 1-day BRIEF boundary, not a citation for a paddy-specific number.
# Change freely; nothing else in the system depends on the exact values,
# only on BRIEF vs SUSTAINED being a real distinction.
RAIN_EVENT_SUSTAINED_MIN_DURATION_DAYS_TUNING = 3
RAIN_EVENT_SUSTAINED_ACCUMULATION_MM_TUNING = 50.0
RAIN_EVENT_SUSTAINED_INTENSITY_MM_TUNING = 40.0
```

### Decision 12: the classification, purely deterministic

```python
class RainEventClass(str, Enum):
    NONE = "none"          # no breach at all -- the existing no-trigger case
    BRIEF = "brief"
    SUSTAINED = "sustained"

def classify_rain_event(
    forecast: list[ForecastDay], rain_threshold_mm: float,
) -> RainEventClass:
    """Reads the same forecast usable_harvest_days() already reads --
    finds the run of consecutive 'wet' days (precipitation_mm >
    rain_threshold_mm) starting at the first breach, and classifies it
    by duration, total accumulation, and single-day intensity. Purely a
    function of already-fetched data; makes no network call of its own.
    """
```

Duration, total accumulation, and single-day intensity are each checked
independently — meeting *any one* of the three `RAIN_EVENT_SUSTAINED_*_TUNING`
thresholds classifies the whole event `SUSTAINED`; otherwise `BRIEF`. **The
ambiguous middle case (e.g., exactly 2 wet days, moderate accumulation)
resolves to `BRIEF`** only if none of the three thresholds are met —
there is no separate third bucket, per the brief's "at minimum" two
categories. **A wet run that reaches the end of the fetched forecast
without an observed recovery day is classified `SUSTAINED` regardless of
its measured-so-far duration** — the forecast-too-short failure case,
resolved toward caution rather than a confident `BRIEF` the data can't
actually support, logged distinctly so it's visible this was a
horizon-limited call, not a real 3+-day observation.

### Decision 13: urgency boost — the fairness-invariant interaction, proven, enforced in code, not just argued in prose

```python
RAIN_URGENCY_BOOST_SUSTAINED_TUNING = 0.15  # see the naming note in Decision 11
RAIN_URGENCY_BOOST_BRIEF_TUNING = 0.0       # a recoverable shower doesn't raise urgency
```

Applied inside `assess_plot()`, to every ready (non-`TOO_GREEN`) plot's
urgency, uniformly across the cluster for that trigger day:
`urgency = min(1.0, decay_fraction(days_past_maturity) + boost)`.

**The question worth actually working through, not just asserting**:
does capping this at 1.0 let the boost ever *invert* which of two plots
is more urgent, in a way that could let fairness decide a case ADR-005
Decision 4's invariant says it must not? Worked the algebra, not just
the intuition. Let plot A have raw urgency `x`, plot B have raw urgency
`y = x - g` for gap `g > 0` (A more urgent). Post-boost, capped:
`x' = min(1, x+b)`, `y' = min(1, y+b)`.

- If neither is capped: `x' - y' = x - y = g`, unchanged exactly.
- If `x` is capped (`x=1.0` already, i.e. `days_past_maturity >=
  DECAY_HORIZON_DAYS_ESTIMATED`) but `y+b <= 1`: `x'-y' = 1-(x-g+b) =
  g-(1-x)-b`. Worst case, `x=1.0` exactly: `x'-y' = g-b`. For a boost
  larger than the gap (`b > g`), this goes to zero or below — meaning A
  and B can become **tied**, never inverted (`x' >= y'` always holds,
  since `min(1,x+b) >= min(1,y+b)` whenever `x >= y`, for any fixed `b`
  — `min(1, ·+b)` is monotonic non-decreasing).

**The boost can compress a gap toward zero, including to an exact tie in
the worst case; it can never make the less-urgent plot end up strictly
ahead.** A tie is not a violation of "fairness must never let a less
urgent plot outrank a more urgent one" — it's the boundary case where
both plots genuinely become equally urgent under a sustained event
(e.g. 19 and 20+ days overripe are both "severely at risk" once a
week-long closure is added to the picture), and letting fairness settle
a genuine tie is exactly the "tilt a close call" behavior the mechanism
exists for, not a breach of it.

**Enforced in code, per your instruction, two ways — not left as ADR
prose alone:**

1. A comment placed directly beside the existing `MAX_FAIRNESS_BONUS`
   assert in `agents/coordinator.py` (not off in `scheduling/rain_event.py`
   where a reader of the invariant might never see it), stating the
   monotonicity property above in the three sentences it takes, and
   naming the test below as where it's checked, not just claimed.
2. A behavioral test, same genre as ADR-005's own "physics wins outright"
   gate (a documented reasoning point *and* a runtime check, "together
   they're the honest claim, neither alone is"):
   `test_rain_urgency_boost_can_tie_but_never_invert_urgency_ordering` —
   constructs the exact worst-case pair from the algebra above (one plot
   at the urgency cap, the other exactly `_URGENCY_GRANULARITY` below it,
   both under `RAIN_URGENCY_BOOST_SUSTAINED_TUNING`), and asserts the
   originally-more-urgent plot still wins or ties in every negotiation
   outcome — **never loses** — across both the concession-free
   score-comparison path and a further check with maximum fairness bonus
   stacked onto the *less* urgent side, proving the invariant holds even
   in genuine combination, not just the boost in isolation.

`TriggerContext`/`DecisionRecord` gain two fields,
`rain_event_classification: str` and `rain_urgency_boost: float` — the
same "make the number visible in the audit trail, don't just compute it
and discard it" discipline ADR-010 Part 0.5 already established for
everything else the trigger decides.

### Decision 14: reflected in messages, without a new re-notification mechanism

`not_ready` gains an optional classification-aware clause, appended
*only* for `SUSTAINED` — `BRIEF`/`NONE` render byte-identical to today's
wording, so the common case doesn't grow at all:

English: *"...We're tracking it every day; you'll hear from us when it's
time or something changes. Rain looks set in for several days, so it may
be a little longer than usual."*

Tamil (draft): *"...நாங்கள் தினமும் கண்காணிக்கிறோம்; நேரம் வரும்போது
அல்லது மாற்றம் இருந்தால் தெரியப்படுத்துவோம். பல நாட்களுக்கு மழை
நீடிக்கும் என்பதால், வழக்கத்தை விட சற்று தாமதமாகலாம்."*

**Does a shower upgrading to sustained between two runs trigger a new
alert? No new mechanism is built, and none is needed.** `not_ready`/
`harvest_scheduled` are already re-sent fresh on every trigger day a
plot is still in the schedulable pool — nothing in this codebase
suppresses a repeat send for having been sent before (a `TOO_GREEN` plot
already gets a fresh `not_ready` every triggered day it's checked). A
reclassification is automatically reflected the next time that plot's
message goes out, because the classification is recomputed fresh every
time, exactly like GDD and urgency already are. This is the existing
daily-refresh mechanism doing its job, not a new one — stated explicitly
because the brief asked for a decision, and "reuse what's already there"
is the decision. A plot already dispatched and excluded via
`harvested_plot_ids` gets nothing further, correctly — the machine
already went.

### Tests

`tests/test_rain_event.py`: each of the three `RAIN_EVENT_SUSTAINED_*_TUNING`
triggers (duration/accumulation/intensity) individually, and that none
alone firing yields `BRIEF`; the forecast-too-short case defaults to
`SUSTAINED` with the distinct log line; the urgency boost applied and
capped at 1.0; `not_ready` wording unchanged for `BRIEF`/`NONE`, the
sustained clause appended only for `SUSTAINED`; a plot's message wording
differing between two consecutive triggered days as the classification
changes, with no special-cased re-notify path involved.

Plus, in `tests/test_coordinator.py` (beside the existing fairness-invariant
tests, not a separate file — the same invariant, the same suite):
`test_rain_urgency_boost_can_tie_but_never_invert_urgency_ordering`, per
Decision 13's enforcement plan — the worst-case pair (one plot at the
urgency cap, one exactly `_URGENCY_GRANULARITY` below it, both boosted),
asserting the originally-more-urgent plot never loses, with and without
maximum fairness bonus stacked onto the less-urgent side.

---

## Part 4: Advance harvest notice

### Decision 15: reuse the projection algorithm, not a new one — and avoid a second live call

`agronomy/calibration.py:project_maturity_for_plot` already does exactly
this computation for registration-time messaging (ADR-009 Part 3):
elapsed real GDD plus rate-based extrapolation for the days no forecast
can reach. Calling it fresh for every plot on every trigger day would
mean a second live Open-Meteo call per plot per day, on top of the one
`_run_daily_watch_one` already makes to build `plot_days` for the
capacity/classification pipeline — the same daily temperature series,
fetched twice.

**Refactor, not a new algorithm**: the elapsed-GDD-plus-rate logic moves
into a new pure function, `project_maturity_from_days(days, transplant_date,
cluster, *, today)`, taking already-fetched data. `project_maturity_for_plot`
becomes a thin wrapper — fetch, then call the pure version — so
registration's call site is unchanged. `watcher.py`'s new advance-notice
check calls the pure version directly with the `plot_days[plot.plot_id]`
it already has in hand. Zero new network calls.

### Decision 16: one record per plot per season, checked every trigger day like the drying alert

```python
@dataclass(frozen=True)
class AdvanceNoticeRecord:
    plot_id: str
    farmer_id: str
    cluster_id: str
    season_id: str
    sent_at: str
    projected_maturity_date: str  # frozen at send time -- never updated,
    # even if a later run's own projection shifts. See Decision 17.
```

`Storage` gains `put_advance_notice_record`, `get_advance_notice_record(plot_id,
season_id)`. `watcher.py` gains `_check_advance_harvest_notices`, called
at the same point `_check_drying_window_alerts` already is — unconditionally,
whether or not today's rain trigger fires, using `plot_days` the caller
already fetched:

```python
ADVANCE_NOTICE_DAYS_BEFORE_MATURITY = 7  # unsourced judgment call, same
# status as UNCONFIRMED_HARVEST_WINDOW_DAYS/DRYING_WINDOW_DAYS -- "roughly
# a week" as stated in the brief, not independently derived.
```

For every plot not yet harvested this season, not yet sent this notice:
`days_until = (projected_date - today).days`. Sent only if `0 <
days_until <= ADVANCE_NOTICE_DAYS_BEFORE_MATURITY`. If `days_until <= 0`
the *first* time a plot is ever checked — maturity already imminent or
past at registration, or the watcher hasn't run in a while — **the
notice is skipped permanently for that plot this season**, never sent
late or same-day, per the explicit instruction. A plot registered after
its own notice window has already passed hits this same branch, correctly.

### Decision 17: exactly one message, never a correction — the projection can move, the notice does not

`AdvanceNoticeRecord`'s existence (any record at all, regardless of what
`projected_maturity_date` it froze) is the entire suppression check —
once sent, nothing about a later, more accurate projection ever triggers
a second message. **This is deliberate and stated plainly, matching the
brief's own reasoning verbatim**: a farmer told "actually the 17th now"
has learned the system is imprecise, not that it's careful. The frozen
`projected_maturity_date` field exists for the audit trail
(`explain_decision.py`-style traceability — what was this farmer
actually told, and when), not to support a correction path that isn't
built.

### Decision 18: wording — "expected," "around," no action required

English (draft): *"Your {area} plot is expected to be ready around
{date}. The machine will be scheduled close to then. No need to reply —
a good time to start arranging transport, gunny bags, and drying space."*

Tamil (draft, pending native-speaker review like every string in this
project): *"உங்கள் {area} வயல் {date} அளவில் தயாராக இருக்கும் என
எதிர்பார்க்கப்படுகிறது. அதற்கு அருகில் இயந்திரம் திட்டமிடப்படும்.
பதிலளிக்க வேண்டியதில்லை — இப்போதே போக்குவரத்து, சாக்குப்பைகள்,
உலர்த்தும் இடம் ஆகியவற்றை ஏற்பாடு செய்ய ஏற்ற நேரம்."*

Weather-unavailable failure path: **already structurally covered, not a
new case to handle.** `plot_days` is built inside `_run_daily_watch_one`'s
existing `try/except WeatherError` block, *before* the point where
`_check_advance_harvest_notices` would run — a weather failure already
aborts the whole trigger cleanly (`status: error, reason:
weather_unavailable`) before this new check is ever reached. No plot
gets a notice built from partial or missing data; the whole cluster's
check is retried next scheduled run, exactly as every other weather
failure already behaves.

### Tests

`tests/test_advance_notice.py`: sent exactly once at the 7-day boundary,
never resent even when a later run's projection would differ materially;
skipped permanently for a plot already inside the window (or past
maturity) at first check; skipped for an already-harvested plot; the
frozen `projected_maturity_date` differs from what a later recomputation
would say, and the stored record still reflects the original, not the
new figure; `project_maturity_from_days` and `project_maturity_for_plot`
produce identical output for the same inputs (the refactor changed
nothing about the math, proven, not just asserted); weather-unavailable
degrades via the existing top-level path, no notice sent, no crash.

---

## Cross-cutting: what Parts 2–4 add to ADR-010's persisted decision record

`TriggerContext`/`DecisionRecord` (ADR-010 Part 0.5) gain three fields
total across this ADR: `trigger_reason` (Part 2), `rain_event_classification`
and `rain_urgency_boost` (Part 3). Backward compatible — every existing
caller that doesn't know about them gets the defaults
(`trigger_reason="scheduled"`, `rain_event_classification="none"`,
`rain_urgency_boost=0.0`); no existing test's assertions change. Part 4
adds no new fields here — `AdvanceNoticeRecord` is its own entity, not a
decision.

## Consequences

- **The fairness mechanism can finally fire in production, not only in
  fixtures.** This is the headline consequence, the same way ADR-010's
  headline was "the system didn't remember why it decided" — this ADR's
  is "the system couldn't roll a season forward, so the ledger's cross-season
  math was theoretical." Both are structural gaps found by asking
  "does this actually work end to end," not feature requests.
- Machine breakdown handling adds a second, deliberately-separate
  ledger-adjacent entity (`BreakdownDisplacement`) rather than
  overloading `LedgerEntry` — keeps "who lost to whom" and "what broke"
  permanently distinguishable in every report that reads the ledger.
- Rain classification is explicitly unsourced, same honesty treatment as
  the decay curve — a real search was run, documented, and came up empty
  for a paddy-specific number; the constants are named `# DERIVED:` and
  flagged, not dressed up.
- No new farmer-initiated surface anywhere in this ADR — every new
  message is the agent speaking first (rollover prompt, advance notice)
  or a bounded reply to something it asked (rollover's yes/date, the
  existing confirmation taps). Checked explicitly per part, not assumed.
- Nothing here touches the deployed AgentCore artifact. Redeploying with
  this code is a separate, later decision, reported before it happens.

## Sequence

Part 1 (season entity decision, `SeasonRolloverPrompt`, the exclusion
filter, the rollover FSM, tests, Tamil strings) — commit, stop, report.
Then Part 2 (breakdown tap, recompute, `BreakdownDisplacement`,
confirmation suppression, `equity_report.py` extension, tests, Tamil
strings) — commit, stop, report. Then Part 3 (`rain_event.py`, urgency
boost, message wording, tests) — commit, stop, report. Then Part 4
(`project_maturity_from_days` refactor, `AdvanceNoticeRecord`, the
watcher check, tests, Tamil strings) — commit, stop, report.
