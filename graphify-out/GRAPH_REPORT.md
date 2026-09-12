# Graph Report - Devpost Hackthon  (2026-09-12)

## Corpus Check
- 136 files · ~212,771 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2255 nodes · 6358 edges · 123 communities (89 shown, 34 thin omitted)
- Extraction: 84% EXTRACTED · 16% INFERRED · 0% AMBIGUOUS · INFERRED: 992 edges (avg confidence: 0.94)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `94407989`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_watcher.py
- messages_ta.py
- Plot
- messages_en.py
- ARCHITECTURE.md
- test_route_override.py
- test_equity_report.py
- handle_route_drop_confirm_callback
- StorageResult
- FileStorage
- test_coordinator.py
- test_explain_decision.py
- test_machinery_gap.py
- /graphify Skill
- watcher.py
- test_harvest_confirmation.py
- test_operator_enrollment.py
- test_webhook.py
- proxy_registration.py
- test_notify.py
- Cluster
- test_registration.py
- DynamoStorage
- ._put
- SendResult
- operator_enrollment.py
- registration.py
- test_machine_breakdown.py
- test_season_rollover.py
- test_farmer_why.py
- contracts.py
- PlotOutcome
- webhook.py
- test_link_farmer.py
- AdvocateClaim
- interface.py
- dynamo.py
- test_advance_notice.py
- test_help.py
- DailyTemperature
- get_storage
- openmeteo.py
- ForecastDay
- Deterministic Core
- otel.py
- calibration.py
- ADR-006: Deployment
- crop_params.py
- test_farmer_authorization.py
- Harvest Convoy — Feature Inventory
- test_proxy_registration.py
- parametrize
- build_plot_facts
- negotiate_pair
- generate_architecture_diagram.py
- equity_report.py
- BreakdownDisplacement
- test_messages.py
- _complete_registration
- explain_decision.py Replay Tool
- coordinator.py
- RouteOverride Entity
- solver.py
- TelegramClient
- format_area
- handle_operator_lang_callback
- ADR-009: Harvest Lifecycle and Validation
- Full Callback Authorization Audit
- trigger_scenario.py
- HarvestConfirmation
- SeasonRolloverPrompt
- format_area
- ._call_with_retry
- RouteOverride
- models.py
- test_kamatchipuram_agents_gate.py
- test_kamatchipuram_gate.py
- run_daily_watch
- webhook.py Has No Live Entrypoint
- ADR-008: TN Generalization and Tamil
- OperatorEnrollmentCode
- _day_word
- OperatorAuditEvent
- handle_linkfarmer_confirm_callback
- .__init__
- Storage
- test_weather_seam.py
- market_params.py
- handle_breakdown_followup_callback
- teardown.sh
- reporting/__init__.py
- drying_window_alert
- format_date
- format_direction
- help_operator_addendum
- location_hint
- projected_maturity_sentence
- why_not_recorded
- why_not_ready_answer
- why_lost_answer
- resolution_reason
- format_date
- location_hint
- test_location_hint_is_compressed_in_tamil_village_centre_stated_once
- test_format_date_is_day_first_in_both_languages
- test_format_area_preserves_fractional_acres
- test_format_area_cents_are_whole_for_whole_cent_registrations
- test_advocate_argument_never_returns_model_prose
- test_harvest_confirmation_prompt_area_includes_unit_not_bare_number
- test_drying_window_alert_with_neither_set_drops_the_second_line_entirely
- test_drying_window_alert_english_never_promises_payment
- test_harvest_scheduled_uses_natural_first_not_1vathu_in_tamil
- test_drying_window_alert_english_names_common_grade_explicitly
- test_drying_window_alert_has_the_rain_warning_before_the_market_context
- test_harvest_scheduled_uses_spelled_ordinal_for_second_and_later_in_tamil
- test_location_hint_is_not_half_translated_in_tamil
- harvest-convoy

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
- **Live Verification Finds What String Review Misses** — docs_adr_adr_008_half_translation_bugs, docs_adr_adr_013_tamil_doubled_noun_bug, docs_adr_adr_015_float_int_coercion_bug [EXTRACTED 0.85]
- **Callback Authorization Audit Table Lineage** — docs_adr_adr_012_callback_authorization_audit, docs_adr_adr_013_route_override_entity, docs_adr_adr_013_linkfarmer_mechanism, docs_adr_adr_013_farmer_why, docs_adr_adr_014_help_command_exception [EXTRACTED 0.95]
- **EventBridge to Lambda shim to AgentCore Runtime to watcher.run_daily_watch daily trigger chain** — docs_architecture_eventbridge_schedule, docs_architecture_lambda_shim, docs_architecture_agentcore_runtime, src_harvest_convoy_watcher_run_daily_watch [EXTRACTED 1.00]
- **ADR-008 Tamil & Calibration Cluster** — claude_adr_008, readme_tamil_script_failure_finding, claude_tamil_hand_authored, readme_maturity_gdd_estimated [INFERRED 0.75]
- **Not-deployed inbound Telegram surface: bot API, webhook, registration, tested only via run_polling / ADR-016** — docs_architecture_telegram_bot_api, src_harvest_convoy_telegram_webhook, src_harvest_convoy_telegram_registration, scripts_run_polling, docs_architecture_adr_016 [INFERRED 0.75]
- **Deterministic Core and LLM Judgment both write to DynamoDB, Telegram notify, and ADOT tracing** — docs_architecture_deterministic_core, docs_architecture_llm_judgment, docs_architecture_dynamodb, src_harvest_convoy_telegram_notify, docs_architecture_adot_cloudwatch_xray [INFERRED 0.85]
- **graphify Extraction Pipeline** — _claude_skills_graphify_skill_pipeline, _claude_skills_graphify_skill_ast_extraction, _claude_skills_graphify_skill_semantic_extraction, _claude_skills_graphify_skill_extraction_cache [INFERRED 0.85]
- **LLM-Never-Computes-a-Number Design Pattern** — architecture_llm_never_computes_number, claude_one_architectural_rule, architecture_get_advocate_claim, claude_tamil_hand_authored [INFERRED 0.85]
- **Silence Is Not Evidence Design Pattern** — docs_adr_adr_009_silence_not_noshow, docs_adr_adr_011_season_rollover_prompt, docs_adr_adr_013_proposal_not_gatekeeping [INFERRED 0.85]

