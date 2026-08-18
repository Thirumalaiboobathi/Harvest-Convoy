"""Deterministic test of the null-gap bridging logic.

Live Open-Meteo data currently has no gap under the default best-match
blend (see ADR-001), so we can't rely on real API state to exercise the
bridge path. This mocks the fetch layer to simulate a gap the way the raw
ERA5-only model actually produces one (see the ADR's `models=era5` probe),
and asserts the bridge fills it correctly from Forecast past_days.
"""

from datetime import date

import pytest

import harvest_convoy.weather.openmeteo as om


def test_null_gap_in_archive_is_bridged_from_forecast_past_days(monkeypatch) -> None:
    def fake_fetch(url: str, params: dict, cache_dir, key: str) -> dict:
        if url == om.ARCHIVE_URL:
            return {
                "daily": {
                    "time": ["2026-08-10", "2026-08-11", "2026-08-12"],
                    "temperature_2m_max": [34.2, None, None],
                    "temperature_2m_min": [25.4, None, None],
                }
            }
        if url == om.FORECAST_URL and "past_days" in params:
            return {
                "daily": {
                    "time": ["2026-08-09", "2026-08-10", "2026-08-11", "2026-08-12"],
                    "temperature_2m_max": [33.0, 34.2, 35.9, 36.8],
                    "temperature_2m_min": [24.0, 25.4, 25.5, 25.2],
                }
            }
        raise AssertionError(f"unexpected fetch: {url} {params}")

    monkeypatch.setattr(om, "_fetch", fake_fetch)

    result = om.get_daily_temperatures(
        10.0,
        77.5,
        date(2026, 8, 10),
        date(2026, 8, 12),
        cache_dir=None,
        today=date(2026, 8, 12),
    )

    assert [d.date for d in result] == ["2026-08-10", "2026-08-11", "2026-08-12"]
    assert result[1].t_max_c == 35.9
    assert result[1].t_min_c == 25.5
    assert result[2].t_max_c == 36.8
    assert result[2].t_min_c == 25.2


def test_bridge_raises_if_forecast_also_missing_the_day(monkeypatch) -> None:
    def fake_fetch(url: str, params: dict, cache_dir, key: str) -> dict:
        if url == om.ARCHIVE_URL:
            return {
                "daily": {
                    "time": ["2026-08-12"],
                    "temperature_2m_max": [None],
                    "temperature_2m_min": [None],
                }
            }
        if url == om.FORECAST_URL and "past_days" in params:
            return {
                "daily": {
                    "time": ["2026-08-12"],
                    "temperature_2m_max": [None],
                    "temperature_2m_min": [None],
                }
            }
        raise AssertionError(f"unexpected fetch: {url} {params}")

    monkeypatch.setattr(om, "_fetch", fake_fetch)

    with pytest.raises(om.WeatherError):
        om.get_daily_temperatures(
            10.0,
            77.5,
            date(2026, 8, 12),
            date(2026, 8, 12),
            cache_dir=None,
            today=date(2026, 8, 12),
        )


def test_precipitation_forecast_trims_trailing_null_days(monkeypatch) -> None:
    """Live-caught: requesting the full 16-day FORECAST_MAX_HORIZON_DAYS
    window, day 16's precipitation came back null while days 1-15 were
    real -- Open-Meteo hadn't finished computing the far end of the
    window yet. A trailing null is "not computed yet," not a data gap;
    the fix trims it rather than hard-failing the whole forecast."""

    def fake_fetch(url: str, params: dict, cache_dir, key: str) -> dict:
        return {
            "daily": {
                "time": ["2026-08-18", "2026-08-19", "2026-08-20"],
                "precipitation_sum": [1.6, 3.0, None],
            }
        }

    monkeypatch.setattr(om, "_fetch", fake_fetch)

    result = om.get_precipitation_forecast(
        10.0, 77.5, date(2026, 8, 18), date(2026, 8, 20), cache_dir=None
    )

    assert [d.date for d in result] == ["2026-08-18", "2026-08-19"]
    assert result[0].precipitation_mm == 1.6
    assert result[1].precipitation_mm == 3.0


def test_precipitation_forecast_still_raises_on_a_null_in_the_middle(monkeypatch) -> None:
    """A null that ISN'T at the trailing edge is a genuine gap/anomaly,
    not "not computed yet" -- must still fail loud, not silently drop a
    day from the middle of the window."""

    def fake_fetch(url: str, params: dict, cache_dir, key: str) -> dict:
        return {
            "daily": {
                "time": ["2026-08-18", "2026-08-19", "2026-08-20"],
                "precipitation_sum": [1.6, None, 0.4],
            }
        }

    monkeypatch.setattr(om, "_fetch", fake_fetch)

    with pytest.raises(om.WeatherError):
        om.get_precipitation_forecast(
            10.0, 77.5, date(2026, 8, 18), date(2026, 8, 20), cache_dir=None
        )


def test_precipitation_forecast_raises_if_every_day_is_null(monkeypatch) -> None:
    def fake_fetch(url: str, params: dict, cache_dir, key: str) -> dict:
        return {
            "daily": {
                "time": ["2026-08-18", "2026-08-19"],
                "precipitation_sum": [None, None],
            }
        }

    monkeypatch.setattr(om, "_fetch", fake_fetch)

    with pytest.raises(om.WeatherError):
        om.get_precipitation_forecast(
            10.0, 77.5, date(2026, 8, 18), date(2026, 8, 19), cache_dir=None
        )
