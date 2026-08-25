# ADR-016: No live inbound path — every farmer/operator-initiated feature has never been reachable on the deployed system

- Status: **Approved and implemented (2026-08-25).** Approved as
  written, plus the package-layout two-state distinction and the demo
  video note below.
- Date: 2026-08-25

## Finding

Discovered during the ADR-006/012/013/014/015 redeploy to v12: the
deployed AgentCore Runtime runs exactly one entrypoint,
`app.py`'s `handler(payload)`, which takes `{cluster_id, season_id,
force}` and calls `watcher.run_daily_watch()`. Nothing in this codebase
calls `webhook.handle_update()` from anywhere reachable in production —
no `setWebhook` call exists anywhere, and confirmed against the account
directly: one Lambda (`harvest-convoy-watcher-invoker`, the schedule's
shim), one EventBridge schedule, zero API Gateways. `scripts/run_polling.py`
says so itself: *"NOT the production path -- AgentCore Runtime with a
real webhook is Phase 6"* — planned, never built.

**Deployed and live**: the daily watcher's outbound half — rain check,
`solve()`, negotiation, dispatch/escalation/route messages sent via
`notify.py`. This has been verified live repeatedly (ADR-006 Decisions
9/10/11).

**Never deployed, code-complete, tested only via local polling or the
test suite**: registration (ADR-004, ADR-009 Part 3), `/addfarmer`/
`/linkfarmer` (ADR-013 Part 2), the harvest confirmation reply
(ADR-009 Part 2), escalation/breakdown/rollover taps (ADR-011, ADR-012),
route Accept/Modify (ADR-013 Part 1), farmer "why?" (ADR-013 Part 3),
`/help` (ADR-014) — every one of these is `webhook.py` dispatch, and
`webhook.py` has no live entry point.

This was never a secret held back deliberately — it's an unnoticed gap.
Every ADR since ADR-004 wrote and tested `webhook.handle_update()`
correctly; none of them asked "is this reachable from the deployed
artifact," because the answer had been "no" since before ADR-004 existed
and nothing about later ADRs changed it. Tests passed, the code was
correct, and reachability was never the thing being checked.

## Decision: disclose accurately now, build the webhook later, not here

Not building a live webhook in this ADR — that's real new infrastructure
(an inbound HTTP path), not a documentation fix, and deserves its own
ADR with its own failure-path analysis when there's time to do it
properly, not appended to a redeploy.

**Sketch, for scoping only, not committed to:**

| Option | Shape | Rough size |
|---|---|---|
| API Gateway (HTTP API) → existing Lambda pattern → `webhook.handle_update()` | New API Gateway, a second Lambda (or repoint the shim) invoking the same handler code already tested locally, `setWebhook` call once at setup | Small-medium: mostly wiring, the handler logic already exists and is tested |
| Second AgentCore Runtime entrypoint (a `webhook` payload shape on the same runtime, fronted by API Gateway → `InvokeAgentRuntime`) | Reuses Decision 7's blob-payload lesson; one runtime, two logical entrypoints | Medium: same blob-marshalling problem Decision 7 already solved once, needs redoing for synchronous request/response instead of fire-and-forget |

Either needs: a public HTTPS endpoint, `setWebhook` registered with
Telegram, and — because this project's authorization model
(ADR-012, ADR-014 Decision 5) has never had to defend against an
internet-facing endpoint before — a fresh look at what an unauthenticated
POST to that endpoint can and can't do. Not scoped further here.

## Documentation corrected, not just flagged

A judge reading `README.md`/`ARCHITECTURE.md` would find this gap the
same way it was found here — by checking, not by being told. Both docs
currently read as if registration, escalation replies, and `/help` run
live. Corrected in this pass:

1. **`README.md`'s architecture diagram** (the ASCII block under
   "Architecture summary") draws an arrow from Telegram into the
   AgentCore Runtime box and lists `webhook.py` alongside `notify.py` as
   one outbound unit. Split: `notify.py` stays inside the runtime box
   (real, deployed, outbound-only); `webhook.py` moves outside it,
   labeled explicitly as code-complete but not live.
2. **`README.md`'s Honesty section** — add a paragraph next to the
   existing "simulated cluster, not a live deployment" disclosure,
   stating the inbound/outbound split plainly: outbound is live and
   verified; every inbound feature has a real, tested handler with no
   live way to be reached.
3. **`README.md`'s "A farmer self-onboards entirely by messaging the
   bot... no code, no external step"** line — true of the tested code
   path, not true of the deployed system. Add the same caveat.
4. **`ARCHITECTURE.md`'s "Request flow, end to end" step 6** — "go out
   via Telegram (`notify.py` for farmers, `webhook.py` for the
   operator's escalation response)" wrongly implies `webhook.py` is part
   of an outbound flow. Rewritten to state the escalation message going
   out is real (`notify.py`); the operator's reply coming back would need
   `webhook.py`, which has no live path.
5. **`ARCHITECTURE.md`'s Package layout table**, the `telegram/*` row —
   "unreachable live" alone would collapse two different states into
   one: code that has only ever run in tests, and code that has
   genuinely run against a real phone, just not through the deployed
   path. Corrected to distinguish them per module: `client.py`/
   `notify.py` verified against a real phone (the deployed outbound
   path — ADR-006 Decisions 9/10/11); `registration.py` verified
   against a real phone locally via `run_polling.py` (the "yes-words
   widened for real phone typing" work, ADR-008; the flow
   `run_polling.py`'s own docstring says it exists to exercise);
   `webhook.py` also verified against a real phone locally, for the
   dispatch paths that carried those same sessions (registration,
   proxy registration — receipted on a real device per ADR-013
   Decision 21) — not confirmed that way for every callback branch it
   routes (escalation resolution, `/help`, route taps specifically
   have no documented real-device session). A reader should be able to
   tell "we built this" from "we have watched this work, just not
   deployed" — those are different claims and the table now makes
   them separately.
6. **`docs/generate_architecture_diagram.py`** — the PNG has the same
   two problems as the ASCII diagram (an inbound arrow from Telegram,
   and a combined "notify.py / webhook.py" box inside the runtime
   container). Fixed at the generator, then regenerated, so the image
   and the text agree. Worth naming as its own small lesson: the same
   incorrect belief — that `webhook.py` runs inside the deployed
   runtime — was encoded twice, independently, once in prose and once
   in a diagram generator. Fixing only the rendered PNG without fixing
   the generator would have left the wrong belief in the one place set
   up to survive a future "just regenerate the diagram" — the next run
   of the script would have silently restored the exact thing this ADR
   corrects.

## Demo video note — record now, before it's lost between here and filming

Any filmed inbound interaction — registration, `/help`, an escalation
tap, a harvest-confirmation reply — necessarily runs through local
polling (`run_polling.py`) or the test suite, never the deployed path,
because there is no deployed path for it to run through. The video must
say so plainly wherever it shows one of these, the same way `docs/DEMO.md`
already distinguishes Beat 4 (`trigger_scenario.py`, local) from Beat 5
(deployed runtime) for the outbound side. Not saying so would let the
video imply exactly the thing this ADR exists to correct in the written
docs. `docs/DEMO.md` should get the same treatment when the runbook is
next revised — flagged here so it isn't rediscovered independently
later; not edited in this pass, since this ADR's scope is `README.md`/
`ARCHITECTURE.md`.

## README verification finding

Added as its own entry, not folded into the eight or the "equality
check" pair — different in kind and larger in scope than any of them:
every prior finding was "this code has a bug." This one is "this code
was correct and reachable in every test, and unreachable in production,
and nothing ever checked which of those two questions it was answering."
Tests confirm behavior given an input; nothing confirmed the input could
ever arrive. Found the same way as everything else in that section: by
tracing an actual path end to end (what does the deployed runtime
actually invoke?) instead of trusting that passing tests meant the
feature worked in production.

## Consequences

- No code changes. Documentation now states plainly what runs live and
  what doesn't, with a pointer to this ADR for anyone who wants the
  full trace.
- The webhook gap is a real, scoped, future ADR — not committed to a
  timeline here.
- Nothing about the daily-watch path (verified live on v12, ADR-006
  Decision 11) changes.
