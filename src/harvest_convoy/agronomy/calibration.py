"""Per-cluster maturity-threshold calibration.

Generalizes ADR-002's Theni-specific derivation (fixed May15-Aug12 window,
five most recent complete years, real Open-Meteo Archive history) to any
cluster's own coordinates, instead of applying Theni's rate everywhere.
See docs/adr/ADR-008-tn-generalization-and-tamil.md Decision 2 for why
this exists: a 5-year check against a second real TN district
(Naducauvery, Thanjavur delta) measured a ~10.6% higher GDD/day rate than
Theni's, implying ~9 days of maturity-projection drift if Theni's number
were applied TN-wide unmodified.

Makes real network calls (Open-Meteo Archive, five years). Callers decide
what happens on WeatherError -- this module doesn't apply a fallback
itself (see scheduling/solver.py:solve(), which resolves
Cluster.maturity_gdd_override and logs loudly when it falls back to the
global crop_params.MATURITY_GDD_ESTIMATED reference constant).
"""

from __future__ import annotations

from datetime import date

from harvest_convoy.agronomy import crop_params
from harvest_convoy.agronomy.gdd import accumulate_gdd
from harvest_convoy.weather.openmeteo import get_daily_temperatures

# Same window ADR-002 used for Theni: a Kuruvai-season proxy window, not
# independently re-derived per district -- paddy Kuruvai transplanting
# timing is broadly TN-wide (monsoon-tied), so reusing the same calendar
# window across districts is a reasonable, disclosed simplification, not
# a second unstated assumption stacked on the first.
CALIBRATION_WINDOW_START_MONTH_DAY = (5, 15)
CALIBRATION_WINDOW_END_MONTH_DAY = (8, 12)
CALIBRATION_YEARS = 5


def derive_reference_gdd_rate(
    lat: float,
    lon: float,
    *,
    years: int = CALIBRATION_YEARS,
    today: date | None = None,
) -> float:
    """Mean daily GDD accrual at (lat, lon), computed the exact way
    ADR-002 computed KURUVAI_MEAN_GDD_PER_DAY_REFERENCE_ESTIMATED for
    Theni: T_BASE_C GDD summed over the fixed May15-Aug12 window, across
    the `years` most recently complete calendar years (strictly before
    `today`'s year, so a partial current-year window is never included).

    Raises WeatherError (propagated from weather.openmeteo) if Open-Meteo
    is unreachable for any of the requested years.
    """
    today = today or date.today()
    start_month, start_day = CALIBRATION_WINDOW_START_MONTH_DAY
    end_month, end_day = CALIBRATION_WINDOW_END_MONTH_DAY

    total_gdd = 0.0
    total_days = 0
    for year in range(today.year - years, today.year):
        window_start = date(year, start_month, start_day)
        window_end = date(year, end_month, end_day)
        days = get_daily_temperatures(lat, lon, window_start, window_end, today=today)
        total_gdd += accumulate_gdd(days, crop_params.T_BASE_C)
        total_days += len(days)

    return total_gdd / total_days


def derive_cluster_maturity_gdd(
    lat: float,
    lon: float,
    *,
    years: int = CALIBRATION_YEARS,
    today: date | None = None,
) -> float:
    """The per-cluster equivalent of crop_params.MATURITY_GDD_ESTIMATED,
    computed from this cluster's own climate instead of Theni's: this
    location's derived mean GDD/day times the same
    ADT45_FIELD_DURATION_DAYS_ESTIMATED (90 days) used for the fallback
    constant. Same derivation shape, different, cluster-specific input.
    """
    rate = derive_reference_gdd_rate(lat, lon, years=years, today=today)
    return crop_params.ADT45_FIELD_DURATION_DAYS_ESTIMATED * rate
