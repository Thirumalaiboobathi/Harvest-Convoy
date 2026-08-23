"""Fairness ledger: per-farmer bump history, and the decayed accumulation
weighting agents/coordinator.py uses to argue harder for repeatedly-bumped
farmers. See ADR-005 Decision 4 for the full reasoning, including the
invariant that this must never be able to override genuine agronomic
urgency -- that invariant is enforced in coordinator.py (where the score
comparison happens), not here; this module only produces the number.
"""

from __future__ import annotations

from datetime import datetime, timezone

from harvest_convoy.storage.interface import LedgerEntry, Storage, StorageResult

# DERIVED, tuning constant, not sourced: each season further back, a
# bump's contribution to the weighted total is multiplied by this factor.
# Geometric decay is asymptotic -- old bumps fade, never fully vanish.
FAIRNESS_SEASON_DECAY = 0.5


def get_ledger_history(farmer_id: str, storage: Storage) -> list[LedgerEntry]:
    """All recorded seasons for this farmer, most recent first. Empty for
    a farmer with no history -- not an error, the neutral baseline.
    Requires season_id to sort chronologically as a plain string (e.g.
    "2025", "2026-kuruvai") -- see ADR-005 Decision 2.
    """
    entries = storage.get_ledger_entries(farmer_id)
    return sorted(entries, key=lambda e: e.season_id, reverse=True)


def weighted_bump_days(farmer_id: str, storage: Storage) -> float:
    """Sum of days_bumped across every recorded season, decayed by how
    long ago each one was. A farmer bumped repeatedly accumulates more
    weight than one bumped once; a bump from many seasons ago contributes
    little. This is the number agents/coordinator.py's fairness bonus
    actually uses -- see ADR-005 Decision 4 for the worked example.
    """
    history = get_ledger_history(farmer_id, storage)
    return sum(
        entry.days_bumped * (FAIRNESS_SEASON_DECAY**seasons_ago)
        for seasons_ago, entry in enumerate(history)
    )


def was_bumped_last_season(farmer_id: str, storage: Storage) -> bool:
    """True if the single most recent recorded season had a bump. This is
    the plain fact surfaced in AdvocateClaim.bumped_last_season and
    farmer-facing copy -- distinct from weighted_bump_days, which is what
    actually drives scoring.
    """
    history = get_ledger_history(farmer_id, storage)
    if not history:
        return False
    return history[0].days_bumped > 0


def operator_follow_through_rate(
    cluster_id: str, season_id: str, storage: Storage
) -> float | None:
    """Confirmed-yes count over (confirmed-yes + confirmed-no) count for
    this cluster/season -- a derived diagnostic, not stored anywhere. See
    ADR-009 Part 2, Decision 7.

    Deliberately excludes plots still "pending" or "unknown"
    (watcher.confirmation_status) from both halves of the ratio: silence
    carries no signal either way (per your explicit instruction), so it
    must not move this number in either direction. Returns None, not
    0.0 or 1.0, when nobody has answered anything yet for this
    cluster/season -- there is no rate to report, not a 0% one.
    """
    confirmations = storage.get_confirmations_for_cluster(cluster_id, season_id)
    yes_count = sum(1 for c in confirmations if c.confirmed is True)
    no_count = sum(1 for c in confirmations if c.confirmed is False)
    total = yes_count + no_count
    if total == 0:
        return None
    return yes_count / total


def record_bump(
    farmer_id: str,
    season_id: str,
    *,
    days_bumped: int,
    outcome: str,
    cluster_id: str,
    plot_id: str,
    opponent_plot_id: str | None,
    storage: Storage,
    decided_by: str = "agent",
) -> StorageResult:
    """Record one season's outcome for a farmer. Idempotent per
    (farmer_id, season_id) -- see Storage.put_ledger_entry's contract; a
    duplicate call (e.g. a race on escalation resolution) fails cleanly
    rather than double-recording.

    opponent_plot_id may be None when no specific other farmer benefits
    from the freed capacity (e.g. an operator-override drop with no
    "add a plot in its place" mechanism -- ADR-013 Decision 6).
    decided_by defaults to "agent" (reserved for a hypothetical future
    fully-automatic bump path); every real caller today passes
    "operator_escalation" or "operator_override" explicitly -- see
    ADR-013 Decision 6 and "Resolved on review."
    """
    entry = LedgerEntry(
        farmer_id=farmer_id,
        season_id=season_id,
        days_bumped=days_bumped,
        outcome=outcome,
        resolved_at=datetime.now(timezone.utc).isoformat(),
        cluster_id=cluster_id,
        plot_id=plot_id,
        opponent_plot_id=opponent_plot_id,
        decided_by=decided_by,
    )
    return storage.put_ledger_entry(entry)
