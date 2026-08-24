# ADR-012: callback authorization audit, operator self-enrollment, and documenting the onboarding asymmetry

## Status

Implemented. This document was updated after the initial review round to
reflect two corrections made during implementation (Decision 1's binding
mechanism, and the broader authorization audit) rather than left as the
original proposal — see "What changed after review" below.

## Context

Four things surfaced together, three of them only after the first was
investigated:

**The escalation "resolve" tap had no authorization check at all.**
Breakdown and machine-back taps (ADR-011 Part 2) check the tapping
user's Telegram identity against `Cluster.operator_chat_id`
(`_is_operator`, `webhook.py`). The escalation tap — the button that
picks a winner between two contested plots and writes a real
`LedgerEntry` — never did. Anyone who obtained or was forwarded an
escalation message could tap it and mutate the fairness ledger,
regardless of whether they were the cluster's actual operator.

**A full audit of every callback path found the same bug class twice
more, farmer-owned instead of operator-owned.** `handle_confirmation_
callback` (the evening "did the machine come?" yes/no tap) and
`handle_rollover_callback` (the season-rollover yes/no tap) never
checked the tapping user's identity against the plot's actual farmer at
all. A stranger — or another farmer — could tap either callback on
someone else's behalf: falsely corroborate or deny a harvest (freeing
the plot and crediting the fairness ledger on a fabricated "no-show"),
or answer another farmer's rollover prompt, including starting to
receive *that farmer's* follow-up date-reply flow and setting *their*
new `transplant_date`. Same class of bug as the escalation hole, just
acting on behalf of a farmer instead of an operator.

**No operator onboarding surface at all.** Farmers self-onboard by
messaging the bot (`registration.py`). The operator has never had an
equivalent: `Cluster.operator_chat_id` is set by whoever runs
`seed_cluster.py`, by hand, at provisioning time. Fine for a single demo
cluster, does not scale past a handful, and the only way to correct a
wrong `operator_chat_id` today is to re-run a seed script.

**The onboarding asymmetry itself was never written down**, and the
backtest section's use of 2025 dates was only partially explained.

### Full callback audit

Every `callback_query` shape `webhook.handle_update` dispatches, and
what authorizes each one:

| Prefix | Handler | Authorized against | Status before this ADR | Status now |
|---|---|---|---|---|
| `lang:` | `registration.handle_language_callback` | Now: `from.id == message.chat.id` — the tap must come from the same identity the keyboard was sent to | Used only `message.chat.id`, not `from.id` | **Fixed** |
| `confirm:` | `handle_confirmation_callback` | Now: the confirmation's farmer (`Farmer.telegram_chat_id`) via `_is_farmer` | **No check at all** | **Fixed** |
| `rollover:` | `handle_rollover_callback` | Now: the prompt's farmer, via `_is_farmer` | **No check at all** | **Fixed** |
| `breakdown:` | `handle_breakdown_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked | Unchanged |
| `breakdown_followup:` | `handle_breakdown_followup_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked | Unchanged |
| `machine_back:` | `handle_machine_back_callback` | `Cluster.operator_chat_id` via `_is_operator` | Checked | Unchanged |
| `operator_lang:` | `handle_operator_lang_callback` (new) | The chat_id that sent the originating `/operator <code>` command, via `operator_enrollment.matches_pending` | New | Checked from the start |
| `operator_replace:` | `handle_operator_replace_callback` (new) | Same as above | New | Checked from the start |
| `resolve:` (anything unmatched) | `handle_callback_query` | Now: `Cluster.operator_chat_id` via `_is_operator` | **No check at all** | **Fixed** |
| `route_accept:` | `handle_route_accept_callback` (new, ADR-013) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `route_modify:` | `handle_route_modify_callback` (new, ADR-013) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `route_swap:` | `handle_route_swap_callback` (new, ADR-013) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `route_drop:` | `handle_route_drop_callback` (new, ADR-013) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `route_drop_confirm:` | `handle_route_drop_confirm_callback` (new, ADR-013) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `route_done:` | `handle_route_done_callback` (new, ADR-013) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `addfarmer_confirm:` | `handle_addfarmer_confirm_callback` (new, ADR-013 Part 2) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `linkfarmer_proxy:` | `handle_linkfarmer_proxy_callback` (new, ADR-013 Part 2) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `linkfarmer_match:` | `handle_linkfarmer_match_callback` (new, ADR-013 Part 2) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `linkfarmer_confirm:` | `handle_linkfarmer_confirm_callback` (new, ADR-013 Part 2) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `linkfarmer_undo:` | `handle_linkfarmer_undo_callback` (new, ADR-013 Part 2 revision 2026-08-24) | `Cluster.operator_chat_id` via `_is_operator` | New | Checked from the start |
| `why_notready:` | `handle_why_notready_callback` (new, ADR-013 Part 3) | The plot's own farmer, via `_is_farmer` | New | Checked from the start |
| `why_lost:` | `handle_why_lost_callback` (new, ADR-013 Part 3) | The plot's own farmer, via `_is_farmer` | New | Checked from the start |

