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

from datetime import date, timedelta

from harvest_convoy.agronomy import crop_params
from harvest_convoy.agronomy.gdd import accumulate_gdd
from harvest_convoy.models import Cluster, Plot
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


def project_maturity_for_plot(
    plot: Plot, cluster: Cluster, *, today: date | None = None
) -> str:
    """Projected maturity date for a plot that (usually) hasn't reached
    maturity yet -- built for registration-time farmer messaging (ADR-009
    Part 3), where the real project_maturity_date() in agronomy/gdd.py
    can't be used directly: a farmer registers right around transplant,
    ~90-110 real field days before maturity, while Open-Meteo's forecast
    horizon is only 16 days -- walking real+forecast data that far ahead
    would almost always return None.

    Composed instead from two pieces this codebase already has, not a
    third independently-invented method: real elapsed GDD from
    transplant_date to `today` (a genuine live Open-Meteo call -- raises
    WeatherError on failure, same as every other GDD computation here),
    plus the cluster's own already-derived rate (or the global reference
    rate, if uncalibrated) for the remaining days no weather data can
    reach yet. Same maturity-threshold resolution scheduling/solver.py
    uses: cluster.maturity_gdd_override if calibrated, else
    crop_params.MATURITY_GDD_ESTIMATED.

    If `plot.transplant_date` is still in the future relative to `today`
    (a farmer registering ahead of transplanting), there's no elapsed
    GDD to fetch at all -- no network call is made, and the projection
    runs purely off the rate, anchored at the transplant date itself
    rather than today.
    """
    today = today or date.today()

    maturity_gdd = cluster.maturity_gdd_override
    if maturity_gdd is None:
        maturity_gdd = crop_params.MATURITY_GDD_ESTIMATED
    if cluster.maturity_gdd_override is not None:
        rate = cluster.maturity_gdd_override / crop_params.ADT45_FIELD_DURATION_DAYS_ESTIMATED
    else:
        rate = crop_params.KURUVAI_MEAN_GDD_PER_DAY_REFERENCE_ESTIMATED

    if plot.transplant_date > today:
        accumulated_so_far = 0.0
        anchor = plot.transplant_date
    else:
        days = get_daily_temperatures(plot.lat, plot.lon, plot.transplant_date, today)
        accumulated_so_far = accumulate_gdd(days, crop_params.T_BASE_C)
        anchor = today

    remaining_gdd = max(0.0, maturity_gdd - accumulated_so_far)
    days_remaining = round(remaining_gdd / rate)
    return (anchor + timedelta(days=days_remaining)).isoformat()
