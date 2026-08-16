"""Usable-harvest-day budget from a rain forecast. DETERMINISTIC.

Given a forecast and a rain threshold, computes how many days are usable
before the weather closes the window, and converts that into an acreage
budget via the cluster's machine capacity.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MACHINE_CAPACITY_ACRES_PER_DAY: float = 3.5


@dataclass(frozen=True)
class ForecastDay:
    date: str
    precipitation_mm: float


def usable_harvest_days(
    forecast: list[ForecastDay], rain_threshold_mm: float
) -> int:
    """Number of consecutive usable days from the start of `forecast`, up
    to (not including) the first day whose precipitation exceeds the
    threshold. The window does not reopen after the first breach, even if
    a later day is forecast dry again -- lodged or wet paddy doesn't
    un-lodge because tomorrow happens to be sunny. An empty forecast, or
    one with no day exceeding the threshold, returns the full length.
    """
    count = 0
    for day in forecast:
        if day.precipitation_mm > rain_threshold_mm:
            break
        count += 1
    return count


def harvest_day_budget_acres(
    usable_days: int,
    machine_capacity_acres_per_day: float = DEFAULT_MACHINE_CAPACITY_ACRES_PER_DAY,
) -> float:
    """Total acreage the shared machine can cover before the window closes."""
    return usable_days * machine_capacity_acres_per_day
