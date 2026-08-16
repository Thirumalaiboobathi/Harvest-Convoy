"""Read-only fairness ledger stub. See ADR-003 Decision 2.

Phase 5 replaces this in-memory dict with DynamoDB behind the same lookup
signature. Seeded here so the Kamatchipuram negotiation scenario has a real
fairness-vs-urgency tension to resolve -- f04 was bumped last season, f03
(with strictly higher raw urgency but no prior bump) was not.
"""

from __future__ import annotations

from strands import tool

# farmer_id -> was this farmer's harvest bumped/delayed last season?
_STUB_LEDGER: dict[str, bool] = {
    "f04": True,
}


def was_bumped_last_season(farmer_id: str) -> bool:
    return _STUB_LEDGER.get(farmer_id, False)


@tool
def fairness_lookup(farmer_id: str) -> dict:
    """Look up whether this farmer's plot was bumped (delayed past its
    ideal harvest slot) last season. Use this before deciding how hard to
    argue -- a farmer bumped last season has a stronger fairness claim this
    time."""
    return {
        "farmer_id": farmer_id,
        "bumped_last_season": was_bumped_last_season(farmer_id),
    }
