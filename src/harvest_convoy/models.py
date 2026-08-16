"""Core entity model: plain dataclasses, no persistence or transport concerns.

Persistence (DynamoDB) lands in Phase 5; these are the same shapes storage/
will (de)serialize, but this module has no dependency on that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Farmer:
    farmer_id: str
    name: str
    cluster_id: str
    telegram_chat_id: int | None = None


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
    area_acres: float


@dataclass(frozen=True)
class Cluster:
    cluster_id: str
    name: str
    machine_capacity_acres_per_day: float
    machine_start_lat: float
    machine_start_lon: float
    operator_chat_id: int | None = None
