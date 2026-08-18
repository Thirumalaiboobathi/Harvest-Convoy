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
from harvest_convoy.storage.interface import HarvestConfirmation, LedgerEntry, StorageResult

logger = logging.getLogger(__name__)

DEFAULT_FILE_PATH = Path(".data/harvest_convoy.json")

_EMPTY: dict = {
    "clusters": {}, "farmers": {}, "plots": {}, "ledger": {}, "watcher": {},
    "harvest": {},  # harvest[cluster_id][season_id][plot_id] = dispatched_at
    "confirmations": {},  # confirmations[plot_id][season_id] = HarvestConfirmation dict
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