**`lang:` was reconsidered and fixed, not left as a documented
exception.** The original reasoning — this callback doesn't act on
behalf of an already-identified other party, so a stray tap only ever
sets *its own chat's* language — was sound as far as it went, but it
missed the concrete case: in a group chat, or if the keyboard reached a
second person, that second person could set *the farmer's* language to
one the farmer can't read. The harm is low-probability but real — a
harvest message arriving in a language the farmer can't read is a
missed harvest, the same failure mode the rest of this ADR exists to
prevent. `from.id` is available on this callback exactly like every
other one, so there was no reason to special-case it: doing so would
have left a reader auditing the table one exception to reason about
instead of one uniform rule (`from.id` must match the identity the
message concerns). Fixed the same way as `confirm:`/`rollover:` — a tap
whose `from.id` doesn't match `message.chat.id` is refused before the
language is set. See `test_language_tap_from_a_different_chat_is_
refused_and_does_not_set_language` and `test_language_tap_with_no_
from_field_is_refused` in `tests/test_registration.py`.

**New test coverage for the audit**: `tests/test_operator_authorization.py`
(escalation-resolve: non-operator refused with no state mutation, no
`from` field refused, unregistered cluster refused, real operator's tap
still works) and `tests/test_farmer_authorization.py` (confirmation and
rollover: non-farmer refused for both yes and no, no `from` field
refused, the real farmer's own tap still works — including the more
dangerous rollover "yes" case, where an unauthorized tap would
previously have redirected the follow-up date-reply flow to the
imposter's own chat).

### Test-fixture audit (why the hole was invisible)

Every existing test that exercised escalation resolution constructed its
`Cluster` fixtures **without ever registering a `Cluster` in storage at
all** — `_is_operator` would have refused them by construction the
moment the check existed, so the fixture shortcut and the missing check
were two sides of the same gap. Same root cause in
`tests/test_harvest_confirmation.py`: its `_callback_query` helper never
set a `from` field, while `_seed_confirmation` did set
`telegram_chat_id=101` — coincidentally matching `message.chat.id`
(never checked) but never matching what `_is_farmer` actually reads
(`from.id`). Fixed by adding `from` to the fixture, matching the
seeded farmer's chat_id.

Checked and found clean elsewhere: `tests/test_machine_breakdown.py`'s
confirmation-callback fixtures already used a correct `from.id`
matching the seeded farmer (unrelated feature under test, written
carefully already); `tests/test_season_rollover.py`'s rollover-tap
helper already passed `from.id` matching the chat_id argument.
`tests/test_scripts_smoke.py` mocks `handle_update` entirely (a spy) and
never exercises real authorization logic either way — not a gap, just
not applicable. One collateral fix: `test_machine_breakdown.py`'s
`_resolve_escalation` helper hardcoded `operator_chat_id=999` in every
call, which broke once authorization was enforced for a test resolving
an escalation against a *second* cluster whose real operator was `998`
— fixed by making the operator chat_id a parameter.

## Decision

### Decision 1 (revised after review): bind enrollment to the initiating chat_id, don't trust callback_data alone

The original proposal for operator enrollment used no server-side
pending state at all — callback_data alone would carry the code and
cluster_id through the language-choice and replacement-confirmation
taps. On review: **if callback_data alone is the credential, then
anyone who obtains that callback_data can complete enrollment**, and
whether a forwarded Telegram message can carry a live, tappable keyboard
into a different chat was not verified from documentation. Per explicit
instruction, this is treated as unverified rather than assumed safe.

**Fix**: `operator_enrollment.py` keeps an in-memory `_STATE_STORE`
(same disclosed limitation as `rollover.py`'s own store — does not
survive a process restart between the command and the tap; the code is
still unused in that case, so the fix is just re-sending the command),
keyed by the chat_id that sent the original `/operator <code>` command.
Every subsequent button tap (`operator_lang:`, `operator_replace:`) is
checked via `operator_enrollment.matches_pending(tapper_id, cluster_id,
code)` **before anything else** — the tapping chat_id must have its own
pending enrollment for that exact cluster_id/code. A well-formed,
genuinely-valid callback_data string tapped from any other chat is
refused. New tests specifically prove this: a language-choice or
replace tap from a chat that never sent the originating command is
refused even though the code itself is real and unused
(`test_language_tap_from_a_different_chat_than_the_command_is_refused`,
`test_replace_tap_from_a_different_chat_is_refused`).

### Decision 2: every enrollment and replacement is an auditable event, surfaced where it affects a decision

Operator identity determines who can report a machine breakdown or
resolve a scheduling conflict for an entire cluster — the same
"reconstructable later" bar every scheduling decision already has to
clear (`DecisionRecord`, ADR-010 Part 0.5). New entity:

```python
@dataclass(frozen=True)
class OperatorAuditEvent:
    cluster_id: str
    event_type: str  # "enrolled" | "replaced"
    occurred_at: str
    code_used: str
    new_operator_chat_id: int
    previous_operator_chat_id: int | None = None
```

Append-only (`put_operator_audit_event` / `get_operator_audit_events_
for_cluster`). Written inside `operator_enrollment.complete_enrollment`,
the single function both the fresh-enrollment and replacement paths call
— so `Cluster.operator_chat_id`, the code's `used_at`, and the audit
event are always written together, never one without the others. A
replacement also logs at `WARNING` with both chat_ids; a fresh
enrollment logs at `INFO`.

**Surfaced in `explain_decision.py`, scoped to decisions an operator
actually touched** — not on every decision, which would bury the cases
that matter. A `DecisionRecord` whose `resolution` is
`escalated_won`/`escalated_lost` (an operator resolved a conflict) or
whose `trigger_reason` is `breakdown_recompute` (an operator reported a
breakdown) now shows which operator was in effect that day, reconstructed
via `operator_enrollment.operator_as_of(storage, cluster_id, cutoff)` —
the most recent `OperatorAuditEvent` with `occurred_at` on or before an
end-of-day cutoff for the decision's date (not a naive string compare
against the bare date, which would wrongly exclude an operator who
enrolled earlier the same day, since a full ISO timestamp string sorts
*after* the bare date string it starts with). When no audit event exists
for that cluster by that date — the operator was set by hand, predating
this ADR — the report states that plainly instead of showing nothing.

