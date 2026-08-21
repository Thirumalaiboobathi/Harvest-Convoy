"""Real DynamoDB-backed storage. Works against real AWS (default) or
DynamoDB Local (pass endpoint_url, or set DYNAMODB_ENDPOINT_URL) -- same
code path either way, just configuration. See ADR-005 Decision 1/2 for
the backend choice and the single-table PK/SK scheme this implements.

Never throws into the agent loop: every method catches botocore/boto3
errors, logs, and degrades (None/[] for reads, StorageResult for writes).
See ADR-005 Decision 5.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from datetime import date
from decimal import Decimal

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from harvest_convoy.models import Cluster, Farmer, Plot
from harvest_convoy.storage.interface import (
    AdvanceNoticeRecord,
    BreakdownDisplacement,
    DecisionRecord,
    HarvestConfirmation,
    LedgerEntry,
    MachineStatus,
    SeasonRolloverPrompt,
    StorageResult,
)

logger = logging.getLogger(__name__)

DEFAULT_TABLE_NAME = "harvest_convoy"

# Short, explicit timeouts rather than boto3's defaults: a hung DynamoDB
# call must fail fast into the degrade-and-log path, not block whatever
# triggered it (a scheduling run, a webhook response) for a minute-plus.
# See ADR-005 Decision 5, DynamoDB unavailable.
_CLIENT_CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1})


def _to_decimal(value):
    # Recurses into dicts/lists, not just top-level values -- needed since
    # ADR-010's DecisionRecord carries nested dicts (own_claim/
    # opponent_claim, an AdvocateClaim.model_dump()) containing floats
    # DynamoDB still requires as Decimal even inside a Map attribute.
    # Every entity before DecisionRecord was flat, so this recursion is a
    # pure extension, not a behavior change for anything else.
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


def _from_decimal(value):
    # DynamoDB's Number type doesn't distinguish int from float -- every
    # numeric field round-trips through here regardless of which one the
    # dataclass declares. Unconditionally casting to float (the previous
    # behavior) silently turned every int field, e.g. Farmer.telegram_chat_id,
    # into e.g. 1276258406.0 -- caught live wiring up a real chat_id for
    # deployment verification (ADR-008 follow-up): a JSON float in that
    # position is not a valid Telegram chat_id. An integral Decimal now
    # comes back as int; a genuinely fractional one (area_acres, GDD
    # values, etc.) still comes back as float. Recurses into dicts/lists
    # for the same reason _to_decimal now does.
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if as_int == value else float(value)
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    return value


def _encode(d: dict) -> dict:
    return {k: _to_decimal(v) for k, v in d.items()}


def _decode(d: dict) -> dict:
    return {k: _from_decimal(v) for k, v in d.items()}


class DynamoStorage:
    def __init__(
        self,
        table_name: str | None = None,
        region_name: str | None = None,
        endpoint_url: str | None = None,
    ):
        self.table_name = table_name or os.environ.get(
            "DYNAMODB_TABLE_NAME", DEFAULT_TABLE_NAME
        )
        endpoint_url = endpoint_url or os.environ.get("DYNAMODB_ENDPOINT_URL")
        resource_kwargs = {"config": _CLIENT_CONFIG}
        if region_name:
            resource_kwargs["region_name"] = region_name
        if endpoint_url:
            resource_kwargs["endpoint_url"] = endpoint_url
        self._resource = boto3.resource("dynamodb", **resource_kwargs)
        self._table = self._resource.Table(self.table_name)

    # --- Cluster ---

    def get_cluster(self, cluster_id: str) -> Cluster | None:
        try:
            resp = self._table.get_item(
                Key={"PK": f"CLUSTER#{cluster_id}", "SK": "METADATA"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("get_cluster(%s) failed: %s", cluster_id, exc)
            return None
        item = resp.get("Item")
        if item is None:
            return None
        return Cluster(**_decode(_strip_keys(item)))

    def put_cluster(self, cluster: Cluster) -> StorageResult:
        item = {
            "PK": f"CLUSTER#{cluster.cluster_id}",
            "SK": "METADATA",
            **_encode(asdict(cluster)),
        }
        return self._put(item)

    def list_cluster_ids(self) -> list[str]:
        """The one Scan in this file -- every other method here is a
        targeted GetItem/Query. Justified because there is no other way
        to enumerate "every cluster" without a dedicated index for it, and
        this is a low-frequency, on-demand read (ADR-010 Part 3's
        machinery_gap.py), not a per-trigger call."""
        try:
            resp = self._table.scan(
                FilterExpression="SK = :sk AND begins_with(PK, :pk_prefix)",
                ExpressionAttributeValues={":sk": "METADATA", ":pk_prefix": "CLUSTER#"},
                ProjectionExpression="cluster_id",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("list_cluster_ids() failed: %s", exc)
            return []
        return sorted(i["cluster_id"] for i in resp.get("Items", []))

    # --- Farmer ---

    def get_farmer(self, farmer_id: str) -> Farmer | None:
        try:
            resp = self._table.get_item(
                Key={"PK": f"FARMER#{farmer_id}", "SK": "METADATA"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("get_farmer(%s) failed: %s", farmer_id, exc)
            return None
        item = resp.get("Item")
        if item is None:
            return None
        return Farmer(**_decode(_strip_keys(item, extra=("GSI1PK", "GSI1SK"))))

    def put_farmer(self, farmer: Farmer) -> StorageResult:
        item = {
            "PK": f"FARMER#{farmer.farmer_id}",
            "SK": "METADATA",
            "GSI1PK": f"CLUSTER#{farmer.cluster_id}",
            "GSI1SK": f"FARMER#{farmer.farmer_id}",
            **_encode(asdict(farmer)),
        }
        return self._put(item)

    def get_farmers_for_cluster(self, cluster_id: str) -> list[Farmer]:
        items = self._query_gsi1(cluster_id, "FARMER#")
        return [Farmer(**_decode(_strip_keys(i, extra=("GSI1PK", "GSI1SK")))) for i in items]

    # --- Plot ---

    def get_plot(self, plot_id: str) -> Plot | None:
        try:
            resp = self._table.get_item(Key={"PK": f"PLOT#{plot_id}", "SK": "METADATA"})
        except Exception as exc:  # noqa: BLE001
            logger.error("get_plot(%s) failed: %s", plot_id, exc)
            return None
        item = resp.get("Item")
        if item is None:
            return None
        decoded = _decode(_strip_keys(item, extra=("GSI1PK", "GSI1SK")))
        decoded["transplant_date"] = date.fromisoformat(decoded["transplant_date"])
        return Plot(**decoded)

    def put_plot(self, plot: Plot) -> StorageResult:
        d = asdict(plot)
        d["transplant_date"] = plot.transplant_date.isoformat()
        item = {
            "PK": f"PLOT#{plot.plot_id}",
            "SK": "METADATA",
            "GSI1PK": f"CLUSTER#{plot.cluster_id}",
            "GSI1SK": f"PLOT#{plot.plot_id}",
            **_encode(d),
        }
        return self._put(item)

    def get_plots_for_cluster(self, cluster_id: str) -> list[Plot]:
        items = self._query_gsi1(cluster_id, "PLOT#")
        result = []
        for i in items:
            decoded = _decode(_strip_keys(i, extra=("GSI1PK", "GSI1SK")))
            decoded["transplant_date"] = date.fromisoformat(decoded["transplant_date"])
            result.append(Plot(**decoded))
        return result

    # --- Fairness ledger ---

    def get_ledger_entries(self, farmer_id: str) -> list[LedgerEntry]:
        try:
            resp = self._table.query(
                KeyConditionExpression=(
                    "PK = :pk AND begins_with(SK, :sk_prefix)"
                ),
                ExpressionAttributeValues={
                    ":pk": f"FARMER#{farmer_id}",
                    ":sk_prefix": "LEDGER#",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("get_ledger_entries(%s) failed: %s", farmer_id, exc)
            return []
        return [LedgerEntry(**_decode(_strip_keys(i))) for i in resp.get("Items", [])]

    def put_ledger_entry(self, entry: LedgerEntry) -> StorageResult:
        item = {
            "PK": f"FARMER#{entry.farmer_id}",
            "SK": f"LEDGER#{entry.season_id}",
            **_encode(asdict(entry)),
        }
        try:
            self._table.put_item(
                Item=item, ConditionExpression="attribute_not_exists(PK)"
            )
            return StorageResult(success=True)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return StorageResult(
                    success=False,
                    error="ledger entry already exists for this farmer/season",
                )
            logger.error("put_ledger_entry failed: %s", exc)
            return StorageResult(success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.error("put_ledger_entry failed: %s", exc)
            return StorageResult(success=False, error=str(exc))

    # --- Watcher idempotency marker ---

    def get_watcher_last_run(self, cluster_id: str) -> str | None:
        try:
            resp = self._table.get_item(
                Key={"PK": f"CLUSTER#{cluster_id}", "SK": "WATCHER#RUN"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("get_watcher_last_run(%s) failed: %s", cluster_id, exc)
            return None
        item = resp.get("Item")
        return item.get("last_run_date") if item else None

    def set_watcher_last_run(self, cluster_id: str, run_date: str) -> StorageResult:
        item = {
            "PK": f"CLUSTER#{cluster_id}",
            "SK": "WATCHER#RUN",
            "last_run_date": run_date,
        }
        return self._put(item)

    # --- Plot harvest lifecycle -- ADR-009 Part 1.5 ---

    def mark_plot_harvested(
        self, plot_id: str, cluster_id: str, season_id: str, dispatched_at: str
    ) -> StorageResult:
        item = {
            "PK": f"PLOT#{plot_id}",
            "SK": f"HARVEST#{season_id}",
            "GSI1PK": f"CLUSTER#{cluster_id}",
            "GSI1SK": f"HARVEST#{season_id}#{plot_id}",
            "plot_id": plot_id,
            "cluster_id": cluster_id,
            "season_id": season_id,
            "dispatched_at": dispatched_at,
        }
        return self._put(item)

    def clear_plot_harvest(
        self, plot_id: str, cluster_id: str, season_id: str
    ) -> StorageResult:
        try:
            self._table.delete_item(
                Key={"PK": f"PLOT#{plot_id}", "SK": f"HARVEST#{season_id}"}
            )
            return StorageResult(success=True)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "clear_plot_harvest(%s, %s, %s) failed: %s",
                plot_id, cluster_id, season_id, exc,
            )
            return StorageResult(success=False, error=str(exc))

    def get_harvested_plot_ids(self, cluster_id: str, season_id: str) -> set[str]:
        items = self._query_gsi1(cluster_id, f"HARVEST#{season_id}#")
        return {i["plot_id"] for i in items}

    # --- Harvest confirmation loop -- ADR-009 Part 2 ---

    def get_harvest_confirmation(
        self, plot_id: str, season_id: str
    ) -> HarvestConfirmation | None:
        try:
            resp = self._table.get_item(
                Key={"PK": f"PLOT#{plot_id}", "SK": f"CONFIRM#{season_id}"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "get_harvest_confirmation(%s, %s) failed: %s", plot_id, season_id, exc
            )
            return None
        item = resp.get("Item")
        if item is None:
            return None
        return HarvestConfirmation(**_decode(_strip_keys(item, extra=("GSI1PK", "GSI1SK"))))

    def put_harvest_confirmation(
        self, confirmation: HarvestConfirmation
    ) -> StorageResult:
        item = {
            "PK": f"PLOT#{confirmation.plot_id}",
            "SK": f"CONFIRM#{confirmation.season_id}",
            "GSI1PK": f"CLUSTER#{confirmation.cluster_id}",
            "GSI1SK": f"CONFIRM#{confirmation.season_id}#{confirmation.plot_id}",
            **_encode(asdict(confirmation)),
        }
        return self._put(item)

    def get_confirmations_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[HarvestConfirmation]:
        items = self._query_gsi1(cluster_id, f"CONFIRM#{season_id}#")
        return [
            HarvestConfirmation(**_decode(_strip_keys(i, extra=("GSI1PK", "GSI1SK"))))
            for i in items
        ]

    # --- Decision records -- ADR-010 Part 0.5 ---

    def put_decision_record(self, record: DecisionRecord) -> StorageResult:
        item = {
            "PK": f"PLOT#{record.plot_id}",
            "SK": f"DECISION#{record.season_id}#{record.decision_date}",
            "GSI1PK": f"CLUSTER#{record.cluster_id}",
            "GSI1SK": f"DECISION#{record.season_id}#{record.decision_date}#{record.plot_id}",
            **_encode(asdict(record)),
        }
        return self._put(item)

    def get_decision_record(
        self, plot_id: str, season_id: str, decision_date: str
    ) -> DecisionRecord | None:
        try:
            resp = self._table.get_item(
                Key={"PK": f"PLOT#{plot_id}", "SK": f"DECISION#{season_id}#{decision_date}"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "get_decision_record(%s, %s, %s) failed: %s",
                plot_id, season_id, decision_date, exc,
            )
            return None
        item = resp.get("Item")
        if item is None:
            return None
        return DecisionRecord(**_decode(_strip_keys(item, extra=("GSI1PK", "GSI1SK"))))

    def get_decision_records_for_plot(
        self, plot_id: str, season_id: str | None = None
    ) -> list[DecisionRecord]:
        sk_prefix = f"DECISION#{season_id}#" if season_id is not None else "DECISION#"
        items = self._query_pk_prefix(f"PLOT#{plot_id}", sk_prefix)
        return [
            DecisionRecord(**_decode(_strip_keys(i, extra=("GSI1PK", "GSI1SK"))))
            for i in items
        ]

    def get_decision_records_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[DecisionRecord]:
        items = self._query_gsi1(cluster_id, f"DECISION#{season_id}#")
        return [
            DecisionRecord(**_decode(_strip_keys(i, extra=("GSI1PK", "GSI1SK"))))
            for i in items
        ]

    # --- Season rollover -- ADR-011 Part 1 ---

    def put_season_rollover_prompt(self, prompt: SeasonRolloverPrompt) -> StorageResult:
        item = {
            "PK": f"PLOT#{prompt.plot_id}",
            "SK": f"ROLLOVER#{prompt.new_season_id}",
            "GSI1PK": f"CLUSTER#{prompt.cluster_id}",
            "GSI1SK": f"ROLLOVER#{prompt.new_season_id}#{prompt.plot_id}",
            **_encode(asdict(prompt)),
        }
        return self._put(item)

    def get_season_rollover_prompt(
        self, plot_id: str, season_id: str
    ) -> SeasonRolloverPrompt | None:
        try:
            resp = self._table.get_item(
                Key={"PK": f"PLOT#{plot_id}", "SK": f"ROLLOVER#{season_id}"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "get_season_rollover_prompt(%s, %s) failed: %s", plot_id, season_id, exc
            )
            return None
        item = resp.get("Item")
        if item is None:
            return None
        return SeasonRolloverPrompt(**_decode(_strip_keys(item, extra=("GSI1PK", "GSI1SK"))))

    def get_season_rollover_prompts_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[SeasonRolloverPrompt]:
        items = self._query_gsi1(cluster_id, f"ROLLOVER#{season_id}#")
        return [
            SeasonRolloverPrompt(**_decode(_strip_keys(i, extra=("GSI1PK", "GSI1SK"))))
            for i in items
        ]

    # --- Machine breakdown -- ADR-011 Part 2 ---

    def put_breakdown_displacement(self, displacement: BreakdownDisplacement) -> StorageResult:
        item = {
            "PK": f"PLOT#{displacement.plot_id}",
            "SK": f"BREAKDOWN#{displacement.season_id}#{displacement.original_scheduled_date}",
            "GSI1PK": f"CLUSTER#{displacement.cluster_id}",
            "GSI1SK": (
                f"BREAKDOWN#{displacement.season_id}#"
                f"{displacement.original_scheduled_date}#{displacement.plot_id}"
            ),
            **_encode(asdict(displacement)),
        }
        return self._put(item)

    def get_breakdown_displacements_for_cluster(
        self, cluster_id: str, season_id: str
    ) -> list[BreakdownDisplacement]:
        items = self._query_gsi1(cluster_id, f"BREAKDOWN#{season_id}#")
        return [
            BreakdownDisplacement(**_decode(_strip_keys(i, extra=("GSI1PK", "GSI1SK"))))
            for i in items
        ]

    def get_breakdown_displacements_for_date(
        self, cluster_id: str, season_id: str, report_date: str
    ) -> list[BreakdownDisplacement]:
        items = self._query_gsi1(cluster_id, f"BREAKDOWN#{season_id}#{report_date}#")
        return [
            BreakdownDisplacement(**_decode(_strip_keys(i, extra=("GSI1PK", "GSI1SK"))))
            for i in items
        ]

    def put_machine_status(self, status: MachineStatus) -> StorageResult:
        item = {
            "PK": f"CLUSTER#{status.cluster_id}",
            "SK": "MACHINE#STATUS",
            **_encode(asdict(status)),
        }
        return self._put(item)

    def get_machine_status(self, cluster_id: str) -> MachineStatus | None:
        try:
            resp = self._table.get_item(
                Key={"PK": f"CLUSTER#{cluster_id}", "SK": "MACHINE#STATUS"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("get_machine_status(%s) failed: %s", cluster_id, exc)
            return None
        item = resp.get("Item")
        if item is None:
            return None
        return MachineStatus(**_decode(_strip_keys(item)))

    def clear_machine_status(self, cluster_id: str) -> StorageResult:
        try:
            self._table.delete_item(
                Key={"PK": f"CLUSTER#{cluster_id}", "SK": "MACHINE#STATUS"}
            )
            return StorageResult(success=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("clear_machine_status(%s) failed: %s", cluster_id, exc)
            return StorageResult(success=False, error=str(exc))

    # --- Advance harvest notice -- ADR-011 Part 4 ---

    def put_advance_notice_record(self, record: AdvanceNoticeRecord) -> StorageResult:
        item = {
            "PK": f"PLOT#{record.plot_id}",
            "SK": f"NOTICE#{record.season_id}",
            **_encode(asdict(record)),
        }
        return self._put(item)

    def get_advance_notice_record(
        self, plot_id: str, season_id: str
    ) -> AdvanceNoticeRecord | None:
        try:
            resp = self._table.get_item(
                Key={"PK": f"PLOT#{plot_id}", "SK": f"NOTICE#{season_id}"}
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "get_advance_notice_record(%s, %s) failed: %s", plot_id, season_id, exc
            )
            return None
        item = resp.get("Item")
        if item is None:
            return None
        return AdvanceNoticeRecord(**_decode(_strip_keys(item)))

    # --- internals ---

    def _put(self, item: dict) -> StorageResult:
        try:
            self._table.put_item(Item=item)
            return StorageResult(success=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("put_item failed: %s", exc)
            return StorageResult(success=False, error=str(exc))

    def _query_pk_prefix(self, pk: str, sk_prefix: str) -> list[dict]:
        """Base-table query (no GSI) -- every DecisionRecord for one
        plot, e.g. explain_decision.py's season-less lookup, is a lookup
        by the plot's own PK, not by cluster, so this doesn't reuse
        _query_gsi1."""
        try:
            resp = self._table.query(
                KeyConditionExpression=(
                    "PK = :pk AND begins_with(SK, :sk_prefix)"
                ),
                ExpressionAttributeValues={":pk": pk, ":sk_prefix": sk_prefix},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "base-table query failed for pk %s prefix %s: %s", pk, sk_prefix, exc
            )
            return []
        return resp.get("Items", [])

    def _query_gsi1(self, cluster_id: str, sk_prefix: str) -> list[dict]:
        try:
            resp = self._table.query(
                IndexName="GSI1",
                KeyConditionExpression=(
                    "GSI1PK = :pk AND begins_with(GSI1SK, :sk_prefix)"
                ),
                ExpressionAttributeValues={
                    ":pk": f"CLUSTER#{cluster_id}",
                    ":sk_prefix": sk_prefix,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "GSI1 query failed for cluster %s prefix %s: %s",
                cluster_id, sk_prefix, exc,
            )
            return []
        return resp.get("Items", [])


def _strip_keys(item: dict, extra: tuple[str, ...] = ()) -> dict:
    return {k: v for k, v in item.items() if k not in ("PK", "SK", *extra)}
