# ADR-004: Telegram Interface

- Status: Proposed (awaiting go-ahead)
- Date: 2026-08-16

## Context

Phase 4 is the only farmer-facing surface: a four-message registration
flow, three (really four, see Decision 3) distinct outbound message shapes,
and an inline-keyboard escalation resolution. The design thesis this must
protect: the agent talks, farmers mostly don't. Every decision below is
checked against that.

**`TELEGRAM_BOT_TOKEN` is absent from `.env`** — no `.env` file exists in
the repo yet. Per your instruction, this is built against the real
Telegram Bot API interface anyway, but **the live path is unverified.**
Nothing here has been run against a real bot or a real chat. The gate at
the end of this phase (you registering from your phone) is the actual
verification; everything before that is "should work against the
documented API," not "confirmed working."

## Decision 1: raw Bot API via httpx, no new Telegram library

`python-telegram-bot` (or similar) would be a new, fairly heavy dependency
(its own event loop / Updater abstraction) for what the Telegram Bot API
actually is: plain HTTPS POST endpoints returning JSON. `httpx` is already
a dependency (Phase 1, for Open-Meteo). `telegram/client.py` is a thin
wrapper — `send_message`, `answer_callback_query`,
`edit_message_reply_markup` — over `https://api.telegram.org/bot<token>/<method>`,
following the same pattern as `weather/openmeteo.py`: a small, auditable
client rather than a framework. No new dependency added.

Retry: send failures get 3 attempts with linear backoff (1s/2s), then
degrade and log rather than raise — a farmer notification that fails to
send must not take down whatever triggered it (a scheduling run,
another farmer's registration).

## Decision 2: registration is a pure FSM + a placeholder store

`telegram/registration.py` separates two things that don't belong tangled
together:

1. **A pure state-transition function**, `advance_registration(state, incoming) -> (new_state, outbound_message)`.
   Given the current step and one incoming Telegram message, it returns
   the next state and what to send back. Fully unit-testable with no
   network, no storage, no Telegram involved.
2. **A per-chat state store.** Phase 5 doesn't exist yet (DynamoDB), so
   this phase uses a module-level in-memory dict keyed by `chat_id` as an
   explicit placeholder. This is enough to satisfy "a farmer who abandons
   partway and returns later" *within one running process* — send message
   1, wait, send message 2 later, the FSM picks up where it left off. It
   does **not** survive a process restart; that gap is real and is exactly
   what Phase 5's DynamoDB-backed store closes. Flagging this now so it
   isn't discovered as a surprise at Phase 5.

The four messages, using the anchor field name from ADR-001
(`transplant_date`, not `sowing_date`):

1. Village name (free text).
2. Plot location (Telegram's native location-share message type — if the
   farmer sends text instead, re-prompt with instructions rather than
   accept a wrong-typed answer).
3. Crop/variety confirmation. The project is paddy/ADT 45 only (locked
   scope) — this step states that plainly and asks for a yes/any-reply
   confirmation rather than asking an open question the system can't
   actually answer for other crops.
4. Transplant date **and** area acres, parsed from one free-text reply
   (e.g. "18 May 2026, 2.5 acres"). A small regex-based parser extracts a
   date (a few explicit formats: ISO, `DD Month YYYY`, `DD/MM/YYYY`) and a
   number near "acre"/"ac"/"acres". Unparseable input re-prompts with an
   example rather than crashing or silently guessing.

## Decision 3: "not ready" is a first-class, distinctly-worded message

`telegram/notify.py` has four message builders, not three — the brief
calls out one as the product; a resolved escalation needs its own wording
too, distinct from the routine case:

- `harvest_scheduled` — plot fits, gives route position.
- `not_ready` — **the message that matters most.** Not "no update" or
  silence: an explicit, reassuring message stating the plot isn't ready
  yet, why (grain still filling, safer standing than cut early), and when
  it'll be checked again. This is the concrete artifact of "the agent
  telling farmers not to act" — it has to exist and read as good news, not
  as an error or a non-response.
- `escalation_resolved` — sent to both farmers after a human taps a
  resolution: the winner gets `harvest_scheduled`-equivalent wording; the
  loser gets a distinctly-worded message ("the machine is going to a
  nearby plot first due to a closer conflict; you're still on today's
  list") -- different from `not_ready` because the reason is a genuine
  conflict, not immaturity, and telling a farmer the wrong reason for a
  delay is worse than no reason.
- `operator_route_summary` — to the operator's chat, the day's ordered
  route.

## Decision 4: escalation is one message, two tap targets, idempotent

`telegram/webhook.py` renders `EscalationPayload` (Phase 3) as one message
to the operator with an inline keyboard: two buttons, one per contested
plot, `callback_data` encoding `cluster_id`, both plot ids, and the chosen
plot id. On tap:

1. Look up the escalation by its encoded id in an in-memory
   resolved-escalations set (same placeholder-store caveat as Decision 2).
2. **If already resolved** (a second tap, possibly from a race or a retry
   on Telegram's side): answer the callback with "already resolved," do
   not re-process, do not re-notify. This is an explicit failure path, not
   an edge case skipped — a double-notification to two farmers about the
   same conflict is a real trust cost.
3. Otherwise: mark resolved, edit the original message's keyboard away
   (so a second real tap isn't even visually possible), and notify both
   farmers via `escalation_resolved`.

**Cluster with no operator configured** (`operator_chat_id is None`):
the coordinator still produces the escalation payload, but there is no one
to send it to. `notify.py` degrades and logs this explicitly (a distinct
log line, not silently swallowed) rather than raising — the scheduling run
must still complete for every other plot.

## Consequences

- Nothing in this phase is verified against a live Telegram bot. The gate
  is the verification; this ADR and the code are "matches the documented
  API," not "confirmed working."
- The in-memory registration and escalation-resolution stores are real,
  disclosed placeholders — both need DynamoDB in Phase 5 to survive a
  restart. Noted twice (Decisions 2 and 4) because it's the same gap
  affecting two different flows.
- No new dependency: `httpx` (already present) covers the entire Telegram
  surface.
