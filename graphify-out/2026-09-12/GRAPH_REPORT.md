# Graph Report - Devpost Hackthon  (2026-08-26)

## Corpus Check
- 137 files · ~208,746 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2234 nodes · 6338 edges · 126 communities (93 shown, 33 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 992 edges (avg confidence: 0.94)
- Token cost: 559,297 input · 0 output

## Community Hubs (Navigation)
- Rain Event Classification
- Tamil Message Strings
- Telegram Notification Builders
- English Message Strings
- ADR Index & Architecture
- Escalation Authorization Tests
- Equity Report Script
- Route Edit Webhook Callbacks
- FileStorage Interface Methods
- FileStorage Tests
- Coordinator Negotiation Logic
- Decision Replay Script
- Machinery Gap Report
- Graphify Skill Reference Docs
- Daily Watcher Core
- Watcher Health & Confirmation Tests
- Operator Enrollment Tests
- Escalation Resolution Tests
- Proxy Registration Flow
- Notify Tamil Rendering Tests
- Scheduling Solver Tests
- Registration FSM Tests
- Dynamo Encode/Decode Tests
- DynamoStorage Implementation
- Telegram Client Send Methods
- Operator Enrollment Module
- Farmer Registration Flow
- Machine Breakdown Tests
- Season Rollover Tests
- Farmer Why-Answer Tests
- Advocate Agent Module
- 2025 Kuruvai Backtest
- Telegram Client & Callbacks
- LinkFarmer Tests
- Agent Contracts (PlotFacts)
- Storage Interface Design
- Dynamo DecisionRecord Storage
- Advance Notice Tests
- Help Command Tests
- GDD & Solver Core
- Cluster Seeding Scripts
- Open-Meteo Weather Client
- Fairness Ledger Module
- Route Ordering (Haversine)
- AgentCore Entrypoint & Tracing
- Per-Cluster GDD Calibration
- Stack, Strands & Deploy ADRs
- Agronomy & Tamil ADRs
- Farmer Authorization Tests
- Help & Escalation Resolution
- Proxy Registration Tests
- Message Rendering Tests
- Fairness Ledger Gate Tests
- Weather Bridging Tests
- Architecture Diagram Generator
- Harvest Capacity Budget
- Breakdown Displacement Entity
- Tamil/English Message Tests
- Registration Completion Tests
- Reporting & Operator Auth ADRs
- Coordinator Core Logic
- Rollover/Breakdown/Backtest Findings
- Overripe Decay Curve
- Advance Notice Record Storage
- Notify Text Builders
- Operator Enrollment Callbacks
- Telegram & Harvest ADRs
- Route Proposal & LinkFarmer ADR
- Manual Verification Harness
- Harvest Confirmation Entity
- Season Rollover Prompt Entity
- Notify Text Builders (Route)
- Telegram Client Retry Calls
- Route Override Entity
- Rollover Reply Flow
- Agents Gate Test (Phase 5)
- Kamatchipuram Gate Test
- Polling Smoke Tests
- No Live Inbound Path ADR
- Machine Status Entity
- Operator Enrollment Code Entity
- Tamil Day-Word Helpers
- Proxy ID Minting
- Dynamo Float Fidelity ADR
- Tamil String Dump Script
- Farmer Why-Text Builder
- Live Weather Seam Tests
- Help Command ADR
- Breakdown Followup Keyboard
- Breakdown Followup Undo
- LinkFarmer Undo Logic
- Unregistered Help Text
- Teardown Script
- Reporting Scripts Plumbing
- Drying Window Alert Text
- Date Formatting Helper
- Direction Formatting Helper
- Operator Help Addendum
- Location Hint Text
- Maturity Sentence Text
- Why-Not-Recorded Text
- Why-Not-Ready Text
- Why-Lost Text
- Resolution Reason Text
- Date Formatting (Tamil)
- Location Hint (Tamil)
- Tamil Density Fix Test
- Date Convention Test
- Fractional Acreage Test
- Whole-Cent Area Test
- Advocate Template Test
- Confirmation Area Unit Test
- Drying Alert Figure Test
- Payment Wording Test
- Tamil Ordinal Test
- MSP Grade Test
- Rain Warning Order Test
- Tamil Ordinal (4th+) Test
- Half-Translation Regression Test
- Repo Root Package

## God Nodes (most connected - your core abstractions)
1. `FileStorage` - 373 edges
2. `Storage` - 144 edges
3. `Plot` - 104 edges
4. `Farmer` - 97 edges
5. `Cluster` - 81 edges
6. `ForecastDay` - 74 edges
7. `SendResult` - 72 edges
8. `StorageResult` - 66 edges
9. `TelegramClient` - 65 edges
10. `DynamoStorage` - 63 edges

## Surprising Connections (you probably didn't know these)
- `_synthetic_days()` --uses--> `DailyTemperature`  [INFERRED]
  tests/test_machine_breakdown.py → src/harvest_convoy/agronomy/gdd.py
- `_synthetic_days()` --uses--> `DailyTemperature`  [INFERRED]
  tests/test_season_rollover.py → src/harvest_convoy/agronomy/gdd.py
- `_synthetic_days()` --uses--> `DailyTemperature`  [INFERRED]
  tests/test_watcher.py → src/harvest_convoy/agronomy/gdd.py
- `test_why_lost_with_no_record_says_not_recorded()` --uses--> `FileStorage`  [INFERRED]
  tests/test_farmer_why.py → src/harvest_convoy/storage/file_storage.py
- `test_why_not_ready_with_empty_decision_date_never_calls_storage()` --uses--> `FileStorage`  [INFERRED]
  tests/test_farmer_why.py → src/harvest_convoy/storage/file_storage.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Deterministic Core and LLM Judgment both write to DynamoDB, Telegram notify, and ADOT tracing** — docs_architecture_deterministic_core, docs_architecture_llm_judgment, docs_architecture_dynamodb, src_harvest_convoy_telegram_notify, docs_architecture_adot_cloudwatch_xray [INFERRED 0.85]
- **EventBridge to Lambda shim to AgentCore Runtime to watcher.run_daily_watch daily trigger chain** — docs_architecture_eventbridge_schedule, docs_architecture_lambda_shim, docs_architecture_agentcore_runtime, src_harvest_convoy_watcher_run_daily_watch [EXTRACTED 1.00]
- **Not-deployed inbound Telegram surface: bot API, webhook, registration, tested only via run_polling / ADR-016** — docs_architecture_telegram_bot_api, src_harvest_convoy_telegram_webhook, src_harvest_convoy_telegram_registration, scripts_run_polling, docs_architecture_adr_016 [INFERRED 0.75]
- **graphify Extraction Pipeline** — _claude_skills_graphify_skill_pipeline, _claude_skills_graphify_skill_ast_extraction, _claude_skills_graphify_skill_semantic_extraction, _claude_skills_graphify_skill_extraction_cache [INFERRED 0.85]
- **LLM-Never-Computes-a-Number Design Pattern** — architecture_llm_never_computes_number, claude_one_architectural_rule, architecture_get_advocate_claim, claude_tamil_hand_authored [INFERRED 0.85]
- **ADR-008 Tamil & Calibration Cluster** — claude_adr_008, readme_tamil_script_failure_finding, claude_tamil_hand_authored, readme_maturity_gdd_estimated [INFERRED 0.75]
- **Callback Authorization Audit Table Lineage** — docs_adr_adr_012_callback_authorization_audit, docs_adr_adr_013_route_override_entity, docs_adr_adr_013_linkfarmer_mechanism, docs_adr_adr_013_farmer_why, docs_adr_adr_014_help_command_exception [EXTRACTED 0.95]
- **Silence Is Not Evidence Design Pattern** — docs_adr_adr_009_silence_not_noshow, docs_adr_adr_011_season_rollover_prompt, docs_adr_adr_013_proposal_not_gatekeeping [INFERRED 0.85]
- **Live Verification Finds What String Review Misses** — docs_adr_adr_008_half_translation_bugs, docs_adr_adr_013_tamil_doubled_noun_bug, docs_adr_adr_015_float_int_coercion_bug [EXTRACTED 0.85]

## Communities (126 total, 33 thin omitted)

### Community 0 - "Rain Event Classification"
Cohesion: 0.08
Nodes (76): AgentCore Runtime (bedrock_agentcore.BedrockAgentCoreApp), EventBridge Schedule, Lambda shim (watcher-invoker), Open-Meteo API, ForecastDay, classify_rain_event(), Enum, str (+68 more)

### Community 2 - "Telegram Notification Builders"
Cohesion: 0.08
Nodes (54): Telegram Bot API, Market context for the post-harvest drying-window alert (ADR-009 Part 4).…, Farmer, Plot, _argument_line(), _bearing_deg(), build_advance_harvest_notice_text(), build_breakdown_keyboard() (+46 more)

### Community 4 - "ADR Index & Architecture"
Cohesion: 0.05
Nodes (58): CI Excludes bedrock-marked Tests, CI Workflow, ADR-016: No Live Inbound Path, AdvocateClaim, AgentCore Runtime, coordinator.run_cluster(), DynamoDB Single-Table Design, EventBridge Schedule (+50 more)

### Community 5 - "Escalation Authorization Tests"
Cohesion: 0.11
Nodes (47): get_ledger_history(), All recorded seasons for this farmer, most recent first. Empty for a farmer…, One of "modified" (current_route differs from proposed_route, regardless of…, route_override_status(), _claim(), _cluster(), _escalation_update(), _FakeClient (+39 more)

### Community 6 - "Equity Report Script"
Cohesion: 0.12
Nodes (49): build_result(), _build_season_section(), _discover_season_ids(), EquityReport, main(), Equity report: was allocation of the shared machine fair, and did the fairness…, Cross-season by nature -- put_ledger_entry allows at most one entry per farmer…, _render_section_text() (+41 more)

### Community 7 - "Route Edit Webhook Callbacks"
Cohesion: 0.08
Nodes (47): build_route_drop_confirm_keyboard(), build_route_edit_keyboard(), build_route_edit_text(), The per-stop editing view opened by a Modify tap -- one row per stop (an up-…, apply_link(), The one, narrow, purpose-built mutation Decision 18 performs -- not a general…, _default_lookup(), _edit_message() (+39 more)

### Community 9 - "FileStorage Tests"
Cohesion: 0.06
Nodes (45): _cluster(), _decision_record(), _entry(), _farmer(), _plot(), A pre-ADR-008 stored record (written before Farmer.language existed) has no…, Same free-default story as language/area_unit, for ADR-008's per-cluster…, Same free-default story, for Cluster.operator_language (added in the same… (+37 more)

### Community 10 - "Coordinator Negotiation Logic"
Cohesion: 0.10
Nodes (42): ClaimProvider, CoordinatorOutcome, _fairness_was_decisive(), negotiate_pair(), date, None if the round was resolved by a concession (a model judgment, not a score…, Pairwise negotiation between two contested plots. Resolves as soon as either…, Same orchestration, with an injectable claim provider -- used for deterministic… (+34 more)

### Community 11 - "Decision Replay Script"
Cohesion: 0.17
Nodes (43): build_result(), DecisionReplayResult, main(), _narrative_from_decision_record(), Decision replay: reconstruct, from stored records only, why the system…, A plain-English note when an operator's route override touched this specific…, render_text(), _resolve_season() (+35 more)

### Community 12 - "Machinery Gap Report"
Cohesion: 0.11
Nodes (42): build_cluster_result(), build_result(), ClusterGapResult, _error_result(), MachineryGapReport, main(), _parse_date(), date (+34 more)

### Community 13 - "Graphify Skill Reference Docs"
Cohesion: 0.05
Nodes (43): graphify Trigger Directive (.claude/CLAUDE.md), /graphify add URL Ingestion, graphify add / --watch Reference, --watch Auto-Rebuild Mode, Token Reduction Benchmark, graphify Exports Reference, FalkorDB Export, graphify MCP Server (+35 more)

### Community 14 - "Daily Watcher Core"
Cohesion: 0.09
Nodes (34): Protocol, The weather/capacity/threshold context that produced one trigger day's solve()…, TriggerContext, Production entrypoint: calls the real advocate agents., run_cluster(), get_storage(), Every cluster_id this backend has a Cluster record for. Nothing in the…, ISO date string of the last day the daily watcher completed a check for this… (+26 more)

### Community 15 - "Watcher Health & Confirmation Tests"
Cohesion: 0.10
Nodes (34): check_confirmation_follow_through(), check_marker(), check_runtime_errors(), check_schedule_recent_activity(), do_invoke(), main(), Manual health check for the deployed daily watcher (see ADR-006 Decision 1, and…, Diagnostic only -- never affects the pass/fail exit code. Operator- facing… (+26 more)

### Community 16 - "Operator Enrollment Tests"
Cohesion: 0.14
Nodes (31): main(), _cluster(), _code(), _FakeClient, _lang_tap(), _message_update(), ADR-012 Part 2: operator self-enrollment. Hermetic -- no live Telegram/Bedrock…, Decision 1: callback_data alone (well-formed, referencing a genuinely valid… (+23 more)

### Community 17 - "Escalation Resolution Tests"
Cohesion: 0.11
Nodes (28): _claim(), _decision_record_for(), _FakeClient, _farmer(), _operator_cluster(), _plot(), A registered Cluster with operator_language="en" gets the English "Machine…, A real farmer's name is a proper noun and takes a spaced case marker ("{name}… (+20 more)

### Community 18 - "Proxy Registration Flow"
Cohesion: 0.12
Nodes (35): advance_proxy_registration(), build_linkfarmer_confirm_keyboard(), build_linkfarmer_match_message(), build_linkfarmer_undo_keyboard(), clear_state(), _confirm_keyboard(), _confirm_summary_message(), handle_addfarmer_command() (+27 more)

### Community 19 - "Notify Tamil Rendering Tests"
Cohesion: 0.13
Nodes (34): _claim(), _cluster(), _FakeClient, _farmer(), _plot(), Operator-facing text is now per-language too (Cluster.operator_language) --…, ADR-008 Decision 12: facts first, unchanged; the advocate's argument is one…, A model string must never block the escalation -- a fallback-path claim… (+26 more)

### Community 20 - "Scheduling Solver Tests"
Cohesion: 0.19
Nodes (32): Cluster, Returns (maturity_gdd, "calibrated" | "fallback"). Extracted out of solve()…, Full scheduling pass over a cluster's plots for one weather trigger. 0. Plots…, resolve_maturity_gdd(), solve(), _days_to_maturity(), _make_days(), _plot() (+24 more)

### Community 21 - "Registration FSM Tests"
Cohesion: 0.21
Nodes (34): advance_registration(), IncomingMessage, str, Pure state transition: given the current step and one incoming message, return…, RegistrationState, RegistrationStep, parametrize, registration.py tests. See ADR-008 Part 2 for the Tamil-support design this… (+26 more)

### Community 22 - "Dynamo Encode/Decode Tests"
Cohesion: 0.10
Nodes (28): slow, _decode(), _from_decimal(), Base-table query (no GSI) -- every DecisionRecord for one plot, e.g.…, _strip_keys(), _to_decimal(), dynamo.py tests. Pure encode/decode helpers are tested directly (no AWS…, No real table needed -- this exercises the exact… (+20 more)

### Community 23 - "DynamoStorage Implementation"
Cohesion: 0.14
Nodes (8): DynamoStorage, _encode(), The one Scan in this file -- every other method here is a targeted…, Only call this after a check actually completed -- not on a failed check (e.g.…, A plot the coordinator dispatched the machine to this trigger -- excludes it…, Removes the marker -- returns the plot to the schedulable pool on solve()'s…, The symmetric "machine is back" action -- removes the down status. Safe on a…, StorageResult

### Community 25 - "Operator Enrollment Module"
Cohesion: 0.09
Nodes (32): operator_command_usage(), operator_expired_code(), operator_invalid_code(), operator_language_prompt(), operator_command_usage(), operator_expired_code(), operator_invalid_code(), operator_language_prompt() (+24 more)

### Community 26 - "Farmer Registration Flow"
Cohesion: 0.11
Nodes (28): ADR-016, Local dev runner: long-polls Telegram's getUpdates instead of running a webhook…, _bilingual_greeting_text(), find_farmer_by_chat_id(), get_or_create_state(), handle_incoming(), handle_language_callback(), _lang_module() (+20 more)

### Community 27 - "Machine Breakdown Tests"
Cohesion: 0.17
Nodes (29): handle_machine_breakdown(), ADR-011 Part 2: an operator's "machine down today" tap. Marks today's…, _claim(), _cluster(), _FakeClient, _farmer(), _patch_weather(), _plot() (+21 more)

### Community 28 - "Season Rollover Tests"
Cohesion: 0.20
Nodes (29): Code-only entrypoint, operator-triggered -- ADR-011 Part 1. Not wired to any…, run_season_rollover(), _claim(), _cluster(), _FakeClient, _farmer(), _patch_weather(), _plot() (+21 more)

### Community 29 - "Farmer Why-Answer Tests"
Cohesion: 0.13
Nodes (26): _cluster(), _FakeClient, _farmer(), _lost_record(), _not_ready_record(), _plot(), parametrize, ADR-013 Part 3: a farmer's one-tap "why" on a not_ready or… (+18 more)

### Community 30 - "Advocate Agent Module"
Cohesion: 0.12
Nodes (26): bedrock, Facts Overwritten With Ground Truth, Model, _build_model(), _build_prompt(), get_advocate_claim(), _make_fairness_tool(), One advocate agent per plot. Structured claims only, never prose. Never throws… (+18 more)

### Community 31 - "2025 Kuruvai Backtest"
Cohesion: 0.18
Nodes (26): _calibrate(), ClusterBacktest, _final_outcome(), _first_ready_trigger(), format_narrative(), format_table(), main(), Historical backtest: what the system would have decided against the real 2025… (+18 more)

### Community 32 - "Telegram Client & Callbacks"
Cohesion: 0.10
Nodes (21): Thin wrapper over the Telegram Bot API. Raw HTTPS + JSON, no framework -- see…, TelegramClient, handle_confirmation_callback(), handle_rollover_callback(), _handle_why_callback(), handle_why_lost_callback(), handle_why_notready_callback(), _is_farmer() (+13 more)

### Community 33 - "LinkFarmer Tests"
Cohesion: 0.20
Nodes (25): _cbq(), _cluster(), _FakeClient, _proxy_farmer(), _proxy_plot(), ADR-013 Part 2 Decision 18: /linkfarmer. Fully stateless and taps only -- every…, If the candidate's plot no longer points back at this proxy_farmer_id (e.g. an…, _seed_pair() (+17 more)

### Community 34 - "Agent Contracts (PlotFacts)"
Cohesion: 0.13
Nodes (23): BaseModel, field_validator, RainVulnerability, _offline_claim(), Deterministic, no-AWS fallback claim provider -- see module docstring. Always…, _fallback_claim(), AdvocateClaim, classify_rain_vulnerability() (+15 more)

### Community 35 - "Storage Interface Design"
Cohesion: 0.10
Nodes (16): FileStorage Default, DynamoStorage Alternate, ADR-005: Persistence and Fairness, Single-Table DynamoDB Design, main(), Operator correction: a plot was marked harvested that shouldn't have been --…, Generates a one-time operator enrollment code for a cluster -- the provisioner-…, Zero-setup local storage: a JSON file on disk. Default backend -- see ADR-005…, Storage backend selection. See ADR-005 Decision 1: FileStorage is the default… (+8 more)

### Community 36 - "Dynamo DecisionRecord Storage"
Cohesion: 0.11
Nodes (14): _cast_claim_floats(), _floats(), _item_to_cluster(), _item_to_decision_record(), _item_to_plot(), Real DynamoDB-backed storage. Works against real AWS (default) or DynamoDB…, Cast the named top-level keys to float where present and not None -- see…, DecisionRecord (+6 more)

### Community 37 - "Advance Notice Tests"
Cohesion: 0.21
Nodes (22): _cluster(), _FakeClient, _farmer(), _plot(), date, ADR-011 Part 4: advance harvest notice. Weather is monkeypatched (same pattern…, Decision 17: the AdvanceNoticeRecord's mere existence suppresses forever,…, days_until <= 0 the first time a plot is checked -- never sent late or same-… (+14 more)

### Community 38 - "Help Command Tests"
Cohesion: 0.20
Nodes (21): _advance_notice(), _cluster(), _FakeClient, _farmer(), _help_update(), _plot(), ADR-014: /help, a read-only status command for a registered farmer or the…, ADR-014 Decision 2, reversed on review: the farmer view must win even when the… (+13 more)

### Community 39 - "GDD & Solver Core"
Cohesion: 0.17
Nodes (19): accumulate_gdd(), daily_gdd(), DailyTemperature, project_maturity_date(), Growing Degree Day accumulation. Deterministic arithmetic only -- no LLM…, GDD contribution of a single day. Never negative., Sum of daily GDD across a sequence of days, in whatever order given., First date at which accumulated GDD reaches maturity_gdd, walking `days` in… (+11 more)

### Community 40 - "Cluster Seeding Scripts"
Cohesion: 0.13
Nodes (18): main(), main(), The second seeded demo cluster: Naducauvery, Thanjavur district -- proves the…, Write the fixture data above into a real Storage backend. See…, seed_into_storage(), The 8-plot Kamatchipuram demo cluster. Plain fixture data only -- no DynamoDB…, Write the fixture data above into a real Storage backend. The…, seed_into_storage() (+10 more)

### Community 41 - "Open-Meteo Weather Client"
Cohesion: 0.22
Nodes (20): _assert_no_gaps(), _cache_key(), _fetch(), _fetch_archive(), _fetch_forecast_forward(), _fetch_forecast_past_days(), get_daily_temperatures(), get_precipitation_forecast() (+12 more)

### Community 42 - "Fairness Ledger Module"
Cohesion: 0.17
Nodes (18): Fairness Ledger Accumulated/Decayed/Capped, RainEventClass BRIEF/SUSTAINED Classification, Rain Urgency Boost Cannot Invert Urgency Ordering, Fairness ledger: per-farmer bump history, and the decayed accumulation…, Sum of days_bumped across every recorded season, decayed by how long ago each…, True if the single most recent recorded season had a bump. This is the plain…, Record one season's outcome for a farmer. Idempotent per (farmer_id, season_id)…, record_bump() (+10 more)

### Community 43 - "Route Ordering (Haversine)"
Cohesion: 0.17
Nodes (18): ADOT -> CloudWatch / X-Ray, Deterministic Core, DynamoDB (harvest_convoy), Escalation is the product, not a fallback, LLM Judgment, Math decides, judgment only argues, Resolution (deterministic), haversine_km() (+10 more)

### Community 44 - "AgentCore Entrypoint & Tracing"
Cohesion: 0.13
Nodes (16): entrypoint, AgentCore Runtime deployment root entrypoint. /var/task (the zip's extraction…, SpanExporter, handler(), AgentCore Runtime entrypoint. See docs/adr/ADR-006-deploy.md Decision 1.…, configure_tracing(), get_tracer(), _log_local_collector_reachability() (+8 more)

### Community 45 - "Per-Cluster GDD Calibration"
Cohesion: 0.16
Nodes (18): derive_cluster_maturity_gdd(), derive_reference_gdd_rate(), project_maturity_for_plot(), project_maturity_from_days(), date, Per-cluster maturity-threshold calibration. Generalizes ADR-002's Theni-…, Projected maturity date for a plot that (usually) hasn't reached maturity yet…, Mean daily GDD accrual at (lat, lon), computed the exact way ADR-002 computed… (+10 more)

### Community 46 - "Stack, Strands & Deploy ADRs"
Cohesion: 0.12
Nodes (19): Deterministic Core vs LLM Advocacy Split, ADR-000: Stack and Architecture, Locked Technology Stack, Fairness Ledger Read-Only Stub Tool, Pairwise Negotiation Capped at 3 Rounds, ADR-003: Strands Agents, AgentCore Runtime codeConfiguration Deploy, ADR-006: Deployment (+11 more)

### Community 47 - "Agronomy & Tamil ADRs"
Cohesion: 0.13
Nodes (17): ADR-001: Agronomy Core, GDD Anchored to transplant_date, ADT45 Maturity Threshold Derived Not Sourced, Open-Meteo Archive/Forecast Seam Bridging, T_BASE_C = 10.0 (sourced), Capacity Budget (usable_harvest_days), Overripe Decay Linear Proxy, GDD Rate Reconciled Against 5-Year Climatology (+9 more)

### Community 48 - "Farmer Authorization Tests"
Cohesion: 0.26
Nodes (17): _cluster(), _confirm_update(), _FakeClient, _farmer(), _plot(), ADR-012: the full-callback audit found two more instances of the same…, The more dangerous half: an unauthorized "yes" would previously have let the…, _rollover_update() (+9 more)

### Community 49 - "Help & Escalation Resolution"
Cohesion: 0.12
Nodes (17): FarmerPlotLookup, _active_plot_for(), build_help_reply(), `/help` -- ADR-014. A read-only status check for a registered farmer or the…, By construction (every registration path in this codebase mints exactly one…, A concrete, honest, human reason for the losing side, rendered in *their*…, resolution_reason_text(), _escalation_key() (+9 more)

### Community 50 - "Proxy Registration Tests"
Cohesion: 0.22
Nodes (17): get_pending_state(), _addfarmer_update(), _cluster(), _complete_proxy_flow(), ADR-013 Part 2: /addfarmer proxy registration. Mirrors test_registration.py's…, Decision 13: has_phone changes only the operator's closing message, never what…, A proxy chat can never supply the farmer's own chat_id, regardless of the…, Drives the pure state machine through every text step, stopping at… (+9 more)

### Community 51 - "Message Rendering Tests"
Cohesion: 0.11
Nodes (18): parametrize, test_all_prompts_render(), test_bumped_suffix_renders(), test_complete_message_renders(), test_drying_window_alert_omits_moisture_when_unset(), test_drying_window_alert_omits_msp_when_unset(), test_drying_window_alert_with_both_figures_set_includes_both(), test_escalation_resolved_lost_renders_without_keyerror() (+10 more)

### Community 52 - "Fairness Ledger Gate Tests"
Cohesion: 0.20
Nodes (15): build_plot_facts(), _language_test_decision(), _language_test_plot(), ADR-008 Decision 12: PlotFacts.language comes from the plot's own farmer's…, test_build_plot_facts_defaults_to_tamil_when_farmer_not_found(), test_build_plot_facts_pulls_the_farmers_registered_language(), _decision(), _plot() (+7 more)

### Community 53 - "Weather Bridging Tests"
Cohesion: 0.16
Nodes (13): get_historical_daily(), Temperature AND precipitation for a fully historical date range, in one Archive…, Deterministic test of the null-gap bridging logic. Live Open-Meteo data…, A null that ISN'T at the trailing edge is a genuine gap/anomaly, not "not…, Live-caught: requesting the full 16-day FORECAST_MAX_HORIZON_DAYS window, day…, test_bridge_raises_if_forecast_also_missing_the_day(), test_get_historical_daily_raises_on_a_null_day(), test_get_historical_daily_rejects_inverted_range() (+5 more)

### Community 54 - "Architecture Diagram Generator"
Cohesion: 0.23
Nodes (11): arrow(), arrowhead(), centered_text(), elbow_arrow(), elbow_arrow_hv(), font(), Generates docs/architecture.png. Not part of the shipped package -- a build-…, Horizontal-then-vertical connector: goes sideways at bend_y (while still clear… (+3 more)

### Community 55 - "Harvest Capacity Budget"
Cohesion: 0.23
Nodes (11): harvest_day_budget_acres(), Usable-harvest-day budget from a rain forecast. DETERMINISTIC. Given a forecast…, Number of consecutive usable days from the start of `forecast`, up to (not…, Total acreage the shared machine can cover before the window closes., usable_harvest_days(), test_budget_is_days_times_capacity(), test_budget_uses_default_capacity_when_unspecified(), test_empty_forecast_gives_zero_usable_days() (+3 more)

### Community 56 - "Breakdown Displacement Entity"
Cohesion: 0.17
Nodes (5): BreakdownDisplacement, A plot un-harvested because the machine broke down, not because another…, Overwrite semantics -- a retried write for the same (plot_id, season_id,…, Every breakdown displacement for this cluster/season -- equity_report.py's…, Filtered to one report_date -- the breakdown callback's double-tap idempotency…

### Community 57 - "Tamil/English Message Tests"
Cohesion: 0.15
Nodes (4): messages_ta.py / messages_en.py rendering tests. See ADR-008 Part 2, Decision…, test_crop_confirm_declined_message_renders(), test_harvest_scheduled_renders_without_keyerror(), test_transplant_info_missing_prefix_renders_for_every_combination()

### Community 58 - "Registration Completion Tests"
Cohesion: 0.22
Nodes (13): _cluster(), _complete_registration(), The required negative test: registration still completes, the farmer/plot still…, ADR-009 Prerequisite: a second complete run from the same chat_id updates the…, ADR-013 Part 2 Decision 18: generalizes ADR-009's "same chat_id -> update, not…, test_re_registration_after_linkfarmer_updates_the_canonical_proxy_plot_not_the_retired_duplicate(), test_registration_completion_appends_maturity_sentence_when_weather_available(), test_registration_completion_falls_back_to_plain_message_on_weather_error() (+5 more)

### Community 59 - "Reporting & Operator Auth ADRs"
Cohesion: 0.18
Nodes (12): Native ADOT Tracing Export, CloudWatch Traces Are Not a Substitute for Persistence, DecisionRecord Entity, explain_decision.py Replay Tool, machinery_gap.py Report, System Computes Reasoning Then Discards It, ADR-010: Reporting and Decision Replay, DecisionRecord Retained Indefinitely (+4 more)

### Community 60 - "Coordinator Core Logic"
Cohesion: 0.20
Nodes (11): EscalationPayload, Exactly one message to a human: both plots, the trade-off, two tap targets…, _base_decision_record(), ClusterResult, _fairness_bonus(), NegotiationResult, Coordinator: owns the machine calendar for one weather trigger. Calls one…, The deterministic-core fields every DecisionRecord shares, regardless of… (+3 more)

### Community 61 - "Rollover/Breakdown/Backtest Findings"
Cohesion: 0.24
Nodes (11): 2025 Kuruvai Historical Backtest, solve() Re-Competes FITS Plots Bug -> HARVESTED Outcome, equity_report.py Fairness Report, BreakdownDisplacement Entity, Machine Breakdown Recompute, No Season Entity; Rollover is Operator-Triggered, ADR-011: Rollover, Breakdown, Rain, Advance Notice, SeasonRolloverPrompt Entity (+3 more)

### Community 62 - "Overripe Decay Curve"
Cohesion: 0.29
Nodes (8): decay_fraction(), Overripe decay curve. DERIVED, NOT sourced -- see docs/adr/ADR-002-scheduling-…, Fraction (0.0-1.0) representing how far a plot has decayed past its projected…, test_decay_caps_at_one_beyond_horizon(), test_decay_is_monotonic(), test_decay_reaches_one_at_horizon(), test_negative_days_raises(), test_zero_days_past_maturity_is_zero_decay()

### Community 63 - "Advance Notice Record Storage"
Cohesion: 0.18
Nodes (8): AdvanceNoticeRecord, Proof that the one-time "arrange transport, drying space" notice (ADR-011 Part…, Write-once in practice -- the caller checks get_advance_notice_record first and…, None means this plot has never been sent its advance notice this season -- the…, _float_field_names(), Every field on cls whose resolved annotation is float or float | None -- the…, Completeness guard for ADR-015's per-type deserializers: a future float field…, test_every_float_field_across_all_dataclasses_is_covered_by_a_dynamo_cast_list()

### Community 64 - "Notify Text Builders"
Cohesion: 0.18
Nodes (11): advance_harvest_notice(), escalation_resolved_lost(), escalation_resolved_won(), format_area(), harvest_confirmation_prompt(), harvest_scheduled(), not_ready(), _ordinal_word() (+3 more)

### Community 65 - "Operator Enrollment Callbacks"
Cohesion: 0.22
Nodes (11): clear_state(), complete_enrollment(), matches_pending(), Writes the three things a successful enrollment or replacement touches:…, The binding check every operator_lang:/operator_replace: callback must pass…, handle_operator_lang_callback(), handle_operator_replace_callback(), parse_operator_lang_callback_data() (+3 more)

### Community 66 - "Telegram & Harvest ADRs"
Cohesion: 0.20
Nodes (10): Escalation Resolution Idempotency, not_ready as First-Class Distinct Message, advance_registration Pure FSM, ADR-004: Telegram Interface, run_daily_watch Multi-Cluster Iteration, Post-Harvest Drying-Window Rain Alert, HarvestConfirmation Tri-State confirmed bool|None, ADR-009: Harvest Lifecycle and Validation (+2 more)

### Community 67 - "Route Proposal & LinkFarmer ADR"
Cohesion: 0.20
Nodes (10): AgentCore Memory Skipped Deliberately, 'Why?' Button, Stored-Records-Only Answers, /linkfarmer Duplicate-Linking Mechanism, Bounded-Window Undo for /linkfarmer, Operator-Only Proxy Registration, No Helper Role, /addfarmer Proxy Registration for Phone-less Farmers, Reachability Decides Whether Rollover Prompt Is Written At All, ADR-013: Route Proposal and Operator Override (+2 more)

### Community 68 - "Manual Verification Harness"
Cohesion: 0.31
Nodes (9): build_deadlock_facts(), build_escalation(), main(), date, Manual verification harness: drives the seeded Kamatchipuram cluster through…, Runs the REAL negotiate_pair() loop against the engineered deadlock facts --…, Long-poll getUpdates until the escalation callback arrives, or…, _synthetic_days() (+1 more)

### Community 69 - "Harvest Confirmation Entity"
Cohesion: 0.20
Nodes (5): HarvestConfirmation, None if no confirmation record exists for this plot/season -- e.g. it was never…, Overwrite semantics, like every put_* here except put_ledger_entry -- both the…, Every confirmation record for this cluster/season -- small by construction (one…, Did the machine actually come? One record per (plot_id, season_id), created the…

### Community 70 - "Season Rollover Prompt Entity"
Cohesion: 0.20
Nodes (5): Whether a farmer confirmed participation in a new season after a rollover…, Overwrite semantics, like every put_* here except put_ledger_entry -- a…, None if no rollover was ever run for this plot/season -- either this plot is…, Every rollover-prompt record for this cluster/season -- the watcher's…, SeasonRolloverPrompt

### Community 71 - "Notify Text Builders (Route)"
Cohesion: 0.20
Nodes (10): advance_harvest_notice(), escalation_resolved_lost(), escalation_resolved_won(), format_area(), harvest_confirmation_prompt(), harvest_scheduled(), not_ready(), Sent exactly once per plot per season, roughly a week before projected maturity… (+2 more)

### Community 73 - "Route Override Entity"
Cohesion: 0.22
Nodes (5): The day's proposed route and whatever the operator has done to it so far. One…, Overwrite semantics, like every put_* here except put_ledger_entry -- keyed by…, None if no route was ever proposed for this exact key -- never asked (no FITS…, Every proposed route for this cluster/season -- equity_report.py's Operator…, RouteOverride

### Community 74 - "Rollover Reply Flow"
Cohesion: 0.33
Nodes (8): advance_rollover_reply(), clear_state(), get_pending_state(), _lang_module(), Season rollover reply flow -- ADR-011 Part 1. A much narrower cousin of…, Returns (resolved, outbound). resolved=True means the date was parsed and…, RolloverState, start_awaiting_date()

### Community 75 - "Agents Gate Test (Phase 5)"
Cohesion: 0.36
Nodes (8): date, Phase 3 gate, corrected in Phase 5: too-green plots concede, and p03 correctly…, _run_gate_scenario(), _synthetic_days(), test_p03_beats_p04_outright_with_no_escalation(), test_ready_plots_do_not_concede(), test_too_green_plots_concede(), _truthful_claim()

### Community 76 - "Kamatchipuram Gate Test"
Cohesion: 0.36
Nodes (8): date, Phase 2 gate: given the 8 seeded Kamatchipuram plots and a rain window, the…, _run_gate_scenario(), _synthetic_days(), test_fits_plots_get_a_route_position_and_contested_plots_do_not(), test_gate_scenario_is_identical_across_repeated_runs(), test_kamatchipuram_gate_produces_all_three_outcomes(), test_too_green_plots_have_no_route_position_or_urgency()

### Community 77 - "Polling Smoke Tests"
Cohesion: 0.25
Nodes (5): main(), _FakeResponse, Same fake used by tests/test_client.py -- kept local since these tests exercise…, test_run_polling_main_calls_handle_update_with_current_signature(), test_trigger_scenario_main_offline_calls_handle_update_with_current_signature()

### Community 78 - "No Live Inbound Path ADR"
Cohesion: 0.29
Nodes (7): Silence Recorded as Unknown, Never as No-Show, Propose, Never Gatekeep (Separate Notification from Authority), RouteOverride Entity, /help as the One Deliberate Farmer-Initiated Exception, README/ARCHITECTURE.md Corrected to Disclose Inbound Gap, ADR-016: No Live Inbound Path, webhook.py Has No Live Entrypoint

### Community 79 - "Machine Status Entity"
Cohesion: 0.29
Nodes (4): MachineStatus, Whether a cluster's shared machine is known to be down indefinitely -- set by…, Overwrite semantics, keyed by cluster_id alone -- one status per cluster. See…, None means operational -- the default, common case. A status record only exists…

### Community 80 - "Operator Enrollment Code Entity"
Cohesion: 0.29
Nodes (4): OperatorEnrollmentCode, A one-time code handed to a real operator out-of-band by whoever provisions a…, Overwrite semantics, keyed by `code` alone -- both the generating write…, None if this code was never generated -- an invalid code, not distinguished in…

### Community 81 - "Tamil Day-Word Helpers"
Cohesion: 0.29
Nodes (7): advocate_argument(), _day_word(), overripe_phrase(), See messages_en.py's resolution_reason -- same logic, Tamil wording. Found…, நாள் (singular) / நாட்கள் (plural) -- Tamil count-noun agreement.…, resolution_reason(), why_not_ready_answer()

### Community 82 - "Proxy ID Minting"
Cohesion: 0.29
Nodes (7): _mint_proxy_ids(), persist_proxy_registration(), farmer-proxy-{hex}/plot-proxy-{hex} -- a disjoint namespace from the ordinary…, Called only on an authorized 'yes' tap at AWAITING_CONFIRM. telegram_chat_id is…, handle_addfarmer_confirm_callback(), parse_addfarmer_confirm_callback_data(), The [Confirm]/[Cancel] tap ending /addfarmer (Decision 13). Re-derives…

### Community 83 - "Dynamo Float Fidelity ADR"
Cohesion: 0.33
Nodes (6): chat_id Float/Int Coercion Bug Found Live, Half-Translation Bugs Found via Live Telegram Verification, why_lost_text Doubled-Noun Grammar Bug Found Live, ADR-015: Dynamo Float Fidelity, _from_decimal Value-Based Heuristic Wrong for Float Fields, Per-Type Deserializers Plus Reflection Completeness Test

### Community 84 - "Tamil String Dump Script"
Cohesion: 0.47
Nodes (5): main(), Prints every Tamil string in messages_ta.py, plus the Tamil-relevant word…, _reconfigure_stdout_utf8(), _section(), test_print_tamil_strings_main_runs_without_error()

### Community 85 - "Farmer Why-Text Builder"
Cohesion: 0.60
Nodes (5): _formatted_date(), Farmer-facing "why?" answers -- ADR-013 Part 3. Assembled entirely from a…, _record_or_none(), why_lost_text(), why_not_ready_text()

### Community 86 - "Live Weather Seam Tests"
Cohesion: 0.47
Nodes (5): network, Live tests against the real Open-Meteo API for a Theni-area coordinate. These…, test_archive_to_forecast_seam_has_no_gaps_or_duplicates(), test_beyond_forecast_horizon_raises_not_silently_truncates(), test_purely_historical_range_has_no_gaps()

### Community 87 - "Help Command ADR"
Cohesion: 0.40
Nodes (5): Projected Maturity Date at Registration, AdvanceNoticeRecord, One-Time Notice, ADR-014: /help Command, Farmer View Wins When chat_id Is Both Farmer and Operator, Projected-Ready Date From AdvanceNoticeRecord Only

### Community 88 - "Breakdown Followup Keyboard"
Cohesion: 0.40
Nodes (5): build_breakdown_followup_keyboard(), Optional second tap, sent as a follow-up after the first tap's recompute…, handle_breakdown_callback(), parse_breakdown_callback_data(), ADR-011 Part 2: the operator's "machine down today" tap, attached to the day's…

### Community 89 - "Breakdown Followup Undo"
Cohesion: 0.40
Nodes (5): build_machine_back_keyboard(), The symmetric clearing action for "down indefinitely" -- sent alongside the…, handle_breakdown_followup_callback(), parse_breakdown_followup_callback_data(), The optional second tap -- "back tomorrow" (the default; sets no state at all,…

### Community 90 - "LinkFarmer Undo Logic"
Cohesion: 0.40
Nodes (5): Reverses apply_link -- three writes back to their pre-link values, the exact…, undo_link(), handle_linkfarmer_undo_callback(), parse_linkfarmer_undo_callback_data(), Reverses a /linkfarmer link within proxy_registration.LINKFARMER_UNDO_WINDOW of…

### Community 91 - "Unregistered Help Text"
Cohesion: 0.50
Nodes (4): help_unregistered_text(), Bilingual -- we genuinely don't know this person's language yet, same reasoning…, help_unregistered(), help_unregistered()

## Ambiguous Edges - Review These
- `webhook.py` → `Telegram Bot API`  [AMBIGUOUS]
  docs/architecture.png · relation: references

## Knowledge Gaps
- **44 isolated node(s):** `harvest-convoy`, `teardown.sh script`, `EventBridge Schedule`, `DynamoDB (harvest_convoy)`, `CI Workflow` (+39 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **33 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `webhook.py` and `Telegram Bot API`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `FileStorage` connect `FileStorage Interface Methods` to `Rain Event Classification`, `Telegram Notification Builders`, `Escalation Authorization Tests`, `Equity Report Script`, `FileStorage Tests`, `Coordinator Negotiation Logic`, `Decision Replay Script`, `Machinery Gap Report`, `Daily Watcher Core`, `Watcher Health & Confirmation Tests`, `Operator Enrollment Tests`, `Escalation Resolution Tests`, `Proxy Registration Flow`, `Scheduling Solver Tests`, `DynamoStorage Implementation`, `Farmer Registration Flow`, `Machine Breakdown Tests`, `Season Rollover Tests`, `Farmer Why-Answer Tests`, `LinkFarmer Tests`, `Storage Interface Design`, `Dynamo DecisionRecord Storage`, `Advance Notice Tests`, `Help Command Tests`, `Cluster Seeding Scripts`, `Fairness Ledger Module`, `Farmer Authorization Tests`, `Proxy Registration Tests`, `Fairness Ledger Gate Tests`, `Breakdown Displacement Entity`, `Registration Completion Tests`, `Advance Notice Record Storage`, `Harvest Confirmation Entity`, `Season Rollover Prompt Entity`, `Route Override Entity`, `Agents Gate Test (Phase 5)`, `Polling Smoke Tests`, `Machine Status Entity`, `Operator Enrollment Code Entity`?**
  _High betweenness centrality (0.181) - this node is a cross-community bridge._
- **Why does `Storage` connect `Daily Watcher Core` to `Rain Event Classification`, `Telegram Notification Builders`, `Escalation Authorization Tests`, `Equity Report Script`, `Route Edit Webhook Callbacks`, `Coordinator Negotiation Logic`, `Decision Replay Script`, `Machinery Gap Report`, `Watcher Health & Confirmation Tests`, `Proxy Registration Flow`, `Scheduling Solver Tests`, `DynamoStorage Implementation`, `Operator Enrollment Module`, `Farmer Registration Flow`, `Machine Breakdown Tests`, `Season Rollover Tests`, `Advocate Agent Module`, `Telegram Client & Callbacks`, `Storage Interface Design`, `Dynamo DecisionRecord Storage`, `Cluster Seeding Scripts`, `Fairness Ledger Module`, `Help & Escalation Resolution`, `Fairness Ledger Gate Tests`, `Breakdown Displacement Entity`, `Coordinator Core Logic`, `Advance Notice Record Storage`, `Operator Enrollment Callbacks`, `Harvest Confirmation Entity`, `Season Rollover Prompt Entity`, `Route Override Entity`, `Rollover Reply Flow`, `Machine Status Entity`, `Operator Enrollment Code Entity`, `Proxy ID Minting`, `Farmer Why-Text Builder`, `Breakdown Followup Keyboard`, `Breakdown Followup Undo`, `LinkFarmer Undo Logic`?**
  _High betweenness centrality (0.108) - this node is a cross-community bridge._
- **Why does `Cluster` connect `Scheduling Solver Tests` to `Rain Event Classification`, `Telegram Notification Builders`, `Escalation Authorization Tests`, `Equity Report Script`, `Route Edit Webhook Callbacks`, `FileStorage Interface Methods`, `FileStorage Tests`, `Decision Replay Script`, `Machinery Gap Report`, `Daily Watcher Core`, `Operator Enrollment Tests`, `Escalation Resolution Tests`, `Notify Tamil Rendering Tests`, `Dynamo Encode/Decode Tests`, `DynamoStorage Implementation`, `Operator Enrollment Module`, `Machine Breakdown Tests`, `Season Rollover Tests`, `Farmer Why-Answer Tests`, `2025 Kuruvai Backtest`, `LinkFarmer Tests`, `Storage Interface Design`, `Dynamo DecisionRecord Storage`, `Advance Notice Tests`, `Help Command Tests`, `GDD & Solver Core`, `Per-Cluster GDD Calibration`, `Agronomy & Tamil ADRs`, `Farmer Authorization Tests`, `Proxy Registration Tests`, `Registration Completion Tests`, `Advance Notice Record Storage`, `Operator Enrollment Callbacks`?**
  _High betweenness centrality (0.072) - this node is a cross-community bridge._
- **Are the 296 inferred relationships involving `FileStorage` (e.g. with `Cluster` and `Farmer`) actually correct?**
  _`FileStorage` has 296 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `Storage` (e.g. with `build_result()` and `_build_season_section()`) actually correct?**
  _`Storage` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 74 inferred relationships involving `Plot` (e.g. with `ClusterBacktest` and `_shift_to_2025()`) actually correct?**
  _`Plot` has 74 INFERRED edges - model-reasoned connections that need verification._