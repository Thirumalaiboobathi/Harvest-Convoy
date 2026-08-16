"""Live tests against the real Open-Meteo API for a Theni-area coordinate.

These make real network calls (no API key required, free tier). Marked
`network` so they can be excluded with `-m "not network"` if ever needed
(e.g. no internet in a sandboxed environment), but they run by default,
including in CI, per docs/adr/ADR-001-agronomy-core.md Decision 5 -- the
seam behavior can only be verified against the real API's current state.
"""

from datetime import date, timedelta

import pytest

from harvest_convoy.weather.openmeteo import WeatherError, get_daily_temperatures

THENI_LAT = 10.0
THENI_LON = 77.5


@pytest.mark.network
def test_archive_to_forecast_seam_has_no_gaps_or_duplicates(tmp_path) -> None:
    start = date.today() - timedelta(days=20)
    end = date.today() + timedelta(days=5)

    result = get_daily_temperatures(
        THENI_LAT, THENI_LON, start, end, cache_dir=tmp_path
    )

    dates = [d.date for d in result]
    assert len(dates) == len(set(dates)), "duplicate dates in joined series"

    expected = [
        (start + timedelta(days=i)).isoformat()
        for i in range((end - start).days + 1)
    ]
    assert dates == expected

    for entry in result:
        assert entry.t_max_c > entry.t_min_c


@pytest.mark.network
def test_purely_historical_range_has_no_gaps(tmp_path) -> None:
    start = date.today() - timedelta(days=30)
    end = date.today() - timedelta(days=10)

    result = get_daily_temperatures(
        THENI_LAT, THENI_LON, start, end, cache_dir=tmp_path
    )

    dates = [d.date for d in result]
    expected = [
        (start + timedelta(days=i)).isoformat()
        for i in range((end - start).days + 1)
    ]
    assert dates == expected


@pytest.mark.network
def test_beyond_forecast_horizon_raises_not_silently_truncates(tmp_path) -> None:
    start = date.today()
    end = date.today() + timedelta(days=30)  # beyond the 16-day horizon

    with pytest.raises(WeatherError):
        get_daily_temperatures(THENI_LAT, THENI_LON, start, end, cache_dir=tmp_path)
