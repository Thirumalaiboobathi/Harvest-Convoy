"""Shared storage interface. Two implementations -- FileStorage (default,
zero-setup local dev) and DynamoStorage (real AWS or DynamoDB Local via
endpoint_url) -- both implement this same Protocol against the identical
single-table PK/SK scheme documented in ADR-005.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from harvest_convoy.models import Cluster, Farmer, Plot


@dataclass(frozen=True)
class StorageResult:
    success: bool
    error: str | None = None


@dataclass(frozen=True)
class LedgerEntry:
    """One farmer's outcome for one season. See ADR-005 Decision 2 -- this
    is the whole record ("bump count, which seasons, by how many days, and
    the outcome each time") for a single season; a farmer's full history
    is just every LedgerEntry they have.
    """

    farmer_id: str
    season_id: str
    days_bumped: int
    outcome: str  # e.g. "bumped", "won", "no_conflict"
    resolved_at: str  # ISO 8601 timestamp
    cluster_id: str
    plot_id: str
    opponent_plot_id: str


@dataclass(frozen=True)
class HarvestConfirmation:
    """Did the machine actually come? One record per (plot_id, season_id),
    created the moment the watcher sends a harvest_scheduled notification
    -- before any farmer reply exists. See ADR-009 Part 2.

    `confirmed: bool | None` is the load-bearing type: None is a real,
    distinct third state (never asked, or asked and no reply yet) --
    never assumed True or False. Per your explicit instruction, silence
    past the confirmation window is recorded as unknown, not as a no-show
    -- there is no separate "timed out" flag; `confirmed is None` plus
    `asked_at` being older than `watcher.UNCONFIRMED_HARVEST_WINDOW_DAYS`
    is what "unknown" means, computed at read time, not written.
    """

    plot_id: str
    farmer_id: str
    cluster_id: str
    season_id: str
    scheduled_date: str        # ISO date -- the day the route said "today"
    asked_at: str | None = None      # ISO timestamp the evening prompt was sent
    confirmed: bool | None = None    # None = unknown/no reply -- never assumed
    confirmed_at: str | None = None  # ISO timestamp of the farmer's reply, if any
    # One flag for the whole post-harvest drying window (ADR-009 Part 4),
    # not per day and not per rain event -- once True, the daily watcher
    # skips this plot for the rest of its 4-day window regardless of how
    # many more days show rain. Meaningless (stays False forever) unless
    # confirmed is True -- the drying window is chained off a *confirmed*
    # harvest, never a merely-scheduled one.
    drying_alert_sent: bool = False
    # True when a machine breakdown invalidated the dispatch this
    # confirmation was created for (ADR-011 Part 2, Decision 9) -- a
    # cancelled confirmation is never asked about in the evening sweep,
    # and a reply against it (a farmer truthfully tapping "no" after the
    # prompt already went out) is acknowledged but never credited to the
    # fairness ledger. Distinct from "unknown": unknown means we don't
    # know what happened; cancelled means we know, and it wasn't the
    # farmer's or another farmer's doing.
    cancelled: bool = False


@dataclass(frozen=True)
class DecisionRecord:
    """What the deterministic core and the coordinator actually computed
    for one plot on one trigger day -- written at the moment the decision
    is made (or, for an escalated pair, updated at the moment a human
    resolves it), so a later reader never has to recompute or guess.
    See ADR-010 Part 0.5.
    """

    plot_id: str
    farmer_id: str
    cluster_id: str
    season_id: str
    decision_date: str  # ISO date -- the trigger day this record is for

    # scheduling/solver.py:PlotDecision, copied at the moment it's known
    accumulated_gdd: float
    maturity_gdd_used: float
    threshold_source: str  # "calibrated" | "fallback"
    outcome: str  # PlotOutcome.value -- too_green | fits | contested | harvested
    days_past_maturity: int | None
    urgency: float
    route_position: int | None  # only set for fits

    # The weather/capacity context that produced this trigger day's
    # classification -- identical for every plot decided this run, stored
    # per-record anyway so a single plot's DecisionRecord is fully
    # self-contained and never needs a second lookup to make sense.
    rain_threshold_mm: float
    forecast_horizon_days: int
    usable_harvest_days: int
    machine_capacity_acres_per_day: float
    capacity_budget_acres: float

    # Negotiation -- populated only for a plot that went through
    # negotiate_pair() this trigger day; None for too_green/fits/harvested
    # and for a contested plot with no pairing partner this round.
    opponent_plot_id: str | None = None
    own_claim: dict | None = None  # AdvocateClaim.model_dump()
    opponent_claim: dict | None = None  # AdvocateClaim.model_dump()
    rounds_run: int | None = None
    resolution: str | None = None
    # "won" | "lost" (resolved this trigger, no escalation) |
    # "escalated" (pending human resolution) |
    # "escalated_won" | "escalated_lost" (webhook.py updates this after a
    # human taps) | "contested_no_partner_this_round" (odd plot out,
    # ADR-003 Decision 4's pairwise-only scope limit)
    fairness_decisive: bool | None = None
    # True/False only when a genuine automatic score comparison decided
    # the round -- derived post-hoc, pure arithmetic over the two
    # already-known claims (see coordinator.py:_fairness_was_decisive).
    # None means "not applicable": resolved by concession (a model
    # judgment, not a score comparison), resolved by a human tap, or not
    # yet resolved at all (still "escalated").
    resolved_at: str | None = None  # ISO timestamp of the FINAL resolution
    # "scheduled" (the normal daily trigger) | "breakdown_recompute" (this
    # record was produced by a machine-breakdown recompute -- ADR-011
    # Part 2 -- not the day's original trigger). Lets a reader of
    # explain_decision.py tell *why* a plot's numbers look the way they
    # do on a given day, e.g. zero capacity because of a breakdown, not
    # because of rain.
    trigger_reason: str = "scheduled"
    # "none" | "brief" | "sustained" and the urgency boost it produced --
    # ADR-011 Part 3. See TriggerContext for the same fields' meaning;
    # copied here verbatim at write time.
    rain_event_classification: str = "none"
    rain_urgency_boost: float = 0.0


@dataclass(frozen=True)
class SeasonRolloverPrompt:
    """Whether a farmer confirmed participation in a new season after a
    rollover prompt. replied=None (never answered) and replied=False
    (explicit "no") both exclude the plot from the new season's
    scheduling pool -- only replied=True includes it. Mirrors
    HarvestConfirmation's confirmed: bool | None shape and the same
    silence-is-not-consent discipline (ADR-009 Part 2), applied to the
    opposite question (should this plot be scheduled at all this season,
    not did the machine come). "Declined" (replied=False) and "did not
    respond" (replied=None) are different facts about a person and must
    stay distinguishable everywhere this record is read -- both exclude
    a plot from scheduling identically, but never collapse into one
    "excluded" state in storage or in any report built on top of it. See
    ADR-011 Part 1.
    """

    plot_id: str
    farmer_id: str
    cluster_id: str
    old_season_id: str
    new_season_id: str
    asked_at: str
    replied: bool | None = None
    replied_at: str | None = None


@dataclass(frozen=True)
class BreakdownDisplacement:
    """A plot un-harvested because the machine broke down, not because
    another farmer's claim was stronger. Deliberately separate from
    LedgerEntry -- record_bump()/LedgerEntry exist to detect one farmer
    losing to another farmer's genuinely stronger claim; crediting a
    mechanical failure into that same ledger would make it measure
    equipment reliability instead of the thing it's actually for,
    silently, in a way nobody reading equity_report.py later could tell
    apart from a real bump. Nothing in the breakdown path ever writes a
    LedgerEntry. See ADR-011 Part 2, Decision 8.
    """

    plot_id: str
    farmer_id: str
    cluster_id: str
    season_id: str
    original_scheduled_date: str
    reported_at: str
    reason: str = "machine_breakdown"


@dataclass(frozen=True)
class MachineStatus:
    """Whether a cluster's shared machine is known to be down
    indefinitely -- set by the operator's "down indefinitely" follow-up
    tap (ADR-011 Part 2), cleared only by a symmetric "machine is back"
    tap. Absence of a record (get_machine_status returns None) means
    operational -- the common, default case, matching every other
    entity's "no record = nothing unusual" convention in this codebase.
    A status that could only ever be set and never cleared would leave a
    cluster permanently stuck at zero capacity, which is why the
    clearing action exists and is built in the same part, not deferred.
    """

    cluster_id: str
    status: str  # "down" -- "operational" is represented by no record at all
    reported_at: str


@dataclass(frozen=True)
class AdvanceNoticeRecord:
    """Proof that the one-time "arrange transport, drying space" notice
    (ADR-011 Part 4) was already sent for this plot this season. Its mere
    existence, regardless of what `projected_maturity_date` it froze, is
    the entire suppression check -- once sent, a later, more accurate
    projection never triggers a second message. `projected_maturity_date`
    is kept only for the audit trail (what was this farmer actually told,
    and when), not to support a correction path that isn't built. See
    Decision 17.
    """

    plot_id: str
    farmer_id: str
    cluster_id: str
    season_id: str
    sent_at: str
    projected_maturity_date: str


@dataclass(frozen=True)
class OperatorEnrollmentCode:
    """A one-time code handed to a real operator out-of-band by whoever
    provisions a cluster, so they can self-enroll (`/operator <code>`)
    instead of having `Cluster.operator_chat_id` set by hand in a seed
    script. See ADR-012 Part 2.

    Looked up by `code` alone -- the operator only ever types the code,
    never a cluster_id, so the code itself is what resolves the cluster.
    Single-use (`used_at`) AND time-limited (`expires_at`) -- defense in
    depth, not either/or. `expires_at`'s window is an unsourced judgment
    call, same status as DRYING_WINDOW_DAYS/
    ADVANCE_NOTICE_DAYS_BEFORE_MATURITY.
    """

    code: str
    cluster_id: str
    created_at: str
    expires_at: str
    used_at: str | None = None
    used_by_chat_id: int | None = None


@dataclass(frozen=True)
class OperatorAuditEvent:
    """An auditable record of who became a cluster's operator and when --
    ADR-012 Part 2, Decision 2. Operator identity determines who can
    report a machine breakdown or resolve a scheduling conflict for an
    entire cluster, so a change of operator must be reconstructable
    later, the same way every scheduling decision already is
    (DecisionRecord, ADR-010 Part 0.5). `previous_operator_chat_id` is
    None for a first enrollment (no prior operator existed to record);
    set for a replacement, alongside the new one, so both halves of the
    change are on one record rather than requiring a diff against
    whatever `Cluster.operator_chat_id` happened to be before this write.
    Append-only: nothing here is ever overwritten or deleted.
    """

    cluster_id: str
    event_type: str  # "enrolled" | "replaced"
    occurred_at: str
    code_used: str
    new_operator_chat_id: int
    previous_operator_chat_id: int | None = None


class Storage(Protocol):
    def get_cluster(self, cluster_id: str) -> Cluster | None: ...
    def put_cluster(self, cluster: Cluster) -> StorageResult: ...

    def list_cluster_ids(self) -> list[str]:
        """Every cluster_id this backend has a Cluster record for. Nothing
        in the scheduling/watcher path needs this -- every real entrypoint
        is always given explicit cluster_id(s). Added for ADR-010 Part 3
        (scripts/machinery_gap.py), which runs across all clusters by
        default with no other way to discover what "all" means. Read-only,
        low-frequency (an on-demand report, not a per-trigger call)."""
        ...

    def get_farmer(self, farmer_id: str) -> Farmer | None: ...
    def put_farmer(self, farmer: Farmer) -> StorageResult: ...
    def get_farmers_for_cluster(self, cluster_id: str) -> list[Farmer]: ...

    def get_plot(self, plot_id: str) -> Plot | None: ...
    def put_plot(self, plot: Plot) -> StorageResult: ...
    def get_plots_for_cluster(self, cluster_id: str) -> list[Plot]: ...

    def get_ledger_entries(self, farmer_id: str) -> list[LedgerEntry]: ...

    def put_ledger_entry(self, entry: LedgerEntry) -> StorageResult:
        """Must be idempotent per (farmer_id, season_id): a second write
        for the same key fails with StorageResult(success=False), it does
        not silently overwrite. See ADR-005 Decision 5, concurrent writes.
        """
        ...

    def get_watcher_last_run(self, cluster_id: str) -> str | None:
        """ISO date string of the last day the daily watcher completed a
        check for this cluster (trigger or no-trigger, either counts), or
        None if it has never run. See ADR-006 Decision 2 -- this is the
        real idempotency guard against firing twice in one day."""
        ...

    def set_watcher_last_run(self, cluster_id: str, run_date: str) -> StorageResult:
        """Only call this after a check actually completed -- not on a
        failed check (e.g. Open-Meteo down), so a failed day gets retried
        rather than silently skipped."""
        ...

    def mark_plot_harvested(
        self, plot_id: str, cluster_id: str, season_id: str, dispatched_at: str
    ) -> StorageResult:
        """A plot the coordinator dispatched the machine to this trigger --
        excludes it from scheduling.solver.solve() for the rest of this
        season. Overwrite semantics, like every put_* here except
        put_ledger_entry -- a plot legitimately marked twice (e.g. a
        retried write) should just update dispatched_at, not error. See
        ADR-009 Part 1.5."""
        ...

    def clear_plot_harvest(
        self, plot_id: str, cluster_id: str, season_id: str
    ) -> StorageResult:
        """Removes the marker -- returns the plot to the schedulable pool
        on solve()'s next call. Safe on a plot never marked (a no-op
        success, not an error). Used today for an operator correcting a
        bad mark (scripts/clear_plot_harvest.py); designed to also be the
        reversal hook Part 2's farmer "it never came" confirmation will
        call once that loop is built -- not wired up yet."""
        ...

    def get_harvested_plot_ids(self, cluster_id: str, season_id: str) -> set[str]:
        """Every plot_id marked harvested for this cluster/season. Empty
        set for a season with no records yet -- there is no separate
        Season entity in this codebase; season_id is an opaque string, and
        a new one has no prior harvest state by construction."""
        ...

    def get_harvest_confirmation(
        self, plot_id: str, season_id: str
    ) -> HarvestConfirmation | None:
        """None if no confirmation record exists for this plot/season --
        e.g. it was never scheduled, or a stale/forged callback references
        a key that was never created. See ADR-009 Part 2."""
        ...

    def put_harvest_confirmation(
        self, confirmation: HarvestConfirmation
    ) -> StorageResult:
        """Overwrite semantics, like every put_* here except
        put_ledger_entry -- both the creation write (watcher.py, on
        dispatch) and the farmer's reply write (webhook.py) go through
        this same method."""
        ...

    def get_confirmations_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[HarvestConfirmation]:
        """Every confirmation record for this cluster/season -- small by
        construction (one per dispatched plot per season, matching this
        project's demo-scale clusters), so callers filter in Python
        (pending-to-ask, still-unknown, yes/no counts) rather than this
        method taking a menu of server-side filter parameters."""
        ...

    def put_decision_record(self, record: DecisionRecord) -> StorageResult:
        """Overwrite semantics, like every put_* here except
        put_ledger_entry -- a retried write for the same (plot_id,
        season_id, decision_date) updates rather than errors, matching
        mark_plot_harvested's contract. See ADR-010 Part 0.5."""
        ...

    def get_decision_record(
        self, plot_id: str, season_id: str, decision_date: str
    ) -> DecisionRecord | None:
        """None if no record exists for this exact key -- never asked,
        write failed and was only logged (ADR-010 Part 0.5 Decision C),
        or this decision predates Part 0.5 entirely. Callers distinguish
        those cases by other evidence (a LedgerEntry, a stored code
        version), not by anything this method returns."""
        ...

    def get_decision_records_for_plot(
        self, plot_id: str, season_id: str | None = None
    ) -> list[DecisionRecord]:
        """This plot's full decision history, optionally filtered to one
        season. season_id=None (the default) returns every recorded
        season -- needed when the caller doesn't yet know which season a
        requested date falls under."""
        ...

    def get_decision_records_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[DecisionRecord]:
        """Every decision record for this cluster/season -- reporting's
        fairness-mechanism-activity aggregate reads this."""
        ...

    def put_season_rollover_prompt(self, prompt: SeasonRolloverPrompt) -> StorageResult:
        """Overwrite semantics, like every put_* here except
        put_ledger_entry -- a farmer's reply updates the same record the
        prompt-send created, keyed by (plot_id, new_season_id). See
        ADR-011 Part 1."""
        ...

    def get_season_rollover_prompt(
        self, plot_id: str, season_id: str
    ) -> SeasonRolloverPrompt | None:
        """None if no rollover was ever run for this plot/season -- either
        this plot is brand new this season (registered directly, nothing
        to ask), or run_season_rollover() hasn't been triggered yet for
        this cluster/season."""
        ...

    def get_season_rollover_prompts_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[SeasonRolloverPrompt]:
        """Every rollover-prompt record for this cluster/season -- the
        watcher's scheduling-exclusion filter and equity_report.py's
        Season participation section both read this."""
        ...

    def put_breakdown_displacement(self, displacement: BreakdownDisplacement) -> StorageResult:
        """Overwrite semantics -- a retried write for the same
        (plot_id, season_id, original_scheduled_date) key updates rather
        than errors. See ADR-011 Part 2."""
        ...

    def get_breakdown_displacements_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[BreakdownDisplacement]:
        """Every breakdown displacement for this cluster/season --
        equity_report.py's Breakdown displacements section reads this."""
        ...

    def get_breakdown_displacements_for_date(
        self, cluster_id: str, season_id: str, report_date: str
    ) -> list[BreakdownDisplacement]:
        """Filtered to one report_date -- the breakdown callback's
        double-tap idempotency check reads this before doing anything
        else: if a displacement already exists for today, the second tap
        is a no-op, not a second recompute and a second round of
        notifications."""
        ...

    def put_machine_status(self, status: MachineStatus) -> StorageResult:
        """Overwrite semantics, keyed by cluster_id alone -- one status
        per cluster. See ADR-011 Part 2."""
        ...

    def get_machine_status(self, cluster_id: str) -> MachineStatus | None:
        """None means operational -- the default, common case. A status
        record only exists while a cluster is down; clear_machine_status
        removes it entirely rather than writing status="operational"."""
        ...

    def clear_machine_status(self, cluster_id: str) -> StorageResult:
        """The symmetric "machine is back" action -- removes the down
        status. Safe on a cluster that was never marked down (a no-op
        success, not an error), same discipline as clear_plot_harvest."""
        ...

    def put_advance_notice_record(self, record: AdvanceNoticeRecord) -> StorageResult:
        """Write-once in practice -- the caller checks
        get_advance_notice_record first and only calls this when no
        record exists yet. Overwrite semantics like every other put_*
        here (except put_ledger_entry) purely for retry safety, not
        because a second real send is ever intended. See ADR-011 Part 4,
        Decision 17."""
        ...

    def get_advance_notice_record(
        self, plot_id: str, season_id: str
    ) -> AdvanceNoticeRecord | None:
        """None means this plot has never been sent its advance notice
        this season -- the only thing _check_advance_harvest_notices
        needs to decide whether to send."""
        ...

    def put_operator_enrollment_code(self, record: OperatorEnrollmentCode) -> StorageResult:
        """Overwrite semantics, keyed by `code` alone -- both the
        generating write (generate_operator_code.py) and the
        used_at/used_by_chat_id update on successful enrollment go
        through this same method. See ADR-012 Part 2."""
        ...

    def get_operator_enrollment_code(self, code: str) -> OperatorEnrollmentCode | None:
        """None if this code was never generated -- an invalid code, not
        distinguished in the farmer-facing message from a reused or
        expired one (see operator_enrollment.py), though the caller can
        tell them apart from the fields on what this returns."""
        ...

    def put_operator_audit_event(self, event: OperatorAuditEvent) -> StorageResult:
        """Append-only -- there is no update or delete for this entity.
        A retried write after a successful one would just record the
        same enrollment twice; operator_enrollment.py's flow only ever
        calls this once per successful enrollment/replacement, gated by
        the code's own used_at check."""
        ...

    def get_operator_audit_events_for_cluster(self, cluster_id: str) -> list[OperatorAuditEvent]:
        """Every operator enrollment/replacement ever recorded for this
        cluster, in whatever order the backend returns them --
        explain_decision.py sorts by occurred_at itself when it needs
        "who was operator as of this date"."""
        ...
