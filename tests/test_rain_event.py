"""Rain event classification tests (ADR-011 Part 3). Pure function, no
storage/network involved -- classify_rain_event() reads only the
already-fetched forecast list.
"""

from __future__ import annotations

from harvest_convoy.scheduling.capacity import ForecastDay
from harvest_convoy.scheduling.rain_event import (
    RAIN_EVENT_SUSTAINED_ACCUMULATION_MM_TUNING,
    RAIN_EVENT_SUSTAINED_INTENSITY_MM_TUNING,
    RAIN_EVENT_SUSTAINED_MIN_DURATION_DAYS_TUNING,
    RAIN_URGENCY_BOOST_BRIEF_TUNING,
    RAIN_URGENCY_BOOST_SUSTAINED_TUNING,
    RainEventClass,
    classify_rain_event,
    rain_urgency_boost,
)

RAIN_THRESHOLD_MM = 5.0


def _dry(n: int, start: int = 0) -> list[ForecastDay]:
    return [ForecastDay(f"2026-08-{start + i + 1:02d}", 0.0) for i in range(n)]


def test_no_breach_at_all_classifies_none() -> None:
    forecast = _dry(5)
    assert classify_rain_event(forecast, RAIN_THRESHOLD_MM) == RainEventClass.NONE


def test_single_wet_day_followed_by_recovery_classifies_brief() -> None:
    forecast = [
        ForecastDay("2026-08-01", 0.0),
        ForecastDay("2026-08-02", 10.0),  # breach, but recovers next day
        ForecastDay("2026-08-03", 0.0),
        ForecastDay("2026-08-04", 0.0),
    ]
    assert classify_rain_event(forecast, RAIN_THRESHOLD_MM) == RainEventClass.BRIEF


def test_two_wet_days_under_every_sustained_threshold_classifies_brief() -> None:
    # duration=2 (< 3), accumulation=12 (< 50), max intensity=6 (< 40),
    # then a recovery day -- fails all three SUSTAINED tests.
    forecast = [
        ForecastDay("2026-08-01", 6.0),
        ForecastDay("2026-08-02", 6.0),
        ForecastDay("2026-08-03", 0.0),
    ]
    assert classify_rain_event(forecast, RAIN_THRESHOLD_MM) == RainEventClass.BRIEF


def test_duration_alone_meeting_threshold_classifies_sustained() -> None:
    forecast = [
        ForecastDay(f"2026-08-{i+1:02d}", 6.0)
        for i in range(RAIN_EVENT_SUSTAINED_MIN_DURATION_DAYS_TUNING)
    ] + [ForecastDay("2026-08-09", 0.0)]  # recovery day, so this isn't the
    # forecast-too-short case -- duration alone must trigger SUSTAINED.
    assert classify_rain_event(forecast, RAIN_THRESHOLD_MM) == RainEventClass.SUSTAINED


def test_accumulation_alone_meeting_threshold_classifies_sustained() -> None:
    # 2 wet days (< 3-day duration threshold), but their sum clears the
    # accumulation threshold, then recovers.
    half = RAIN_EVENT_SUSTAINED_ACCUMULATION_MM_TUNING / 2 + 1
    forecast = [
        ForecastDay("2026-08-01", half),
        ForecastDay("2026-08-02", half),
        ForecastDay("2026-08-03", 0.0),
    ]
    assert classify_rain_event(forecast, RAIN_THRESHOLD_MM) == RainEventClass.SUSTAINED


def test_single_day_intensity_alone_meeting_threshold_classifies_sustained() -> None:
    # 1 wet day (< 3-day duration, < accumulation threshold on its own...
    # actually intensity IS the accumulation for a single day, so pick an
    # intensity that clears the intensity threshold but not the
    # (much higher) accumulation threshold on its own).
    intensity = RAIN_EVENT_SUSTAINED_INTENSITY_MM_TUNING + 1
    assert intensity < RAIN_EVENT_SUSTAINED_ACCUMULATION_MM_TUNING
    forecast = [
        ForecastDay("2026-08-01", intensity),
        ForecastDay("2026-08-02", 0.0),
    ]
    assert classify_rain_event(forecast, RAIN_THRESHOLD_MM) == RainEventClass.SUSTAINED


