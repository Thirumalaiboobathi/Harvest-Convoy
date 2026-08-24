# ADR-013: Route Proposal, Operator Override, and Proxy Registration

- Status: **Part 1 — Approved, with two corrections (2026-08-23),
  implemented.** See "Resolved on review" below Part 1 for what changed
  from the original proposal and why, before implementation began.
  **Part 2 — Approved, with Decision 18 replaced (2026-08-23),
  implemented, revised 2026-08-24 to add a bounded Undo.** See "Resolved
  on review" at the end of Part 2.
  **Part 3 — Approved with two corrections (2026-08-24), implemented.**
  See "Resolved on review" at the end of this document.
- Date: 2026-08-23 (Part 1); 2026-08-23 (Part 2); 2026-08-24 (Part 3)

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

## Sequence (Part 1)

Prerequisite fix (operator route-sequence bug) — commit, stop, report.
Then Decisions 1–11 as one unit (entity, proposal framing, Accept,
Modify/swap/drop/Done, the fairness-bump distinction and schema
changes, authorization, failure paths, equity report + explain_decision
surfacing, Tamil strings) — commit, stop, report. **Implemented; both
commits landed 2026-08-23.**

---

# Part 2: Proxy Registration for Phone-less Farmers

## Context

Part 1 assumed every farmer already exists in the system. Getting a
farmer into the system at all currently means he personally completes
a four-message Telegram exchange (`telegram/registration.py`) — village
name, a shared GPS pin, a yes/no crop confirmation, then a transplant
date and area. For a pilot of eight farmers per cluster, that is real
friction on its own, and it has a harder floor under it: **some
farmers will not have a smartphone at all**, and the current system has
no path for them to be registered by anyone else. That is not a rough
edge, it is a wall — a phone-less farmer cannot get past message one,
ever, under the current design. This part exists to remove that wall.

Read `telegram/registration.py`, `models.py` (`Farmer`/`Plot`),
`telegram/operator_enrollment.py`, ADR-009's Prerequisite (the
"update, not duplicate" rule), and ADR-011 Part 1 Decision 2 (the
rollover exclusion filter) before reading further — this part leans on
the exact mechanics of all four.

**A relevant existing gap, found while reading `registration.py`, not
fixed here**: the four-message flow asks for the village name
(message 1) purely to make the greeting read conversationally
(`registration.py:6-14`'s own docstring) and **never persists it** —
`Plot` has no `village` field; only `lat`/`lon` are kept. The
instruction for this part names "village, plot location, crop,
transplant date and area" as the four pieces a proxy supplies, which
means a proxy-registered `Plot` needs somewhere to put a village name
if it's going to be collected at all. Rather than replicate a
discard-after-asking into a second flow, **Decision 17 adds a real
`village: str | None` field to `Plot`** and starts populating it for
both proxy and (going forward) self-registration. This is a small,
independent, backward-compatible addition, not a prerequisite bug —
existing rows simply read `village=None`, "asked before this field
existed" being the honest gap, same treatment as every other
first-appearance-of-a-field case in this project. Flagging it as its
own line item rather than silently bundling it into "the same four
things" without comment.

## Decision 12: who may register a plot on a farmer's behalf — operator-only, no helper role

**Decided: for this pilot, only the cluster's registered operator
(`Cluster.operator_chat_id`, the identity ADR-012 already
authenticates) may proxy-register a farmer. No separate "helper" role
is built.**

Justification: a helper role is not free — it would need its own
provisioning mechanism (a code to hand out, exactly like
`operator_enrollment.py`'s `/operator <code>` flow, since there is no
other trust-establishing mechanism anywhere in this codebase), its own
revocation story (what happens when a helper leaves — a question this
project has never had to answer for the operator role itself, which
has no revocation path today either), its own row shape in the
authorization audit, and its own failure paths (a helper registering a
farmer who's actually the operator's rival, a helper who mis-enters
data with no one double-checking it). That is a second enrollment
system, not a small addition, for a role whose necessity is
speculative at eight farmers per cluster: the same operator who
already drives the route past every farmer's field, and who ADR-012
already trusts with route overrides and escalation resolution, is a
plausible sole point of intake for a handful of phone-less
registrations too. **Building a general-purpose role system for a need
this pilot hasn't demonstrated is exactly the premature abstraction
this project's own discipline refuses elsewhere** (CLAUDE.md: "don't
design for hypothetical future requirements").

If a real pilot surfaces a cluster where the operator genuinely cannot
reach every phone-less farmer himself (a large cluster, an operator
who doesn't cover the whole geographic area), that is real evidence a
helper role is needed — revisit then, with a real cluster's actual
shape to design against, rather than now, against a hypothesis.

## Decision 13: the flow — `/addfarmer`, operator-initiated, reusing registration's own shape

**Entry point**: the operator, from his own already-enrolled chat,
sends `/addfarmer` as a plain text command — checked in
`webhook.handle_update` at the exact same point as `/operator <code>`
(`webhook.py:1477`, before any rollover/registration free-text
routing), so it can never be misparsed as a farmer's registration
reply or a rollover date answer. Unlike `/operator <code>`, no code
follows — identity is already established, checked at the command
itself:

```python
if (incoming.text or "").strip().lower().startswith(ADD_FARMER_COMMAND):
    cluster_id = os.environ.get("HARVEST_CONVOY_CLUSTER_ID")
    cluster = storage.get_cluster(cluster_id) if cluster_id else None
    if cluster is None or chat_id != cluster.operator_chat_id:
        client.send_message(chat_id, lang.not_authorized_for_addfarmer())
        return
    # ... start proxy_registration flow for this operator chat_id
```

A non-operator (a farmer, a stranger who found the bot) typing
`/addfarmer` gets a generic refusal and nothing is started — same
posture as every other authorization refusal in this codebase, just
applied to a text command instead of a callback tap, since there is no
callback yet at this point in the flow.

**The state machine itself is a close mirror of `RegistrationState`**,
not a new design: `ProxyRegistrationState` (new module,
`telegram/proxy_registration.py`) walks
`AWAITING_FARMER_NAME → AWAITING_CONTACT_NOTE → AWAITING_VILLAGE →
AWAITING_LOCATION → AWAITING_CROP_CONFIRM → AWAITING_TRANSPLANT_INFO →
AWAITING_HAS_PHONE → AWAITING_CONFIRM → COMPLETE`, reusing
`registration.py`'s own `_parse_date`/`_parse_area`/`_is_yes`/`_is_no`
helpers and prompt-per-step shape unchanged. Two new steps beyond
self-registration's four, and why each is unavoidable here even though
self-registration doesn't need it:

- **`AWAITING_FARMER_NAME`** — self-registration never asks for a name
  because Telegram already hands over `sender_name` for free
  (`registration.py:24-29`'s own docstring: "asking would fail the
  'does the agent already know enough to speak first' test in
  reverse"). That fact doesn't hold here: Telegram gives the
  **operator's** `sender_name`, not the farmer's, since the operator is
  the one chatting. The operator must be asked, as free text — no way
  around it, and this is operator-authored input, the same class as
  every other command/reply the operator already types, not new
  farmer-facing surface.
- **`AWAITING_CONTACT_NOTE`** — optional (`skip` accepted as a valid
  reply, same as an empty text elsewhere in this codebase's convention
  for optional fields). Whatever the operator types — a phone number,
  "no phone," a relative's number — is stored verbatim on
  `Farmer.contact_note: str | None`, a plain informational field.
  **This is never used to attempt a Telegram send.** The Bot API
  cannot address a `chat_id` it has never received an inbound message
  from — a phone number or username is not, by itself, a usable
  send target — so `contact_note` exists purely for the operator's own
  reference and the equity report's provenance story, and the field's
  own docstring says so explicitly to prevent a future reader from
  assuming otherwise.
- **`AWAITING_HAS_PHONE`** — a yes/no question (reusing
  `CONFIRMATION_YES_LABEL`/`_NO_LABEL`, no new label pair). **This does
  not change what gets persisted** — a proxy registration always leaves
  `telegram_chat_id=None` regardless of the answer, since the proxy
  chat can never supply the farmer's own chat_id (Decision 18 has no
  concept of "expected" linking either; it's the same operator tap for
  anyone). It changes only which closing message the *operator* sees:
  a "no phone" answer gets the plain notification-less note (Decision
  21); a "has a phone, just not with him now" answer gets the same
  note plus a line that `/linkfarmer` is how to connect the two records
  once that farmer self-registers.
- **`AWAITING_CONFIRM`** — a final yes/no summary
  ("Register {name}, {village}, {area}, transplanted {date}?
  [Confirm] [Cancel]") before anything is written. Self-registration
  has no equivalent step because the farmer is reporting his own plot
  and has no reason to mis-type someone else's details out of
  unfamiliarity; a proxy relaying information secondhand is exactly
  the case worth one extra confirmation before commit. See Decision
  20's discussion of why this is the *only* mistake-guard this part
  builds, not a full edit/correct mechanism.

Village and location reuse the identical mechanisms as self-
registration unchanged — a typed village name (Tamil script or
Tanglish, same inference rule) and Telegram's native location-share
for GPS, on the theory that a village-level operator physically
visiting or already familiar with a farmer's field can share a
location exactly as easily as the farmer himself could. If that
assumption turns out false in the field (an operator registering
farmers from memory, away from the plot), `village` becomes the more
load-bearing of the two — another reason Decision 17 stops discarding
it.

## Decision 14: identity — a second ID scheme, not a variant of the first

Self-registration's `farmer_id`/`plot_id` scheme
(`farmer-{chat_id}`/`plot-{chat_id}`) cannot be reused for a proxy
registration: the chat doing the registering is the **operator's**,
and deriving an ID from it would either collide across every farmer
the same operator ever proxy-registers (all landing on the same
`farmer-{operator_chat_id}`) or, if a phone-less farmer never gets a
phone at all, has no `chat_id` to derive from in the first place.

**Decided**: proxy-registered farmers and plots get IDs from a short
random suffix, not a chat_id: `farmer-proxy-{6 hex chars}` /
`plot-proxy-{6 hex chars}` (`secrets.token_hex(3)`, collision-checked
against `storage.get_farmer`/`get_plot` before use, retried on the rare
collision — same defensive shape `generate_operator_code.py` already
uses for its own code space). **This ID, once assigned, never changes**
— including after Decision 18's linking step binds a real
`telegram_chat_id` onto it. Self-registration's IDs and proxy IDs are
now two disjoint, permanently distinguishable namespaces by
construction (`-proxy-` in the ID), which doubles as a free, zero-cost
provenance signal alongside Decision 17's explicit `registered_by`
field.

## Decision 15: the no-phone case — weighed both ways, decided, and its consequence traced through

**Option A — proxy relay.** Register the plot fully, keep
`Farmer.telegram_chat_id = None`, and build a mechanism for the
operator to answer harvest confirmations and rollover prompts *as* the
farmer's proxy (some new "answer on behalf of" authorization letting
the operator's tap count as the farmer's reply).

**Option B — notification-less, in person.** Register the plot fully,
`Farmer.telegram_chat_id` stays `None` — exactly the representation
this codebase already uses for "cannot be reached" everywhere else
(all eight guarded `notify.py` send functions, `watcher.py`'s
`_check_drying_window_alerts`/`_check_advance_harvest_notices`/
`run_evening_confirmations`, all already degrade cleanly and log on
this exact condition today). The plot appears on the operator's daily
route precisely as any other plot — route generation has no notion of
`telegram_chat_id` at all — and every farmer-facing message this
system would otherwise have sent simply isn't sent; the operator, who
is already physically visiting this plot today because it's on his
route, is this farmer's entire information channel, out of band,
exactly as it already is for every non-Telegram interaction this
project has never tried to model (weather, hiring labor, arranging
drying space).

**Decided: Option B.** Option A is not a small addition — it requires
a new authorization category (the operator's tap counting as a
different person's answer, on `confirm:`/`rollover:` callbacks
ADR-012 just finished locking down to a single, exact-identity check),
a new place for `decided_by`-style attribution questions to arise
(if the operator's proxy-answer disagrees with what the farmer would
have said, who is accountable for that in the equity report?), and a
doubled test surface for confirmation and rollover handling, all to
serve a channel the operator already has for free: he drives past this
plot. **The test this project already applies —"does the agent already
know enough to speak first? If not, it's out of scope"— cuts the same
way here**: the system does not, and cannot, know what the farmer would
tap in response to a message it can't deliver, and Option A would be
built entirely to paper over exactly that unknown by inventing a
substitute channel this project has no way to verify. Option B invents
nothing: it is the existing, already-tested "unreachable farmer"
degrade path, now reached by a population that gets there by design
rather than by anomaly.

**The consequence Option B doesn't get to skip, and the reason this
decision isn't as simple as "reuse what exists"**: every one of those
eight `telegram_chat_id is None` call sites already degrades safely
for message-sending — but **`run_season_rollover`'s handling of that
same condition does not just skip a send, it currently writes a
`SeasonRolloverPrompt` record specifically so the plot gets *excluded*
from next season's scheduling** (`watcher.py:787-805`, comment:
"recorded as unknown ... nobody could ask, so nobody said yes").
`_apply_rollover_exclusion` (`watcher.py:146-167`) excludes any plot
whose `SeasonRolloverPrompt.replied is not True` — and a farmer who was
never asked can, by construction, never make `replied` become `True`.
**Under today's code, a permanently phone-less farmer's plot would be
correctly scheduled this season, then silently and permanently
excluded starting next season**, the exact opposite of what this part
exists to build, and the specific failure Decision 16 exists to close.

## Decision 16: closing the rollover-exclusion gap — reachability decides whether a prompt is even written, not just whether it's answered

**The fix is one conditional's behavior change in `run_season_rollover`,
not a change to the exclusion filter itself.** `_apply_rollover_exclusion`
already has the rule this needs — Decision 2 of ADR-011 Part 1: *no
`SeasonRolloverPrompt` record at all means include by default* (proven
today by `test_new_farmer_joining_mid_season_has_no_prompt_and_is_included_by_default`).
That rule exists precisely for "never asked"; the bug is that
`run_season_rollover` currently treats "couldn't ask because
unreachable" as if it were "asked and got no answer" by writing a
record anyway, when it should treat "couldn't ask" as "never asked" and
let the existing default do its job.

```python
# watcher.py: run_season_rollover, the farmer.telegram_chat_id is None branch
if farmer.telegram_chat_id is None:
    logger.info(
        "run_season_rollover: farmer %s has no chat_id -- no prompt "
        "recorded, plot=%s stays included by default next trigger "
        "(ADR-011 Part 1 Decision 2)",
        farmer.farmer_id, plot.plot_id,
    )
    skipped_no_chat_id += 1
    continue  # no SeasonRolloverPrompt written -- this is the fix
```

**Why this was ever written the other way, and why changing it now is
safe**: before this part, no code path could produce a *permanently,
by-design* phone-less registered farmer — `telegram_chat_id` was always
set at the one real registration entry point
(`_persist_completed_registration:394`). A `None` chat_id was only
reachable via a seed script or a hand-edited record: an anomaly, not a
designed state, and treating an anomaly conservatively (exclude, don't
silently include) was the defensible default at the time. Proxy
registration makes "registered, permanently unreachable" a real,
intended, expected population for the first time — the old default
now excludes exactly the farmers this part exists to include, so the
justification for the old behavior no longer applies to the population
it now actually governs.

**This does reopen one question the old code closed**: if a
*reachable* farmer's send fails for an ordinary transient reason (a
network error, Telegram rate-limiting) rather than because
`telegram_chat_id is None`, that path is untouched — `send_result.success
== False` already skips writing the record and retries next
invocation (`watcher.py:814-819`), unaffected by this change, since
that condition was never the one being fixed.

