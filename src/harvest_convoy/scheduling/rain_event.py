"""Rain event classification: distinguishes a brief, recoverable shower
from a sustained, monsoon-onset-type closure -- both currently look
identical to the flat RAIN_THRESHOLD_MM breach the trigger already uses.
DETERMINISTIC -- no LLM involvement, same discipline as every other
module in scheduling/.

TUNING PARAMETERS, not measured agronomy thresholds. Do not read the
_TUNING suffix as agronomy/crop_params.py's _ESTIMATED suffix's cousin:
crop_params.py's _ESTIMATED constants are derived through a shown
calculation chain from at least one sourced input (e.g.
MATURITY_GDD_ESTIMATED, built from a sourced T_BASE_C and a measured
GDD/day rate). These three constants have no such chain. No citable
paddy-specific source distinguishing a brief shower from a sustained/
monsoon-onset event was found despite a targeted search (paddy field
trafficability/workability after rainfall, rice lodging recovery time,
combine-harvester field re-entry timing -- see ADR-011 Part 3, Decision
11 for exactly what was searched). What was found: agricultural-
engineering field-workability literature commonly references soil
moisture returning to field capacity around 24 hours after a soaking
rain in compaction-study methodology -- directional corroboration for
the 1-day BRIEF boundary below, not a citation for a paddy-specific
number. Change these freely; nothing else in the system depends on the
exact values, only on BRIEF vs SUSTAINED being a real distinction.
"""

from __future__ import annotations

import logging
from enum import Enum

from harvest_convoy.scheduling.capacity import ForecastDay

logger = logging.getLogger(__name__)

RAIN_EVENT_SUSTAINED_MIN_DURATION_DAYS_TUNING = 3
RAIN_EVENT_SUSTAINED_ACCUMULATION_MM_TUNING = 50.0
RAIN_EVENT_SUSTAINED_INTENSITY_MM_TUNING = 40.0

# Applied to a ready plot's urgency for the duration of a SUSTAINED event
# only -- a brief, recoverable shower doesn't raise urgency. Also a
# tuning parameter, not a measured figure; see agents/coordinator.py's
# MAX_FAIRNESS_BONUS assert for the worked proof that this cannot, at any
# value, let the fairness bonus outrank genuine urgency (ADR-011 Part 3,
# Decision 13) -- the value itself is free to change, that proof is not
# specific to 0.15.
RAIN_URGENCY_BOOST_SUSTAINED_TUNING = 0.15
RAIN_URGENCY_BOOST_BRIEF_TUNING = 0.0


class RainEventClass(str, Enum):
    NONE = "none"          # no breach at all -- the existing no-trigger case
    BRIEF = "brief"
    SUSTAINED = "sustained"


def classify_rain_event(
    forecast: list[ForecastDay], rain_threshold_mm: float,
) -> RainEventClass:
    """Reads the same forecast usable_harvest_days() already reads --
    finds the run of consecutive "wet" days (precipitation_mm >
    rain_threshold_mm) starting at the first breach, and classifies it
    by duration, total accumulation, and single-day intensity. Purely a
    function of already-fetched data; makes no network call of its own.
    Does not change usable_harvest_days()'s own capacity math at all --
    this is a parallel signal, read off the same forecast, used only for
    urgency and message wording (ADR-011 Part 3).

    Meeting *any one* of the three RAIN_EVENT_SUSTAINED_*_TUNING
    thresholds classifies the whole event SUSTAINED; otherwise BRIEF --
    there is no third bucket. A wet run that reaches the end of the
    fetched forecast without an observed recovery day is classified
    SUSTAINED regardless of its measured-so-far duration: the
    forecast-too-short case, resolved toward caution rather than a
    confident BRIEF the data can't actually support.
    """
    breach_index = None
    for i, day in enumerate(forecast):
        if day.precipitation_mm > rain_threshold_mm:
            breach_index = i
            break
    if breach_index is None:
        return RainEventClass.NONE

    wet_run = []
    reached_end_still_wet = True
    for day in forecast[breach_index:]:
        if day.precipitation_mm > rain_threshold_mm:
            wet_run.append(day)
        else:
            reached_end_still_wet = False
            break

    duration = len(wet_run)
    total_accumulation = sum(day.precipitation_mm for day in wet_run)
    max_intensity = max((day.precipitation_mm for day in wet_run), default=0.0)

    if reached_end_still_wet:
        logger.info(
            "rain event classification: wet run reached the end of the "
            "fetched forecast (%d day(s)) without an observed recovery day "
            "-- classified SUSTAINED out of caution, not a confirmed "
            "%d+-day observation",
            duration, RAIN_EVENT_SUSTAINED_MIN_DURATION_DAYS_TUNING,
        )
        return RainEventClass.SUSTAINED

    if (
        duration >= RAIN_EVENT_SUSTAINED_MIN_DURATION_DAYS_TUNING
        or total_accumulation >= RAIN_EVENT_SUSTAINED_ACCUMULATION_MM_TUNING
        or max_intensity >= RAIN_EVENT_SUSTAINED_INTENSITY_MM_TUNING
    ):
        return RainEventClass.SUSTAINED
    return RainEventClass.BRIEF


def rain_urgency_boost(classification: RainEventClass) -> float:
    if classification == RainEventClass.SUSTAINED:
        return RAIN_URGENCY_BOOST_SUSTAINED_TUNING
    return RAIN_URGENCY_BOOST_BRIEF_TUNING