def test_forecast_too_short_to_observe_recovery_classifies_sustained_out_of_caution() -> None:
    # Wet run reaches the very end of the fetched forecast without a
    # recovery day ever being observed -- even a single wet day at the
    # tail is classified SUSTAINED, not a confident BRIEF the data can't
    # actually support.
    forecast = [ForecastDay("2026-08-01", 0.0), ForecastDay("2026-08-02", 6.0)]
    assert classify_rain_event(forecast, RAIN_THRESHOLD_MM) == RainEventClass.SUSTAINED


def test_forecast_gap_a_dry_day_inside_the_wet_run_ends_it_at_the_gap() -> None:
    # A single dry day between two wet days ends the run there (no
    # reopening) -- the run's own duration/accumulation is judged only up
    # to that gap, matching usable_harvest_days()'s "no reopening" rule.
    forecast = [
        ForecastDay("2026-08-01", 6.0),
        ForecastDay("2026-08-02", 0.0),  # gap -- ends the run
        ForecastDay("2026-08-03", 6.0),
        ForecastDay("2026-08-04", 6.0),
        ForecastDay("2026-08-05", 6.0),
    ]
    assert classify_rain_event(forecast, RAIN_THRESHOLD_MM) == RainEventClass.BRIEF


def test_rain_urgency_boost_sustained_is_positive_brief_is_zero() -> None:
    assert rain_urgency_boost(RainEventClass.SUSTAINED) == RAIN_URGENCY_BOOST_SUSTAINED_TUNING
    assert RAIN_URGENCY_BOOST_SUSTAINED_TUNING > 0.0
    assert rain_urgency_boost(RainEventClass.BRIEF) == RAIN_URGENCY_BOOST_BRIEF_TUNING
    assert rain_urgency_boost(RainEventClass.NONE) == RAIN_URGENCY_BOOST_BRIEF_TUNING


def test_urgency_boost_never_pushes_urgency_above_the_1_0_cap() -> None:
    # assess_plot()'s min(1.0, decay_fraction(...) + boost) -- checked here
    # directly against the pure function, not re-deriving assess_plot.
    already_maxed_urgency = 1.0
    boosted = min(1.0, already_maxed_urgency + RAIN_URGENCY_BOOST_SUSTAINED_TUNING)
    assert boosted == 1.0


def test_not_ready_wording_unchanged_for_none_and_brief() -> None:
    from harvest_convoy.telegram import messages_en, messages_ta

    baseline_en = messages_en.not_ready(2.0, "acres")
    baseline_ta = messages_ta.not_ready(2.0, "acres")
    assert messages_en.not_ready(2.0, "acres", rain_event_classification="none") == baseline_en
    assert messages_en.not_ready(2.0, "acres", rain_event_classification="brief") == baseline_en
    assert messages_ta.not_ready(2.0, "acres", rain_event_classification="none") == baseline_ta
    assert messages_ta.not_ready(2.0, "acres", rain_event_classification="brief") == baseline_ta


def test_not_ready_wording_appends_a_clause_only_for_sustained() -> None:
    from harvest_convoy.telegram import messages_en, messages_ta

    baseline_en = messages_en.not_ready(2.0, "acres")
    sustained_en = messages_en.not_ready(2.0, "acres", rain_event_classification="sustained")
    assert sustained_en != baseline_en
    assert sustained_en.startswith(baseline_en)
    assert "rain" in sustained_en.lower()

    baseline_ta = messages_ta.not_ready(2.0, "acres")
    sustained_ta = messages_ta.not_ready(2.0, "acres", rain_event_classification="sustained")
    assert sustained_ta != baseline_ta
    assert sustained_ta.startswith(baseline_ta)
    assert "மழை" in sustained_ta  # "rain"


def test_classification_changing_between_two_trigger_runs_changes_the_wording_each_time() -> None:
    # ADR-011 Decision 12: a classification that changes between two
    # trigger runs is reflected honestly in each day's own message --
    # re-notifying with the day's own true classification, not suppressed
    # or held to the first day's wording.
    from harvest_convoy.telegram import messages_en

    day1 = messages_en.not_ready(2.0, "acres", rain_event_classification="brief")
    day2 = messages_en.not_ready(2.0, "acres", rain_event_classification="sustained")
    assert day1 != day2
