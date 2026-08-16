from harvest_convoy.agronomy.gdd import (
    DailyTemperature,
    accumulate_gdd,
    daily_gdd,
    project_maturity_date,
)

T_BASE = 10.0


def test_hand_computed_five_day_window() -> None:
    # mean = (max+min)/2, gdd = max(0, mean - 10)
    # day1: mean 25 -> 15 | day2: mean 27 -> 17 | day3: mean 23 -> 13
    # day4: mean 6  -> 0  (below base) | day5: mean 26 -> 16
    # total = 15 + 17 + 13 + 0 + 16 = 61
    days = [
        DailyTemperature("2026-06-01", t_max_c=30, t_min_c=20),
        DailyTemperature("2026-06-02", t_max_c=32, t_min_c=22),
        DailyTemperature("2026-06-03", t_max_c=28, t_min_c=18),
        DailyTemperature("2026-06-04", t_max_c=8, t_min_c=4),
        DailyTemperature("2026-06-05", t_max_c=31, t_min_c=21),
    ]
    assert accumulate_gdd(days, T_BASE) == 61.0


def test_day_below_base_contributes_zero_not_negative() -> None:
    assert daily_gdd(t_max_c=8, t_min_c=4, t_base_c=T_BASE) == 0.0


def test_day_exactly_at_base_contributes_zero() -> None:
    assert daily_gdd(t_max_c=10, t_min_c=10, t_base_c=T_BASE) == 0.0


def test_project_maturity_date_returns_first_day_threshold_is_met() -> None:
    days = [
        DailyTemperature("2026-06-01", 30, 20),  # +15 -> 15
        DailyTemperature("2026-06-02", 32, 22),  # +17 -> 32
        DailyTemperature("2026-06-03", 28, 18),  # +13 -> 45
    ]
    assert project_maturity_date(days, T_BASE, maturity_gdd=32) == "2026-06-02"


def test_project_maturity_date_returns_none_when_not_reached() -> None:
    days = [DailyTemperature("2026-06-01", 30, 20)]
    assert project_maturity_date(days, T_BASE, maturity_gdd=1000) is None
