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
from harvest_convoy.storage.interface import LedgerEntry, StorageResult

logger = logging.getLogger(__name__)

DEFAULT_TABLE_NAME = "harvest_convoy"

# Short, explicit timeouts rather than boto3's defaults: a hung DynamoDB
# call must fail fast into the degrade-and-log path, not block whatever
# triggered it (a scheduling run, a webhook response) for a minute-plus.
# See ADR-005 Decision 5, DynamoDB unavailable.
_CLIENT_CONFIG = Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 1})


def _to_decimal(value):
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _from_decimal(value):
    if isinstance(value, Decimal):
        return float(value)
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

    # --- internals ---

    def _put(self, item: dict) -> StorageResult:
        try:
            self._table.put_item(Item=item)
            return StorageResult(success=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("put_item failed: %s", exc)
            return StorageResult(success=False, error=str(exc))

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
