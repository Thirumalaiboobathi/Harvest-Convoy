"""Storage backend selection. See ADR-005 Decision 1: FileStorage is the
default so a clone-and-run needs no AWS account; set
HARVEST_CONVOY_STORAGE=dynamo to use DynamoStorage instead (real AWS, or
DynamoDB Local via DYNAMODB_ENDPOINT_URL).
"""

from __future__ import annotations

import os

from harvest_convoy.storage.interface import LedgerEntry, Storage, StorageResult

__all__ = ["LedgerEntry", "Storage", "StorageResult", "get_storage"]


def get_storage() -> Storage:
    backend = os.environ.get("HARVEST_CONVOY_STORAGE", "file").strip().lower()
    if backend == "dynamo":
        from harvest_convoy.storage.dynamo import DynamoStorage

        return DynamoStorage()
    from harvest_convoy.storage.file_storage import FileStorage

    return FileStorage()