## Communities (123 total, 34 thin omitted)

### Community 0 - "test_watcher.py"
Cohesion: 0.15
Nodes (47): _check_drying_window_alerts(), ADR-009 Part 4: for every CONFIRMED harvest (Part 2) still inside its…, _cluster(), _confirmed(), _FakeClient, _farmer(), _patch_weather(), _plot() (+39 more)

### Community 2 - "Plot"
Cohesion: 0.08
Nodes (60): Farmer, Plot, _argument_line(), _bearing_deg(), build_advance_harvest_notice_text(), build_breakdown_followup_keyboard(), build_breakdown_keyboard(), build_confirmation_keyboard() (+52 more)

### Community 4 - "ARCHITECTURE.md"
Cohesion: 0.05
Nodes (58): CI Excludes bedrock-marked Tests, CI Workflow, ADR-016: No Live Inbound Path, AdvocateClaim, AgentCore Runtime, coordinator.run_cluster(), DynamoDB Single-Table Design, EventBridge Schedule (+50 more)

### Community 5 - "test_route_override.py"
Cohesion: 0.08
Nodes (62): get_ledger_history(), Fairness ledger: per-farmer bump history, and the decayed accumulation…, All recorded seasons for this farmer, most recent first. Empty for a farmer…, Sum of days_bumped across every recorded season, decayed by how long ago each…, True if the single most recent recorded season had a bump. This is the plain…, Record one season's outcome for a farmer. Idempotent per (farmer_id, season_id)…, record_bump(), was_bumped_last_season() (+54 more)

### Community 6 - "test_equity_report.py"
Cohesion: 0.17
Nodes (37): build_result(), _discover_season_ids(), main(), _cluster(), _decision_record(), _farmer(), _plot(), ADR-010 Part 2: equity_report.py. Hermetic -- FileStorage only, no live calls,… (+29 more)

### Community 7 - "handle_route_drop_confirm_callback"
Cohesion: 0.14
Nodes (24): build_route_edit_text(), _edit_message(), handle_route_accept_callback(), handle_route_done_callback(), handle_route_drop_callback(), handle_route_drop_confirm_callback(), handle_route_modify_callback(), handle_route_swap_callback() (+16 more)

