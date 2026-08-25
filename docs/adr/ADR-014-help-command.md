# ADR-014: `/help` — a read-only status command for farmer and operator

- Status: **Proposed 2026-08-25, awaiting go-ahead.**
- Date: 2026-08-25

## Context

Every farmer-facing surface in this project today is either the agent
speaking first (a scheduling outcome, a prompt) or a bounded one-tap
reply to something it already asked (confirm/rollover, and, since
ADR-013 Part 3, the "Why?" button on a not-ready or lost-escalation
message). There is no way for a farmer or operator to just check
something — what plot is on file, when it's expected ready, what a
command does — without waiting for the bot to bring it up first. This
ADR adds exactly one bounded command, `/help`, that answers that and
nothing else.

Read `telegram/registration.py`, `telegram/farmer_why.py` (ADR-013 Part
3 — the precedent for "assembled from stored records only, one message,
no follow-up"), `telegram/webhook.py`'s message-dispatch chain, and
`models.py`'s `Plot`/`Farmer`/`Cluster` before implementing this.

**Housekeeping found while reading `ARCHITECTURE.md` for this ADR:**
ADR-013 was never added to its ADR index — the same gap ADR-013's own
Context section found and fixed for ADR-009 through ADR-012, recurring
once more. Fixed at implementation alongside adding this ADR's own
entry, not left to compound a third time.

## The central question: is a farmer-typed command a new farmer-initiated surface?

`/help` is farmer-typed, not agent-initiated and not a reply to a prior
prompt — it fits neither of CLAUDE.md's two named categories ("the
agent speaking first" or "a bounded one-tap reply"), and that is
genuinely new: this ADR is the explicit stop-and-raise CLAUDE.md
requires before building it, not a case waved through because the
request sounds bounded. It's granted anyway because it stays inside
what the rule is actually protecting against — one exact string,
matched not parsed, one deterministic reply from data already in
`Storage`, no keyboard, no follow-up, nothing left pending, the same
"pure read, safe to repeat" shape ADR-013 Part 3's "Why?" button
already established — and because a farmer today has no way to check
anything the system already knows without a live human. If this ships,
it's the one and only farmer-initiated exception, not a precedent for
adding a second command casually later.

## Decision 1: command shape — one exact string, matched like every other command

```python
# telegram/help.py
HELP_COMMAND = "/help"
```

Matched in `webhook.py`'s message branch the same way
`operator_enrollment.COMMAND`/`proxy_registration.ADD_FARMER_COMMAND`/
`LINK_FARMER_COMMAND` already are:
`(incoming.text or "").strip().lower().startswith(HELP_COMMAND)`.
Checked **before** the `/addfarmer`/`/linkfarmer` operator-identity
check, the operator's mid-`/addfarmer`-flow free-text handoff, the
pending-rollover free-text handoff, and `registration.handle_incoming`
— same reasoning as every command already ahead of those: a farmer or
operator mid-any-other-flow who types `/help` must get the help text,
not have it silently swallowed by whatever parser currently owns their
next message.

**Never touches any pending state.** A pending registration
(`RegistrationState`), a pending rollover date-reply, or an operator's
pending `/addfarmer` flow is left exactly as it was — `/help` is read,
answer, done; the farmer's or operator's *next* real message continues
whatever was already in progress, unaffected. Checked explicitly rather
than assumed: `/help`'s handler never calls `save_state`/`clear_state`
for any of the three state stores in this codebase.

## Decision 2: three audiences, resolved from `chat_id` alone, no parameters

```python
# telegram/help.py
def build_help_reply(
    storage: Storage, cluster_id: str | None, chat_id: int, season_id: str,
) -> str:
    if not cluster_id:
        logger.error("HARVEST_CONVOY_CLUSTER_ID not set -- /help from chat_id=%s answered generically", chat_id)
        return messages_ta.help_unavailable()
    cluster = storage.get_cluster(cluster_id)
    if cluster is None:
        logger.error("HARVEST_CONVOY_CLUSTER_ID=%s not found -- /help from chat_id=%s answered generically", cluster_id, chat_id)
        return messages_ta.help_unavailable()

    farmer = registration.find_farmer_by_chat_id(storage, cluster_id, chat_id)
    is_operator = cluster.operator_chat_id == chat_id

    if farmer is None:
        if is_operator:
            mod = notify._lang_module(cluster.operator_language)
            return mod.help_operator_reply(cluster.name)
        return help_unregistered_text()  # bilingual -- see Decision 4

    mod = notify._lang_module(farmer.language)
    plot = _active_plot_for(storage, cluster_id, farmer.farmer_id)
    if plot is None:
        logger.error("help: farmer %s has no active plot on record", farmer.farmer_id)
        return mod.help_no_plot_found()

    notice = storage.get_advance_notice_record(plot.plot_id, season_id)
    projected_text = (
        mod.format_date(date.fromisoformat(notice.projected_maturity_date))
        if notice is not None else None
    )
    reply = mod.help_farmer_reply(
        village=plot.village,
        area_text=mod.format_area(plot.area_acres, plot.area_unit),
        transplant_date_text=mod.format_date(plot.transplant_date),
        projected_ready_text=projected_text,
    )
    if is_operator:
        reply += "\n" + mod.help_operator_addendum(cluster.name)
    return reply
```

**Farmer-first, reversed on review.** The original draft checked
operator status before the farmer lookup, so an operator who also
farms would never see his own plot through `/help` at all — in a real
pilot the operator very likely farms too, and his own crop must not be
the one thing his own status command can't show him. Now the farmer
view always wins when both are true, with one appended line
(`help_operator_addendum`) pointing to the operator commands rather
than duplicating `help_operator_reply` in full — he still learns he has
operator access from the same message, just not at the cost of his own
plot going unreported.

`_active_plot_for` filters `storage.get_plots_for_cluster(cluster_id)`
to `farmer_id == farmer.farmer_id and retired_reason is None`. By
construction (every registration path in this codebase mints exactly
one farmer_id/plot_id pair, and ADR-013 Part 2's `/linkfarmer` clears
the losing side's own `telegram_chat_id` the moment it retires that
side's plot) a `Farmer` reachable via `find_farmer_by_chat_id` should
have exactly one active plot. Handled defensively anyway, not assumed:
zero matches logs `ERROR` and returns an honest "no plot on record"
line rather than crashing; more than one match logs `ERROR` and uses
the first (sorted by `plot_id` for determinism) rather than picking
arbitrarily or silently listing all of them, which the "one message, no
menu" constraint rules out.

## Decision 3: the projected-ready date is `AdvanceNoticeRecord` or nothing — never a live recompute

The obvious-looking alternative — call `agronomy.calibration.
project_maturity_for_plot()`, the same function registration and the
advance-notice flow already use — is rejected here for the identical
reason ADR-013 Part 3 Decision 23 rejected it for the "Why isn't my
plot ready" answer: it makes a live Open-Meteo call and computes a
fresh projection at whatever moment it's called, so two `/help` taps
minutes apart could honestly return two different dates for the same
plot. `/help`'s own stated constraint is "stored records only," which
this project already has a durable answer for:
`AdvanceNoticeRecord.projected_maturity_date` — the exact date a farmer
was actually told, frozen at the moment the one-time advance notice was
sent (ADR-011 Part 4, Decision 17), never corrected afterward by
design. `/help` reads that same frozen value, not a second,
independently-computed one — so a farmer who already got the advance
notice sees the identical date in both places, and one who hasn't
(`get_advance_notice_record` returns `None` for most of a season, since
that notice fires only ~`ADVANCE_NOTICE_DAYS_BEFORE_MATURITY`
(7) days ahead of projected maturity) sees that the estimate isn't
available *yet*, said plainly, not just an absence — **"not available
yet -- we send that estimate about a week before your plot is ready"**,
not a bare "not yet available." A farmer checking in July, months out,
should read the absence as by design (the estimate genuinely doesn't
exist yet, on a known schedule) rather than as the system not knowing
his plot exists. Reworded on review for exactly this reason — a
one-word gap between "not yet available" and "not a guess" is where a
farmer's trust in the rest of the message would have quietly leaked
out. This is expected to be the common case for a newly-registered or
early-season plot, not an edge case — stated here so it isn't mistaken
for a bug once it ships.

## Decision 4: message content, both languages (drafts, for your review before this ships)

```python
# telegram/messages_en.py

HELP_COMMAND_LABEL = "/help"  # not a button; documented for completeness

def help_unavailable() -> str:
    return "Something's not set up right on our end -- please try again later."

def help_unregistered() -> str:
    return "You're not registered yet -- send me any message to get started."

def help_no_plot_found() -> str:
    return "We have your registration but no plot on record -- please contact your operator."

def help_farmer_reply(
    village: str | None, area_text: str, transplant_date_text: str, projected_ready_text: str | None,
) -> str:
    village_text = village or "village not recorded"
    ready_text = (
        f"expected around {projected_ready_text}" if projected_ready_text
        else "not available yet -- we send that estimate about a week before your plot is ready"
    )
    return (
        f"Your plot: {village_text}, {area_text}, transplanted {transplant_date_text}.\n"
        f"Projected ready: {ready_text}.\n"
        f"We'll message you when the machine is scheduled or your plot needs "
        f"attention -- no need to check in."
    )

def help_operator_reply(cluster_name: str) -> str:
    return (
        f"You're the operator for {cluster_name}.\n"
        f"Commands: /addfarmer to register a farmer without a phone, "
        f"/linkfarmer to connect them once they register themselves, "
        f"/operator <code> to replace yourself. Route changes and breakdown "
        f"reports happen via the buttons on today's messages."
    )

def help_operator_addendum(cluster_name: str) -> str:
    """Appended to a farmer reply, not the standalone operator reply --
    for the chat_id-is-both case (Decision 2, reversed on review): the
    farmer view always wins, this is the one-line pointer that still
    surfaces operator access from the same message."""
    return (
        f"You're also the operator for {cluster_name} -- commands: "
        f"/addfarmer, /linkfarmer, /operator <code>."
    )
```

`help_unregistered()` is rendered **bilingually**, matching
`registration._bilingual_greeting_text()`'s own precedent for "we
genuinely don't know this person's language yet" rather than defaulting
to Tamil the way a resolvable-but-refused identity does elsewhere
(`unrecognized_action()`). Composed the same way that function is —
from each language module's own string, not a third hardcoded copy:

```python
# telegram/help.py
def help_unregistered_text() -> str:
    return f"{messages_en.help_unregistered()} / {messages_ta.help_unregistered()}"
```

`help_unavailable()` stays Tamil-default (`messages_ta.help_unavailable()`,
called directly, no bilingual composition) — this path fires only when
`cluster_id`/`Cluster` itself can't be resolved, meaning no identity
lookup was even possible, the same "truly unresolvable" shape
`notify._lang_module("ta").unrecognized_action()` already uses
throughout `webhook.py`.

Tamil (drafts):

```python
# telegram/messages_ta.py

def help_unavailable() -> str:
    return "எங்கள் தரப்பில் ஏதோ சரியாக அமைக்கப்படவில்லை -- பின்னர் முயற்சிக்கவும்."
    # "Something's not set up right on our end -- please try again later."

def help_unregistered() -> str:
    return "நீங்கள் இன்னும் பதிவு செய்யவில்லை -- தொடங்க எனக்கு ஏதேனும் செய்தி அனுப்புங்கள்."
    # "You're not registered yet -- send me any message to get started."

def help_no_plot_found() -> str:
    return "உங்கள் பதிவு உள்ளது, ஆனால் வயல் பதிவில் இல்லை -- உங்கள் ஆபரேட்டரைத் தொடர்பு கொள்ளவும்."
    # "We have your registration but no plot on record -- please contact your operator."

def help_farmer_reply(village, area_text, transplant_date_text, projected_ready_text):
    village_text = village or "கிராமம் பதிவு செய்யப்படவில்லை"
    if projected_ready_text:
        ready_text = f"{projected_ready_text} அளவில் தயாராக இருக்கும் என எதிர்பார்க்கப்படுகிறது"
    else:
        ready_text = (
            "இன்னும் கிடைக்கவில்லை -- உங்கள் வயல் தயாராகும் ஒரு வாரத்திற்கு "
            "முன்பு அந்த மதிப்பீட்டை அனுப்புவோம்"
        )
    return (
        f"உங்கள் வயல்: {village_text}, {area_text}, நடவு தேதி {transplant_date_text}.\n"
        f"தயாராகும் தேதி: {ready_text}.\n"
        f"இயந்திரம் திட்டமிடப்படும்போது அல்லது உங்கள் வயலுக்கு கவனம் "
        f"தேவைப்படும்போது நாங்கள் உங்களுக்குச் செய்தி அனுப்புவோம் -- விசாரிக்க "
        f"வேண்டாம்."
    )
    # "Your plot: {village}, {area}, transplanted {date}. Ready date:
    #  {ready}. We'll message you when the machine is scheduled or your
    #  plot needs attention -- no need to ask." -- ready_text's fallback:
    #  "not available yet -- we send that estimate about a week before
    #  your plot is ready."

def help_operator_reply(cluster_name: str) -> str:
    return (
        f"நீங்கள் {cluster_name}-க்கான ஆபரேட்டர்.\n"
        f"கட்டளைகள்: தொலைபேசி இல்லாத விவசாயியைப் பதிவு செய்ய /addfarmer, "
        f"சுய-பதிவுக்குப் பிறகு இணைக்க /linkfarmer, உங்களை மாற்ற "
        f"/operator <code>. பாதை மாற்றங்களும் பழுது அறிக்கைகளும் இன்றைய "
        f"செய்திகளில் உள்ள பொத்தான்கள் மூலம் நடக்கும்."
    )
    # "You're the operator for {cluster_name}. Commands: /addfarmer to
    #  register a phone-less farmer, /linkfarmer to connect after
    #  self-registration, /operator <code> to replace yourself. Route
    #  changes and breakdown reports happen via the buttons on today's
    #  messages."

def help_operator_addendum(cluster_name: str) -> str:
    return (
        f"நீங்கள் {cluster_name}-க்கான ஆபரேட்டரும் கூட -- கட்டளைகள்: "
        f"/addfarmer, /linkfarmer, /operator <code>."
    )
    # "You're also the operator for {cluster_name} -- commands:
    #  /addfarmer, /linkfarmer, /operator <code>."
```

Printed via `scripts/print_tamil_strings.py` under a new "ADR-014"
section for your review before this ships, same as every prior part.

## Decision 5: authorization — no external identifier, nothing to spoof

Every prior authorization fix in this project (ADR-012's audit,
ADR-013 Part 3's `_is_farmer` checks) exists because a `callback_data`
string or a stored record's key carries a **plot_id/farmer_id/cluster_id
supplied separately from the tapper's own identity**, which a wrong
party could tap on someone else's behalf. `/help` has no such
parameter at all — `chat_id` (from `message.chat.id`, the same field
every registration/rollover/addfarmer message-handler already keys off
of) is simultaneously the only input and the only identity the reply is
built from. There is no `plot_id` to mismatch against a `farmer_id`,
no `cluster_id` argument a stranger could substitute — the query *is*
"whoever is texting me right now," structurally incapable of the
cross-party class of bug ADR-012 found three times. No new row in
ADR-012's callback-authorization table, because `/help` is not a
`callback_query` and carries nothing to check an identity against
beyond the identity that's already resolving it. Stated explicitly
rather than left implicit, per CLAUDE.md's "state whose authority the
action requires" — the authority required here is simply "being the
Telegram account attached to this `chat_id`," which Telegram itself
already guarantees for every inbound message.

## Decision 6: failure paths

| Case | Behavior |
|---|---|
| `HARVEST_CONVOY_CLUSTER_ID` unset or its cluster missing | `help_unavailable()`, logged `ERROR` — same degrade shape `registration.py`'s completion handler already uses. |
| Sender is neither the operator nor a registered farmer | Bilingual `help_unregistered_text()` (Decision 4). |
| Sender is both the operator and a registered farmer | Farmer view wins, with `help_operator_addendum` appended (Decision 2, reversed on review). |
| Farmer record exists, no active plot found (shouldn't happen — see Decision 2) | `help_no_plot_found()`, logged `ERROR`, never a crash. |
| Farmer record exists, more than one active plot found (shouldn't happen) | The first by `plot_id`, logged `ERROR` — never silently lists both, never picks randomly. |
| No `AdvanceNoticeRecord` yet (the common case most of a season) | States plainly that the estimate arrives about a week before the plot is ready, not just "not yet available" — so the absence reads as by design, not as the system not knowing (Decision 3). |
| `/help` sent mid-registration, mid-pending-rollover, or mid-operator-`/addfarmer`-flow | Answered normally; the pending state is untouched, so the farmer's or operator's next real message continues exactly where it left off (Decision 1). Deliberately not distinguished from "never registered at all" in the unregistered branch's wording — the same one-line instruction ("send any message") is correct advice either way, and a step-aware message would be new complexity for a narrow case. |
| Repeated `/help` (double-send, or checking back later) | Identical answer each time — pure read, nothing to double-anything (Decision 1). |
| Restart between commands | No effect — no in-memory state exists for this command at all, unlike `registration.py`'s/`rollover.py`'s/`proxy_registration.py`'s own disclosed `_STATE_STORE` limitation. |
| Weather/network unavailable | Not applicable by construction — no live call is made (Decision 3). |
| A farmer's plot was retired via `/linkfarmer` and this is the now-chat-id-less losing record | Can't reach `/help` at all — `telegram_chat_id` is `None` on that record, so no inbound Telegram message can ever arrive attributed to it; `find_farmer_by_chat_id` only ever matches the canonical, still-linked record. |

## Tests (`tests/test_help.py`, plus `tests/test_webhook.py` dispatch additions)

`build_help_reply`: registered farmer with a sent `AdvanceNoticeRecord`
renders the real date; without one renders the "about a week before
your plot is ready" line, not a bare "not yet available"; a plot with
`village=None` renders "village not recorded" rather than `"None"` or a
crash; the operator (by `cluster.operator_chat_id`, no matching farmer
record) gets the standalone operator reply, in
`cluster.operator_language`; a chat_id matching neither gets the bilingual
unregistered text; a chat_id matching both the operator and a farmer
record gets the farmer reply with `help_operator_addendum` appended,
not the standalone operator reply (Decision 2's tie-break, reversed on
review, asserted directly against both the plot details and the
addendum text being present); a farmer with zero active plots gets
`help_no_plot_found()`, logged, not raised; a farmer with two active
plots (constructed fixture) gets the lower `plot_id`, deterministically,
across repeated calls; `cluster_id=None` and an unresolvable `cluster_id`
each degrade to `help_unavailable()` rather
than raising.

`webhook.handle_update`: `/help` (and `/HELP`, case-insensitivity) from
a chat mid-pending-registration/-rollover/-`/addfarmer` leaves that
pending state byte-identical before and after, and the farmer's next
real message still completes the original flow correctly. No-Bedrock
assertion, matching every read path in this project.

## Consequences

- A farmer or operator can now check their own status without waiting
  for the bot to bring it up, closing the one remaining "must wait to
  be told" gap in an otherwise entirely agent-initiated (or
  bounded-reply) surface.
- The one deliberate exception to CLAUDE.md's "agent speaks first, or a
  bounded reply" rule, argued explicitly rather than assumed — not a
  precedent for adding a second farmer-typed command without the same
  scrutiny.
- Zero new `Storage` entities, zero new writes — `/help` is exactly as
  read-only as `explain_decision.py`/`farmer_why.py`, extended to a
  chat command instead of a script or a button.
- The projected-ready date, when shown, is guaranteed to match what the
  advance-notice message already told the farmer, because it's the same
  stored field, not a second live computation that could disagree with
  it.
- `ARCHITECTURE.md`'s ADR index gets both ADR-013's and ADR-014's
  missing entries, closing a gap that had already recurred once before
  this ADR found it again.

## Sequence

Implement `telegram/help.py`, the two new `messages_en.py`/
`messages_ta.py` string sets, the dispatch check in `webhook.py`, the
`ARCHITECTURE.md` index fix, full test coverage, run the whole suite —
commit, then stop and report, with the Tamil dump delivered as a file
path, per your standing instruction.
