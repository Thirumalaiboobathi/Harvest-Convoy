"""Route ordering by straight-line distance. DETERMINISTIC.

No real road routing -- explicitly out of scope per the project brief;
straight-line (haversine) distance between plot centroids only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Mean Earth radius, IUGG value, km.
EARTH_RADIUS_KM: float = 6371.0088


@dataclass(frozen=True)
class RoutePoint:
    plot_id: str
    lat: float
    lon: float


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometers."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def order_route(
    points: list[RoutePoint], start_lat: float, start_lon: float
) -> list[str]:
    """Greedy nearest-neighbor ordering of `points` starting from
    (start_lat, start_lon). Deterministic: distance ties are broken by
    plot_id so the result doesn't depend on input order or dict/set
    iteration order.
    """
    remaining = list(points)
    ordered: list[str] = []
    cur_lat, cur_lon = start_lat, start_lon
    while remaining:
        remaining.sort(
            key=lambda p: (haversine_km(cur_lat, cur_lon, p.lat, p.lon), p.plot_id)
        )
        nxt = remaining.pop(0)
        ordered.append(nxt.plot_id)
        cur_lat, cur_lon = nxt.lat, nxt.lon
    return ordered
