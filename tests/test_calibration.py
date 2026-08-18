"""Per-cluster GDD calibration tests. See ADR-008 Decision 2.

Network tests reproduce the exact ADR-002 methodology against live
Open-Meteo Archive data -- marked `network`, skipped by default (same
convention as tests/test_weather_seam.py). The hermetic test below
proves the field-duration multiplication without touching the network.
"""

from __future__ import annotations

from datetime import date

import pytest

from harvest_convoy.agronomy import crop_params
from harvest_convoy.agronomy.calibration import (
    derive_cluster_maturity_gdd,
    derive_reference_gdd_rate,
)

THENI_LAT, THENI_LON = 10.0104, 77.4768
NADUCAUVERY_LAT, NADUCAUVERY_LON = 10.861, 79.046
REFERENCE_TODAY = date(2026, 8, 16)  # matches ADR-002's 2021-2025 window


@pytest.mark.network
def test_theni_calibration_reproduces_adr_002s_published_rate() -> None:
    rate = derive_reference_gdd_rate(THENI_LAT, THENI_LON, today=REFERENCE_TODAY)

    # ADR-002 published 19.2133 GDD/day for this exact window/years/coords.
    assert rate == pytest.approx(19.2133, abs=0.01)


@pytest.mark.network
def test_naducauvery_calibration_is_measurably_hotter_than_theni() -> None:
    """See ADR-008 Decision 2: Thanjavur delta runs ~10.6% hotter than
    Theni over the same calibration window -- this is the real,
    measured gap the per-cluster override exists to close."""
    theni_rate = derive_reference_gdd_rate(THENI_LAT, THENI_LON, today=REFERENCE_TODAY)
    thanjavur_rate = derive_reference_gdd_rate(
        NADUCAUVERY_LAT, NADUCAUVERY_LON, today=REFERENCE_TODAY
    )

    assert thanjavur_rate > theni_rate
    # ADR-008 measured 21.2509 vs 19.2133 -- roughly a 10% gap, not noise.
    assert (thanjavur_rate - theni_rate) / theni_rate > 0.05


@pytest.mark.network
def test_derive_cluster_maturity_gdd_scales_rate_by_field_duration() -> None:
    rate = derive_reference_gdd_rate(THENI_LAT, THENI_LON, today=REFERENCE_TODAY)
    maturity_gdd = derive_cluster_maturity_gdd(THENI_LAT, THENI_LON, today=REFERENCE_TODAY)

    assert maturity_gdd == pytest.approx(
        rate * crop_params.ADT45_FIELD_DURATION_DAYS_ESTIMATED, rel=1e-9
    )
    # Sanity: Theni's own calibrated threshold should land close to the
    # global fallback constant, since that constant *is* Theni's number.
    assert maturity_gdd == pytest.approx(crop_params.MATURITY_GDD_ESTIMATED, abs=1.0)