**Test change required**: `test_no_chat_id_farmer_is_recorded_as_unknown_not_defaulted_to_included`
(`tests/test_season_rollover.py:406`) currently asserts the old
behavior by name and must be rewritten, not just left passing by
accident — its replacement,
`test_no_chat_id_farmer_writes_no_prompt_and_is_included_by_default`,
asserts `storage.get_season_rollover_prompt("p1", SEASON_2) is None`
after `run_season_rollover`, then runs `run_daily_watch` and asserts
the plot is scheduled, mirroring
`test_new_farmer_joining_mid_season_has_no_prompt_and_is_included_by_default`'s
own shape since both cases now collapse to the identical mechanism.

**`run_evening_confirmations`'s existing no-chat_id branch
(`watcher.py:662-672`) needs no change**: it already just skips
sending and leaves the `HarvestConfirmation` at `asked_at=None`
("unknown" forever), and nothing reads that confirmation's status to
*exclude* a plot from being scheduled in the first place — only from
follow-through-rate and late-reply ledger credit, both already-accepted
gaps for any existing unreachable farmer. Drying-window alerts and
advance-harvest notices (`watcher.py:482`, `558`) also need no change
for the same reason — they inform, they don't gate. **Considered and
rejected**: redirecting these safety-relevant alerts to the operator
instead of silently dropping them. Rejected for this part specifically
because the plot's daily presence on the operator's own route already
re-surfaces it to him every single day it remains unharvested — a
second, separate alert channel for the same fact would be new
machinery serving information the operator already receives by another
path. Flagged, not silently decided, in case a future pilot shows the
route alone isn't a strong enough signal in practice.

## Decision 17: consent and provenance — where a plot came from, in storage and in the equity report

**`Plot` gains three fields, all additive and backward-compatible:**

```python
# models.py
@dataclass(frozen=True)
class Plot:
    plot_id: str
    farmer_id: str
    cluster_id: str
    lat: float
    lon: float
    crop: str
    variety: str
    transplant_date: date
    area_acres: float
    area_unit: Literal["acre", "cent"] = "acre"
    village: str | None = None          # NEW -- see Context; None for
    # every row that predates this field, honestly "asked before this
    # existed" rather than "no village."
    registered_by: str = "self"         # NEW -- "self" or
    # f"operator:{operator_chat_id}". Default "self" is correct for
    # every row written before this part existed: self-registration was
    # the only path.
    registered_at: str | None = None    # NEW -- ISO timestamp. None for
    # every pre-existing row; self-registration starts setting this too
    # (small addition to _persist_completed_registration, not just the
    # new proxy path) so provenance is equally complete for both paths
    # going forward.
    retired_reason: str | None = None   # NEW -- set only by Decision 18's
    # link action, on the losing (duplicate) side of a link. Format
    # f"linked_to:{canonical_farmer_id}" -- a plain string, not a new
    # entity, deliberately embedding the one fact a reader or a future
    # dedupe check needs (which farmer_id is now canonical) rather than
    # a bare boolean. A plot with this set is excluded from scheduling
    # (Decision 18) but never removed from storage or from the equity
    # report -- the same "filter at read time, never delete" discipline
    # RouteOverride/rollover exclusion already use.
```

