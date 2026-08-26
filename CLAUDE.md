# CLAUDE.md

Operational rules for working on Harvest Convoy. For what the system is
and how it's built, see [ARCHITECTURE.md](ARCHITECTURE.md); its ADR
index has a fuller per-ADR summary than the one-liners below.

## ADR index — decisions only

For session-start triage, not for citing reasoning. Each line is what an
ADR decided, nothing more — the argument, failure paths, and sourcing
live in the ADR itself. **Do not read every ADR at session start.**
Read only the ones a given task actually names, using this index to
figure out which those are. All Implemented unless marked otherwise.

- **ADR-000** — Stack: Python 3.12, Strands Agents SDK, Bedrock Nova Pro
  (`ap-south-1`), Telegram Bot API, DynamoDB, AgentCore Runtime, OTel, uv, pytest.
- **ADR-001** — Agronomy core: GDD anchored to `transplant_date`;
  `T_BASE_C=10.0` sourced; ADT 45 maturity threshold derived, not sourced;
  Open-Meteo archive/forecast seam detected and bridged, not assumed.
- **ADR-002** — Scheduling core: GDD rate reconciled against real 5-year
  climatology; overripe decay is a derived linear proxy; capacity budget,
  route ordering; "too green" is a hard exclusion, not a ranking position.
- **ADR-003** — Strands agents: facts are always computed, never trusted
  from the model; one real Strands Agent construction per advocate call;
  negotiation is pairwise, capped at 3 rounds, escalates on no resolution.
- **ADR-004** — Telegram interface: raw Bot API via httpx, no library;
  registration is a pure FSM; "not ready" is a distinct message;
  escalation is one message, two tap targets, idempotent.
- **ADR-005** — Persistence: AgentCore Memory skipped deliberately;
  FileStorage (not DynamoDB Local) for dev; single-table DynamoDB design;
  fairness ledger accumulated/decayed/capped.
- **ADR-006** — Deployment: AgentCore Runtime (`codeConfiguration`,
  `PUBLIC`), Lambda shim for EventBridge→`InvokeAgentRuntime`, real
  DynamoDB, native ADOT tracing, prompt-cached Nova Pro cost (64% cut).
  Two redeploy rounds fixed a chat_id int/float bug, an Open-Meteo
  horizon gap, a missing bot token, two Tamil half-translations.
  Redeployed again 2026-08-25 to **live version 12** (Decision 11) with
  HEAD's code through ADR-015 — rollback mechanics recorded there too.
- **ADR-007** — number never assigned; no such document exists.
- **ADR-008** — TN generalization + Tamil: multi-cluster/multi-climate
  support, per-cluster calibrated GDD; Tamil interface, hand-authored
  strings only (Nova Pro can't reliably produce valid Tamil).
- **ADR-009** — Harvest lifecycle: 2025 Kuruvai backtest; plot harvest
  lifecycle; harvest confirmation loop (`bool | None` tri-state, silence
  is never a no-show); projected maturity date stored at registration;
  post-harvest drying-window rain alert.
- **ADR-010** — Reporting: persisted `DecisionRecord` per plot per
  trigger day; decision replay script; equity report; machinery gap
  report; CHC positioning in README (no real CHC integration).
- **ADR-011** — Seasonal rollover (opt back in per season, silence
  excludes); machine breakdown (one-tap recompute, tracked separately
  from the fairness ledger); rain event classification (duration/
  accumulation-scaled urgency); advance harvest notice (~1 week before
  maturity, stored-record only, never a live recompute).
- **ADR-012** — Callback authorization audit (closed 3 unchecked
  handlers) + operator self-enrollment via one-time code. Owns the
  authorization audit table every new callback must be added to.
- **ADR-013** — Part 1: route proposal, Accept/Modify, operator
  override. Part 2: proxy registration (`/addfarmer`), farmer linking
  (`/linkfarmer`), bounded-window Undo. Part 3: farmer-facing "why?"
  answers for not-ready/lost, from stored records only.
- **ADR-014** — `/help`: read-only status command (farmer's plot /
  operator's cluster+commands); farmer view wins when the operator also
  farms; the one deliberate exception to "no new farmer-initiated surface."
- **ADR-015** — *Proposed, awaiting go-ahead.* Fixes `DynamoStorage`'s
  float/int coercion bug (`Plot`/`Cluster`/`DecisionRecord` float fields)
  with explicit per-type deserializers + a reflection-based completeness
  test guarding against a repeat.

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
- **From ADR-016 onward, keep ADRs to decisions and consequences, with
  reasoning compressed.** The argument matters; the exploration of it
  doesn't need to survive at full length. Existing ADRs (through
  ADR-015) stay as written — don't retroactively trim them.
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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
