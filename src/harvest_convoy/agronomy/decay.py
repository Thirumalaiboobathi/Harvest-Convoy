"""Overripe decay curve.

DERIVED, NOT sourced -- see docs/adr/ADR-002-scheduling-core.md, Decision 2,
for what was searched (including a search-hallucinated figure that was
caught by direct verification and discarded) and why no citable
day-past-maturity loss curve for paddy could be confirmed. This is a linear
ranking proxy, not a validated agronomic yield-loss curve -- do not present
its output as a measured quality-loss percentage.
"""

from __future__ import annotations

from harvest_convoy.agronomy import crop_params


def decay_fraction(days_past_maturity: int) -> float:
    """Fraction (0.0-1.0) representing how far a plot has decayed past its
    projected maturity date, for use as an urgency-ranking signal. Reaches
    1.0 (full decay) at crop_params.DECAY_HORIZON_DAYS_ESTIMATED and stays
    capped there.
    """
    if days_past_maturity < 0:
        raise ValueError("days_past_maturity must be >= 0")
    return min(
        1.0, days_past_maturity / crop_params.DECAY_HORIZON_DAYS_ESTIMATED
    )
