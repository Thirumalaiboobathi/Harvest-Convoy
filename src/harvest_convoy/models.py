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
    # ADR-013 Part 2, Decision 13/17: a proxy registration's operator
    # jots this down verbatim (a phone number, "no phone", a relative's
    # number) for his own reference and the equity report's provenance
    # story. Never used to attempt a Telegram send -- the Bot API cannot
    # address a chat_id it has never received an inbound message from,
    # so this is not, and can never become, a usable send target.
    contact_note: str | None = None


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
    # ADR-013 Part 2: message 1 of the four-message registration flow
    # asks for this but, before this field existed, discarded it --
    # registration.py:6-14's own docstring says it was only ever asked
    # to make the greeting read conversationally. None means "asked
    # before this field existed," not "no village." Now persisted for
    # both self- and proxy-registration going forward.
    village: str | None = None
    # "self" (the only path before ADR-013 Part 2) or
    # f"operator:{operator_chat_id}" for a proxy registration. See
    # ADR-013 Part 2 Decision 17.
    registered_by: str = "self"
    # ISO timestamp. None for every row written before this field
    # existed -- an honest "not recorded," not "unknown origin."
    registered_at: str | None = None
    # Set only by ADR-013 Part 2 Decision 18's operator-tap link action,
    # on the losing (duplicate) side of a link: f"linked_to:{canonical
    # farmer_id}". A plot with this set is excluded from scheduling but
    # never removed from storage -- filtered at read time, same
    # discipline as rollover exclusion, never deleted.
    retired_reason: str | None = None


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