### Community 8 - "StorageResult"
Cohesion: 0.12
Nodes (5): Only call this after a check actually completed -- not on a failed check (e.g.…, A plot the coordinator dispatched the machine to this trigger -- excludes it…, Removes the marker -- returns the plot to the schedulable pool on solve()'s…, The symmetric "machine is back" action -- removes the down status. Safe on a…, StorageResult

### Community 9 - "FileStorage"
Cohesion: 0.05
Nodes (46): FileStorage, _cluster(), _decision_record(), _entry(), _farmer(), _plot(), A pre-ADR-008 stored record (written before Farmer.language existed) has no…, Same free-default story as language/area_unit, for ADR-008's per-cluster… (+38 more)

### Community 10 - "test_coordinator.py"
Cohesion: 0.17
Nodes (25): Same orchestration, with an injectable claim provider -- used for deterministic…, run_cluster_with_claims(), _decision(), _FailingDecisionStorage, _FailingMarkStorage, _plot(), Coordinator orchestration tests, using an injected deterministic claim provider…, ADR-011 Part 3, Decision 13: the monotonicity proof in agents/coordinator.py's… (+17 more)

### Community 11 - "test_explain_decision.py"
Cohesion: 0.17
Nodes (43): build_result(), DecisionReplayResult, main(), _narrative_from_decision_record(), Decision replay: reconstruct, from stored records only, why the system…, A plain-English note when an operator's route override touched this specific…, render_text(), _resolve_season() (+35 more)

### Community 12 - "test_machinery_gap.py"
Cohesion: 0.11
Nodes (42): build_cluster_result(), build_result(), ClusterGapResult, _error_result(), MachineryGapReport, main(), _parse_date(), date (+34 more)

### Community 13 - "/graphify Skill"
Cohesion: 0.05
Nodes (43): graphify Trigger Directive (.claude/CLAUDE.md), /graphify add URL Ingestion, graphify add / --watch Reference, --watch Auto-Rebuild Mode, Token Reduction Benchmark, graphify Exports Reference, FalkorDB Export, graphify MCP Server (+35 more)

### Community 14 - "watcher.py"
Cohesion: 0.11
Nodes (26): The weather/capacity/threshold context that produced one trigger day's solve()…, TriggerContext, harvest_day_budget_acres(), Usable-harvest-day budget from a rain forecast. DETERMINISTIC. Given a forecast…, Number of consecutive usable days from the start of `forecast`, up to (not…, Total acreage the shared machine can cover before the window closes., usable_harvest_days(), _apply_rollover_exclusion() (+18 more)

### Community 15 - "test_harvest_confirmation.py"
Cohesion: 0.14
Nodes (26): operator_follow_through_rate(), Confirmed-yes count over (confirmed-yes + confirmed-no) count for this…, _callback_query(), _decision(), _FakeClient, _plot(), ADR-009 Part 2: harvest confirmation loop. Two groups of tests: 1. The webhook…, A self-contradicting double-tap: the second answer updates the stored… (+18 more)

### Community 16 - "test_operator_enrollment.py"
Cohesion: 0.13
Nodes (32): main(), Generates a one-time operator enrollment code for a cluster -- the provisioner-…, _cluster(), _code(), _FakeClient, _lang_tap(), _message_update(), ADR-012 Part 2: operator self-enrollment. Hermetic -- no live Telegram/Bedrock… (+24 more)

### Community 17 - "test_webhook.py"
Cohesion: 0.11
Nodes (30): EscalationPayload, Exactly one message to a human: both plots, the trade-off, two tap targets…, _claim(), _decision_record_for(), _FakeClient, _farmer(), _operator_cluster(), _plot() (+22 more)

### Community 18 - "proxy_registration.py"
Cohesion: 0.12
Nodes (35): advance_proxy_registration(), build_linkfarmer_confirm_keyboard(), build_linkfarmer_match_message(), build_linkfarmer_undo_keyboard(), clear_state(), _confirm_keyboard(), _confirm_summary_message(), handle_addfarmer_command() (+27 more)

### Community 19 - "test_notify.py"
Cohesion: 0.13
Nodes (34): _claim(), _cluster(), _FakeClient, _farmer(), _plot(), Operator-facing text is now per-language too (Cluster.operator_language) --…, ADR-008 Decision 12: facts first, unchanged; the advocate's argument is one…, A model string must never block the escalation -- a fallback-path claim… (+26 more)

### Community 20 - "Cluster"
Cohesion: 0.19
Nodes (32): Cluster, Returns (maturity_gdd, "calibrated" | "fallback"). Extracted out of solve()…, Full scheduling pass over a cluster's plots for one weather trigger. 0. Plots…, resolve_maturity_gdd(), solve(), _days_to_maturity(), _make_days(), _plot() (+24 more)

### Community 21 - "test_registration.py"
Cohesion: 0.21
Nodes (34): advance_registration(), IncomingMessage, str, Pure state transition: given the current step and one incoming message, return…, RegistrationState, RegistrationStep, parametrize, registration.py tests. See ADR-008 Part 2 for the Tamil-support design this… (+26 more)

### Community 22 - "DynamoStorage"
Cohesion: 0.07
Nodes (32): slow, _decode(), DynamoStorage, _from_decimal(), _item_to_cluster(), _item_to_plot(), The one Scan in this file -- every other method here is a targeted…, Base-table query (no GSI) -- every DecisionRecord for one plot, e.g.… (+24 more)

### Community 23 - "._put"
Cohesion: 0.17
Nodes (5): _encode(), MachineStatus, Whether a cluster's shared machine is known to be down indefinitely -- set by…, Overwrite semantics, keyed by cluster_id alone -- one status per cluster. See…, None means operational -- the default, common case. A status record only exists…

### Community 25 - "operator_enrollment.py"
Cohesion: 0.09
Nodes (30): operator_command_usage(), operator_expired_code(), operator_invalid_code(), operator_language_prompt(), operator_command_usage(), operator_expired_code(), operator_invalid_code(), operator_language_prompt() (+22 more)

### Community 26 - "registration.py"
Cohesion: 0.14
Nodes (24): ADR-016, _bilingual_greeting_text(), get_or_create_state(), handle_incoming(), handle_language_callback(), _is_no(), _lang_module(), _persist_completed_registration() (+16 more)

### Community 27 - "test_machine_breakdown.py"
Cohesion: 0.17
Nodes (29): handle_machine_breakdown(), ADR-011 Part 2: an operator's "machine down today" tap. Marks today's…, _claim(), _cluster(), _FakeClient, _farmer(), _patch_weather(), _plot() (+21 more)

### Community 28 - "test_season_rollover.py"
Cohesion: 0.18
Nodes (31): One of "confirmed" (replied=True), "declined" (replied=False), or "unknown"…, Code-only entrypoint, operator-triggered -- ADR-011 Part 1. Not wired to any…, rollover_status(), run_season_rollover(), _claim(), _cluster(), _FakeClient, _farmer() (+23 more)

### Community 29 - "test_farmer_why.py"
Cohesion: 0.13
Nodes (26): _cluster(), _FakeClient, _farmer(), _lost_record(), _not_ready_record(), _plot(), parametrize, ADR-013 Part 3: a farmer's one-tap "why" on a not_ready or… (+18 more)

### Community 30 - "contracts.py"
Cohesion: 0.12
Nodes (28): bedrock, Facts Overwritten With Ground Truth, Model, _build_model(), _build_prompt(), get_advocate_claim(), _make_fairness_tool(), One advocate agent per plot. Structured claims only, never prose. Never throws… (+20 more)

### Community 31 - "PlotOutcome"
Cohesion: 0.18
Nodes (26): _calibrate(), ClusterBacktest, _final_outcome(), _first_ready_trigger(), format_narrative(), format_table(), main(), Historical backtest: what the system would have decided against the real 2025… (+18 more)

### Community 32 - "webhook.py"
Cohesion: 0.07
Nodes (49): Telegram Bot API, FarmerPlotLookup, _default_lookup(), _escalation_key(), handle_addfarmer_confirm_callback(), handle_breakdown_callback(), handle_callback_query(), handle_confirmation_callback() (+41 more)

### Community 33 - "test_link_farmer.py"
Cohesion: 0.20
Nodes (25): _cbq(), _cluster(), _FakeClient, _proxy_farmer(), _proxy_plot(), ADR-013 Part 2 Decision 18: /linkfarmer. Fully stateless and taps only -- every…, If the candidate's plot no longer points back at this proxy_farmer_id (e.g. an…, _seed_pair() (+17 more)

### Community 34 - "AdvocateClaim"
Cohesion: 0.14
Nodes (19): BaseModel, field_validator, RainVulnerability, _fallback_claim(), AdvocateClaim, classify_rain_vulnerability(), A too-green plot's grain is too light to lodge; vulnerability climbs with how…, Structured claim an advocate agent returns. Never prose. (+11 more)

### Community 35 - "interface.py"
Cohesion: 0.10
Nodes (14): FileStorage Default, DynamoStorage Alternate, ADR-005: Persistence and Fairness, Single-Table DynamoDB Design, DecisionRecord Entity, DecisionRecord Retained Indefinitely, Zero-setup local storage: a JSON file on disk. Default backend -- see ADR-005…, AdvanceNoticeRecord, LedgerEntry (+6 more)

### Community 36 - "dynamo.py"
Cohesion: 0.12
Nodes (16): chat_id Float/Int Coercion Bug Found Live, ADR-015: Dynamo Float Fidelity, _from_decimal Value-Based Heuristic Wrong for Float Fields, Per-Type Deserializers Plus Reflection Completeness Test, _cast_claim_floats(), _floats(), _item_to_decision_record(), Real DynamoDB-backed storage. Works against real AWS (default) or DynamoDB… (+8 more)

### Community 37 - "test_advance_notice.py"
Cohesion: 0.23
Nodes (21): _cluster(), _FakeClient, _farmer(), _plot(), ADR-011 Part 4: advance harvest notice. Weather is monkeypatched (same pattern…, Decision 17: the AdvanceNoticeRecord's mere existence suppresses forever,…, days_until <= 0 the first time a plot is checked -- never sent late or same-…, A plot registered late enough that its projected maturity is already within 0… (+13 more)

### Community 38 - "test_help.py"
Cohesion: 0.20
Nodes (21): _advance_notice(), _cluster(), _FakeClient, _farmer(), _help_update(), _plot(), ADR-014: /help, a read-only status command for a registered farmer or the…, ADR-014 Decision 2, reversed on review: the farmer view must win even when the… (+13 more)

### Community 39 - "DailyTemperature"
Cohesion: 0.27
Nodes (11): daily_gdd(), DailyTemperature, project_maturity_date(), Growing Degree Day accumulation. Deterministic arithmetic only -- no LLM…, GDD contribution of a single day. Never negative., First date at which accumulated GDD reaches maturity_gdd, walking `days` in…, test_day_below_base_contributes_zero_not_negative(), test_day_exactly_at_base_contributes_zero() (+3 more)

### Community 40 - "get_storage"
Cohesion: 0.06
Nodes (38): check_confirmation_follow_through(), check_marker(), check_runtime_errors(), check_schedule_recent_activity(), do_invoke(), main(), Manual health check for the deployed daily watcher (see ADR-006 Decision 1, and…, Diagnostic only -- never affects the pass/fail exit code. Operator- facing… (+30 more)

### Community 41 - "openmeteo.py"
Cohesion: 0.12
Nodes (33): _assert_no_gaps(), _cache_key(), _fetch(), _fetch_archive(), _fetch_forecast_forward(), _fetch_forecast_past_days(), get_daily_temperatures(), get_historical_daily() (+25 more)

### Community 42 - "ForecastDay"
Cohesion: 0.18
Nodes (19): ForecastDay, classify_rain_event(), Enum, str, rain_urgency_boost(), RainEventClass, Rain event classification: distinguishes a brief, recoverable shower from a…, Reads the same forecast usable_harvest_days() already reads -- finds the run of… (+11 more)

### Community 43 - "Deterministic Core"
Cohesion: 0.17
Nodes (18): ADOT -> CloudWatch / X-Ray, Deterministic Core, DynamoDB (harvest_convoy), Escalation is the product, not a fallback, LLM Judgment, Math decides, judgment only argues, Resolution (deterministic), haversine_km() (+10 more)

### Community 44 - "otel.py"
Cohesion: 0.13
Nodes (16): entrypoint, AgentCore Runtime deployment root entrypoint. /var/task (the zip's extraction…, SpanExporter, handler(), AgentCore Runtime entrypoint. See docs/adr/ADR-006-deploy.md Decision 1.…, configure_tracing(), get_tracer(), _log_local_collector_reachability() (+8 more)

### Community 45 - "calibration.py"
Cohesion: 0.14
Nodes (21): derive_cluster_maturity_gdd(), derive_reference_gdd_rate(), project_maturity_for_plot(), project_maturity_from_days(), date, Per-cluster maturity-threshold calibration. Generalizes ADR-002's Theni-…, Projected maturity date for a plot that (usually) hasn't reached maturity yet…, Mean daily GDD accrual at (lat, lon), computed the exact way ADR-002 computed… (+13 more)

### Community 46 - "ADR-006: Deployment"
Cohesion: 0.15
Nodes (16): Deterministic Core vs LLM Advocacy Split, ADR-000: Stack and Architecture, Locked Technology Stack, Fairness Ledger Read-Only Stub Tool, Pairwise Negotiation Capped at 3 Rounds, ADR-003: Strands Agents, AgentCore Runtime codeConfiguration Deploy, ADR-006: Deployment (+8 more)

### Community 47 - "crop_params.py"
Cohesion: 0.19
Nodes (12): ADR-001: Agronomy Core, GDD Anchored to transplant_date, ADT45 Maturity Threshold Derived Not Sourced, Open-Meteo Archive/Forecast Seam Bridging, T_BASE_C = 10.0 (sourced), Capacity Budget (usable_harvest_days), Overripe Decay Linear Proxy, GDD Rate Reconciled Against 5-Year Climatology (+4 more)

### Community 48 - "test_farmer_authorization.py"
Cohesion: 0.26
Nodes (17): _cluster(), _confirm_update(), _FakeClient, _farmer(), _plot(), ADR-012: the full-callback audit found two more instances of the same…, The more dangerous half: an unauthorized "yes" would previously have let the…, _rollover_update() (+9 more)

### Community 49 - "Harvest Convoy — Feature Inventory"
Cohesion: 0.10
Nodes (20): 1. Inbound features (farmer-initiated), 2. Outbound features (agent-initiated), 3. Deterministic scheduling, 4. LLM-mediated decisions, 5. Storage and audit, 6. Reporting and audit tools (`scripts/`), 7. Deployment reality, 8. What is NOT implemented — do not claim these in the video (+12 more)

### Community 50 - "test_proxy_registration.py"
Cohesion: 0.15
Nodes (23): get_pending_state(), _mint_proxy_ids(), persist_proxy_registration(), farmer-proxy-{hex}/plot-proxy-{hex} -- a disjoint namespace from the ordinary…, Called only on an authorized 'yes' tap at AWAITING_CONFIRM. telegram_chat_id is…, _addfarmer_update(), _cluster(), _complete_proxy_flow() (+15 more)

### Community 51 - "parametrize"
Cohesion: 0.11
Nodes (18): parametrize, test_all_prompts_render(), test_bumped_suffix_renders(), test_complete_message_renders(), test_drying_window_alert_omits_moisture_when_unset(), test_drying_window_alert_omits_msp_when_unset(), test_drying_window_alert_with_both_figures_set_includes_both(), test_escalation_resolved_lost_renders_without_keyerror() (+10 more)

### Community 52 - "build_plot_facts"
Cohesion: 0.21
Nodes (14): build_plot_facts(), _language_test_plot(), ADR-008 Decision 12: PlotFacts.language comes from the plot's own farmer's…, test_build_plot_facts_defaults_to_tamil_when_farmer_not_found(), test_build_plot_facts_pulls_the_farmers_registered_language(), _decision(), _plot(), The Phase 5 gate. Two tests, both required, together the honest claim that the… (+6 more)

### Community 53 - "negotiate_pair"
Cohesion: 0.19
Nodes (16): ClaimProvider, _fairness_was_decisive(), negotiate_pair(), NegotiationResult, None if the round was resolved by a concession (a model judgment, not a score…, Pairwise negotiation between two contested plots. Resolves as soon as either…, _claim(), _facts() (+8 more)

### Community 54 - "generate_architecture_diagram.py"
Cohesion: 0.23
Nodes (11): arrow(), arrowhead(), centered_text(), elbow_arrow(), elbow_arrow_hv(), font(), Generates docs/architecture.png. Not part of the shipped package -- a build-…, Horizontal-then-vertical connector: goes sideways at bend_y (while still clear… (+3 more)

### Community 55 - "equity_report.py"
Cohesion: 0.22
Nodes (12): _build_season_section(), EquityReport, Equity report: was allocation of the shared machine fair, and did the fairness…, Cross-season by nature -- put_ledger_entry allows at most one entry per farmer…, _render_section_text(), render_text(), _repeat_bumps(), RepeatBumpEntry (+4 more)

### Community 56 - "BreakdownDisplacement"
Cohesion: 0.22
Nodes (5): BreakdownDisplacement, A plot un-harvested because the machine broke down, not because another…, Overwrite semantics -- a retried write for the same (plot_id, season_id,…, Every breakdown displacement for this cluster/season -- equity_report.py's…, Filtered to one report_date -- the breakdown callback's double-tap idempotency…

### Community 57 - "test_messages.py"
Cohesion: 0.15
Nodes (4): messages_ta.py / messages_en.py rendering tests. See ADR-008 Part 2, Decision…, test_crop_confirm_declined_message_renders(), test_harvest_scheduled_renders_without_keyerror(), test_transplant_info_missing_prefix_renders_for_every_combination()

### Community 58 - "_complete_registration"
Cohesion: 0.22
Nodes (13): _cluster(), _complete_registration(), The required negative test: registration still completes, the farmer/plot still…, ADR-009 Prerequisite: a second complete run from the same chat_id updates the…, ADR-013 Part 2 Decision 18: generalizes ADR-009's "same chat_id -> update, not…, test_re_registration_after_linkfarmer_updates_the_canonical_proxy_plot_not_the_retired_duplicate(), test_registration_completion_appends_maturity_sentence_when_weather_available(), test_registration_completion_falls_back_to_plain_message_on_weather_error() (+5 more)

### Community 59 - "explain_decision.py Replay Tool"
Cohesion: 0.12
Nodes (17): Native ADOT Tracing Export, Half-Translation Bugs Found via Live Telegram Verification, Projected Maturity Date at Registration, CloudWatch Traces Are Not a Substitute for Persistence, explain_decision.py Replay Tool, machinery_gap.py Report, System Computes Reasoning Then Discards It, ADR-010: Reporting and Decision Replay (+9 more)

### Community 60 - "coordinator.py"
Cohesion: 0.18
Nodes (12): _base_decision_record(), ClusterResult, CoordinatorOutcome, _fairness_bonus(), date, Coordinator: owns the machine calendar for one weather trigger. Calls one…, The deterministic-core fields every DecisionRecord shares, regardless of…, Never blocks or crashes the run -- same discipline ADR-009 Part 1.5 established… (+4 more)

### Community 61 - "RouteOverride Entity"
Cohesion: 0.16
Nodes (14): Fairness Ledger Accumulated/Decayed/Capped, 2025 Kuruvai Historical Backtest, solve() Re-Competes FITS Plots Bug -> HARVESTED Outcome, equity_report.py Fairness Report, BreakdownDisplacement Entity, Machine Breakdown Recompute, No Season Entity; Rollover is Operator-Triggered, RainEventClass BRIEF/SUSTAINED Classification (+6 more)

### Community 62 - "solver.py"
Cohesion: 0.16
Nodes (15): decay_fraction(), Overripe decay curve. DERIVED, NOT sourced -- see docs/adr/ADR-002-scheduling-…, Fraction (0.0-1.0) representing how far a plot has decayed past its projected…, assess_plot(), PlotDecision, date, Enum, Combines agronomy assessment, capacity budgeting, and route ordering into the… (+7 more)

### Community 63 - "TelegramClient"
Cohesion: 0.23
Nodes (7): TelegramClient, _FakeResponse, test_answer_callback_query_builds_correct_payload(), test_send_message_degrades_after_exhausting_retries(), test_send_message_retries_then_succeeds(), test_send_message_succeeds_on_first_try(), test_telegram_api_error_response_is_treated_as_failure()

### Community 64 - "format_area"
Cohesion: 0.18
Nodes (11): advance_harvest_notice(), escalation_resolved_lost(), escalation_resolved_won(), format_area(), harvest_confirmation_prompt(), harvest_scheduled(), not_ready(), _ordinal_word() (+3 more)

### Community 65 - "handle_operator_lang_callback"
Cohesion: 0.19
Nodes (13): clear_state(), complete_enrollment(), matches_pending(), Returns (record, status). status is one of "invalid" (no such code -- record is…, Writes the three things a successful enrollment or replacement touches:…, The binding check every operator_lang:/operator_replace: callback must pass…, validate_code(), handle_operator_lang_callback() (+5 more)

### Community 66 - "ADR-009: Harvest Lifecycle and Validation"
Cohesion: 0.40
Nodes (5): run_daily_watch Multi-Cluster Iteration, Post-Harvest Drying-Window Rain Alert, ADR-009: Harvest Lifecycle and Validation, MSP/Moisture Constants Left None Until Sourced, Registration Never Persisted Farmer/Plot (Prerequisite Fix)

### Community 67 - "Full Callback Authorization Audit"
Cohesion: 0.16
Nodes (14): AgentCore Memory Skipped Deliberately, HarvestConfirmation Tri-State confirmed bool|None, Silence Recorded as Unknown, Never as No-Show, SeasonRolloverPrompt Entity, Full Callback Authorization Audit, /linkfarmer Duplicate-Linking Mechanism, Bounded-Window Undo for /linkfarmer, Operator-Only Proxy Registration, No Helper Role (+6 more)

### Community 68 - "trigger_scenario.py"
Cohesion: 0.26
Nodes (11): build_deadlock_facts(), build_escalation(), main(), _offline_claim(), date, Manual verification harness: drives the seeded Kamatchipuram cluster through…, Runs the REAL negotiate_pair() loop against the engineered deadlock facts --…, Long-poll getUpdates until the escalation callback arrives, or… (+3 more)

### Community 69 - "HarvestConfirmation"
Cohesion: 0.22
Nodes (5): HarvestConfirmation, None if no confirmation record exists for this plot/season -- e.g. it was never…, Overwrite semantics, like every put_* here except put_ledger_entry -- both the…, Every confirmation record for this cluster/season -- small by construction (one…, Did the machine actually come? One record per (plot_id, season_id), created the…

### Community 70 - "SeasonRolloverPrompt"
Cohesion: 0.22
Nodes (5): Whether a farmer confirmed participation in a new season after a rollover…, Overwrite semantics, like every put_* here except put_ledger_entry -- a…, None if no rollover was ever run for this plot/season -- either this plot is…, Every rollover-prompt record for this cluster/season -- the watcher's…, SeasonRolloverPrompt

### Community 71 - "format_area"
Cohesion: 0.20
Nodes (10): advance_harvest_notice(), escalation_resolved_lost(), escalation_resolved_won(), format_area(), harvest_confirmation_prompt(), harvest_scheduled(), not_ready(), Sent exactly once per plot per season, roughly a week before projected maturity… (+2 more)

### Community 73 - "RouteOverride"
Cohesion: 0.22
Nodes (5): The day's proposed route and whatever the operator has done to it so far. One…, Overwrite semantics, like every put_* here except put_ledger_entry -- keyed by…, None if no route was ever proposed for this exact key -- never asked (no FITS…, Every proposed route for this cluster/season -- equity_report.py's Operator…, RouteOverride

### Community 74 - "models.py"
Cohesion: 0.12
Nodes (15): Local dev runner: long-polls Telegram's getUpdates instead of running a webhook…, The 8-plot Kamatchipuram demo cluster. Plain fixture data only -- no DynamoDB…, Core entity model: plain dataclasses, no persistence or transport concerns.…, Storage backend selection. See ADR-005 Decision 1: FileStorage is the default…, Thin wrapper over the Telegram Bot API. Raw HTTPS + JSON, no framework -- see…, _parse_date(), date, advance_rollover_reply() (+7 more)

### Community 75 - "test_kamatchipuram_agents_gate.py"
Cohesion: 0.36
Nodes (8): date, Phase 3 gate, corrected in Phase 5: too-green plots concede, and p03 correctly…, _run_gate_scenario(), _synthetic_days(), test_p03_beats_p04_outright_with_no_escalation(), test_ready_plots_do_not_concede(), test_too_green_plots_concede(), _truthful_claim()

### Community 76 - "test_kamatchipuram_gate.py"
Cohesion: 0.36
Nodes (8): date, Phase 2 gate: given the 8 seeded Kamatchipuram plots and a rain window, the…, _run_gate_scenario(), _synthetic_days(), test_fits_plots_get_a_route_position_and_contested_plots_do_not(), test_gate_scenario_is_identical_across_repeated_runs(), test_kamatchipuram_gate_produces_all_three_outcomes(), test_too_green_plots_have_no_route_position_or_urgency()

### Community 77 - "run_daily_watch"
Cohesion: 0.25
Nodes (9): AgentCore Runtime (bedrock_agentcore.BedrockAgentCoreApp), EventBridge Schedule, Lambda shim (watcher-invoker), Open-Meteo API, Entry point for one scheduled invocation. Returns a plain dict summary (never…, run_daily_watch(), ADR-008 Decision 4: passing a list iterates independently -- proven here with…, test_cluster_id_list_returns_one_summary_per_cluster_in_order() (+1 more)

### Community 78 - "webhook.py Has No Live Entrypoint"
Cohesion: 0.25
Nodes (8): Escalation Resolution Idempotency, not_ready as First-Class Distinct Message, advance_registration Pure FSM, ADR-004: Telegram Interface, /help as the One Deliberate Farmer-Initiated Exception, README/ARCHITECTURE.md Corrected to Disclose Inbound Gap, ADR-016: No Live Inbound Path, webhook.py Has No Live Entrypoint

### Community 79 - "ADR-008: TN Generalization and Tamil"
Cohesion: 0.29
Nodes (7): agronomy/calibration.py Per-Cluster GDD, Farmer.language Field, Second Cluster: Naducauvery, Thanjavur, Nova Pro Cannot Generate Valid Tamil Script, advocate_argument Templated From Facts, ADR-008: TN Generalization and Tamil, Beat 6: Cost and Honesty Disclosure

### Community 80 - "OperatorEnrollmentCode"
Cohesion: 0.18
Nodes (8): OperatorEnrollmentCode, A one-time code handed to a real operator out-of-band by whoever provisions a…, Overwrite semantics, keyed by `code` alone -- both the generating write…, None if this code was never generated -- an invalid code, not distinguished in…, _float_field_names(), Every field on cls whose resolved annotation is float or float | None -- the…, Completeness guard for ADR-015's per-type deserializers: a future float field…, test_every_float_field_across_all_dataclasses_is_covered_by_a_dynamo_cast_list()

### Community 81 - "_day_word"
Cohesion: 0.29
Nodes (7): advocate_argument(), _day_word(), overripe_phrase(), See messages_en.py's resolution_reason -- same logic, Tamil wording. Found…, நாள் (singular) / நாட்கள் (plural) -- Tamil count-noun agreement.…, resolution_reason(), why_not_ready_answer()

### Community 82 - "OperatorAuditEvent"
Cohesion: 0.29
Nodes (4): OperatorAuditEvent, An auditable record of who became a cluster's operator and when -- ADR-012 Part…, Append-only -- there is no update or delete for this entity. A retried write…, Every operator enrollment/replacement ever recorded for this cluster, in…

### Community 83 - "handle_linkfarmer_confirm_callback"
Cohesion: 0.40
Nodes (5): apply_link(), The one, narrow, purpose-built mutation Decision 18 performs -- not a general…, handle_linkfarmer_confirm_callback(), parse_linkfarmer_confirm_callback_data(), Step 3 of /linkfarmer -- the only step that mutates anything. On yes:…

### Community 85 - "Storage"
Cohesion: 0.07
Nodes (26): Protocol, Every cluster_id this backend has a Cluster record for. Nothing in the…, ISO date string of the last day the daily watcher completed a check for this…, Every plot_id marked harvested for this cluster/season. Empty set for a season…, Storage, _formatted_date(), Farmer-facing "why?" answers -- ADR-013 Part 3. Assembled entirely from a…, _record_or_none() (+18 more)

### Community 86 - "test_weather_seam.py"
Cohesion: 0.47
Nodes (5): network, Live tests against the real Open-Meteo API for a Theni-area coordinate. These…, test_archive_to_forecast_seam_has_no_gaps_or_duplicates(), test_beyond_forecast_horizon_raises_not_silently_truncates(), test_purely_historical_range_has_no_gaps()

### Community 89 - "handle_breakdown_followup_callback"
Cohesion: 0.29
Nodes (7): build_machine_back_keyboard(), The symmetric clearing action for "down indefinitely" -- sent alongside the…, handle_breakdown_followup_callback(), parse_breakdown_followup_callback_data(), The optional second tap -- "back tomorrow" (the default; sets no state at all,…, The "down indefinitely" follow-up (ADR-011 Part 2) -- suppresses dispatch on…, set_machine_down()

## Ambiguous Edges - Review These
- `webhook.py` → `Telegram Bot API`  [AMBIGUOUS]
  docs/architecture.png · relation: references

## Knowledge Gaps
- **60 isolated node(s):** ``/start` — does not exist as a command`, `Registration FSM`, ``/help``, ``/addfarmer` (proxy registration)`, ``/linkfarmer`` (+55 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **34 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `webhook.py` and `Telegram Bot API`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `FileStorage` connect `FileStorage` to `test_watcher.py`, `Plot`, `test_route_override.py`, `test_equity_report.py`, `StorageResult`, `test_coordinator.py`, `test_explain_decision.py`, `test_machinery_gap.py`, `test_harvest_confirmation.py`, `test_operator_enrollment.py`, `test_webhook.py`, `Cluster`, `._put`, `registration.py`, `test_machine_breakdown.py`, `test_season_rollover.py`, `test_farmer_why.py`, `test_link_farmer.py`, `interface.py`, `dynamo.py`, `test_advance_notice.py`, `test_help.py`, `get_storage`, `test_farmer_authorization.py`, `test_proxy_registration.py`, `build_plot_facts`, `equity_report.py`, `BreakdownDisplacement`, `_complete_registration`, `HarvestConfirmation`, `SeasonRolloverPrompt`, `RouteOverride`, `models.py`, `test_kamatchipuram_agents_gate.py`, `run_daily_watch`, `OperatorEnrollmentCode`, `OperatorAuditEvent`, `.__init__`?**
  _High betweenness centrality (0.230) - this node is a cross-community bridge._
- **Why does `Storage` connect `Storage` to `test_watcher.py`, `Plot`, `test_route_override.py`, `test_equity_report.py`, `handle_route_drop_confirm_callback`, `StorageResult`, `test_coordinator.py`, `test_explain_decision.py`, `test_machinery_gap.py`, `watcher.py`, `test_harvest_confirmation.py`, `proxy_registration.py`, `Cluster`, `._put`, `operator_enrollment.py`, `registration.py`, `test_machine_breakdown.py`, `test_season_rollover.py`, `contracts.py`, `webhook.py`, `interface.py`, `dynamo.py`, `get_storage`, `test_proxy_registration.py`, `build_plot_facts`, `equity_report.py`, `BreakdownDisplacement`, `coordinator.py`, `handle_operator_lang_callback`, `HarvestConfirmation`, `SeasonRolloverPrompt`, `RouteOverride`, `models.py`, `run_daily_watch`, `OperatorEnrollmentCode`, `OperatorAuditEvent`, `handle_linkfarmer_confirm_callback`, `handle_breakdown_followup_callback`?**
  _High betweenness centrality (0.096) - this node is a cross-community bridge._
- **Why does `Cluster` connect `Cluster` to `test_watcher.py`, `Plot`, `test_route_override.py`, `test_equity_report.py`, `handle_route_drop_confirm_callback`, `StorageResult`, `FileStorage`, `test_explain_decision.py`, `test_machinery_gap.py`, `watcher.py`, `test_operator_enrollment.py`, `test_webhook.py`, `test_notify.py`, `DynamoStorage`, `._put`, `operator_enrollment.py`, `test_machine_breakdown.py`, `test_season_rollover.py`, `test_farmer_why.py`, `PlotOutcome`, `test_link_farmer.py`, `interface.py`, `dynamo.py`, `test_advance_notice.py`, `test_help.py`, `calibration.py`, `test_farmer_authorization.py`, `test_proxy_registration.py`, `_complete_registration`, `solver.py`, `handle_operator_lang_callback`, `models.py`, `run_daily_watch`, `OperatorEnrollmentCode`, `Storage`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Are the 296 inferred relationships involving `FileStorage` (e.g. with `Cluster` and `Farmer`) actually correct?**
  _`FileStorage` has 296 INFERRED edges - model-reasoned connections that need verification._
- **Are the 17 inferred relationships involving `Storage` (e.g. with `build_result()` and `_build_season_section()`) actually correct?**
  _`Storage` has 17 INFERRED edges - model-reasoned connections that need verification._
- **Are the 74 inferred relationships involving `Plot` (e.g. with `ClusterBacktest` and `_shift_to_2025()`) actually correct?**
  _`Plot` has 74 INFERRED edges - model-reasoned connections that need verification._