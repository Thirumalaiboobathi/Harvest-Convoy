from harvest_convoy.scheduling.capacity import (
    ForecastDay,
    harvest_day_budget_acres,
    usable_harvest_days,
)


def test_no_rain_uses_full_forecast_window() -> None:
    forecast = [ForecastDay(f"2026-08-{16+i}", 0.0) for i in range(5)]
    assert usable_harvest_days(forecast, rain_threshold_mm=5.0) == 5


def test_window_closes_at_first_breach_and_does_not_reopen() -> None:
    forecast = [
        ForecastDay("2026-08-16", 0.0),
        ForecastDay("2026-08-17", 2.0),
        ForecastDay("2026-08-18", 20.0),  # breach
        ForecastDay("2026-08-19", 0.0),  # dry again, but window already closed
        ForecastDay("2026-08-20", 0.0),
    ]
    assert usable_harvest_days(forecast, rain_threshold_mm=5.0) == 2


def test_rain_on_day_zero_gives_zero_usable_days() -> None:
    forecast = [ForecastDay("2026-08-16", 50.0)]
    assert usable_harvest_days(forecast, rain_threshold_mm=5.0) == 0


def test_empty_forecast_gives_zero_usable_days() -> None:
    assert usable_harvest_days([], rain_threshold_mm=5.0) == 0


def test_budget_is_days_times_capacity() -> None:
    assert harvest_day_budget_acres(4, machine_capacity_acres_per_day=3.5) == 14.0


def test_budget_uses_default_capacity_when_unspecified() -> None:
    assert harvest_day_budget_acres(2) == 7.0
