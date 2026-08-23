# ADR-013: Route Proposal and Operator Override — Authority Without Gatekeeping

- Status: **Approved, with two corrections (2026-08-23).** See
  "Resolved on review" at the end of this document for what changed
  from the original proposal and why, before implementation began.
- Date: 2026-08-23

## Context

Today, `watcher.py` computes a route and sends it to the operator as a
statement of fact (`build_operator_route_summary_text`: "Today's route
for {cluster}: 1. ... 2. ...") at the same moment farmers are notified.
The operator is a business owner with standing arrangements, existing
obligations, and his own judgment about his machine. Software that
reorders his day without asking is the single most likely reason this
gets quietly ignored in the field — the risk isn't that he disagrees
loudly, it's that he stops reading the messages at all.

**No `CLAUDE.md` existed in this repo at the start of this project** —
noted in every prior ADR that hit the same gap (ADR-008, 010, 011); it
was created earlier in this session, alongside filling in ARCHITECTURE.md's
ADR index, which had silently stopped at ADR-006/008 and never listed
ADR-009 through ADR-012. Read ADR-000 through ADR-012 in full before
writing this, plus the current source for `watcher.py`,
`agents/coordinator.py`, `scheduling/solver.py`, `scheduling/route.py`,
`storage/interface.py`, `storage/fairness.py`, `telegram/notify.py`, and
`telegram/webhook.py`.

**No contradiction with ADR-000–ADR-012 was found.** One place checked
specifically: does an operator overriding a route conflict with
ADR-002 Decision 4/5's "deterministic route ordering, no LLM
involvement"? No — the solver still computes the route exactly as
today; nothing here changes `order_route()` or gives a model any say in
scheduling. What's added is a human's ability to override the solver's
output after the fact, recorded distinctly from the solver's own
output, which is a different concern from who computes the route.

## The design tension, and why acceptance-before-send is rejected

The obvious design — hold the route, wait for the operator to accept
it, only then notify farmers — was considered and rejected. It breaks
the project's core thesis stated in ADR-000: *"the farmer is silent for
three months... the agent should not need the farmer to answer
questions to do its job."* The same property has to hold for the
operator, for the same reason, applied to a different party: if the
route waits on a human tap, then an operator who is asleep, in the
field with no signal, or simply hasn't opened Telegram yet at 6:01 AM
means **no farmer learns anything that day**, and the entire system
degrades from "an agent that acts" to "a notification queue waiting on
a human to press go." That failure mode is worse than an occasional
override, because it is silent and total — nothing happens, nobody is
told why, and it happens every single day the operator doesn't happen
to be at his phone within some window after 6:00 AM.

**The resolution**: separate *notification* from *authority*. The agent
still acts — computes the route, notifies farmers, marks plots
harvested — exactly as it does today, with nothing waiting on the
operator. What changes is only the operator's *own* message: it is
framed and buttoned as a proposal he can act on, not a report he has
already missed the chance to act on. His authority is real, but it is
exercised through **override** (he can always change what already
happened) rather than **gatekeeping** (nothing happens until he says
so). This is the identical asymmetry ADR-009 Part 2 already established
for harvest confirmation — *"an unanswered message is not evidence the
machine failed to come"* — applied here to the opposite direction:
*an unanswered proposal is not evidence the operator disagrees with it.*
Silence is the default outcome in both cases, not a stalled state
waiting to be resolved.

## Prerequisite, found during design research — the operator's route sequence does not reliably match the farmers'

Before a proposal/override mechanism can be built on top of "the
route," the route itself has to be one coherent sequence, not two
that can silently disagree. Reading the actual call path end to end,
rather than assuming `send_operator_route_summary`'s input already
matches what farmers were told, surfaced a real, live bug:

- `notify.send_harvest_scheduled` (per-farmer) is called with
  `decision.route_position` — the position `scheduling/route.py:order_route()`
  actually assigned, nearest-neighbor from the machine's start point
  (`scheduling/solver.py`'s `fits_routed` loop).
- `watcher.py:_send_notifications` builds `fits_route` (the list handed
  to the operator) by iterating `result.outcomes` and appending every
  `FITS` entry **in the order `result.outcomes` happens to be in** —
  and `result.outcomes` is explicitly `sorted(outcomes, key=lambda o:
  o.plot_id)` (`agents/coordinator.py:459`), a **plot-id sort**, not a
  route-position sort.
- `build_operator_route_summary_text` then numbers `fits_route`
  `1..N` **by that list's position**, not by `route_position`.

Concretely: with plots `p07` (route_position 1), `p02` (route_position
2), `p09` (route_position 3), the operator's message today would list
them as "1. p02, 2. p07, 3. p09" (plot-id order) while the farmers were
individually told "you're #1" (p07), "#2" (p02), "#3" (p09) — three
different numbers than what the operator sees next to their names. No
existing test catches this: nothing in `tests/test_notify.py` or
`tests/test_watcher.py` constructs a multi-plot `fits_route` and checks
its order against `route_position`; the gate scenarios that exist
either have one FITS plot (no ordering to get wrong) or don't assert
the operator-facing sequence at all.

This has been silently wrong since `send_operator_route_summary` first
shipped — it predates every part of this ADR and isn't caused by
anything proposed here. But it has to be fixed *before* Part 1, not
alongside it: `RouteOverride.proposed_route` (below) is meant to be the
authoritative record of what was actually proposed, read by
`explain_decision.py` and `equity_report.py` as ground truth. Building
that record from the already-wrong plot-id-sorted list would enshrine
a wrong sequence into the audit trail this ADR exists to create — the
exact failure ADR-009's Part 1.5 and ADR-010's Part 0.5 both exist to
prevent: don't build new machinery on an unexamined, broken assumption.

**Fix** (small, no behavior change beyond correctness): `_send_notifications`
builds `fits_route` from `decisions_by_id`, filtered to `FITS` outcomes
and **sorted by `decision.route_position`**, instead of following
`result.outcomes`'s incidental order:

```python
fits_route = sorted(
    (
        (farmers_by_id[plots_by_id[o.plot_id].farmer_id], plots_by_id[o.plot_id])
        for o in result.outcomes
        if o.outcome == PlotOutcome.FITS
    ),
    key=lambda pair: decisions_by_id[pair[1].plot_id].route_position,
)
```

One new test, `test_operator_route_summary_order_matches_farmer_route_positions`,
constructs three FITS plots whose plot-id alphabetical order differs
from their `route_position` order and asserts the operator-facing
sequence matches `route_position`, not plot_id. This is a standalone
bug fix, landing first, before Decision 1 below depends on it.

---

## Decision 1: `RouteOverride` — one record per cluster/season/day, status derived, not stored

```python
# storage/interface.py
@dataclass(frozen=True)
class RouteOverride:
    """The day's proposed route and whatever the operator has done to it
    so far. One record per (cluster_id, season_id, decision_date), created
    the moment the route is proposed, updated in place by every accept/
    swap/drop tap that follows. See ADR-013."""

    cluster_id: str
    season_id: str
    decision_date: str  # ISO date -- matches TriggerContext.decision_date
    proposed_route: list[str]  # plot_ids, in order, exactly as first proposed -- never mutated
    current_route: list[str]  # plot_ids, in order, reflecting every applied swap/drop so far
    proposed_at: str  # ISO timestamp
    accepted_at: str | None = None       # set only by an explicit Accept tap
    last_modified_at: str | None = None  # set by any swap or confirmed drop
    last_notified_route: list[str] | None = None
    # the route farmers were last actually notified about -- None means
    # "still just the original dispatch messages, nothing re-sent yet."
    # See Decision 5's notification-batching rule.
```

`Storage` gains `put_route_override`, `get_route_override(cluster_id,
season_id, decision_date)` — same Protocol-then-both-backends shape as
every other entity. `DynamoStorage`: `PK=CLUSTER#{cluster_id}`,
`SK=ROUTE_OVERRIDE#{season_id}#{decision_date}` (per-cluster-per-day,
same colocation style as `WATCHER#RUN`, just carrying a date since this
one needs history, not just "most recent"). `FileStorage`: one more
top-level dict, `route_overrides[cluster_id][season_id][decision_date]`.

**Status is derived at read time, never stored**, matching this
project's existing pattern (`confirmation_status()`, `rollover_status()`,
`weighted_bump_days()`):

```python
# watcher.py or storage/fairness.py -- exact module TBD at implementation
def route_override_status(o: RouteOverride) -> str:
    """"modified" if the route differs from what was first proposed,
    regardless of whether an Accept tap ever happened; otherwise
    "accepted" if an explicit tap was recorded, else "no_response" --
    the silence case, not a failure state. Three distinct facts, same
    discipline as confirmation_status()/rollover_status()."""
    if o.current_route != o.proposed_route:
        return "modified"
    if o.accepted_at is not None:
        return "accepted"
    return "no_response"
```

No sweep, no scheduled job, no timeout — `no_response` isn't something
that gets finalized at end of day, it's simply what the record says
whenever anyone reads it, for as long as nobody has touched it. This is
the concrete implementation of "silence is not a veto": there is no
code path anywhere that treats an unaccepted, unmodified route as
anything other than the standing route.

## Decision 2: proposal framing — same content, different posture

`build_operator_route_summary_text`'s header changes from a declarative
statement to an explicit proposal, in both languages:

- English: `"Today's route for {cluster}:"` → `"Proposed route for
  {cluster} today:"`.
- Tamil (draft, pending review like every string in this project):
  `"இன்று {cluster}-க்கான பரிந்துரைக்கப்பட்ட பாதை:"` — literally
  "today's recommended/suggested route for {cluster}" —
  பரிந்துரைக்கப்பட்ட (recommended) does the same posture work "proposed"
  does in English: it names this as a suggestion from a tool, not an
  instruction from a system. Reuses the same root already picked for
  the escalation argument label in ADR-008 Decision 15
  (`"ஏஜென்ட்டின் பரிந்துரை"` — "agent's recommendation") rather than
  inventing a second word for the same posture.

Stop lines themselves (`route_stop_line`) are unchanged — the facts
(who, how much, where) don't need to read differently, only the framing
around them.

Two buttons appended to the existing keyboard (which already carries
the `breakdown:` button from ADR-011 Part 2 — unrelated, unaffected):
`✅ Accept` (`route_accept:{cluster_id}:{season_id}:{date}`) and
`✏️ Modify` (`route_modify:{cluster_id}:{season_id}:{date}`). **Neither
button is sent when `fits_route` is empty** — nothing to accept or
modify; the existing `route_summary_empty` rendering is unaffected.

## Decision 3: Accept — records, changes nothing else

`handle_route_accept_callback`: authorization (Decision 8) →
staleness check (Decision 9's Case 3) → `storage.put_route_override(replace(o,
accepted_at=<now>))` → answer the tap with a short toast
(`"✅ ஏற்றுக்கொள்ளப்பட்டது"` / `"✅ Accepted"`) → no message edit, no
notification to anyone. This is deliberately the smallest possible
handler in this ADR: accepting a proposal that already stands changes
no farmer-facing state, because nothing was withheld from farmers in
the first place.

## Decision 4: Modify — edit-in-place, taps only, two different stakes for two different actions

Tapping `✏️ Modify` edits the *same* message in place (`edit_message_text`
+ `edit_message_reply_markup` — the same "edit the message's keyboard
away" mechanism ADR-004 Decision 4 already established for escalation
resolution) into a numbered, per-stop editing view, then edits back to
the summary view on `✅ Done`. One message per cluster per day,
throughout its whole lifecycle — no message proliferation.

Each stop row gets up to two buttons:

```
1. Muthu Pandian, 2.5 acres              [✕]
2. Meena Subramani, 3.0 acres      [↑]   [✕]
3. Kannan Raja, 1.5 acres          [↑]   [✕]
```

(Row 1 has no `↑` — nothing above it to swap with.)

- **`↑` (swap up)** — `route_swap:{cluster_id}:{season_id}:{date}:{position}`
  swaps `current_route[position-1]` and `current_route[position-2]`
  (1-indexed on the button, 0-indexed in the list), sets
  `last_modified_at`, re-renders the same message with updated numbers
  immediately. **No confirmation step** — low-stakes, and self-correcting
  (tapping `↑` on the row that moved down undoes it). **No farmer
  notification yet** — see the batching rule below.
- **`✕` (drop)** — `route_drop:{cluster_id}:{season_id}:{date}:{plot_id}`
  does **not** drop immediately. It edits the message to a one-line
  confirmation (*"Drop {name}'s plot from today's route? [Confirm]
  [Cancel]"*, with an added warning line if this is the last remaining
  stop — Decision 9's Case 2) with
  `route_drop_confirm:{...}:{plot_id}:{yes|no}` buttons. Only `yes`
  removes the plot from `current_route`, and — unlike a swap — this
  is where the real side effects happen (Decision 6), **applied and the
  farmer notified immediately**, not batched.
- **`✅ Done`** — `route_done:{cluster_id}:{season_id}:{date}` finalizes
  any accumulated swaps (Decision 5), edits the message back to the
  summary view showing `current_route`, and answers with a short
  confirmation toast.

**Why this satisfies "taps only" without a position-picker or free
text**: repeated adjacent swaps can reach any ordering of N items (the
same principle as a bubble sort) — this is intentionally the simplest
mechanism that needs no typed input and no encoding of "move item 3 to
position 7" in a single callback_data string, at the cost of possibly
several taps for a large reorder. For this project's scale (single-
digit route lengths per the seed clusters), that cost is small. If a
future cluster's route routinely needs many stops reordered at once,
a position-picker (tap a stop, then tap a destination-position button
from a rendered list — still taps only) would be the next step, not a
redesign; flagging this rather than over-building for a scale this
project doesn't have.

## Decision 5: two different notification stakes, two different timings — argued, not just asserted

**A dropped plot's farmer is notified immediately, on confirm. A
reordered plot's farmer is notified only when `Done` is tapped, and
only if their position actually changed since the last notification.**
This is a real asymmetry, not an oversight, and it rests on a fact
already established in ADR-009 Part 1.5 Decision C: **every `FITS`
plot is marked harvested at dispatch time, unconditionally, regardless
of its route position** — the coordinator calls `mark_plot_harvested`
the moment `solve()` classifies a plot `FITS`, before route ordering
has any bearing on anything downstream. This system has no model of
hours-in-a-day or which stop the machine can physically reach before
dark; `route_position` is a visiting *sequence*, not an allocation
mechanism. A farmer moved from position 1 to position 8 of an 8-stop
route is still, as far as this system knows, being harvested *today* —
only later in the sequence. A farmer whose plot is dropped is not being
harvested today at all.

Given that: a reorder is a real, disclosed fact worth recording (an
operator visibly exercising judgment about sequence, which matters for
trust and for the equity report), but it changes no farmer's actual
outcome — it doesn't warrant an urgent, individual interruption for
each intermediate tap while the operator is still arranging his day. A
drop changes a farmer's actual outcome, and the earlier that farmer
knows, the more time he has to adjust (arrange labor, drying space,
whatever a lost harvest day costs him) — it warrants immediate,
uninterruptible notification, not batching behind an arbitrary `Done`
tap the operator might not get to for hours.

**Mechanically**: `route_done` diffs `o.last_notified_route or
o.proposed_route` against `o.current_route`, position by position, for
every plot still present in both, and sends each farmer whose position
changed an updated `harvest_scheduled`-shaped message (new position
number, same wording otherwise — no new message shape needed, just a
new call with the new number). Then sets `last_notified_route =
current_route`. A plot dropped since the last notification is excluded
from this diff (it was already, separately, notified at drop-confirm
time) — `route_done` only ever handles position changes among plots
still on the route.

**If you'd rather every position change also wait for `Done`, including
none sent until the operator explicitly finalizes anything** — that's a
one-line change (call the diff-and-notify logic from `route_accept` and
`route_done` both, or from neither until `Done`); flagged as the one
design choice in this decision I'd treat as genuinely arguable rather
than settled, since it trades off "farmer told sooner" against
"farmer not told about an order the operator was still fiddling with."

## Decision 6: the fairness-bump question — corrected on review, not as originally proposed

**Original framing (this ADR's first draft): any operator override that
bumps a farmer is a fairness bump — the farmer lost to another farmer,
exactly what the ledger exists to detect, recorded as operator-decided
rather than agent-decided.** On review, that framing was too broad —
**corrected: only a confirmed drop writes a `LedgerEntry`; a pure
reorder does not.** The correction, and why the original framing was
wrong, not just narrower:

Per Decision 5's own reasoning: **a pure reorder does not remove a
farmer from today's harvest — the plot is already marked harvested at
dispatch, before route order is even computed downstream, and stays
harvested regardless of where it sits in the sequence.** No day is
lost, no maturity risk is added, nothing measurable changes about
whether or when-this-season the crop comes off the field. Writing a
`LedgerEntry` for a farmer who is still being harvested today, just
third instead of first, would credit a bump for a harm this system has
no way to show actually happened — the same category of problem
ADR-011 Part 2 Decision 8 refused to let breakdown displacement create
in the other direction (crediting a bump for a cause that isn't a real
farmer-vs-farmer loss).

**A drop is different in kind: the plot is removed from today's route
entirely, `clear_plot_harvest` returns it to the schedulable pool
(Decision 7), and the farmer's harvest that was going to happen today
now does not.** That is the real thing the ledger exists to detect,
whether or not a specific other farmer can be named as the direct
beneficiary of the freed capacity (see the `opponent_plot_id: str |
None` widening below — often there won't be one, since this ADR builds
no "add a plot in its place" mechanism; the operator's actual reason is
usually his own standing obligation, not a specific competing farmer,
and the ledger should still see it).

**Decided, on review: only a confirmed drop writes a `LedgerEntry`; a
pure reorder does not, and is recorded solely in `RouteOverride`
(visible in the equity report's new "operator activity" section and in
`explain_decision.py`, per Decision 10) without touching fairness
scoring.** A demotion to a much later position in the sequence, on the
theory that a farmer visited last genuinely bears more risk than one
visited first even within the same day, was considered and rejected for
the same reason as any other unmeasured number in this project: this
system has no time-of-day model and no per-stop duration estimate, so
scoring that risk would mean inventing a number with no basis — the
exact thing this project's honesty rule refuses to do elsewhere. Flagged
as a real, disclosed gap rather than papered over with an arbitrary
partial-bump value.

**Ledger schema, two small widenings, both backward-compatible:**

```python
# storage/interface.py
@dataclass(frozen=True)
class LedgerEntry:
    farmer_id: str
    season_id: str
    days_bumped: int
    outcome: str
    resolved_at: str
    cluster_id: str
    plot_id: str
    opponent_plot_id: str | None = None  # WIDENED: None when no specific
    # other farmer benefits from the freed capacity -- see ADR-013
    # Decision 6. Every existing caller passes a real string; only new
    # operator-override bumps can pass None.
    decided_by: str = "agent"  # NEW: "agent" | "operator_escalation" |
    # "operator_override". Default "agent" preserves every existing
    # row's classification for a hypothetical future fully-automatic
    # bump path -- no such path exists in this codebase today (see the
    # correction below).
