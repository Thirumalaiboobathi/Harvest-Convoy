"""Zero-setup local storage: a JSON file on disk. Default backend -- see
ADR-005 Decision 1 (Docker was installed but not running on the machine
this was built on; requiring it for "clone and run" was a worse bar than
the brief's "no AWS account needed").

Single-process only. Does NOT provide real concurrency safety -- the
idempotency check in put_ledger_entry is correct for sequential calls
within one process but is not atomic across concurrent processes. That
guarantee is DynamoStorage's job (conditional writes). See ADR-005
Decision 5.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.interface import (
    AdvanceNoticeRecord,
    BreakdownDisplacement,
    DecisionRecord,
    HarvestConfirmation,
    LedgerEntry,
    MachineStatus,
    OperatorAuditEvent,
    OperatorEnrollmentCode,
    RouteOverride,
    SeasonRolloverPrompt,
    StorageResult,
)

logger = logging.getLogger(__name__)

DEFAULT_FILE_PATH = Path(".data/harvest_convoy.json")

_EMPTY: dict = {
    "clusters": {}, "farmers": {}, "plots": {}, "ledger": {}, "watcher": {},
    "harvest": {},  # harvest[cluster_id][season_id][plot_id] = dispatched_at
    "confirmations": {},  # confirmations[plot_id][season_id] = HarvestConfirmation dict
    "decisions": {},  # decisions[plot_id][season_id][decision_date] = DecisionRecord dict
    "rollover_prompts": {},  # rollover_prompts[plot_id][new_season_id] = SeasonRolloverPrompt dict
    "breakdowns": {},  # breakdowns[cluster_id][season_id][plot_id+"#"+date] = BreakdownDisplacement dict
    "machine_status": {},  # machine_status[cluster_id] = MachineStatus dict
    "advance_notices": {},  # advance_notices[plot_id][season_id] = AdvanceNoticeRecord dict
    "operator_codes": {},  # operator_codes[code] = OperatorEnrollmentCode dict
    "operator_audit": {},  # operator_audit[cluster_id] = [OperatorAuditEvent dict, ...] (append-only)
    "route_overrides": {},  # route_overrides[cluster_id][season_id][decision_date] = RouteOverride dict
}


class FileStorage:
    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_FILE_PATH
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                for key, default in _EMPTY.items():
                    data.setdefault(key, dict(default))
                return data
            except Exception as exc:  # noqa: BLE001 -- degrade, never throw
                logger.error("failed to load storage file %s: %s", self.path, exc)
        return {k: dict(v) for k, v in _EMPTY.items()}

    def _save(self) -> StorageResult:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2, default=str))
            return StorageResult(success=True)
        except Exception as exc:  # noqa: BLE001 -- degrade, never throw
            logger.error("failed to save storage file %s: %s", self.path, exc)
            return StorageResult(success=False, error=str(exc))

    # Cluster
    def get_cluster(self, cluster_id: str) -> Cluster | None:
        raw = self._data["clusters"].get(cluster_id)
        return Cluster(**raw) if raw else None

    def list_cluster_ids(self) -> list[str]:
        return sorted(self._data["clusters"].keys())

    def put_cluster(self, cluster: Cluster) -> StorageResult:
        self._data["clusters"][cluster.cluster_id] = asdict(cluster)
        return self._save()

    # Farmer
    def get_farmer(self, farmer_id: str) -> Farmer | None:
        raw = self._data["farmers"].get(farmer_id)
        return Farmer(**raw) if raw else None

    def put_farmer(self, farmer: Farmer) -> StorageResult:
        self._data["farmers"][farmer.farmer_id] = asdict(farmer)
        return self._save()

    def get_farmers_for_cluster(self, cluster_id: str) -> list[Farmer]:
        return [
            Farmer(**f)
            for f in self._data["farmers"].values()
            if f["cluster_id"] == cluster_id
        ]

    # Plot
    def get_plot(self, plot_id: str) -> Plot | None:
        raw = self._data["plots"].get(plot_id)
        if raw is None:
            return None
        raw = dict(raw)
        raw["transplant_date"] = date.fromisoformat(raw["transplant_date"])
        return Plot(**raw)

    def put_plot(self, plot: Plot) -> StorageResult:
        d = asdict(plot)
        d["transplant_date"] = plot.transplant_date.isoformat()
        self._data["plots"][plot.plot_id] = d
        return self._save()

    def get_plots_for_cluster(self, cluster_id: str) -> list[Plot]:
        result = []
        for raw in self._data["plots"].values():
            if raw["cluster_id"] == cluster_id:
                r = dict(raw)
                r["transplant_date"] = date.fromisoformat(r["transplant_date"])
                result.append(Plot(**r))
        return result

    # Fairness ledger
    def get_ledger_entries(self, farmer_id: str) -> list[LedgerEntry]:
        entries = self._data["ledger"].get(farmer_id, {})
        return [LedgerEntry(**e) for e in entries.values()]

    def put_ledger_entry(self, entry: LedgerEntry) -> StorageResult:
        farmer_entries = self._data["ledger"].setdefault(entry.farmer_id, {})
        if entry.season_id in farmer_entries:
            return StorageResult(
                success=False,
                error="ledger entry already exists for this farmer/season",
            )
        farmer_entries[entry.season_id] = asdict(entry)
        return self._save()

    # Watcher idempotency marker
    def get_watcher_last_run(self, cluster_id: str) -> str | None:
        return self._data["watcher"].get(cluster_id)

    def set_watcher_last_run(self, cluster_id: str, run_date: str) -> StorageResult:
        self._data["watcher"][cluster_id] = run_date
        return self._save()

    # Plot harvest lifecycle -- ADR-009 Part 1.5
    def mark_plot_harvested(
        self, plot_id: str, cluster_id: str, season_id: str, dispatched_at: str
    ) -> StorageResult:
        cluster_seasons = self._data["harvest"].setdefault(cluster_id, {})
        season_plots = cluster_seasons.setdefault(season_id, {})
        season_plots[plot_id] = dispatched_at
        return self._save()

    def clear_plot_harvest(
        self, plot_id: str, cluster_id: str, season_id: str
    ) -> StorageResult:
        season_plots = self._data["harvest"].get(cluster_id, {}).get(season_id, {})
        season_plots.pop(plot_id, None)
        return self._save()

    def get_harvested_plot_ids(self, cluster_id: str, season_id: str) -> set[str]:
        return set(self._data["harvest"].get(cluster_id, {}).get(season_id, {}).keys())

    # Harvest confirmation loop -- ADR-009 Part 2
    def get_harvest_confirmation(
        self, plot_id: str, season_id: str
    ) -> HarvestConfirmation | None:
        raw = self._data["confirmations"].get(plot_id, {}).get(season_id)
        return HarvestConfirmation(**raw) if raw else None

    def put_harvest_confirmation(
        self, confirmation: HarvestConfirmation
    ) -> StorageResult:
        plot_confirmations = self._data["confirmations"].setdefault(confirmation.plot_id, {})
        plot_confirmations[confirmation.season_id] = asdict(confirmation)
        return self._save()

    def get_confirmations_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[HarvestConfirmation]:
        result = []
        for by_season in self._data["confirmations"].values():
            raw = by_season.get(season_id)
            if raw and raw["cluster_id"] == cluster_id:
                result.append(HarvestConfirmation(**raw))
        return result

    # Decision records -- ADR-010 Part 0.5
    def put_decision_record(self, record: DecisionRecord) -> StorageResult:
        by_season = self._data["decisions"].setdefault(record.plot_id, {})
        by_date = by_season.setdefault(record.season_id, {})
        by_date[record.decision_date] = asdict(record)
        return self._save()

    def get_decision_record(
        self, plot_id: str, season_id: str, decision_date: str
    ) -> DecisionRecord | None:
        raw = self._data["decisions"].get(plot_id, {}).get(season_id, {}).get(decision_date)
        return DecisionRecord(**raw) if raw else None

    def get_decision_records_for_plot(
        self, plot_id: str, season_id: str | None = None
    ) -> list[DecisionRecord]:
        by_season = self._data["decisions"].get(plot_id, {})
        seasons = [season_id] if season_id is not None else list(by_season.keys())
        result = []
        for sid in seasons:
            for raw in by_season.get(sid, {}).values():
                result.append(DecisionRecord(**raw))
        return result

    def get_decision_records_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[DecisionRecord]:
        result = []
        for by_season in self._data["decisions"].values():
            for raw in by_season.get(season_id, {}).values():
                if raw["cluster_id"] == cluster_id:
                    result.append(DecisionRecord(**raw))
        return result

    # Season rollover -- ADR-011 Part 1
    def put_season_rollover_prompt(self, prompt: SeasonRolloverPrompt) -> StorageResult:
        by_season = self._data["rollover_prompts"].setdefault(prompt.plot_id, {})
        by_season[prompt.new_season_id] = asdict(prompt)
        return self._save()

    def get_season_rollover_prompt(
        self, plot_id: str, season_id: str
    ) -> SeasonRolloverPrompt | None:
        raw = self._data["rollover_prompts"].get(plot_id, {}).get(season_id)
        return SeasonRolloverPrompt(**raw) if raw else None

    def get_season_rollover_prompts_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[SeasonRolloverPrompt]:
        result = []
        for by_season in self._data["rollover_prompts"].values():
            raw = by_season.get(season_id)
            if raw and raw["cluster_id"] == cluster_id:
                result.append(SeasonRolloverPrompt(**raw))
        return result

    # Machine breakdown -- ADR-011 Part 2
    def put_breakdown_displacement(self, displacement: BreakdownDisplacement) -> StorageResult:
        by_season = self._data["breakdowns"].setdefault(displacement.cluster_id, {})
        by_key = by_season.setdefault(displacement.season_id, {})
        key = f"{displacement.plot_id}#{displacement.original_scheduled_date}"
        by_key[key] = asdict(displacement)
        return self._save()

    def get_breakdown_displacements_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[BreakdownDisplacement]:
        raw = self._data["breakdowns"].get(cluster_id, {}).get(season_id, {})
        return [BreakdownDisplacement(**v) for v in raw.values()]

    def get_breakdown_displacements_for_date(
        self, cluster_id: str, season_id: str, report_date: str
    ) -> list[BreakdownDisplacement]:
        raw = self._data["breakdowns"].get(cluster_id, {}).get(season_id, {})
        return [
            BreakdownDisplacement(**v) for v in raw.values()
            if v["original_scheduled_date"] == report_date
        ]

    def put_machine_status(self, status: MachineStatus) -> StorageResult:
        self._data["machine_status"][status.cluster_id] = asdict(status)
        return self._save()

    def get_machine_status(self, cluster_id: str) -> MachineStatus | None:
        raw = self._data["machine_status"].get(cluster_id)
        return MachineStatus(**raw) if raw else None

    def clear_machine_status(self, cluster_id: str) -> StorageResult:
        self._data["machine_status"].pop(cluster_id, None)
        return self._save()

    def put_advance_notice_record(self, record: AdvanceNoticeRecord) -> StorageResult:
        by_season = self._data["advance_notices"].setdefault(record.plot_id, {})
        by_season[record.season_id] = asdict(record)
        return self._save()

    def get_advance_notice_record(
        self, plot_id: str, season_id: str
    ) -> AdvanceNoticeRecord | None:
        raw = self._data["advance_notices"].get(plot_id, {}).get(season_id)
        return AdvanceNoticeRecord(**raw) if raw else None

    def put_operator_enrollment_code(self, record: OperatorEnrollmentCode) -> StorageResult:
        self._data["operator_codes"][record.code] = asdict(record)
        return self._save()

    def get_operator_enrollment_code(self, code: str) -> OperatorEnrollmentCode | None:
        raw = self._data["operator_codes"].get(code)
        return OperatorEnrollmentCode(**raw) if raw else None

    def put_operator_audit_event(self, event: OperatorAuditEvent) -> StorageResult:
        events = self._data["operator_audit"].setdefault(event.cluster_id, [])
        events.append(asdict(event))
        return self._save()

    def get_operator_audit_events_for_cluster(self, cluster_id: str) -> list[OperatorAuditEvent]:
        raw = self._data["operator_audit"].get(cluster_id, [])
        return [OperatorAuditEvent(**v) for v in raw]

    # Route proposal and operator override -- ADR-013
    def put_route_override(self, override: RouteOverride) -> StorageResult:
        by_season = self._data["route_overrides"].setdefault(override.cluster_id, {})
        by_date = by_season.setdefault(override.season_id, {})
        by_date[override.decision_date] = asdict(override)
        return self._save()

    def get_route_override(
        self, cluster_id: str, season_id: str, decision_date: str
    ) -> RouteOverride | None:
        raw = (
            self._data["route_overrides"]
            .get(cluster_id, {})
            .get(season_id, {})
            .get(decision_date)
        )
        return RouteOverride(**raw) if raw else None

    def get_route_overrides_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[RouteOverride]:
        by_date = self._data["route_overrides"].get(cluster_id, {}).get(season_id, {})
        return [RouteOverride(**v) for v in by_date.values()]