### Decision 3: one-time operator enrollment code, `/operator <code>`

`OperatorEnrollmentCode` (`code, cluster_id, created_at, expires_at,
used_at, used_by_chat_id`), looked up **by code alone** — the operator
only ever types the code, not a cluster_id. Single-use (`used_at`)
**and** time-limited (`expires_at`, defense in depth) — 14 days,
unsourced judgment call, same status as `DRYING_WINDOW_DAYS`/
`ADVANCE_NOTICE_DAYS_BEFORE_MATURITY`. Codes are `secrets.token_hex(4).
upper()` (8 uppercase hex characters — no I/l/O ambiguity, short enough
to read over a phone call), generated by `scripts/generate_operator_
code.py --cluster-id <id>`, printed to stdout. Handing the code to the
real operator is out-of-band, outside this system.

Flow (every step after the command is a button tap, and every tap is
checked against Decision 1's pending state):

1. Operator sends `/operator A3F9C1D2`.
2. `operator_enrollment.validate_code` checks the code exists, is
   unused, is unexpired, and its cluster still exists. Any failure
   sends one of the rejection messages below and stops; success starts
   the pending state and sends the bilingual language-choice prompt
   (`operator_lang:{cluster_id}:{code}:ta`/`en`).
3. The language tap re-validates the code (closes the race where two
   chats both hold a valid code — whichever taps first wins, the second
   finds it already used) and checks Decision 1's binding. Then:
   - **No existing operator**: `complete_enrollment` sets
     `operator_chat_id`/`operator_language`, marks the code used, writes
     the audit event, sends a confirmation.
   - **Existing operator already set**: sends a Yes/No replacement
     prompt instead — never a silent overwrite. Only "yes" (also
     binding-checked) performs the replacement; "no" leaves the cluster
     and the code untouched (a genuine change of mind isn't a wasted
     code).

**Failure paths**:

| Case | Behavior |
|---|---|
| Invalid code (not found) | Generic rejection, logged `INFO`. |
| Reused code | Same generic rejection — not distinguished from invalid, since there's no legitimate reason for a real operator to need to tell them apart. |
| Expired code | Rejection with an added "ask your coordinator for a new one" clause. |
| Code's cluster no longer exists | Generic rejection to the sender; logged `ERROR` (a data-consistency problem, not a user error). |
| Cluster already has an operator | Replacement confirmation (Decision 3, step 3), not silent overwrite. |
| Malformed command (`/operator` with no code) | Usage message, never routed into farmer registration's free-text parser. |
| A tap on a well-formed, genuinely-valid callback from the wrong chat | Refused via Decision 1, before any lookup. |
| Double-tap / two chats holding the same code | Self-consistent via the code's own `used_at` check — no special-casing needed. |

**Where this hooks into `webhook.py`**: the `/operator` command is
checked in `handle_update`'s message branch before the rollover
pending-state check and before `registration.handle_incoming`. Two new
callback prefixes dispatch alongside the existing ones.

**Tamil strings** — drafted, printed via `scripts/print_tamil_strings.py`
under "NEW (ADR-012 Part 2)" for review: command usage, invalid/reused
code, expired code, language prompt, replacement prompt, replacement
declined, enrollment confirmed. Reuses `CONFIRMATION_YES_LABEL`/
`CONFIRMATION_NO_LABEL` for the replacement buttons — no new label pair.

### Explicitly out of scope for this MVP (disclosed, not silently dropped)

- No way to list or revoke outstanding codes for a cluster from inside
  the chat, and no admin surface to see who is enrolled where.
- Re-running `generate_operator_code.py` for a cluster that already has
  an outstanding, still-valid code does **not** invalidate the earlier
  one — two valid codes could coexist briefly if the script is run
  twice before the first code is used. Acceptable at this project's
  scale; worth revisiting before any real multi-cluster rollout.

### Decision 4: README documentation

**Onboarding asymmetry** — new paragraph in the Honesty section, right
after the existing "a real operator with a real Telegram account"
sentence: states plainly that farmers self-onboard conversationally and
the operator does not, why that's deliberate (an operator holds
cluster-wide authority a self-onboarding surface would hand out
carelessly), and what a real deployment would still need (a secure,
at-scale code-delivery process this project has no opinion on, and a
real answer for operator turnover beyond the in-chat replacement flow).

