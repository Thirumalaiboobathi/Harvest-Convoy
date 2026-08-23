# CLAUDE.md

Operational rules for working on Harvest Convoy. For what the system is
and how it's built, see [ARCHITECTURE.md](ARCHITECTURE.md) and the ADR
index inside it (`docs/adr/`) — read the relevant ADRs before touching
adjacent code; don't restate their content here, extend it there.

## The one architectural rule

**The LLM never computes a number.** GDD, maturity, capacity, route
order, fairness weight — all deterministic Python, unit-tested like any
other function. The LLM only argues a plot's case under genuine
ambiguity and writes the message a farmer reads (and even then, every
factual field on its structured output is overwritten with ground truth
before use — see ADR-003 Decision 1). If a change would let a model
produce a number that reaches a scheduling decision or a human, it's
wrong regardless of how it tests.

## Honesty discipline

- Never fabricate a value, a cited source, an API's behavior, or a test
  result. If something can't be verified live, say so plainly rather
  than implying it was checked.
- Every constant needs a source URL or an explicit `# DERIVED:` /
  `# TUNING:` comment explaining the derivation chain (or the lack of
  one). No bare numbers, ever — see `crop_params.py` and
  `scheduling/rain_event.py` for the two established conventions.
- A number that "sounds right" from a search summary is not sourced
  until directly fetched and confirmed against primary text (ADR-002
  Decision 2 has a real example of this failing).

## Process

- **ADR before code, every time.** Write it, show it, wait for explicit
  go-ahead before implementing — including for each part of a
  multi-part ADR where the ADR says to stop and report.
- **Failure paths are designed up front, not discovered later.** Every
  ADR needs an explicit answer for the obvious ways a new flow breaks
  (double-tap, stale state, missing chat_id, weather unavailable,
  restart mid-flow) before implementation starts.
- Nothing touches the deployed AgentCore artifact without asking
  separately, even when the underlying code change is approved.
- Commit at ADR-specified boundaries, not continuously through one.

## Code discipline

- **Never throw from the agent loop or a storage write.** Catch, log
  loudly, degrade — a failed advocate call, a failed Telegram send, a
  failed storage write must never take down a scheduling run or block
  notifying other farmers.
- **Authorization is enforced from the first commit on every new
  callback** — state whose authority the action requires, check the
  tapping identity against it before any state mutation, and add a test
  proving a wrong-party tap is refused and changes nothing. ADR-012
  found three handlers shipped with no check at all; don't add a
  fourth. Add every new callback to ADR-012's audit table.
- **No new farmer-initiated surface.** Every farmer-facing message is
  either the agent speaking first or a bounded one-tap reply to
  something it already asked. If a design needs free-text parsing or a
  multi-turn back-and-forth from a farmer, that's the chat-flow drift
  ADR-011 flags explicitly — stop and raise it rather than building it.
- **Tamil is hand-authored, never model-generated.** Nova Pro cannot
  reliably produce valid Tamil script (ADR-008 Decisions 14–15,
  live-verified, not assumed). Every Tamil string ships as a first
  draft, gets dumped via `scripts/print_tamil_strings.py`, and is
  printed for review before it ships — including on a real device via
  live Telegram receipt, since string-level review alone has missed
  real bugs before (ADR-008 Decision 17).

## Permanently out of scope

Dashboards, maps, or any web/query UI; DPC/mandi price or scheme/loan
lookups; pest, disease, soil, or irrigation guidance; new crops beyond
paddy ADT 45; and any integration with a system this project has no
real API access or data-sharing agreement with (e.g. CHC/AGRISNET —
see ADR-010 Part 4). The test for anything new: does the agent already
know enough to speak first? If not, it's out of scope until that
changes.