**`Farmer` gains only the informational contact note from Decision
13** — Decision 18, as revised below, needs no new `Farmer` field at
all: "has this proxy farmer been linked yet" is fully answered by the
existing `telegram_chat_id is None` check, and which plot lost a link
is recorded on the `Plot` side (`retired_reason`, above), not the
`Farmer` side.

```python
# models.py
@dataclass(frozen=True)
class Farmer:
    farmer_id: str
    name: str
    cluster_id: str
    telegram_chat_id: int | None = None
    language: Literal["ta", "en"] = "ta"
    contact_note: str | None = None      # NEW -- see Decision 13.
    # Never used to send a message.
```

No migration needed on either backend: both `FileStorage` and
`DynamoStorage` construct these dataclasses via `Farmer(**raw)`/
`Plot(**raw)` from a stored dict (`file_storage.py:93-115`,
`dynamo.py:155-195`) — a key absent from an old stored row simply falls
through to the new field's default. Verified by reading both
deserialization paths directly, not assumed.

**`equity_report.py`** gains a `proxy_registered_plot_ids: list[str]`
field on `SeasonEquitySection` (plus a matching acres figure, same
shape as the existing smallholder/larger split), computed by filtering
`plots` on `p.registered_by != "self"` — reusing the section's existing
"filter the current plot roster by a `Plot`-level predicate, render a
count-and-list block, fall back to a stated zero-case line" idiom
verbatim, no new rendering pattern. Rendered as its own paragraph:
count proxy-registered vs. self-registered, and *within*
proxy-registered, how many are still notification-less
(`telegram_chat_id is None`) versus since-linked — so a reader of the
report can see this feature's actual uptake and doesn't mistake a
notification-less plot's blank confirmation/rollover columns for
farmer non-responsiveness. That last distinction is the direct answer
to the concern this whole part exists to avoid creating quietly.

## Decision 18: linking — resolved on review, simpler than either option this ADR originally offered

**Neither Option A nor Option B, on review. The actual design: a
phone-less farmer who later gets a phone runs the existing, unchanged
four-message registration flow himself — no code, no new command, no
signal of any kind that he already has a record. This produces a real
duplicate: a fresh `farmer-{chat_id}`/`plot-{chat_id}` alongside the
still-standing `farmer-proxy-{hex}`/`plot-proxy-{hex}`. The operator —
who already knows both records exist, because he created one of them —
then links the two with a tap**, reusing the exact operator-tap shape
this codebase already builds and authorizes for `route_accept`,
`route_modify`, `route_swap`, `route_drop`, `route_drop_confirm`, and
`route_done`: an operator-only text command starts it
(`/linkfarmer` — text is fine here, it's operator-authored, the same
class of input as `/addfarmer`), everything after that is taps.

**No `/join` command, no new farmer-facing surface of any kind, no
free text from a farmer anywhere in this mechanism, and no general
merge primitive** — this does one fixed thing, for one fixed scenario,
with no reconciliation of conflicting field values: the *proxy*
record's plot details stay authoritative unconditionally. If the
farmer's self-registration reported a different transplant date or
area than the proxy record has on file, that correction is silently
lost — a real, disclosed limitation of not building a merge primitive,
stated plainly rather than glossed over, and acceptable at this scale
because the same operator who links the two records is also the
person standing closest to whichever field actually needs correcting
if it matters.

**The mechanism, concretely:**

1. `/linkfarmer` (operator-identity-checked, same as `/addfarmer`)
   lists this cluster's still-unlinked proxy farmers as tap targets —
   "unlinked" is `registered_by != "self" and telegram_chat_id is
   None`, derived at read time, no new field needed to track it. Empty
   list → a plain "nothing to link" reply, no further steps.
   (`linkfarmer_proxy:{proxy_farmer_id}`)
2. Tapping one lists the cluster's other farmers as the candidate
   match — every farmer in the cluster except proxy records and the
   one just picked, sorted most-recently-registered first (`registered_at`,
   Decision 17) since the intended match is almost always the farmer
   who *just* self-registered. (`linkfarmer_match:{proxy_farmer_id}:{candidate_farmer_id}`)
