"""Provenance header every report (text and JSON alike) starts with --
which storage backend was read, when the report was generated, and the
exact code (git commit) that produced it. A report someone finds in six
months must be traceable to the code that produced it. See ADR-010.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from harvest_convoy.storage import Storage

_REPO_ROOT = Path(__file__).resolve().parents[3]


def get_git_commit() -> str:
    """`git rev-parse HEAD` from the repo root, or a clear "unknown"
    string if this isn't a git checkout, git isn't installed, or the
    command fails for any other reason -- a report must still be a
    complete, valid report without one, never crash on this."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=5, check=True,
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001 -- a report must still generate without git
        return "unknown (not a git checkout, or git unavailable)"


@dataclass(frozen=True)
class Provenance:
    storage_backend: str  # type(storage).__name__
    generated_at: str  # ISO 8601 UTC
    git_commit: str
    cluster_ids: list[str]
    season_ids: list[str]


def build_provenance(
    storage: Storage, *, cluster_ids: list[str], season_ids: list[str]
) -> Provenance:
    return Provenance(
        storage_backend=type(storage).__name__,
        generated_at=datetime.now(timezone.utc).isoformat(),
        git_commit=get_git_commit(),
        cluster_ids=cluster_ids,
        season_ids=season_ids,
    )


def render_provenance_text(p: Provenance) -> str:
    lines = [
        "=" * 72,
        "PROVENANCE",
        f"  Storage backend : {p.storage_backend}",
        f"  Generated at    : {p.generated_at}",
        f"  Code version    : {p.git_commit}",
        f"  Cluster(s)      : {', '.join(p.cluster_ids) if p.cluster_ids else '(none)'}",
        f"  Season(s)       : {', '.join(p.season_ids) if p.season_ids else '(none)'}",
        "=" * 72,
    ]
    return "\n".join(lines)
