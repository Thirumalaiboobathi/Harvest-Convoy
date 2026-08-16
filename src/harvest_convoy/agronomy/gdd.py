"""Growing Degree Day accumulation.

Deterministic arithmetic only -- no LLM involvement, per the project's
architectural rule. See docs/adr/ADR-001-agronomy-core.md.

    GDD_day = max(0, ((T_max + T_min) / 2) - T_base)
    GDD_accumulated = sum(GDD_day) from transplant_date to date
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DailyTemperature:
    date: str  # ISO 8601, e.g. "2026-06-01"
    t_max_c: float
    t_min_c: float


def daily_gdd(t_max_c: float, t_min_c: float, t_base_c: float) -> float:
    """GDD contribution of a single day. Never negative."""
    mean_c = (t_max_c + t_min_c) / 2
    return max(0.0, mean_c - t_base_c)


def accumulate_gdd(days: list[DailyTemperature], t_base_c: float) -> float:
    """Sum of daily GDD across a sequence of days, in whatever order given."""
    return sum(daily_gdd(d.t_max_c, d.t_min_c, t_base_c) for d in days)


def project_maturity_date(
    days: list[DailyTemperature],
    t_base_c: float,
    maturity_gdd: float,
) -> str | None:
    """First date at which accumulated GDD reaches maturity_gdd, walking
    `days` in date order. `days` must already be sorted ascending by date.
    Returns None if maturity_gdd isn't reached within the given days.
    """
    total = 0.0
    for d in days:
        total += daily_gdd(d.t_max_c, d.t_min_c, t_base_c)
        if total >= maturity_gdd:
            return d.date
    return None