**Backtest year clarification** — one sentence added to the opening
paragraph of "## Backtest against the real 2025 Kuruvai season," before
the first results table. The existing text already stated *that* dates
were shifted to 2025; it did not state *why 2025 specifically*
(most recently completed Kuruvai season — real archived weather needs a
finished season) or explicitly contrast with the live system's actual
2026 dates. Both added.

## Consequences

- Every callback path that mutates state — operator-owned or
  farmer-owned — is now authorization-checked. No remaining callback
  lets one party act on another's behalf.
- A `callback_data` string is no longer sufficient on its own to
  complete anything sensitive; the enrollment flow requires the tapping
  chat to be the one that started it, not just knowledge of a valid
  code.
- Operator identity changes are now a permanent, append-only record,
  reconstructable the same way scheduling decisions already are, and
  visible in `explain_decision.py` exactly where they're relevant.
- Operator provisioning becomes a repeatable, code-based process instead
  of a manual script edit, without adding any new farmer-facing surface
  or any operator-facing surface beyond one command plus button taps.
- The asymmetry between farmer and operator onboarding is now a stated
  design decision with a documented reason, not a gap a reader has to
  notice on their own.

## What changed after review

The initial draft proposed no pending state for enrollment (callback_data
alone) and covered only the escalation-resolve gap. Both were corrected
before implementation: Decision 1 above (chat_id binding), and the
broader audit that found and fixed the same bug class in
`handle_confirmation_callback`/`handle_rollover_callback`. Decision 2
(the audit trail) was not in the original draft at all — added per
explicit instruction that operator changes must be reconstructable
later.

A second review round, after the initial implementation, found one more
gap: `lang:` had been deliberately left checking only `message.chat.id`
with reasoning recorded in this document. On review, that reasoning
didn't hold up against the concrete case (a second person in a group
chat, or a forwarded keyboard, setting a farmer's language to one they
can't read) and the exception was worse for a reader than the risk it
avoided. Fixed to match every other handler — see the audit table and
note above. `OperatorAuditEvent` pruning was also checked at this point:
no code anywhere calls anything that deletes or overwrites an audit
event — there is no delete method for the entity in any storage backend,
season rollover and cluster updates never touch `operator_audit` — and
`test_operator_audit_events_are_never_pruned_by_unrelated_storage_
operations` (`tests/test_operator_enrollment.py`) now asserts this
directly rather than leaving it as an unverified property of the code.
