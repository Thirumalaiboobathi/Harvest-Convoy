# Harvest Convoy — Feature Inventory

Code-grounded audit for the Devpost submission and demo video. Every claim
below cites a module and, where relevant, a test. ADRs were used only to
know what to go verify — never as the source of truth. Section 8 and the
"Documentation drift" callouts throughout are the parts most likely to
change what you say on camera.

**Headline finding, load-bearing for the whole document:** the deployed
AgentCore artifact is **outbound-only**. `main.py` imports nothing but
`harvest_convoy.app:app` and calls `app.run()`. `src/harvest_convoy/app.py`
defines exactly one `@app.entrypoint`, a synchronous `handler(payload)`
(`app.py:27-43`), which validates `cluster_id`/`season_id` and delegates
immediately to `watcher.run_daily_watch(...)` (`app.py:43`). There is no
other logic in either file. `webhook.py` (and everything it dispatches
to — registration, `/help`, `/addfarmer`, `/linkfarmer`, every callback)
is bundled into the deployment zip as part of the `harvest_convoy` source
tree, but nothing in the deployed artifact ever calls
`webhook.handle_update()`. The only two callers of that function in the
whole repo are `scripts/run_polling.py:71` and `scripts/trigger_scenario.py:198`
— both local, developer-run processes. This matches
`docs/adr/ADR-016-no-live-inbound-path.md` exactly; ADR-016 is the one ADR
that specifically discloses this gap. Read every "reachability" note below
against this fact.

---

## 1. Inbound features (farmer-initiated)

