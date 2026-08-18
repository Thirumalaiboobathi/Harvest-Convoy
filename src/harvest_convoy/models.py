"""Core entity model: plain dataclasses, no persistence or transport concerns.

Persistence (DynamoDB) lands in Phase 5; these are the same shapes storage/
will (de)serialize, but this module has no dependency on that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class Farmer:
    farmer_id: str
    name: str
    cluster_id: str
    telegram_chat_id: int | None = None
    # Per-farmer, not global/per-cluster -- see ADR-008 Part 2. Default
    # "ta" is what makes a pre-this-ADR stored record (no "language" key
    # at all) default to Tamil for free: Farmer(**raw) on a dict missing
    # this key falls through to the dataclass default in both storage
    # backends, no migration needed.
    language: Literal["ta", "en"] = "ta"


@dataclass(frozen=True)
class Plot:
    plot_id: str
    farmer_id: str
    cluster_id: str
    lat: float
    lon: float
    crop: str
    variety: str
    transplant_date: date  # anchor decided in ADR-001 -- transplant, not sowing
    area_acres: float  # canonical value everything computes against
    # How the farmer expressed area at registration -- display only.
    # area_acres stays canonical; no downstream math reads this field.
    # See ADR-008 Decision 9.
    area_unit: Literal["acre", "cent"] = "acre"


@dataclass(frozen=True)
class Cluster:
    cluster_id: str
    name: str
    machine_capacity_acres_per_day: float
    machine_start_lat: float
    machine_start_lon: float
    operator_chat_id: int | None = None
    # Per-cluster maturity GDD threshold, derived from this cluster's own
    # climatology (agronomy/calibration.py) instead of the global,
    # Theni-derived crop_params.MATURITY_GDD_ESTIMATED fallback. None
    # means "not yet calibrated" -- scheduling/solver.py falls back to
    # the global constant and logs loudly when this is None. See ADR-008
    # Decision 2.
    maturity_gdd_override: float | None = None
    # Language for the two operator-facing message shapes (route summary,
    # escalation dispatch) -- separate from any one Farmer.language, since
    # the operator is a single cluster-level role, not a registered
    # farmer. Default "ta" matches the product-wide default. Previously
    # these two shapes were English-only, a disclosed scope boundary
    # (ADR-008 Decision 8); reversed on request -- see Decision 8's
    # revision note.
    operator_language: Literal["ta", "en"] = "ta"