```

**Corrected on review: `decided_by` is three-valued, not two, and both
of `webhook.py`'s existing `record_bump` call sites (escalation
win/loss) change from the original proposal's `"agent"` to
`"operator_escalation"`.** The original draft argued that an escalation
resolution should stay `decided_by="agent"` because the operator is
"exercising judgment inside a process the agent itself designed,"
resolving a tie the system asked a human to break, and reserved
`"operator"` for the narrower case of a human reversing a confident
agent decision. That architectural distinction is real and worth
keeping — but `decided_by`'s actual job is to answer the equity
report's question, *"who decided how this village's machine was
allocated?"*, and to that question a human choosing between two plots
is a human decision regardless of what invoked the choice. Labeling it
`"agent"` understates human involvement in precisely the decisions where
it was highest — a genuinely misleading answer for an auditor reading
the report, not just an imprecise one. **Fix: preserve the distinction
by adding a third value rather than collapsing it** — `"operator_escalation"`
(a human resolved a tie the agent could not) and `"operator_override"`
(a human reversed a decision the agent was confident about) are both
human decisions, tracked separately so the equity report can show the
direct answer (how many bumps were human-decided at all) and the
nuance (which kind) in the same place. `decided_by="agent"` is left in
the schema, defaulted, for a hypothetical fully-automatic bump path —
none exists in this codebase today; every `LedgerEntry` this system has
ever written came from a human tap of one kind or the other.

`record_bump`'s `opponent_plot_id` parameter widens to `str | None` to
match; `days_bumped` for an override-drop reuses the existing
`DEFAULT_DAYS_BUMPED` placeholder (currently `1`, per-escalation, per
`webhook.py`'s own comment that refining this to real calendar days
lost is a future improvement) — not inventing a second placeholder
number for a second kind of bump. New `outcome` value:
`"operator_override"` (distinct from the `decided_by` field of the same
name — `outcome` is the existing "what happened" field, `decided_by` is
the new "who decided" field; ADR-012's escalation-resolution outcomes
`"bumped"`/`"won"` are unchanged, only their `decided_by` value moves
from the default to `"operator_escalation"`).

## Decision 7: reuse, not new machinery, for the reversal itself

A confirmed drop calls exactly the two hooks two prior ADRs already
built for this shape of problem, and invents nothing new for the
mechanics:

1. `storage.clear_plot_harvest(plot_id, cluster_id, season_id)` — the
   exact reversal hook ADR-009 Part 1.5 Decision E designed and left
   unused until ADR-011 Part 2's breakdown recompute became its first
   real caller. This is the second.
2. `HarvestConfirmation.cancelled = True` for that plot's confirmation
   record — the same field ADR-011 Part 2 Decision 9 added so a
   breakdown-displaced farmer's evening "did it come?" prompt is
   suppressed and a stray late reply can't reach `record_bump` through
   a second door. Both of those guards (`run_evening_confirmations`'s
   `to_ask` filter, `handle_confirmation_callback`'s cancelled check)
   already exist and require no new code to also cover this case.

**One necessary addition**: `HarvestConfirmation` gains
`cancellation_reason: str | None = None` (`"machine_breakdown"` |
`"operator_override"`). The *operational* behavior of `cancelled` is
correctly identical for both reasons (suppress the ask, refuse a late
reply's ledger credit) and stays one shared code path — but ADR-011
Part 2 Decision 8's entire point was that "who lost to whom" and "what
broke" must stay **permanently distinguishable in every report that
reads the ledger**, and collapsing a breakdown and an operator override
into one undifferentiated `cancelled=True` with no reason would quietly
violate that same principle from the other direction. The distinguishing
mechanism that actually matters (which entity gets written —
`BreakdownDisplacement` vs. `LedgerEntry`) is already correct and
untouched by this ADR; `cancellation_reason` is the smaller, additive
fix so a reader of the raw confirmation record doesn't have to
cross-reference two other tables to learn why. Existing pre-ADR-013
breakdown-cancelled records simply have `cancellation_reason=None` —
read as "reason not recorded, predates this field," the same disclosed-gap
treatment every other schema addition in this project gets.

Tomorrow's trigger re-evaluates a dropped plot fresh — `clear_plot_harvest`
returning it to the schedulable pool is the entire mechanism; nothing
new needed in `solve()` or `watcher.py` beyond what ADR-011 Part 2
already built and proved.

## Decision 8: authorization — operator-only, checked from the first commit

Every new callback (`route_accept:`, `route_modify:`, `route_swap:`,
`route_drop:`, `route_drop_confirm:`, `route_done:`) is authorized
identically: the tapping user's own Telegram identity
(`callback_query["from"]["id"]`) must equal `cluster.operator_chat_id`,
via the existing `_is_operator` helper (`webhook.py`) — no new
authorization primitive, reusing the exact function ADR-011's
breakdown/machine-back handlers already use and ADR-012's audit
confirmed correct. A mismatch answers with a generic refusal toast,
mutates nothing, and is logged with the tapping user's ID, same
posture as every other authorization refusal in this codebase.

**Added to ADR-012's audit table:**

| Prefix | Handler | Authorized against | Status |
|---|---|---|---|
| `route_accept:` | `handle_route_accept_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked from the start |
| `route_modify:` | `handle_route_modify_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked from the start |
| `route_swap:` | `handle_route_swap_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked from the start |
| `route_drop:` | `handle_route_drop_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked from the start |
| `route_drop_confirm:` | `handle_route_drop_confirm_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked from the start |
| `route_done:` | `handle_route_done_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked from the start |

**Test, per prefix at minimum for `route_accept` and
`route_drop_confirm` (the two that mutate state), per your standing
instruction that a wrong-party tap must be proven to mutate nothing**:
`test_route_accept_from_non_operator_is_refused_and_does_not_set_accepted_at`,
`test_route_drop_confirm_from_non_operator_is_refused_and_does_not_drop_or_bump`
— construct a real `RouteOverride`/route, tap from a chat_id that
isn't `cluster.operator_chat_id`, assert the refusal toast, assert
`RouteOverride` unchanged (byte-for-byte), assert no `LedgerEntry`, no
`clear_plot_harvest` call, no farmer notification sent. A `from` field
missing entirely (matching ADR-012's own test pattern) refuses the
same way, fails closed.

## Decision 9: failure paths

**1. Override arriving after the day's harvests are confirmed.**
`handle_route_drop_confirm_callback` and `handle_route_swap_callback`
both check the plot's `HarvestConfirmation` for today first: if
`confirmed is not None` (the farmer already answered yes or no to "did
the machine come"), the outcome is already a known, real-world fact —
editing the proposal after it can't undo what already happened. Refuse
with a clear toast (*"This plot's harvest was already confirmed —
today's route can't be changed for it."* /
*"இந்த வயலின் அறுவடை ஏற்கனவே உறுதிப்படுத்தப்பட்டது — இன்றைய பாதையை
இதற்கு மாற்ற முடியாது."*), mutate nothing for that plot, log at `INFO`.
For a swap involving two plots, either one being already-confirmed
blocks that specific swap; other still-editable stops remain unaffected.

**2. Override removing the only plot on the route.** Allowed — it's
the operator's prerogative, and this system has no basis to refuse it.
But the drop-confirmation prompt gets an added warning line specifically
for this case (*"This is the last plot on today's route — confirming
leaves nobody scheduled today."* /
*"இது இன்றைய பாதையின் கடைசி வயல் — உறுதிப்படுத்தினால் இன்று யாருக்கும்
இயந்திரம் வராது."*), so the operator isn't one accidental tap away
from an empty day with no extra friction to catch it. After
confirmation, the message renders via the existing
`route_summary_empty` path, unchanged from how an always-empty route
already renders today.

**3. Modify tap on a stale route from a previous day.** Every new
callback's `decision_date` is checked against `date.today()` (IST, same
timezone discipline as every other date comparison in this project)
before anything else runs — a tap on yesterday's still-visible message
is refused with *"This route is from {date} and is no longer active."*
/ *"இந்த பாதை {date} தேதியிலிருந்தது, இப்போது செல்லுபடியாகாது."*,
mutating nothing. This is a same-calendar-day mechanism throughout —
"later in the day," not "any day since."

**4. Override by a non-operator.** Covered by Decision 8 — refused,
logged, no mutation, generic toast, indistinguishable in behavior from
every other authorization refusal in this codebase.

## Decision 10: surfaced in the equity report and in `explain_decision.py`

**`equity_report.py`** gains a new section, "Operator route activity,"
reading every `RouteOverride` for the cluster/season and classifying
each via `route_override_status()`: counts of `accepted` (explicit tap),
`no_response` (silent, route stood), and `modified`, plus, within
`modified`, how many involved at least one drop versus reorder-only.
**Acceptance rate** is defined as `(accepted + no_response) /
total_proposed` — the fraction of proposed routes that stood as
computed, whether confirmed explicitly or by silence, matching this
ADR's own framing that silence and explicit acceptance are the same
outcome for the machine, even though they're kept visibly distinct
counts for the same reason ADR-011 Decision 5's "declined vs. unknown"
split exists: a reader shouldn't have to assume every non-modification
was an active endorsement.

The existing "Repeat bumps" / fairness-mechanism-activity section
(ADR-010 Part 2) gains one more breakdown, reading `LedgerEntry.decided_by`:
count and list of bumps per farmer split three ways — `agent`,
`operator_escalation`, `operator_override` — directly answering "who
decided how this village's machine was allocated" at the exact place a
reader is already looking for bump patterns (the direct answer: how
many bumps were human-decided at all), with the escalation/override
split as the nuance underneath it (a tie the agent asked a human to
break, versus a confident agent decision a human reversed), not off in
a separate section they'd have to cross-reference themselves.

**`explain_decision.py`**: when replaying a plot's `DecisionRecord` for
a date where a `RouteOverride` exists, look it up
(`storage.get_route_override`) alongside the existing lookups and, if
this plot's position in `current_route` differs from `proposed_route`,
or it's present in `proposed_route` but absent from `current_route`,
append an explicit paragraph — e.g. *"This plot's route position was
originally computed as #2 by the deterministic scheduler. The operator
modified today's route at {last_modified_at}, moving it to #5."* or
*"...removing it from today's route at {last_modified_at}. This is
recorded as an operator-decided fairness bump (LedgerEntry,
decided_by=operator_override), separate from any decision the
algorithm made."*
No schema change to `DecisionRecord` itself — `RouteOverride` is the
single source of truth for override facts, cross-referenced at read
time, the same pattern ADR-010 Part 1 already uses for ledger and
confirmation sections alongside the decision record. Per ADR-012
Decision 2's precedent, also resolve and show which operator was in
effect (`operator_enrollment.operator_as_of`) whenever `accepted_at` or
`last_modified_at` is set — an override is exactly the kind of
operator-touched decision that precedent already says belongs there.

## Decision 11: Tamil strings (drafts, for your review before this ships)

All first-pass, not final — same `print_tamil_strings.py`
dump-and-review discipline as every prior string in this project,
including a live-Telegram-receipt round given ADR-008 Decision 17's
finding that string-level review alone has missed real bugs before.

- Proposal header: *"இன்று {cluster}-க்கான பரிந்துரைக்கப்பட்ட பாதை:"*
- Accept button: *"✅ ஏற்றுக்கொள்"* / Accept toast: *"✅ ஏற்றுக்கொள்ளப்பட்டது"*
- Modify button: *"✏️ மாற்று"*
- Drop button (per row): *"✕"* (symbol only, same as English — no
  translation needed for a single glyph)
- Drop confirmation: *"{name}-ன் வயலை இன்றைய பாதையிலிருந்து நீக்கவா?"*
  [✅ உறுதி] [❌ ரத்து]
- Done button: *"✅ முடிந்தது"*
- Farmer drop notification: *"இன்றைய பாதை மாறியுள்ளது — இன்று உங்கள்
  வயலுக்கு இயந்திரம் வராது. அடுத்த வாய்ப்பில் மறு பரிசீலனை
  செய்யப்படும்."* (draft: "today's route has changed — the machine
  won't be coming to your plot today; you'll be reconsidered at the
  next opportunity") — deliberately does **not** name a reason
  (weather, another farmer, the operator's own arrangement) since this
  system doesn't know which of those is true and shouldn't guess; matches
  `escalation_resolved_lost`'s existing discipline of stating the fact
  without fabricating a cause it can't verify.
- Farmer reorder notification: reuses `harvest_scheduled` unchanged,
  just called again with the new `route_position`.
- Stale-route toast, wrong-operator toast, last-plot warning: drafted
  inline in Decision 9 above.

English equivalents for every line above ship alongside, same
`messages_en.py` shape as always.

## Tests

`tests/test_route_override.py` (new): `route_override_status()`
for all three states; Accept sets `accepted_at`, notifies nobody,
changes no route; a swap updates `current_route` and re-renders,
`route_done` notifies only the farmers whose position actually changed
since `last_notified_route`, and does not re-notify on a second `Done`
tap with no further changes; a confirmed drop calls `clear_plot_harvest`,
sets `HarvestConfirmation.cancelled=True` with
`cancellation_reason="operator_override"`, writes a `LedgerEntry` with
`decided_by="operator_override"`, `opponent_plot_id=None`, notifies the
dropped farmer immediately (before any `Done` tap); a pure reorder
writes no `LedgerEntry` at all (the actual "is this a bump" behavior,
tested directly, not just reasoned about); the two existing escalation
`record_bump` call sites now write `decided_by="operator_escalation"`
(a regression test on `test_webhook.py`'s existing escalation-resolution
tests, not just this new file); all four failure paths from Decision 9,
each proven to mutate nothing on refusal; the two authorization tests
from Decision 8; `equity_report.py`'s new section against a hand-built
set of `RouteOverride`/`LedgerEntry` records (mix of
accepted/no_response/modified, mix of all three `decided_by` values);
`explain_decision.py`'s new paragraph for a dropped and a reordered
plot, and its absence when no `RouteOverride` exists for that date.

## Consequences

- The agent's daily action is unchanged — route computed, farmers
  notified, plots marked harvested, nothing waits on the operator. Only
  the operator's own message changes posture, and only his own taps
  create new state.
- **A confirmed drop is the one action in this ADR with real, durable
  consequences**: it un-harvests a plot (returning it to tomorrow's
  contention via machinery ADR-009 and ADR-011 already built), writes a
  `decided_by="operator_override"` fairness bump distinct from every
  existing bump path, and notifies the affected farmer immediately,
  without fabricating a reason this system doesn't actually know.
- **A pure reorder has no fairness consequence** — recorded for trust
  and audit (`RouteOverride`, the equity report's new section,
  `explain_decision.py`), but does not touch `LedgerEntry` or
  `weighted_bump_days`, because no farmer's harvest outcome actually
  changed. This is a correction to this ADR's own original framing, not
  a narrower reading I'm proposing on top of an accepted one — see
  Decision 6 and "Resolved on review" below.
- **Every `LedgerEntry` this system writes now states who decided it**:
  `agent` (reserved, unused today), `operator_escalation` (a human
  broke a tie the agent asked for help on), or `operator_override` (a
  human reversed a decision the agent was confident about) — the
  equity report's direct answer to "who decided this" plus the nuance
  underneath it, in one field.
- No new farmer-initiated surface: every new farmer-facing message in
  this ADR is the agent speaking first (a route-changed notice), never
  a reply the farmer has to compose.
- Two small, backward-compatible schema widenings
  (`LedgerEntry.opponent_plot_id: str | None`, `LedgerEntry.decided_by:
  str = "agent"`) and one additive field
  (`HarvestConfirmation.cancellation_reason: str | None`) — every
  existing row and every existing test's assertions are unaffected.
- Nothing here touches the deployed AgentCore artifact. Redeploying
  with this code is a separate, later decision, reported before it
  happens, same discipline as every prior ADR.

## Resolved on review (2026-08-23)

Three corrections made to the original proposal before implementation
began, all initiated by your review, not left as open questions:

1. **The prerequisite fix ships as its own standalone commit, before
   `RouteOverride` exists** — confirmed as proposed, with an added
   requirement: a regression test asserting the operator's route
   sequence and every farmer's told position agree for the same run,
   so this specific mismatch can't be silently reintroduced later.
2. **Reorders don't bump — the original framing was wrong, corrected.**
   The first draft of Decision 6 treated any override that "bumps a
   farmer" as a fairness event, in line with the framing in the
   original request. On review, using this ADR's own Decision 5
   finding (every `FITS` plot is marked harvested at dispatch,
   regardless of route position), that framing doesn't hold for a pure
   reorder: nobody's harvest actually changes, so there's nothing for
   the fairness ledger to detect. Only a confirmed drop writes a
   `LedgerEntry`. Recorded here as a correction to the request's
   original instruction, not as an idea this ADR originated — the
   trace showing why is Decision 6's, but the instruction it corrects
   was yours.
3. **Escalation resolutions do not stay `decided_by="agent"`.** The
   original proposal's architectural reasoning (escalation resolves a
   tie the agent's own process asked a human to break, distinct from an
   override reversing a confident decision) was sound, but answered a
   different question than the one `decided_by` exists to answer for
   an equity report: "who decided how this village's machine was
   allocated?" A human choosing between two plots is a human decision
   either way, and reporting it as `"agent"` would understate human
   involvement precisely where it was highest. Fixed by keeping the
   architectural distinction as a third value instead of collapsing
   it: `"operator_escalation"` vs. `"operator_override"`, both human,
   both distinguishable from a hypothetical future `"agent"` bump.
   Both existing `record_bump` call sites in `webhook.py` (escalation
   win/loss) updated accordingly.

## Sequence

Prerequisite fix (operator route-sequence bug) — commit, stop, report.
Then Decisions 1–11 as one unit (entity, proposal framing, Accept,
Modify/swap/drop/Done, the fairness-bump distinction and schema
changes, authorization, failure paths, equity report + explain_decision
surfacing, Tamil strings) — commit, stop, report. **This document is
Part 1 in full; awaiting explicit go-ahead before any of it is
implemented**, per your instruction.