**Every row in this section has the same answer to "deployed reachability":
NOT reachable via the deployed AgentCore artifact. Reachable only by
running `scripts/run_polling.py` locally against a real bot token** (long-polls
Telegram's `getUpdates` and feeds each update to `webhook.handle_update()`,
`run_polling.py:71`; its own docstring says so: *"NOT the production path
-- AgentCore Runtime with a real webhook is Phase 6,"* `run_polling.py:3-4`).
No `setWebhook` call, API Gateway, or second Lambda pointed at `webhook.py`
exists anywhere in the repo.

### `/start` — does not exist as a command

There is no `/start` handler. `webhook.handle_update` never checks for that
string. Registration begins implicitly on the *first message of any kind*
from an unseen `chat_id`: `registration.get_or_create_state` (`registration.py:345-348`)
defaults a new chat to `AWAITING_VILLAGE, greeted=False`, and
`advance_registration`'s first branch (`registration.py:264-269`) sends the
bilingual greeting whenever `not state.greeted`, regardless of message
content. If any external material describes `/start` as an explicit
command, that's not what the code does — say "the first message you send"
instead.

### Registration FSM

`src/harvest_convoy/telegram/registration.py`, driven by
`advance_registration` (pure transition function, `registration.py:255-337`)
and `handle_incoming` (stateful wrapper, `registration.py:480-507`, called
from `webhook.py:1936`).

| Step | Trigger | Behavior |
|---|---|---|
| Greeting | first message, `greeted=False` | bilingual greeting + `lang:ta`/`lang:en` keyboard (`registration.py:107-128`) |
| Language pick | callback `lang:{code}` | `handle_language_callback` (`registration.py:510-547`); requires `tapper_id == chat_id` (line 530-535) |
| Village | free text | `_contains_tamil_script` infers language (line 134-135) |
| Location | Telegram `location` attachment | sets lat/lon; retries on missing location |
| Crop confirm | yes/no via word lists (`_is_yes`/`_is_no`, line 145-169) | "no" → terminal decline step |
| Transplant info | free text, parsed date + area (`_parse_date`/`_parse_area`, line 216-252) | both required, else re-prompts for what's missing |
| Complete | internal | `_persist_completed_registration` (`registration.py:396-477`) writes `Farmer`/`Plot`, appends a projected-maturity sentence (degrades silently on weather failure) |

Both languages: `messages_en.py`/`messages_ta.py`, function names mirrored
1:1 (e.g. `GREETING_INTRO`, `COMPLETE_MESSAGE`).

### `/help`

`src/harvest_convoy/telegram/help.py`. Constant `HELP_COMMAND = "/help"`
(`help.py:22`); dispatched in `webhook.py:1880-1884` before registration/
rollover/addfarmer routing so it never disturbs pending FSM state (verified
by `tests/test_help.py::test_help_mid_registration_leaves_pending_state_untouched`).
Read-only — no storage mutation (`help.py:53-102`). Farmer view shows
village/area/transplant date and, if present, a stored advance-notice
maturity date (no live recompute, per the module docstring `help.py:1-8`).
Operator view shows cluster + command list. Farmer view wins when the
operator is also a farmer, with an addendum line (`help.py:97-102`) — this
is ADR-014's one deliberate exception to "no new farmer-initiated surface,"
and it matches the code.

### `/addfarmer` (proxy registration)

`src/harvest_convoy/telegram/proxy_registration.py`, constant
`ADD_FARMER_COMMAND` (`proxy_registration.py:47`). Operator-only gate at
`webhook.py:1894-1913` checks `chat_id == cluster.operator_chat_id` before
starting anything (test: `tests/test_proxy_registration.py::test_addfarmer_from_non_operator_is_refused_and_starts_no_flow`).
Free-text flow (name → contact note → village → location → crop confirm →
transplant info → has-phone) ending in a callback confirmation
(`addfarmer_confirm:{cluster}:{yes|no}`, `webhook.py:1215-1270`), re-checked
by `_is_operator` against the tapping identity, not the embedded id
(`webhook.py:1232`, deliberately, per the inline comment — an
embedded-operator-id check would be self-referential and unsafe). On
confirm, mints disjoint `farmer-proxy-*`/`plot-proxy-*` ids
(`proxy_registration.py:263-274`) and writes `Farmer`/`Plot` with
`registered_by="operator:{chat_id}"`.

### `/linkfarmer`

Same file. Fully stateless after the initial command — every id travels in
`callback_data`, no server-side FSM state. Four-step tap chain, each step
re-checking `_is_operator` independently:
`linkfarmer_proxy:{farmer_id}` (`webhook.py:1280-1308`) →
`linkfarmer_match:{proxy}:{candidate}` (`webhook.py:1319-1356`) →
`linkfarmer_confirm:{proxy}:{candidate}:{yes|no}` (`webhook.py:1367-1420`,
calls `apply_link`, `proxy_registration.py:418-455`, which transplants the
candidate's `telegram_chat_id` onto the canonical proxy farmer id and
retires the candidate's plot) → `linkfarmer_undo:{proxy}:{candidate}:{epoch}`
(`webhook.py:1431-1475`, calls `undo_link`, `proxy_registration.py:490-535`,
refuses past a 1-hour window or if already superseded). Tests:
`tests/test_link_farmer.py` (non-operator refusal at each step: lines 226,
239, 251, 341; expiry at 305; double-undo at 323).

### `/operator <code>` enrollment

`src/harvest_convoy/telegram/operator_enrollment.py`, constant `COMMAND =
"/operator"` (`operator_enrollment.py:48`), dispatched before registration
(`webhook.py:1869-1872`). Validates the one-time code (`validate_code`,
lines 140-155), then requires the *same chat_id that issued the command* to
tap the follow-up language keyboard —
`operator_enrollment.matches_pending(tapper_id, cluster_id, code)`
(`webhook.py:1058`) — closing a race where two chats could hold the same
valid code. Replacing an existing operator requires an explicit second
confirmation (`operator_replace:` callback, `webhook.py:1130-1202`) rather
than silent overwrite, and logs a `"replaced"` `OperatorAuditEvent`. Tests:
`tests/test_operator_enrollment.py` (lines 219, 241, 349).

### Every `callback_data` handler in `webhook.py`

Dispatch order at `webhook.py:1780-1849`. Every handler answers the
callback unconditionally (Telegram requires an answer even on refusal).

| callback_data | Handler | Authorization check | Wrong-party test |
|---|---|---|---|
| `lang:` | `registration.handle_language_callback` | `tapper_id == chat_id` | (in `test_registration.py`, out of this audit's scope) |
| `confirm:{plot}:{season}:{yes\|no}` | `handle_confirmation_callback` (`webhook.py:136-252`) | `_is_farmer` | `test_farmer_authorization.py:96,114,125,136` |
| `rollover:{plot}:{new_season}:{yes\|no}` | `handle_rollover_callback` (`webhook.py:265-348`) | `_is_farmer` | `test_farmer_authorization.py:174,188,206` |
| `breakdown:{cluster}:{season}:{date}` | `handle_breakdown_callback` (`webhook.py:386-463`) | `_is_operator` | not in the eight audited test files |
| `breakdown_followup:{cluster}:{tomorrow\|indefinite}` | `handle_breakdown_followup_callback` (`webhook.py:476-533`) | `_is_operator` | not in the eight audited test files |
| `machine_back:{cluster}` | `handle_machine_back_callback` (`webhook.py:543-586`) | `_is_operator` | not in the eight audited test files |
| `route_accept:...` | `handle_route_accept_callback` (`webhook.py:716-739`) | shared `_resolve_route_context` → `_is_operator` + same-day staleness check | `test_route_override.py:142,159,404` |
| `route_modify:...` | `handle_route_modify_callback` (`webhook.py:742-770`) | same | `test_route_override.py:173` (opens editor); **no dedicated non-operator test isolated to this branch** |
| `route_swap:...:{position}` | `handle_route_swap_callback` (`webhook.py:773-823`) | same, plus already-confirmed guard | `test_route_override.py:424,211` |
| `route_drop:...` | `handle_route_drop_callback` (`webhook.py:826-875`) | same | `test_route_override.py:232`; no branch-isolated non-operator test found |
| `route_drop_confirm:...:{yes\|no}` | `handle_route_drop_confirm_callback` (`webhook.py:878-967`) | same; this one does the real mutation (un-harvest, cancel confirmation, fairness bump, notify) | `test_route_override.py:345,323` |
| `route_done:...` | `handle_route_done_callback` (`webhook.py:970-1020`) | same | `test_route_override.py:367,386`; no branch-isolated non-operator test found |
| `operator_lang:...` | `handle_operator_lang_callback` (`webhook.py:1033-1117`) | `matches_pending` | `test_operator_enrollment.py:219,241` |
| `operator_replace:...` | `handle_operator_replace_callback` (`webhook.py:1130-1202`) | `matches_pending` | `test_operator_enrollment.py:349` |
| `addfarmer_confirm:...` | `handle_addfarmer_confirm_callback` (`webhook.py:1215-1270`) | `_is_operator` | `test_proxy_registration.py:204` |
| `linkfarmer_proxy:` / `_match:` / `_confirm:` / `_undo:` | see above | `_is_operator` each step | `test_link_farmer.py:226,239,251,341` |
| `why_notready:` / `why_lost:` | `_handle_why_callback` (`webhook.py:1486-1541`) | `_is_farmer` | not in the eight audited test files |
| `resolve:{cluster}:{plot_a}:{plot_b}:{chosen}` (fallthrough, no distinguishing prefix) | `handle_callback_query` (`webhook.py:1544-1730`) | `_is_operator`, checked before any ledger write | `test_operator_authorization.py:103,129,149,180` |

The `resolve:` handler is the most consequential one: it writes real
`LedgerEntry` fairness-bump records for winner and loser, is protected by a
durable idempotency boundary (the loser's ledger write itself, not just an
in-memory set — `webhook.py:11-18`), and updates `DecisionRecord.resolution`.

**Cross-check against ADR-012/013/014**: all three match the code with no
drift on the authorization mechanism itself. The risk is narrative, not
factual — none of ADR-012/013/014 individually states "this is unreachable
in production"; only ADR-016 does. Read them together.

---

## 2. Outbound features (agent-initiated)

The daily watcher (`src/harvest_convoy/watcher.py`) is the only code that
runs on a real schedule (`app.py:43` → `run_daily_watch`). Everything in
this section marked "Yes" under "fires from deployed daily watch" is
therefore live in production today; everything marked "No" requires a
human to run a script or tap a button that reaches `webhook.py` (itself
only reachable locally, per Section 1).

| Message | Trigger (file:line) | Module | Fires from deployed daily watch? |
|---|---|---|---|
| `harvest_scheduled` | `PlotOutcome.FITS` in `_send_notifications` (`watcher.py:394-396`) | `notify.send_harvest_scheduled` (`notify.py:204-213`) | **Yes** — normal trigger path (`watcher.py:330-334`); also re-sent by `handle_machine_breakdown`'s recompute and by the operator's route-edit "Done" tap (both non-scheduled paths) |
| `not_ready` | `PlotOutcome.TOO_GREEN` (`watcher.py:385-393`) | `notify.send_not_ready` (`notify.py:237-257`), attaches a "why?" button | **Yes**, same path |
| `route_summary` (operator) | non-empty `fits_route` (`watcher.py:422-448`), writes a `RouteOverride` record first so Accept/Modify have something to act on | `notify.send_operator_route_summary` (`notify.py:474-504`) | **Yes**, same path |
| Escalation (two tap targets) | `result.escalations` non-empty (`watcher.py:450-466`) | `notify.send_escalation` (`notify.py:661-687`); exactly two buttons, matches ADR-004 Decision 4 | **Yes**, same path; also sent manually by `scripts/trigger_scenario.py:281-288` with an engineered deadlock for demos |
| Escalation resolved (winner/loser follow-up) | operator taps a `resolve:` callback | `notify.send_escalation_resolved`, called from `webhook.py:1703,1713` | **No** — reply-to-a-tap, requires the (locally-only) inbound path |
| Drying-window rain alert | runs unconditionally every pass (`watcher.py:246-251`); fires when upcoming rain exceeds threshold AND the plot's harvest is confirmed and not yet alerted, within a 4-day window (`watcher.py:477-496`) | `watcher._check_drying_window_alerts` (`watcher.py:469-523`) → `notify.send_drying_window_alert` | **Yes** — called directly inside `_run_daily_watch_one` |
| Advance harvest notice (~1 week before maturity) | runs unconditionally every pass; `0 < days_until <= 7` (`ADVANCE_NOTICE_DAYS_BEFORE_MATURITY`, `watcher.py:84`), no existing `AdvanceNoticeRecord` for the season | `watcher._check_advance_harvest_notices` (`watcher.py:526-600`) → `notify.send_advance_harvest_notice` | **Yes** — called directly inside `_run_daily_watch_one`. **ADR-011's "stored-record only, never a live recompute" claim is verified true**: the mere existence of a record is the entire suppression check (`watcher.py:560-561`); no later, more-accurate projection ever re-sends or corrects it. |
| Seasonal rollover opt-in prompt | `run_season_rollover(cluster_id, old_season, new_season)`, per plot not yet prompted (`watcher.py:787-830`) | `notify.send_season_rollover_prompt` (`notify.py:572-584`) | **No** — "code-only entrypoint, not wired to any schedule yet" is explicit in the docstring (`watcher.py:761-767`); no caller in `app.py`/`main.py` |
| Harvest confirmation prompt (tri-state `bool \| None`) | `run_evening_confirmations`, for every `HarvestConfirmation` with `asked_at is None` (`watcher.py:637-712`) | `notify.send_harvest_confirmation_prompt` (`notify.py:533-545`) | **No** — docstring says verbatim "Code-only entrypoint -- not wired to any schedule yet" (`watcher.py:645`); a second EventBridge schedule would be a separate, explicit decision |
| Machine breakdown re-notify | operator taps "machine down today"; recomputes `solve()` against reduced capacity and re-sends `harvest_scheduled`/`not_ready`/`route_summary` | `watcher.handle_machine_breakdown` (`watcher.py:858-1030`) | **No** — operator-tap-triggered via `webhook.py`, never called from `app.py`/`main.py` |
| Route-dropped notice | operator confirms a drop in the route-edit UI | `notify.send_route_dropped_notice` (`notify.py:299-311`), called from `webhook.py:951` | **No** — operator-tap-triggered |

Both languages for every message type: `messages_en.py`/`messages_ta.py`,
mirrored function names throughout.

### Documentation drift — the fairness-bump-on-silence claim in ADR-009 does not exist in code

**ADR-009 Decision 6** claims that a farmer whose harvest confirmation goes
unanswered past `UNCONFIRMED_HARVEST_WINDOW_DAYS` gets a fairness-ledger
bump written (`record_bump(..., outcome="unconfirmed")`) on the next daily
run. **The actual code explicitly does not do this.**
`watcher.py:62-66`'s own comment states the opposite, deliberately: *"Per
explicit instruction (ADR-009 Part 2), silence past this window is
recorded as UNKNOWN, never as a no-show -- it gates only how a still-
unanswered HarvestConfirmation is reported ... never a fairness ledger
write."* Confirmed by grep: there is no `record_bump(..., outcome=
"unconfirmed")` call anywhere in `src/harvest_convoy/`; the only
`record_bump` call sites are in `webhook.py:232,938,1602,1634` (escalation-
loss and explicit "no"-tap paths). **Silence carries zero fairness
consequence in the shipped system, contrary to what ADR-009 as written
says.** Do not claim on camera that a farmer is penalized for not
answering the evening confirmation prompt — they aren't, and (per the
table above) that prompt isn't even reachable in production today anyway.

---

## 3. Deterministic scheduling

Confirmed via grep across `agronomy/` and `scheduling/` for
`bedrock|nova|strands|BedrockModel`: zero real imports. Every decision
below is plain Python, unit-tested.

- **GDD accumulation** — `daily_gdd(t_max, t_min, t_base)` =
  `max(0, mean(t_max,t_min) - t_base)`; `accumulate_gdd` sums it over days;
  `project_maturity_date` finds the first day a running sum crosses a
  threshold. `src/harvest_convoy/agronomy/gdd.py:22-47`. Tests:
  `tests/test_gdd.py`.
- **Per-cluster threshold calibration** — `derive_reference_gdd_rate`
  fetches 5 years of real Open-Meteo Archive temperatures for a fixed
  May15–Aug12 window and averages GDD/day for that location;
  `derive_cluster_maturity_gdd` scales by the 90-day estimated field
  duration. `src/harvest_convoy/agronomy/calibration.py:38-84`. Network
  call, but the calculation itself is pure arithmetic — no model call.
  Tests: `tests/test_calibration.py`.
- **Sourced/derived constants** — `T_BASE_C=10.0` (cited), `ADT45_FIELD_
  DURATION_DAYS_ESTIMATED=90`, `MATURITY_GDD_ESTIMATED` (fallback
  threshold when a cluster is uncalibrated), `DECAY_HORIZON_DAYS_
  ESTIMATED=20` (explicitly a ranking proxy, not a validated curve).
  `src/harvest_convoy/agronomy/crop_params.py`.
- **Overripe decay proxy** — `decay_fraction(days_past_maturity) =
  min(1.0, days/DECAY_HORIZON_DAYS_ESTIMATED)`, explicitly documented as
  linear and not agronomically validated. `agronomy/decay.py:16-26`.
  Tests: `tests/test_decay.py`.
- **Capacity budget** — `usable_harvest_days` counts consecutive dry
  forecast days from day 0 until the first rain breach (window never
  reopens); `harvest_day_budget_acres = usable_days * machine_capacity`.
  `src/harvest_convoy/scheduling/capacity.py:21-44`. Tests:
  `tests/test_capacity.py`.
- **Route ordering** — haversine distance + greedy nearest-neighbor, tie-
  broken deterministically by `plot_id`. `src/harvest_convoy/scheduling/
  route.py:23-53`. Tests: `tests/test_route.py`.
- **Plot classification (fits/contested/too-green/harvested)** —
  `solve()`: excludes already-harvested plots; assesses remaining against
  the calibrated-or-fallback maturity threshold (`resolve_maturity_gdd`,
  logs a warning on fallback); anything below threshold is `TOO_GREEN` and
  structurally excluded from contention (never re-enters even with spare
  capacity); ready plots are ranked by `(-urgency, -days_past_maturity,
  plot_id)` and greedily allocated against the capacity budget — plots
  that fit stay `FITS`, overflow becomes `CONTESTED`. `src/harvest_convoy/
  scheduling/solver.py:51-289`. Tests: `tests/test_solver.py` (415 lines,
  covers too-green structural exclusion, budget edge cases, determinism).
- **Rain event classification** — first breach + contiguous wet run;
  `SUSTAINED` if duration ≥3 days OR total accumulation ≥50mm OR single-day
  ≥40mm (any one trigger), else `BRIEF`; a wet run still open at the end of
  the fetched forecast is classified `SUSTAINED` out of caution. Adds a
  flat `+0.15` urgency boost, applied only to ready plots (never to
  `TOO_GREEN`). `src/harvest_convoy/scheduling/rain_event.py:56-119`.
  Tests: `tests/test_rain_event.py`.
- **Fairness weight** — `weighted_bump_days = Σ days_bumped * 0.5^seasons_ago`
  (geometric decay, most recent season weighted 1.0). `src/harvest_convoy/
  storage/fairness.py:31-42`. The invariant that this can never override
  genuine urgency is enforced in `agents/coordinator.py`, not here (see
  Section 4). Tests: `tests/test_fairness.py`,
  `tests/test_fairness_ledger_gate.py` (behavioral proof that fairness
  cannot flip a maximally urgent plot, line 134).

---

## 4. LLM-mediated decisions

**Exactly one call site in the entire codebase constructs a real
`strands.Agent` against Bedrock**: `agents/advocate.py:196-204`, inside
`get_advocate_claim()`. `_build_model()` returns `BedrockModel(model_id=
"apac.amazon.nova-pro-v1:0", region_name="ap-south-1")`
(`advocate.py:26-27,100-101`). `coordinator.py` never touches Strands/
Bedrock directly — it calls `advocate.get_advocate_claim` indirectly
through an injectable `ClaimProvider` callable (`coordinator.py:285-289`).
Confirmed via broad grep of `src/` and `scripts/` for
`BedrockModel|nova-pro|strands\.Agent|structured_output_model` — no other
call site exists. (`app.py`'s `from bedrock_agentcore import
BedrockAgentCoreApp` is the deployment-hosting wrapper, not an LLM call.)

### `advocate.get_advocate_claim`

- **What's asked**: per-round prompt with plot readiness, days past
  maturity, urgency (0–1), rain vulnerability, acreage; round ≥2 adds the
  opponent's prior argument and an instruction to check the fairness
  ledger before re-arguing or conceding (`advocate.py:136-157`). A real
  `@tool`-decorated `fairness_lookup` backed by `storage/fairness.py` is
  available to the agent (`advocate.py:112-133`).
- **What's overwritten with ground truth**: after a successful call,
  `AdvocateClaim.from_facts(facts, argument=..., concedes=...)`
  (`contracts.py:92-109`) rebuilds every field from `PlotFacts` except
  `argument` and `concedes` — **the model's only two load-bearing
  outputs**. Verified by `tests/test_advocate.py::test_successful_call_
  overrides_model_echoed_facts_with_ground_truth` and
  `tests/test_contracts.py::test_claim_from_facts_uses_ground_truth_not_
  model_input`. This matches ADR-003 Decision 1 exactly.
- **One real nuance**: for a Tamil-registered farmer, the model's
  `argument` text is *also* discarded and replaced with a deterministic
  Tamil template driven by ground truth plus the model's `concedes`
  decision (`advocate.py:225-233`, per ADR-008 Decision 14 — Nova Pro
  cannot reliably produce valid Tamil). Only for English-registered
  farmers is the raw model text kept (`advocate.py:235`).
- **On failure**: wrapped in `try/except Exception` explicitly annotated
  "degrade, never throw from the agent loop" (`advocate.py:195-215`).
  Records the exception on the OTel span, logs, and returns
  `_fallback_claim(facts, reason)` built entirely from ground truth, with
  `concedes = not facts.is_ready` and `degraded=True` set for downstream
  presentation (`notify.py` omits fake "agent's case" text when
  `degraded`). Matches CLAUDE.md's "never throw from the agent loop" rule
  and ADR-003 Decision 3. Tests: `tests/test_advocate.py` lines 30, 48, 60.
  A real live-model round-trip test exists at
  `tests/test_advocate_live.py` (network-marked).

### `coordinator.negotiate_pair`

- `MAX_NEGOTIATION_ROUNDS = 3` (`coordinator.py:41`) — matches ADR-003's
  "capped at 3 rounds" exactly.
- Resolves immediately on concession (ties broken by raw urgency if both
  concede); otherwise scores `urgency_score + fairness_bonus` for both
  sides and resolves outright once the score gap exceeds `CLEAR_MARGIN=0.15`;
  breaks to escalation rather than re-arguing once `round_num ==
  max_rounds`; otherwise gives the losing side one more claim call with the
  opponent's argument surfaced (`coordinator.py:150-212`).
- **Fairness-cannot-override-urgency invariant** is a load-bearing runtime
  assertion, not just a comment: `MAX_FAIRNESS_BONUS = (1/DECAY_HORIZON_
  DAYS_ESTIMATED) * 0.8` with `assert MAX_FAIRNESS_BONUS < _URGENCY_
  GRANULARITY` at module import time (`coordinator.py:61-66`) — the import
  itself fails if this invariant were ever violated by a constant change.
- **Escalates on no resolution**: exhausting all rounds returns
  `NegotiationResult(None, max_rounds, claim_a, claim_b, escalated=True)`
  (`coordinator.py:211-212`) — matches ADR-003 exactly.
- Every `TOO_GREEN`/`FITS` plot gets exactly one advocate call each; only
  `CONTESTED` plots are paired sequentially by rank, with an odd one out
  getting a single unpaired claim (`coordinator.py:297-462`) — pairwise
  only, not a general N-way auction, per ADR-003's disclosed scope.
- Tests: `tests/test_coordinator.py` (concession, clear-margin, escalation,
  fairness-tilts-close-not-clear, rain-boost-never-inverts-ordering),
  `tests/test_kamatchipuram_agents_gate.py`.

### Documentation drift

**ADR-003 Decision 2 is stale.** It describes `agents/fairness_stub.py` (an
in-memory dict) as the current fairness mechanism, framed as a future
"Phase 5 replaces the dict with DynamoDB." That file no longer exists as
source (only a stale compiled `.pyc` remains). The real, shipped mechanism
is `storage/fairness.py`'s persisted ledger — the migration already
happened; ADR-003 (still marked "Proposed, awaiting go-ahead" in its own
header) was never updated to say so. All of ADR-003's other decisions
(round cap, escalation trigger, ground-truth overwrite, never-throw
behavior, pairwise scope) match the code exactly.

---

## 5. Storage and audit

Two backends implement one `Storage` Protocol
(`src/harvest_convoy/storage/interface.py:347`): `FileStorage` (plain JSON,
`.data/harvest_convoy.json`, dev default, explicitly not concurrency-safe —
`file_storage.py:6-10`) and `DynamoStorage` (real boto3, single-table
design with one GSI, short timeouts so a hung call degrades fast rather
than blocking the agent loop — `dynamo.py:46`). Every entity below is
supported identically by both backends.

| Entity | ADR that introduced it |
|---|---|
| `Farmer`, `Plot`, `Cluster` | pre-ADR-005 core models; persistence added by ADR-005 |
| `LedgerEntry` (fairness ledger) | ADR-005 Decision 2/4 |
| `HarvestConfirmation` | ADR-009 Part 2 |
| `DecisionRecord` | ADR-010 Part 0.5 |
| `SeasonRolloverPrompt` | ADR-011 Part 1 |
| `BreakdownDisplacement`, `MachineStatus` | ADR-011 Part 2, Decision 8 |
| `AdvanceNoticeRecord` | ADR-011 Part 4, Decision 17 |
| `OperatorEnrollmentCode`, `OperatorAuditEvent` | ADR-012 Part 2 |
| `RouteOverride` | ADR-013 |

Note: ADR-005's own key-scheme table only documents the four entities that
existed when it was written; the nine added later are each documented at
their own point of introduction in `interface.py`'s docstrings, but never
rolled back into ADR-005's table. This is staleness-by-omission, not a
contradiction.

**ADR-015's claimed fix (per-type float/int deserializers +
reflection-based completeness test) is verified accurate in full.**
`_from_decimal` (`dynamo.py:65-83`), the `*_FLOAT_FIELDS` constant lists,
and the per-type deserializers (`_item_to_cluster`, `_item_to_plot`,
`_item_to_decision_record`) all exist exactly as described and are wired
into every relevant read path. The reflection-based completeness test
(`tests/test_dynamo_storage.py:310-355`, using `typing.get_type_hints()`)
asserts every float field across all dataclasses is covered, and a
companion test covers the nested `AdvocateClaim` case (line 358-368).
Regression guards use `type(x) is float`/`is int`, never `==`, per ADR-015's
stated discipline.

**Gap, disclosed and still true**: no chat_id → farmer GSI exists. No test
in the repo exercises `DynamoStorage` against a real or local DynamoDB
table — the only `DynamoStorage`-instantiating test points at an
unreachable localhost port to verify the degrade-on-failure path
(`test_dynamo_storage.py:371-398`). ADR-006's claim of a real live
round-trip during deployment is a one-off manual check, not a repo-committed
test.

---

## 6. Reporting and audit tools (`scripts/`)

| Script | Produces | Live AWS/network? | Audience |
|---|---|---|---|
| `backtest_2025_kuruvai.py` | Markdown table + narrative | No AWS/Bedrock; live Open-Meteo Archive HTTP | Auditor (historical validation) |
| `check_watcher_health.py` | Pass/fail checklist | **Yes** — boto3 `logs`/`cloudwatch`/`lambda`, forces `DynamoStorage` | You / ops (deployment health) |
| `clear_plot_harvest.py` | Status line | No (whichever backend is configured) | You (maintenance) |
| `equity_report.py` | Text + optional JSON | No | Auditor (fairness/equity) |
| `explain_decision.py` | Text narrative + optional JSON | No | Auditor (decision replay) |
| `generate_operator_code.py` | Prints a one-time code | No | You (provisioning) |
| `machinery_gap.py` | Text + optional JSON | No storage-AWS; live Open-Meteo forecast | Auditor (forward-looking capacity) |
| `print_tamil_strings.py` | Dumps every Tamil string | No | You (translation QA) |
| `run_polling.py` | Drives `webhook.handle_update()` locally | No AWS; live Telegram polling | You (local dev substitute for a webhook) |
| `seed_cluster.py` / `seed_cluster_naducauvery.py` | Fixture summary; `--write` persists | No AWS by default; `--calibrate` makes live Open-Meteo Archive calls | You (fixtures) |
| `trigger_scenario.py` | Drives one full scenario end-to-end, sends real Telegram messages | Live Telegram; live Bedrock unless `--offline` | You (demo-video driver) |

`equity_report.py` and `explain_decision.py` are explicitly documented as
read-only and touching neither Bedrock nor the deployed artifact — these
are the two built specifically to hand to an outside auditor.
`check_watcher_health.py` is the only script that makes real,
deployment-checking AWS calls and is squarely a dev/ops tool.

---

## 7. Deployment reality

1. **Deployed AND reachable in production**: `app.py:handler()` →
   `watcher.run_daily_watch()` → rain check → `solve()` → negotiation →
   dispatch/escalation/route notifications (outbound only). Live-verified
   across real deploys per ADR-006 Decisions 9–11 (redeploys through live
   version 12, real Lambda-shim invocations, real DynamoDB idempotency
   marker reads, a real Telegram receipt on a live phone).
2. **Deployed (bundled in the zip) but NOT reachable in production**: all
   of `telegram/webhook.py` and everything it dispatches to
   (`registration.py`, `proxy_registration.py`, `operator_enrollment.py`,
   `rollover.py`, `help.py`). The source tree is copied wholesale into the
   deployment package, but no HTTP/webhook listener, API Gateway, or
   `setWebhook` call wires it up — confirmed by absence, not just by
   ADR-016 saying so.
3. **Local-only**: `scripts/run_polling.py` (the only production-adjacent
   way to drive `webhook.handle_update()`), `scripts/trigger_scenario.py`'s
   callback-wait, everything else in `scripts/`, and the entire `tests/`
   suite.

**Minor internal ADR-006 drift**: its Decision 1 illustrative code snippet
shows `async def handler(...)` with `return await run_daily_watch(...)`.
The actual shipped `app.py:28` is a synchronous `def handler`, no `await`.
Not a functional bug — just an unrevised proposal snippet next to the
real, later implementation.

---

## 8. What is NOT implemented — do not claim these in the video

- **Live farmer/operator interaction against the deployed system.** As
  established throughout: registration, `/help`, `/addfarmer`,
  `/linkfarmer`, harvest confirmation replies, escalation-resolution taps,
  rollover opt-in taps, breakdown reports, route Accept/Modify/Swap/Drop —
  none of it works against the live AgentCore deployment today. All of it
  requires `scripts/run_polling.py` running locally. Only the daily
  outbound watch is live.
- **A real farmer pilot.** No evidence anywhere in the code, tests, or ADRs
  of real farmers using this system. Cluster/farmer/plot data in the repo
  (`seed_cluster.py`, `seed_cluster_naducauvery.py`) is fixture data for
  demos and tests.
- **Field-validated GDD thresholds.** `crop_params.py`'s constants are
  explicitly labeled `_ESTIMATED` and sourced/derived from cited papers and
  real historical weather climatology — not validated against actual field
  outcomes for any specific plot. `decay.py`'s overripe curve is explicitly
  documented as "a linear ranking proxy, not a validated agronomic
  yield-loss curve" (`decay.py:7-8`).
- **Multi-crop support.** The system is paddy ADT 45 only, hardcoded
  throughout `agronomy/crop_params.py`; explicitly out of scope per
  CLAUDE.md ("new crops beyond paddy ADT 45").
- **Scheduled evening confirmations, season rollover, or breakdown
  handling as an automated production process.** All three are code-only
  entrypoints per their own docstrings — they exist and are tested, but
  nothing schedules or triggers them in production; they require a human
  (operator tap, or a developer manually invoking a function) every time.
- **A fairness penalty for silence.** ADR-009 claims one; the code
  deliberately does not implement it (Section 2's documentation-drift
  callout).
- **Any live DPC/mandi price, scheme/loan, CHC/AGRISNET, pest/disease/
  soil/irrigation feature.** All explicitly out of scope per CLAUDE.md; no
  code anywhere touches any of these.
- **Multi-cluster scheduling in one deployed run.** The deployed
  `handler(payload)` takes exactly one `cluster_id` per invocation
  (`app.py:29-30`); multi-cluster support (ADR-008) means the *code* can
  run against different clusters when invoked with different payloads, not
  that one invocation schedules across clusters simultaneously.
- **A hermetic or live-verified test of `DynamoStorage` against a real
  table.** No such test exists in the repo (Section 5); only a
  degrade-on-unreachable-endpoint test exists. The ADR-006 claim of a real
  live round-trip was a one-off manual check during deployment, not
  something CI or `pytest` re-verifies.

---

*Prepared by reading source directly (not ADR summaries) across
`src/harvest_convoy/`, `scripts/`, and `tests/`. Every claim above traces to
a specific file:line or test name. Where an ADR's narrative could mislead a
reader relying on it alone, that is called out explicitly rather than
silently corrected.*