3. Picking a candidate shows an explicit two-name confirmation ("Link
   {proxy farmer's name} (registered by you) with {candidate's name}
   (self-registered {date})? This farmer's separate plot will stop
   being scheduled — {proxy farmer's name}'s existing plot continues,
   now reaching them directly on Telegram.") with Confirm/Cancel.
   (`linkfarmer_confirm:{proxy_farmer_id}:{candidate_farmer_id}:{yes|no}`)
4. On Confirm, three writes: `storage.put_farmer(replace(proxy_farmer,
   telegram_chat_id=candidate_farmer.telegram_chat_id))` — the proxy
   farmer_id stays canonical (it may already carry scheduling/ledger
   history the brand-new duplicate never had a chance to accumulate),
   now reachable. `storage.put_farmer(replace(candidate_farmer,
   telegram_chat_id=None))` — **necessary, not optional**: without
   clearing it, two `Farmer` records would claim the same
   `telegram_chat_id` at once, and `registration.py`'s own
   chat_id-based dedupe (needed regardless, to stop a second
   self-registration from the same phone creating yet another
   duplicate) would have no reliable way to pick which one is
   canonical on a future lookup. Clearing it here is what keeps that
   lookup a plain, unambiguous "find the one farmer with this
   chat_id" — no reference-following, no `retired_reason` parsing
   needed at re-registration time. The candidate's own plot is
   retired: `storage.put_plot(replace(candidate_plot,
   retired_reason=f"linked_to:{proxy_farmer_id}"))` — recorded for a
   human reading raw storage, even though the mechanism above no
   longer depends on reading it back. The candidate `Farmer` record
   itself is left in storage, now with no chat_id and no active plot —
   inert, never looked up again, kept rather than deleted for the same
   reason nothing in this project deletes records.
   On Cancel: nothing is written, message reverts.

**5. Revised on review (2026-08-24): a bounded-window Undo, not an
accepted "no recovery" gap.** On Confirm, the message is edited again —
not just cleared — to *"Linked. You can undo this for the next hour if
it was the wrong match."* with one button,
`linkfarmer_undo:{proxy_farmer_id}:{candidate_farmer_id}:{linked_at_epoch}`
(`linked_at_epoch`: whole Unix seconds — an ISO timestamp was tried
first and silently broke this callback's own colon-delimited parsing,
caught by testing the actual round trip, not just reasoning about the
shape). Tapping it (`proxy_registration.undo_link`) performs the exact
mirror of step 4's three writes: clears the proxy farmer's
`telegram_chat_id`, restores it onto the candidate farmer, and clears
the candidate plot's `retired_reason` — provided (a) less than
`LINKFARMER_UNDO_WINDOW` (1 hour, a `# TUNING:` judgment call, not
sourced) has passed, and (b) the candidate plot's `retired_reason`
still reads exactly `linked_to:{proxy_farmer_id}` — refusing rather
than guessing if either check fails (past the window, already undone,
or superseded by some other action in between). **This was worth
building, on reflection, precisely because it needed no new
machinery**: `retired_reason` already existed, `apply_link` never
deletes anything, and every fact `undo_link` needs to reverse a link is
either already sitting in storage or travels in the Undo button's own
callback_data — no new storage field, no in-memory state, the same
"everything /linkfarmer needs travels in callback_data" design the rest
of this decision already uses. What this does **not** do: retroactively
un-send any message, or unwind any scheduling decision, that already
happened under the merged identity during the window — an accepted,
disclosed limit (see Decision 20 failure path 5's revision), not a gap
this function silently papers over.

**Watcher-side enforcement**: plot selection for scheduling (both
`_run_daily_watch_one`'s normal trigger and `handle_machine_breakdown`'s
recompute — the same two call sites `_apply_rollover_exclusion`
already filters at) additionally excludes any plot with
`retired_reason is not None`, alongside the existing rollover
exclusion, not instead of it.

**If the operator never links them** — the honest, stated consequence:
the duplicate plot is a real, independent row in the schedulable pool,
and will be scheduled, notified, and potentially harvested exactly
like any other plot, under the wrong identity, until someone acts.
**This is deliberately accepted, not solved, for this pilot's scale**:
eight farmers per cluster means the same real person appearing as two
separate names on the operator's own route, or in the equity report,
is not a subtle signal — it is immediately, visibly obvious to the one
person already looking at that list every day, which is exactly why
this design is safe to ship without automatic duplicate detection.
**This does not hold at any meaningfully larger scale.** A cluster of
fifty or a hundred farmers, or an operator managing several clusters,
could easily miss a duplicate that reads as two unremarkable names in
a longer list, and the drop into equity-report-only visibility (a
report that has to be actively read, not a fact sitting in front of
the operator's face every morning) is exactly the shift from "visible
and correctable" to "silent" this document has argued against
elsewhere. **What would replace this at scale**: a real
duplicate-detection signal computed by the system, not left to human
memory — the most honest version is probably a same-cluster,
same-approximate-GPS-and-similar-name heuristic surfaced as a *proposal*
the operator confirms or dismisses (the identical "propose, never
gatekeep, human taps to act" shape this whole ADR already uses for
routes), rather than an automatic silent merge, which would reintroduce
exactly the "the LLM/system computes something that reaches a
scheduling decision without a human confirming it" risk this project's
core rule exists to prevent. Not built now — flagged as the concrete
next step, not a vague "revisit later."

## Decision 19: authorization

**Every new command and every new callback in this part is
operator-identity-checked**: `/addfarmer` and `/linkfarmer` both check
`chat_id == cluster.operator_chat_id` at the moment they're invoked
(Decision 13's shape), and every callback that follows —
`addfarmer_confirm:`, `linkfarmer_proxy:`, `linkfarmer_match:`,
`linkfarmer_confirm:` — checks the tapping identity
(`callback_query["from"]["id"]`) against the same field via the
existing `_is_operator` helper. No new authorization primitive
anywhere in this part; nothing here is farmer-facing, so `_is_farmer`
is never relevant to it.

**Added to ADR-012's audit table:**

| Prefix | Handler | Authorized against | Status |
|---|---|---|---|
| `addfarmer_confirm:` | `handle_addfarmer_confirm_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked from the start |
| `linkfarmer_proxy:` | `handle_linkfarmer_proxy_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked from the start |
| `linkfarmer_match:` | `handle_linkfarmer_match_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked from the start |
| `linkfarmer_confirm:` | `handle_linkfarmer_confirm_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked from the start |

(`/addfarmer` and `/linkfarmer` are text commands, not callback
prefixes, so they sit outside this specific table by the table's own
scope — same as `/operator <code>` today — but their authorization
rule is stated above and gets the identical wrong-party test
treatment.)

**Test, per your standing instruction**:
`test_addfarmer_from_non_operator_is_refused_and_starts_no_flow`,
`test_addfarmer_confirm_from_non_operator_is_refused_and_persists_nothing`,
`test_linkfarmer_from_non_operator_is_refused_and_starts_no_flow`,
`test_linkfarmer_confirm_from_non_operator_is_refused_and_links_nothing`
— construct a pending proxy registration (and, for the link tests, a
real proxy/duplicate pair), tap/text from a chat_id that isn't
`cluster.operator_chat_id`, assert the refusal, assert no
`Farmer`/`Plot` written or changed, no in-memory state created or
consumed.

## Decision 20: failure paths

**1. The operator runs `/addfarmer` twice for the same real farmer**
(forgot he already did it, or wants to fix a typo). **No correction or
duplicate-detection mechanism is built for this.** Each completed run
mints a fresh `farmer-proxy-{hex}`/`plot-proxy-{hex}` pair with no
collision against the first — this is an accepted, disclosed gap for
this pilot's scale, mitigated only by Decision 13's confirmation-
before-commit step catching the *in-flight* version of this mistake
(operator re-reads the summary, recognizes the farmer, cancels). A
committed duplicate has no fix path in this part beyond direct storage
editing — Decision 18's `/linkfarmer` mechanism links a proxy record to
a *self*-registered one, it has no path for two proxy records of the
same person, since neither side of that pair would ever pick up a
`telegram_chat_id` for the other to match against. Flagged, not
silently accepted without saying so: a future "list this cluster's
proxy-registered farmers, tap to edit or retire one" surface is the
natural next step if this proves to matter in practice.

**2. The proxy flow is interrupted mid-way** (operator's phone dies,
restarts the conversation, sends an unrelated message). Same accepted
limitation class as `registration.py`'s and `operator_enrollment.py`'s
own in-memory `_STATE_STORE`s: state does not survive a process
restart, and a stale in-progress flow is simply abandoned and
restarted from `/addfarmer` again with no special handling — identical
risk profile to the two existing flows this project has already
shipped with the same limitation. The same holds for `/linkfarmer`'s
own three-tap sequence.

**3. `/addfarmer` or `/linkfarmer` (or any of their callbacks) from a
non-operator.** Covered by Decision 19 — refused, logged, no mutation.

**4. A farmer who was proxy-registered phone-less later self-registers,
and the operator never runs `/linkfarmer`.** This is Decision 18's
accepted, disclosed outcome, not a bug: a second, real, independent
`farmer-{chat_id}`/`plot-{chat_id}` now exists alongside the proxy
record, both schedulable, until an operator tap resolves it. See
Decision 18's own "if the operator never links them" paragraph for the
full argument for why this is acceptable at pilot scale and what
replaces it if it stops being acceptable.

**5. The operator links the wrong pair** (picks a candidate farmer who
is not actually the same person as the proxy record). The explicit
two-name confirmation step (Decision 18, step 3) is the first guard
against this, and — **revised on review, 2026-08-24** — no longer the
only one: Decision 18's Undo button reverses the exact mistake within
`LINKFARMER_UNDO_WINDOW` (1 hour) of the tap, one more operator tap,
no new machinery. This does not make the mistake risk-free: past the
window, or if the operator doesn't notice within it, the wrongly-matched
candidate's own genuine plot stays retired and stops being scheduled —
still a real harm, and Undo also doesn't retroactively fix any message
already sent or schedule decision already made under the merged
identity during the window. What changed is the shape of the risk, not
its elimination: a mistake caught promptly is now fully, cheaply
recoverable, rather than requiring direct storage edits; a mistake
caught late still isn't. Flagged accordingly — the trust placed in the
operator's initial tap is unchanged, only the cost of getting it wrong
in the first hour has dropped.

**5. Weather/storage failures during the proxy flow.** Identical
treatment to `_persist_completed_registration`'s existing degrade
path — never raises, logs loudly, the operator still gets a completion
message (or, here, a failure message if the write itself failed), no
half-written `Farmer`-without-`Plot` state (both written together or
neither, matching the existing all-or-nothing framing already in place
for self-registration's own persistence step).

## Decision 21: Tamil strings (drafts, for your review before this ships)

Same `print_tamil_strings.py` discipline as every string in this
project — first drafts only, nothing final until reviewed here and
(per ADR-008 Decision 17) receipted on a real device.

- `/addfarmer` usage/refusal: *"இந்தக் கட்டளையை ஆபரேட்டர் மட்டுமே
  பயன்படுத்த முடியும்."* ("Only the operator can use this command.")
- Farmer-name prompt: *"விவசாயியின் பெயர் என்ன?"* ("What is the
  farmer's name?")
- Contact-note prompt (optional): *"தொடர்பு எண் அல்லது குறிப்பு
  (இருந்தால்) தட்டச்சு செய்யவும், இல்லையெனில் 'skip' என தட்டச்சு
  செய்யவும்."* ("Type a contact number or note if there is one,
  otherwise type 'skip'.")
- Has-phone question: *"இந்த விவசாயிக்கு டெலிகிராம் உள்ள மொபைல் போன்
  உள்ளதா?"* ("Does this farmer have a mobile phone with Telegram?")
- Confirm-before-commit summary line: *"{name}, {village}, {area},
  நடவு தேதி {date} — பதிவு செய்யவா?"* ("{name}, {village}, {area},
  transplanted {date} — register?")
- Notification-less registration's closing note to the operator (not
  the farmer — the farmer never receives a message here at all):
  *"பதிவு முடிந்தது. {name}-க்கு தொலைபேசி இல்லாததால், அவருக்கான
  தகவல்களை நீங்கள் நேரடியாகத் தெரிவிக்க வேண்டும்."* ("Registration
  complete. Since {name} has no phone, you'll need to inform them
  directly.")
- `/linkfarmer` with nothing to link: *"இணைக்க எந்த விவசாயியும்
  இல்லை."* ("There is no farmer to link.")
- `/linkfarmer` step 2 confirmation prompt: *"{proxy_name}-ஐ
  {candidate_name}-உடன் இணைக்கவா? {candidate_name}-ன் தனி வயல் இனி
  பட்டியலிடப்படாது."* ("Link {proxy_name} with {candidate_name}?
  {candidate_name}'s separate plot will no longer be scheduled.")
- `/linkfarmer` success toast: *"இணைக்கப்பட்டது."* ("Linked.")
- Undo button (added 2026-08-24): *"↩️ செயல்தவிர்"* ("↩️ Undo")
- Linked-with-undo-offer text: *"இணைக்கப்பட்டது. தவறான பொருத்தமாக
  இருந்தால், அடுத்த ஒரு மணி நேரத்திற்குள் இதைத் திரும்பப் பெறலாம்."*
  ("Linked. You can undo this for the next hour if it was the wrong
  match.")
- Undo expired: *"திரும்பப் பெற தாமதமாகிவிட்டது -- ஒரு மணி
  நேரத்திற்கும் மேலாகிவிட்டது."* ("Too late to undo -- more than an
  hour has passed.")
- Undo failed (stale/superseded): *"திரும்பப் பெற முடியவில்லை -- இந்த
  இணைப்பு ஏற்கனவே மாறியிருக்கலாம்."* ("Couldn't undo -- this link may
  have already changed.")
- Undone toast: *"திரும்பப் பெறப்பட்டது -- எதுவும் இணைக்கப்படவில்லை."*
  ("Undone -- nothing is linked.")

English equivalents ship alongside in `messages_en.py`, same shape as
every prior part.

## Tests

`tests/test_proxy_registration.py` (new): the full
`ProxyRegistrationState` transition sequence including the three new
steps (name, contact note, has-phone); `/addfarmer` authorization
(Decision 19's two tests); `AWAITING_CONFIRM`'s Cancel path writes
nothing; both a has-phone=yes and a has-phone=no completed registration
leave `Farmer.telegram_chat_id=None` and differ only in the operator's
closing message text, proven directly rather than just asserted in
prose, since that answer changes no persisted field.
`route_override_status`-style unit tests for `village`/`registered_by`/
`registered_at`/`retired_reason` defaulting on old rows;
`tests/test_link_farmer.py` (new, detailed above, plus Undo's revision:
a successful link offers the button; undo within the window fully
reverses all three fields; undo past the window is refused and changes
nothing; a second undo of an already-undone link is refused, proving
the first one genuinely took effect rather than silently no-op'ing
both times; undo from a non-operator is refused; `undo_link` refuses
when the candidate's plot has been superseded by a different link in
the meantime); `test_watcher.py`
additions for Decision 16 (the rewritten no-chat_id rollover test, plus
a full-season regression proving a proxy-registered, notification-less
plot is scheduled normally both this season and next) and for retired-
plot exclusion at both scheduling call sites; `test_registration.py`
additions for the chat_id-based dedupe fix, including the resurrection
case (detailed above); `test_equity_report.py` addition for the new
proxy-registration section, including the notification-less-vs-linked
sub-split.

## Consequences

- **The rollover-exclusion fix (Decision 16) changes behavior for any
  existing farmer with `telegram_chat_id=None`, not just new proxy
  registrations** — before this part, such a farmer (however that
  state arose) was excluded from next season by default; after, he is
  included by default, same as any other never-asked plot. No
  production farmer is known to be in this state today (the only route
  to it before this part was a seed script or manual edit), so this is
  a correction with no known present-day behavioral cost, not a live
  regression risk — stated plainly rather than assumed.
- **Two disjoint, permanent ID namespaces now coexist**
  (`farmer-{chat_id}` and `farmer-proxy-{hex}`), distinguishable by
  construction, doubling as a zero-cost provenance signal alongside the
  explicit `registered_by` field.
- **A phone-less farmer's plot is scheduled, harvested, and reported on
  exactly like any other** — the only thing that doesn't happen for him
  is any Telegram message, ever, which the equity report now surfaces
  explicitly rather than leaving indistinguishable from ordinary farmer
  silence.
- **Decision 18's linking mechanism is explicitly a pilot-scope answer,
  not the permanent design** — it depends entirely on an operator
  noticing a duplicate himself, with no automated detection. Stated
  plainly in Decision 18: this does not hold at any meaningfully larger
  scale, and what would replace it (a system-proposed, operator-confirmed
  duplicate match, never an automatic silent merge) is sketched there,
  not built here.
- **A wrong link is now correctable, not just visible** (revised
  2026-08-24): `apply_link`'s effects were always confined to three
  plain fields with nothing deleted, which made a same-shape reversal
  cheap enough to build rather than defer — one more operator tap,
  `LINKFARMER_UNDO_WINDOW` (1 hour) to notice, no new storage entity, no
  in-memory state. Past that window, or if a message already went out
  under the merged identity, the mistake's cost is unchanged from the
  original design — Undo narrows the risk window, it does not remove it.
- Four additive, backward-compatible schema widenings
  (`Plot.village`, `Plot.registered_by`, `Plot.registered_at`,
  `Plot.retired_reason`) and one on `Farmer` (`contact_note`) — every
  existing row and every existing test's assertions are unaffected,
  verified against both backends' deserialization paths directly.
- No general-purpose role system built; no new farmer-facing surface
  and no farmer-initiated free text anywhere in this part — every new
  command (`/addfarmer`, `/linkfarmer`) is operator-authored, and every
  farmer-facing interaction is either unchanged (the existing
  four-message flow) or nonexistent (a notification-less farmer).
- Nothing here touches the deployed AgentCore artifact.

## Sequence (Part 2)

**Approved 2026-08-23, with Decision 18 replaced by the simpler
operator-tap-linking design above** (see "Resolved on review" below).
Single commit for the whole part (no prerequisite bug this time, unlike
Part 1) — schema widenings, `proxy_registration.py` (`/addfarmer` +
`/linkfarmer` + both callback flows), the Decision 16 rollover fix and
its rewritten test, the chat_id-dedupe generalization in
`registration.py` (including the retired-plot resurrection guard),
equity report section, Tamil strings printed for review — commit, stop,
report.

## Resolved on review (2026-08-23)

**Decision 18 replaced, not chosen between.** The original draft
offered two options (a bounded `/join <code>` farmer command, or a
fully operator-mediated merge) and asked for a call between them. On
review, neither was adopted: the actual design needs no code and no
merge primitive at all — the farmer just uses the existing,
unmodified four-message flow, producing a visible duplicate, and the
operator links it with a tap, the same authorization shape already
built six times over for route overrides. Recorded here as a
correction to this document's own framing (it presented two options
when a simpler third was available), not as an idea originating from
either option offered. Explicitly recorded in Decision 18 as a
pilot-scope answer, with its scale limit and replacement sketched
rather than left implicit.

Decisions 12, 13, 14, 15, 17, 19, 20 approved as originally written.
Decision 16 approved with emphasis: the rewritten test must show the
correction, not merely pass. The discarded `village` field is
persisted as proposed, kept flagged as its own independent finding
rather than folded silently into "the same four fields."

## Resolved on review (2026-08-24)

**Decision 18's "no undo is built" was reconsidered, after the Part 2
commit landed, and reversed.** The original framing treated recovery
from a wrong `/linkfarmer` match as out of scope on the theory that
real recovery would need a merge primitive this pilot shouldn't build.
Checked directly rather than assumed: `apply_link` never deletes
anything and touches exactly three plain fields across two `Farmer`
records and one `Plot`, so reversing it is the same three writes run
backward, not a new primitive. Built as a bounded-window Undo button
(`LINKFARMER_UNDO_WINDOW`, 1 hour) attached to the message right after
a successful link. One real bug surfaced during implementation, not
just review: the first version embedded the link timestamp as an ISO
string in the Undo button's callback_data, which contains colons and
silently broke that callback's own colon-delimited parsing — caught by
testing the actual round trip end to end, not by re-reading the code.
Fixed by using whole Unix seconds instead. Decision 20's failure path 5
and the Part 2 Consequences are updated to describe the risk Undo
actually narrows (a mistake caught within the hour is now cheaply
recoverable) rather than the risk it does not touch (a mistake caught
late, or a message already sent under the merged identity during the
window) — recorded as a real remaining limit, not implied away by the
fact that undo now exists.

---

# Part 3: "Why not my plot?" — a farmer's own reachable answer

## Context

A farmer told `not_ready` ("your crop isn't ready, do nothing") while
he can see the neighbour's field being cut has no way to ask why, and
no reason to trust that the system looked at his plot at all. A farmer
told `escalation_resolved_lost` ("today's machine is going to X's plot
instead") gets one sentence of reason and no way to hear it again or
see it laid out more plainly. Both are the moment this project's whole
trust story is won or lost in the first week, and today neither message
offers any way back into the reasoning behind it.

`scripts/explain_decision.py` (ADR-010 Part 1) already reconstructs
exactly this kind of answer — from `DecisionRecord`, never recomputed —
but it's a CLI script an auditor runs with AWS/repo access, not
something a farmer sitting with a phone can reach. This part gives two
of that script's farmer-relevant facts a one-tap path to the person who
actually needs them, without exposing the rest of what that script
prints (round counts, urgency scores, threshold source, capacity
formulas) — a farmer needs an answer, not the audit.

Read `scripts/explain_decision.py`, `storage/interface.py`'s
`DecisionRecord`, `agents/contracts.py`'s `AdvocateClaim`, and
`telegram/notify.py`/`messages_en.py`/`messages_ta.py`'s `not_ready`,
`escalation_resolved_lost`, and `resolution_reason` before implementing
this — the whole point is to reuse what those already compute and
render, not to build a second, independently-worded copy of either.

## Decision 22: where the button attaches, and where it doesn't

One button, added to exactly two message types:

- **`not_ready`** (`watcher.py`, `PlotOutcome.TOO_GREEN`) — every time
  it's sent, every trigger day the plot stays too green. Each day's
  message gets its own button, pinned to that day's own record (Decision
  25) — a farmer who's been not-ready for a week can tap any of the
  week's messages and get that day's honest answer, not today's.
- **`escalation_resolved_lost`** (`webhook.py`, after a human resolves a
  contested pair) — the loser's copy only. The winner's
  `escalation_resolved_won` gets no button; "why did I win" isn't the
  failure mode this part exists to prevent, and a button that always
  answers "you had the stronger claim" teaches a farmer nothing he
  doesn't already know from winning.

No button on `harvest_scheduled`, `advance_harvest_notice`,
`drying_window_alert`, `route_dropped_notice`, or any registration/
rollover/confirmation prompt — none of those carry a "why not" question
this project can honestly answer from a `DecisionRecord`, and CLAUDE.md's
no-new-farmer-surface rule means a button is added only where a bounded,
one-tap answer genuinely exists, not speculatively.

## Decision 23: stored records only — no live weather call, no LLM, and why that's a real constraint here, not a formality

The temptation, for `not_ready`, is to answer "how far is my crop from
ready" with a projected date — `agronomy/calibration.py`'s
`project_maturity_for_plot()` already does exactly that, and it's
already farmer-facing wording (`projected_maturity_sentence`,
`advance_harvest_notice`). **Rejected for this feature.**
`project_maturity_for_plot()` makes a live Open-Meteo call and computes
a fresh projection at whatever moment it's called — which means a tap
today and a tap tomorrow on the *same, already-sent* message would
silently return two different answers to "why wasn't my plot ready,"
neither one being what was actually true at decision time. That's
exactly the failure `explain_decision.py`'s own rule exists to prevent
("a replay must never reconstruct a plausible past; it must report the
recorded one") — recomputing at read time instead of reporting what was
already decided, just with arithmetic instead of a model. This part
answers "why did today's message say what it said," not "what does the
weather look like right now" — the second question isn't being asked,
and answering it anyway would be dishonest even though the wording
"sounds right."

Everything both answers use is already sitting in a `DecisionRecord`,
written at decision time by `coordinator.run_cluster_with_claims`
(too_green case: `accumulated_gdd`, `maturity_gdd_used`,
`rain_threshold_mm`, `usable_harvest_days`, `capacity_budget_acres`,
`machine_capacity_acres_per_day`; lost-escalation case: those plus
`opponent_plot_id`, `own_claim`, `opponent_claim` — both
`AdvocateClaim.model_dump()` dicts frozen the moment the negotiation
ran). No new `Storage` entity, no new write path — this part is
strictly read-only, like Parts 1–3 of ADR-010.

**The lost-escalation reason is the *exact same* function call the
original message used**, not a re-worded copy: `messages_en.py`/
`messages_ta.py`'s `resolution_reason(bumped_winner, bumped_loser,
winner_days_past_maturity, loser_days_past_maturity)` is called again
here, fed from the loser's own `DecisionRecord.own_claim`/
`opponent_claim` dicts instead of the live `AdvocateClaim` objects
`webhook.py` had in hand at resolution time — same four fields, same
function, same wording, guaranteed identical rather than
independently-drafted-to-look-similar. This is the literal mechanism
behind "the same honest wording already used in the loser message."

## Decision 24: a new module, not a repurposing of `explain_decision.py`

```python
# telegram/farmer_why.py
"""Farmer-facing "why?" answers -- ADR-013 Part 3. Assembled entirely
from a DecisionRecord already written at decision time (ADR-010 Part
0.5) -- never a live call, never a model, never a recomputed projection.
Not explain_decision.py's audience or its output shape: that script is
the full audit trail for an auditor with repo/AWS access; this module
returns exactly one short, already-localized answer for the farmer who
asked, reusing the same underlying data and (for the lost-escalation
case) the literal same resolution_reason() call, not a re-derived
narrative.
"""

def _record_or_none(
    storage: Storage, plot_id: str, season_id: str, decision_date: str,
) -> DecisionRecord | None:
    if not decision_date:  # Decision 26, Gap B: no date was ever known
        return None
    return storage.get_decision_record(plot_id, season_id, decision_date)


def _formatted_date(mod, decision_date: str) -> str | None:
    if not decision_date:
        return None
    try:
        return mod.format_date(date.fromisoformat(decision_date))
    except ValueError:
        return None


def why_not_ready_text(
    storage: Storage, plot_id: str, season_id: str, decision_date: str, *, language: str = "ta",
) -> str:
    mod = notify._lang_module(language)
    formatted_date = _formatted_date(mod, decision_date)
    record = _record_or_none(storage, plot_id, season_id, decision_date)
    if record is None or formatted_date is None:
        return mod.why_not_recorded(formatted_date)
    # Capped at 99: a TOO_GREEN record's accumulated_gdd is always below
    # maturity_gdd_used by construction (scheduling/solver.py), but a
    # rounding artifact that happens to land on 100 would read as
    # "fully grown -- but not ready," a real self-contradiction for a
    # farmer to notice. Presentation-only cap, not a change to the
    # underlying stored numbers.
    pct_grown = min(99, round(record.accumulated_gdd / record.maturity_gdd_used * 100))
    return mod.why_not_ready_answer(
        formatted_date, pct_grown, record.capacity_budget_acres, record.usable_harvest_days,
    )


def why_lost_text(
    storage: Storage, plot_id: str, season_id: str, decision_date: str, *, language: str = "ta",
) -> str:
    mod = notify._lang_module(language)
    formatted_date = _formatted_date(mod, decision_date)
    record = _record_or_none(storage, plot_id, season_id, decision_date)
    if (
        record is None or formatted_date is None
        or record.own_claim is None or record.opponent_claim is None
    ):
        return mod.why_not_recorded(formatted_date)
    winner_plot = storage.get_plot(record.opponent_plot_id) if record.opponent_plot_id else None
    winner_farmer = storage.get_farmer(winner_plot.farmer_id) if winner_plot else None
    winner_name = winner_farmer.name if winner_farmer else mod.DEFAULT_WINNER_LABEL
    reason = mod.resolution_reason(
        bumped_winner=record.opponent_claim["bumped_last_season"],
        bumped_loser=record.own_claim["bumped_last_season"],
        winner_days_past_maturity=record.opponent_claim["days_past_maturity"],
        loser_days_past_maturity=record.own_claim["days_past_maturity"],
    )
    return mod.why_lost_answer(formatted_date, winner_name, reason)
```

`why_not_recorded(formatted_date)` is the one honest answer for every
gap this part can hit — a decision from before ADR-010 Part 0.5 shipped
(2026-08-19), a `DecisionRecord` write that failed that day, or
(lost-escalation only) an escalation payload lost to a process restart
with no `decision_date` ever recorded for this button (Decision 26).
Not distinguished by cause in the farmer-facing answer — a one-tap
terminal answer isn't the place for `explain_decision.py`'s
cause-by-cause audit language; "not recorded, we won't guess" is the
whole honest truth a farmer needs here, matching CLAUDE.md's instruction
directly. **`formatted_date` is threaded in and stated up front on every
answer** (`why_not_ready_answer`, `why_lost_answer`, and `why_not_
recorded` itself when a date is known) — reconsidered on review
(2026-08-24): a pure read is safe against a repeat tap by construction
(nothing to corrupt), but a farmer tapping the same message a week later
would otherwise read a dateless answer as news about today rather than
a record of that day's decision. Naming the date up front removes the
ambiguity outright rather than trusting a farmer to infer it from
context he may not remember.

## Decision 25: the not-ready answer, and the lost answer, in full

```python
WHY_BUTTON_LABEL = "❓ Why?"

def why_not_recorded(formatted_date: str | None = None) -> str:
    if formatted_date:
        return (
            f"We don't have a record of {formatted_date}'s decision to "
            f"look back on, so we can't reconstruct why -- we won't guess."
        )
    return (
        "We don't have a record of that day's decision to look back on, "
        "so we can't reconstruct why -- we won't guess."
    )

def why_not_ready_answer(
    formatted_date: str, pct_grown: int, capacity_budget_acres: float, usable_harvest_days: int,
) -> str:
    day_word = "day" if usable_harvest_days == 1 else "days"
    return (
        f"On {formatted_date}: your crop had reached about {pct_grown}% "
        f"of the growth it needs before harvest -- that's why it wasn't "
        f"ready that day. (For reference: that day the machine's total "
        f"capacity across {usable_harvest_days} good {day_word} was "
        f"about {capacity_budget_acres:.1f} acres -- this didn't affect "
        f"your plot, which wasn't ready regardless.)"
    )

def why_lost_answer(formatted_date: str, winner_name: str, reason: str) -> str:
    return f"On {formatted_date}, the machine went to {winner_name}'s plot instead -- {reason}."
```

**Reconsidered on review (2026-08-24), both points below — the original
draft put the percent-grown and capacity sentences side by side as two
independent facts.** On review: a farmer whose crop is genuinely
too-green, watching a neighbour's field get cut, must come away
understanding *his crop wasn't ready* — not that "the machine was busy."
Structurally, a `TOO_GREEN` plot was never in the machine's capacity
pool to begin with (`scheduling/solver.py` excludes it before capacity
allocation runs at all), so a capacity number sitting next to the
percent-grown sentence with equal weight would let a farmer read "you
lost the queue" — false, and worse for trust than saying nothing, per
your instruction. Fixed two ways: the capacity clause is now explicitly
parenthetical and subordinate ("for reference... this didn't affect
your plot, which wasn't ready regardless"), and `formatted_date` opens
every answer (`why_not_ready_answer`, `why_lost_answer`, and `why_not_
recorded` when a date is known) so a farmer tapping an old message days
later reads a dated record of a past decision, not fresh news about
today. `capacity_budget_acres` itself is still the cluster's
whole-machine budget for that trigger day, not this farmer's own
acreage — worded as "the machine's total capacity," never "your plot
could have used," so it can't be misread as a promise specific to him
even in its now-clearly-secondary position.

Tamil (drafts, for your review before this ships — printed via
`scripts/print_tamil_strings.py` at implementation, same as every prior
part):

```python
WHY_BUTTON_LABEL = "❓ ஏன்?"

def why_not_recorded(formatted_date: str | None = None) -> str:
    if formatted_date:
        return (
            f"{formatted_date} அன்றைய முடிவு பதிவு செய்யப்படவில்லை, "
            "எனவே காரணத்தை மீண்டும் கூற முடியாது -- நாங்கள் யூகிக்க "
            "மாட்டோம்."
        )
        # "{date}'s decision was not recorded, so we cannot restate the
        #  reason -- we will not guess."
    return (
        "அந்த நாளின் முடிவு பதிவு செய்யப்படவில்லை, எனவே காரணத்தை "
        "மீண்டும் கூற முடியாது -- நாங்கள் யூகிக்க மாட்டோம்."
    )
    # "That day's decision was not recorded, so we cannot restate the
    #  reason -- we will not guess."

def why_not_ready_answer(formatted_date, pct_grown, capacity_budget_acres, usable_harvest_days):
    # _day_word(n, locative=True) -- a fully-formed word per case
    # (நாளில்/நாட்களில்), not string concatenation. See "Resolved on
    # review" below for the bug this replaced.
    day_word_locative = _day_word(usable_harvest_days, locative=True)
    return (
        f"{formatted_date}: உங்கள் பயிர் அறுவடைக்குத் தேவையான "
        f"வளர்ச்சியில் சுமார் {pct_grown}% ஐ எட்டியிருந்தது -- அன்று "
        f"தயாராக இல்லாததற்கு அதுவே காரணம். (குறிப்புக்கு: அன்று "
        f"இயந்திரத்தின் மொத்த திறன், {usable_harvest_days} நல்ல "
        f"{day_word_locative}, சுமார் {capacity_budget_acres:.1f} ஏக்கர் -- "
        f"இது உங்கள் வயலைப் பாதிக்கவில்லை, அது எப்படியிருந்தாலும் "
        f"தயாராக இருக்கவில்லை.)"
    )
    # "{date}: your crop had reached about {pct}% of the growth needed
    #  for harvest -- that is why it was not ready that day. (For
    #  reference: that day the machine's total capacity, across {n} good
    #  days, was about {x} acres -- this did not affect your plot, which
    #  was not ready regardless.)"

def why_lost_answer(formatted_date, winner_name, reason):
    return f"{formatted_date} அன்று, இயந்திரம் {winner_name} உடைய வயலுக்குச் சென்றது -- {reason}."
    # "On {date}, the machine went to {winner_name}'s plot instead --
    #  {reason}."
```

## Decision 26: `callback_data` carries the exact record to look up — never re-resolved at tap time

`(plot_id, season_id, decision_date)` is the exact primary key
`get_decision_record` needs, and both call sites already have all three
in hand *at the moment the message is sent* — so all three travel in the
button's own `callback_data`, the same "no in-memory state, nothing to
lose on restart" shape `/linkfarmer` established in Part 2:

```
why_notready:{plot_id}:{season_id}:{decision_date}
why_lost:{plot_id}:{season_id}:{decision_date}
```

No field here can contain a colon (plot IDs, season IDs, and ISO dates
never do), so this doesn't risk the colon-splitting bug Part 2's Undo
button hit — noted explicitly because that bug was found by testing,
not by inspection, and the fix there was "don't put timestamps in
`callback_data`," which this design already avoids by construction.

**`not_ready`**: `watcher.py`'s `_send_notifications` already has
`today` (the trigger date) and `season_id` in scope at the call site —
threaded straight into `notify.send_not_ready` as new `season_id`/
`decision_date` keyword parameters (used only to build the button's
`callback_data`; `build_not_ready_text` itself, and the message text it
produces, are unchanged). No lookup, no gap — every `not_ready` message
sent after this ships carries a working button.

**`escalation_resolved_lost`**: `webhook.py`'s resolution handler has
`escalation.decision_date` (`EscalationPayload`'s field, ADR-010
Decision C) whenever `escalation is not None` — the ordinary case.
**Gap B**: if the process restarted between the escalation being sent
and being resolved, `_PENDING_ESCALATIONS` lost the entry, `escalation
is None`, and — as already true today — the loser's message loses its
specific reason clause. `decision_date` is genuinely unrecoverable at
this call site in that branch (nothing stores it outside the popped
in-memory payload); guessing `date.today()` would be reconstructing,
not reporting, exactly what Decision 23 refuses to do. **Decided: omit
the button entirely on this path**, rather than attach one that would
deterministically answer "not recorded" every time it's tapped. This is
different from the pre-Part-0.5 gap (Decision 24's `why_not_recorded`),
where the button *is* shown and answers honestly — there, a real record
might exist and the farmer deserves the chance to ask; here, the answer
is already known to be unreachable before the message is even sent, so
offering a dead-end tap teaches a farmer to distrust the button itself.
Same disclosed limitation class as ADR-005/ADR-009/ADR-010 Decision D's
existing "ordinary today's answer".

## Decision 27: authorization — the farmer who owns the plot, checked first

Same shape as `confirm:`/`rollover:` (ADR-012): the tapping identity
must match the plot's own farmer, via `telegram_chat_id`, checked before
any lookup runs.

```python
def parse_why_callback_data(data: str, expected_prefix: str) -> tuple[str, str, str] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != expected_prefix:
        return None
    _, plot_id, season_id, decision_date = parts
    return plot_id, season_id, decision_date


def _handle_why_callback(
    client: TelegramClient, callback_query: dict, storage: Storage, *, prefix: str, lost: bool,
) -> None:
    # handle_why_notready_callback / handle_why_lost_callback are thin
    # public wrappers around this, each fixing prefix/lost -- one shared
    # body, matching the "why_notready:"/"why_lost:" split at dispatch.
    callback_query_id = callback_query.get("id", "")
    parsed = parse_why_callback_data(callback_query.get("data", ""), prefix)
    if parsed is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    plot_id, season_id, decision_date = parsed
    plot = storage.get_plot(plot_id)
    farmer = storage.get_farmer(plot.farmer_id) if plot is not None else None
    if plot is None or farmer is None:
        client.answer_callback_query(
            callback_query_id, notify._lang_module("ta").unrecognized_action(), show_alert=True,
        )
        return
    if not _is_farmer(callback_query, farmer):
        # ADR-012: the tapping identity must be the plot's own farmer --
        # this is the third farmer-owned callback class after
        # confirm:/rollover:, and gets the exact same check, checked
        # before any record lookup, not after.
        client.answer_callback_query(
            callback_query_id, notify._lang_module(farmer.language).unrecognized_action(), show_alert=True,
        )
        return
    client.answer_callback_query(callback_query_id)  # Telegram requires an answer either way
    build_text = farmer_why.why_lost_text if lost else farmer_why.why_not_ready_text
    client.send_message(
        farmer.telegram_chat_id, build_text(storage, plot_id, season_id, decision_date, language=farmer.language),
    )
```

Two new rows for ADR-012's audit table, added at implementation:
`why_notready:` → `handle_why_callback(lost=False)`, `why_lost:` →
`handle_why_callback(lost=True)`, both `_is_farmer`, both "Checked from
the start."

## Decision 28: one tap, one answer — mechanics

- The answer is sent as a **new message** (`client.send_message`), not a
  callback-query alert popup — Telegram caps an alert at 200 characters,
  and the not-ready answer (percent-grown sentence plus capacity
  sentence) doesn't reliably fit. `answer_callback_query` is still
  called with no text, purely to satisfy Telegram's requirement that
  every callback query be acknowledged (clears the tap's loading spinner).
- **The button is never removed after a tap.** Every other keyboard in
  this codebase clears itself after a tap because the tap *did*
  something (resolved an escalation, linked a farmer, confirmed a
  harvest) and a second tap would be a double-mutation risk. This tap
  mutates nothing — `farmer_why`'s functions are pure reads — so a
  second, third, or tenth tap on the same button is not a "double-tap"
  failure mode at all, just the same honest answer read again. Explicit
  design choice, not an oversight: nothing here needed the double-tap
  guard every prior part in this ADR had to build.
- **No follow-up is possible by construction.** The answer is one
  message with no keyboard of its own; there is nothing further for the
  farmer to tap or type in response. If a real farmer's reaction to this
  answer turns out to need a reply (a factual correction, a dispute), that
  is new scope this ADR does not cover and CLAUDE.md requires it be
  raised as its own decision, not built quietly into this tap.

## Decision 29: failure paths

| Case | Behavior |
|---|---|
| Non-owning farmer / stranger taps | Refused via `_is_farmer`, before any lookup (Decision 27). |
| Malformed or tampered `callback_data` | `parse_why_callback_data` returns `None`, generic refusal, logged. |
| `plot_id` doesn't resolve (shouldn't happen — plots are never deleted) | Generic refusal, logged at `ERROR` as a data-consistency check. |
| No `DecisionRecord` for the exact `(plot_id, season_id, decision_date)` — pre-Part-0.5, or a same-day write failure | `why_not_recorded()` — honest, not a crash, not a guess (Decision 24). |
| Escalation payload lost to a process restart before resolution (Gap B) | Button omitted at send time — see Decision 26. Nothing to fail at tap time, because there is no tap available. |
| Double-tap / repeated tap | Answered identically every time — read-only, no dedup needed (Decision 28). |
| Restart between send and tap | No effect — everything needed travels in `callback_data` plus `Storage`, no in-memory state to lose, same property `/linkfarmer`'s three-tap flow already has. |
| A farmer merged into another record by `/linkfarmer` (Part 2) taps an old button sent before the merge | The merged-away `Farmer.telegram_chat_id` is now `None` (Part 2, `apply_link`) — `_is_farmer` fails closed, refusing even the original recipient's own chat. Disclosed, not fixed here: rare (`/linkfarmer` runs at most a handful of times per cluster), and the same tradeoff Part 2 already accepted for provenance over convenience at pilot scale. |
| Weather/network unavailable | Not applicable by construction — this part makes no live calls (Decision 23). |
| No `chat_id` for the farmer | Not applicable — a farmer with no reachable Telegram identity cannot have tapped a button in Telegram in the first place. |

## Tests (`tests/test_farmer_why.py`, plus `tests/test_watcher.py`/`tests/test_webhook.py` additions)

`why_not_ready_text`: a `DecisionRecord` present renders the correct
percent (including the 99%-cap case, constructed with
`accumulated_gdd == maturity_gdd_used`) and capacity sentence; no record
renders `why_not_recorded()` byte-for-byte; `decision_date=""` short-
circuits without a `Storage` call (Gap B shape, reused for symmetry even
though `not_ready`'s own send path never produces an empty date).
`why_lost_text`: a full escalated-and-lost record renders the same
`winner_name`/`reason` a direct call to `resolution_reason()` with the
same claim fields would produce (byte-identical, asserted directly, not
just "looks similar"); missing `opponent_plot_id`/claim data each
independently fall back to `why_not_recorded()`; no `DecisionRecord`
falls back the same way.

`handle_why_callback`: real owning farmer's tap succeeds and sends the
expected text, for both `lost=True` and `lost=False`; a non-owning
farmer's tap is refused, `send_message` never called, matching
`test_farmer_authorization.py`'s existing pattern; malformed
`callback_data` refused; unresolvable `plot_id` refused and logged;
tapping twice sends the same answer twice (proving no unintended dedup
was added); `answer_callback_query` is called on every path, including
refusals (Telegram-correctness, not a business rule).

`watcher.py`: a `TOO_GREEN` outcome's `send_not_ready` call now carries
today's `decision_date`, asserted against the built keyboard's
`callback_data`. `webhook.py`: the resolved-escalation happy path
attaches a working `why_lost:` button with the escalation's real
`decision_date`; the `escalation is None` (Gap B) path sends the loser's
message with **no** `why_lost:` button in its `reply_markup` — asserted
directly, not just "doesn't crash."

No-Bedrock assertion for the whole module, same as every prior report/
explain path in this project.

## Consequences

- A farmer told "not ready" or "you lost" now has a bounded, one-tap way
  to hear the actual reason on record, in his own language, without
  starting a conversation the system can't sustain.
- The reason for a loss is now guaranteed byte-identical to what the
  original message already said, because it's the same function call —
  not a second, independently-worded narrative that could quietly drift
  from the first over time.
- Nothing here recomputes or projects anything live — every answer is a
  direct read of a `DecisionRecord` already written at decision time,
  same discipline as `explain_decision.py`, extended to the one audience
  that script was never reachable by.
- A decision made before 2026-08-19 (ADR-010 Part 0.5), or lost to a
  write failure, now has an honest, farmer-facing "not recorded" answer
  instead of no way to ask at all.
- One real, disclosed gap remains open, not fixed here: an escalation
  resolved after a process restart loses both its specific reason
  *and*, now, its "why" button — a farmer in that situation is no worse
  off than today, but no better off either. Acceptable at this project's
  restart frequency; would need the escalation payload itself persisted
  to close, which is out of scope for this part.
- Two new farmer-owned callback prefixes (`why_notready:`, `why_lost:`)
  join ADR-012's audit table at implementation, both authorization-
  checked from the first commit, extending rather than breaking that
  table's own discipline.

## Sequence (Part 3)

Implement `telegram/farmer_why.py`, the `decision_date` threading in
`watcher.py`/`notify.py`, the two new callback handlers and their audit-
table rows in `webhook.py`/ADR-012, full test coverage, run the whole
suite — commit, then stop and report, with the Tamil dump (this part's
new strings, printed via `scripts/print_tamil_strings.py`) delivered as
a file path, per your standing instruction.

## Resolved on review (2026-08-24)

Two checks requested before implementation, both addressed in the code
as shipped, not just reasoned about in the abstract:

**Repeat taps days later.** A pure read is safe against corruption by
construction — nothing to double-write. But safety from corruption
isn't the same as clarity for the reader: a farmer tapping an old
`not_ready` or `escalation_resolved_lost` message a week on, with no
date in the answer, could read a fresh-arriving message as news about
today. Fixed by threading `formatted_date` (`messages_*.format_date`,
already used everywhere else a date reaches a farmer) into every "why"
answer, opening every one of them — `why_not_ready_answer`,
`why_lost_answer`, and `why_not_recorded` whenever a date is known.
Decisions 24 and 25's code blocks above reflect the shipped signatures.

**The not-ready answer's emphasis.** The original draft put the
percent-grown sentence and the capacity sentence side by side as two
independent facts, both farmer-relevant per the original request. On
review, that reads as "you lost the queue" to a farmer whose crop
genuinely isn't ready — false (a `TOO_GREEN` plot was never in the
machine's capacity pool at all, `scheduling/solver.py` excludes it
before capacity allocation runs) and worse for trust than saying
nothing. Fixed by making the capacity clause an explicit, subordinate
parenthetical that states directly it did not affect the plot, rather
than a second sentence carrying equal weight. See Decision 25's full
"Reconsidered on review" note for the exact wording change.

Both checks were verified against the real, implemented functions —
see the rendered examples in the implementation report, not
hand-transcribed copies of the code above.

**Two real Tamil grammar bugs found while generating those rendered
examples, not by re-reading the draft — the second inside the fix for
the first.** The original `why_not_ready_answer` built the locative
"in N days" phrase by appending a hardcoded plural suffix ("களில்")
onto whatever `_day_word` returned — correct for the plural case
(நாட்கள் + களில் → நாட்களில், matching existing usage elsewhere in
this file) but wrong for the singular: `_day_word(1)` returns "நாள்",
and appending "களில்" produced "நாள்களில்" — the plural marker "கள்"
grafted onto an already-singular word, not a real word. A single-
usable-day trigger day (a tight-capacity day, exactly the scenario the
second check asked to see rendered) would have shipped this. The first
fix attempt (`f"{_day_word(n)}இல்"`, plain string concatenation of the
base word and the locative suffix இல்) was itself wrong for the
singular case for a different reason: நாள்'s locative sandhi is நாளில்
(a single fused word), not "நாள்" followed by a separately-glyphed
"இல்" ("நாள்இல்") — Tamil case suffixes are not always literal
string-appends. Fixed properly by giving `_day_word` a `locative=True`
mode that returns the correct fully-formed word per case (நாளில்/
நாட்களில்), the same shape `adverbial=True` already used for
நாளாக/நாட்களாக — not a third ad hoc concatenation attempt. Both
languages' rendered output in the implementation report reflect this
final fix.

**Follow-up sweep (2026-08-24) found the same root cause pre-existing
elsewhere, outside this part's own diff.** A grep for the same
"suffix appended to a variable word rather than the fully-formed word
being selected" shape across `messages_ta.py` turned up
`escalation_resolved_assigned` (ADR-005/008 era, untouched by ADR-013
until now): its template, `f"இயந்திரம் {winner_name} க்கு
ஒதுக்கப்பட்டது."`, is correct when `winner_name` is a real farmer's
name (a proper noun takes a spaced case marker) but wrong when the
caller substitutes `DEFAULT_WINNER_LABEL` — a genuine Tamil common
noun — for the rare case where the winning plot's farmer can't be
resolved: "வயல் க்கு" instead of the grammatically required fused
"வயலுக்கு". Fixed the way you asked — not by changing the template
(which is correct for the common case it was built for), but by never
letting the bare fallback noun reach it: `escalation_resolved_assigned`
now takes `winner_name: str | None`, and `None` selects a new
pre-fused constant, `DEFAULT_WINNER_DATIVE`, directly — the same shape
`DEFAULT_OTHER_FARMER_LABEL` already used for exactly this reason. The
general rule (proper nouns take a spaced case marker, common nouns
fuse) is now recorded as a comment beside `DEFAULT_WINNER_LABEL` in
`messages_ta.py`, flagged for whoever adds the next string built the
same way. `webhook.py`'s call site was updated to pass `None` instead
of pre-resolving to the fallback label, and `messages_en.py`'s
counterpart was widened to `str | None` to keep the two languages'
signatures matching (English has no case-agreement to get wrong, but a
divergent signature between the two modules is its own hazard).
`route_drop_confirm_prompt` (webhook.py's other `DEFAULT_WINNER_LABEL`
call site, a hyphenated genitive `-ன்` rather than a dative) was found
to have the identical latent shape and was deliberately **not** fixed
here — flagged, not silently bundled into an otherwise small, single-
purpose commit.
