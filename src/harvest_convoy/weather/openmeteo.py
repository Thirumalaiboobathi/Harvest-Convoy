"""Open-Meteo client: historical (Archive) + forecast, bridged and cached.

See docs/adr/ADR-001-agronomy-core.md, Decision 5, for the empirical basis
of the seam design. Measured live against a Theni-area coordinate: the
Archive API's default "best match" blend (ERA5 + IFS HRES, no `models`
override) had no practical gap up to today at measurement time, but its
documented ~5-6 day ERA5-only lag can reappear. Rather than assume a fixed
lag, this client detects gaps by checking for null values in the response
and backfills exactly those dates from the Forecast API's `past_days` data.

Timezone is fixed to Asia/Kolkata on every call so daily boundaries align
to IST calendar days, matching farmer-reported dates.
"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from datetime import date, timedelta
from pathlib import Path

import httpx

from harvest_convoy.agronomy.gdd import DailyTemperature
from harvest_convoy.observability.otel import get_tracer
from harvest_convoy.scheduling.capacity import ForecastDay

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEZONE = "Asia/Kolkata"
DAILY_FIELDS = "temperature_2m_max,temperature_2m_min"
PRECIPITATION_FIELD = "precipitation_sum"
FORECAST_MAX_HORIZON_DAYS = 16  # confirmed empirically; see ADR-001
FORECAST_MAX_PAST_DAYS = 92  # Open-Meteo documented limit

# The system temp dir, not a relative ".cache/" path: a real deployment
# (AgentCore Runtime) found this the hard way -- /var/task (the working
# directory there) is read-only, so a relative path raised PermissionError
# and took the whole watcher run down before it ever reached the weather
# data. tempfile.gettempdir() resolves to a genuinely writable location
# both locally (Windows/Linux dev machines) and in that environment.
DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "harvest_convoy" / "open-meteo"

logger = logging.getLogger(__name__)
tracer = get_tracer(__name__)


class WeatherError(Exception):
    """Raised when Open-Meteo data can't be retrieved or is unusable."""


def _cache_key(endpoint: str, lat: float, lon: float, start: str, end: str) -> str:
    raw = f"{endpoint}|{lat:.4f}|{lon:.4f}|{start}|{end}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _has_null_daily_values(data: dict) -> bool:
    """True if any daily series in the response contains a null -- an
    unsettled/lagging tail, regardless of which fields were requested."""
    daily = data.get("daily", {})
    return any(
        v is None
        for field, series in daily.items()
        if field != "time"
        for v in series
    )


def _fetch(
    url: str,
    params: dict,
    cache_dir: Path | None,
    key: str,
) -> dict:
    """GET with an optional disk cache. Responses containing null daily
    values (an unsettled/lagging tail) are never persisted, so the next
    call re-fetches and can pick up newly-settled data. Fully-resolved
    responses are cached indefinitely -- historical data doesn't change
    once settled.
    """
    if cache_dir is not None:
        try:
            path = cache_dir / f"{key}.json"
            if path.exists():
                return json.loads(path.read_text())
        except OSError as exc:  # noqa: BLE001 -- degrade, never let caching break a fetch
            logger.warning("cache read failed for %s, fetching fresh: %s", cache_dir, exc)

    with tracer.start_as_current_span("openmeteo.fetch", attributes={"url": url}):
        try:
            response = httpx.get(url, params=params, timeout=15.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise WeatherError(f"Open-Meteo request failed: {exc}") from exc
        data = response.json()

    if cache_dir is not None and not _has_null_daily_values(data):
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / f"{key}.json").write_text(json.dumps(data))
        except OSError as exc:  # noqa: BLE001 -- degrade, never let caching break a fetch
            logger.warning("cache write failed for %s, continuing without caching: %s", cache_dir, exc)

    return data


def _parse_daily(data: dict) -> list[tuple[str, float | None, float | None]]:
    daily = data["daily"]
    return list(
        zip(
            daily["time"],
            daily["temperature_2m_max"],
            daily["temperature_2m_min"],
        )
    )


def _fetch_archive(
    lat: float, lon: float, start: date, end: date, cache_dir: Path | None
) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": DAILY_FIELDS,
        "timezone": TIMEZONE,
    }
    key = _cache_key("archive", lat, lon, start.isoformat(), end.isoformat())
    return _fetch(ARCHIVE_URL, params, cache_dir, key)


def _fetch_forecast_past_days(
    lat: float, lon: float, past_days: int, cache_dir: Path | None
) -> dict:
    past_days = min(max(past_days, 1), FORECAST_MAX_PAST_DAYS)
    params = {
        "latitude": lat,
        "longitude": lon,
        "past_days": past_days,
        "forecast_days": 1,
        "daily": DAILY_FIELDS,
        "timezone": TIMEZONE,
    }
    key = _cache_key("forecast_past", lat, lon, str(past_days), "")
    return _fetch(FORECAST_URL, params, cache_dir, key)


def _fetch_forecast_forward(
    lat: float, lon: float, start: date, end: date, cache_dir: Path | None
) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": DAILY_FIELDS,
        "timezone": TIMEZONE,
    }
    key = _cache_key("forecast_forward", lat, lon, start.isoformat(), end.isoformat())
    return _fetch(FORECAST_URL, params, cache_dir, key)


def get_daily_temperatures(
    lat: float,
    lon: float,
    start_date: date,
    end_date: date,
    *,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    today: date | None = None,
) -> list[DailyTemperature]:
    """Continuous, gap-free, deduplicated daily max/min temperatures for
    [start_date, end_date], bridging Archive (historical) and Forecast
    (future) as needed.

    `today` is injectable for testing; defaults to the real current date.

    Raises WeatherError if any requested date can't be resolved (e.g. it's
    beyond the 16-day forecast horizon -- there is no climatology fallback
    yet, see ADR-001).
    """
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")
    today = today or date.today()

    days: dict[str, DailyTemperature] = {}

    archive_end = min(end_date, today)
    if start_date <= archive_end:
        archive_data = _fetch_archive(lat, lon, start_date, archive_end, cache_dir)
        missing_dates: list[str] = []
        for d, t_max, t_min in _parse_daily(archive_data):
            if t_max is None or t_min is None:
                missing_dates.append(d)
            else:
                days[d] = DailyTemperature(date=d, t_max_c=t_max, t_min_c=t_min)

        if missing_dates:
            earliest_missing = date.fromisoformat(min(missing_dates))
            past_days = (today - earliest_missing).days + 1
            bridge_data = _fetch_forecast_past_days(lat, lon, past_days, cache_dir)
            bridge_rows = {
                d: (t_max, t_min) for d, t_max, t_min in _parse_daily(bridge_data)
            }
            for d in missing_dates:
                row = bridge_rows.get(d)
                if row is None or row[0] is None or row[1] is None:
                    raise WeatherError(
                        f"Could not bridge missing historical day {d}: "
                        f"absent from both Archive and Forecast past_days."
                    )
                days[d] = DailyTemperature(date=d, t_max_c=row[0], t_min_c=row[1])

    if end_date > today:
        horizon_end = today + timedelta(days=FORECAST_MAX_HORIZON_DAYS - 1)
        if start_date > today and start_date > horizon_end:
            raise WeatherError(
                f"{start_date} is beyond the {FORECAST_MAX_HORIZON_DAYS}-day "
                f"forecast horizon and there is no climatology fallback yet "
                f"(see ADR-001)."
            )
        forecast_start = max(start_date, today + timedelta(days=1))
        forecast_end = min(end_date, horizon_end)
        forecast_data = _fetch_forecast_forward(
            lat, lon, forecast_start, forecast_end, cache_dir
        )
        for d, t_max, t_min in _parse_daily(forecast_data):
            if t_max is None or t_min is None:
                raise WeatherError(f"Forecast API returned null for {d}")
            days[d] = DailyTemperature(date=d, t_max_c=t_max, t_min_c=t_min)

        if forecast_end < end_date:
            raise WeatherError(
                f"No climatology fallback yet for dates beyond the forecast "
                f"horizon ({forecast_end} < {end_date}). See ADR-001."
            )

    ordered = sorted(days.values(), key=lambda d: d.date)
    _assert_no_gaps(ordered, start_date, end_date)
    return ordered


def get_historical_daily(
    lat: float,
    lon: float,
    start_date: date,
    end_date: date,
    *,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> tuple[list[DailyTemperature], list[ForecastDay]]:
    """Temperature AND precipitation for a fully historical date range, in
    one Archive API call. Confirmed live that Open-Meteo's Archive API
    returns both `temperature_2m_max`/`temperature_2m_min` and
    `precipitation_sum` together for a past date range -- see
    docs/adr/ADR-009-harvest-lifecycle-and-validation.md Decision 2. Built
    for scripts/backtest_2025_kuruvai.py, which needs both series for the
    same real past dates and has no reason to pay for two separate calls
    where one already carries both fields.

    Archive-only, no forecast bridging: callers must ensure `end_date` is
    safely in the past. The backtest always is, by construction -- it
    replays a prior season, so every date it asks for has long settled.
    Raises WeatherError on any null day (a genuine gap, not a lagging tail
    -- there is no "not computed yet" case for data this old).
    """
    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": f"{DAILY_FIELDS},{PRECIPITATION_FIELD}",
        "timezone": TIMEZONE,
    }
    key = _cache_key(
        "archive_full", lat, lon, start_date.isoformat(), end_date.isoformat()
    )
    data = _fetch(ARCHIVE_URL, params, cache_dir, key)
    daily = data["daily"]
    rows = list(
        zip(
            daily["time"],
            daily["temperature_2m_max"],
            daily["temperature_2m_min"],
            daily[PRECIPITATION_FIELD],
        )
    )

    temperatures: list[DailyTemperature] = []
    precipitation: list[ForecastDay] = []
    for d, t_max, t_min, precip in rows:
        if t_max is None or t_min is None or precip is None:
            raise WeatherError(
                f"Archive API returned null data for {d} in "
                f"{start_date}..{end_date} (temp_max={t_max}, "
                f"temp_min={t_min}, precipitation={precip})"
            )
        temperatures.append(DailyTemperature(date=d, t_max_c=t_max, t_min_c=t_min))
        precipitation.append(ForecastDay(date=d, precipitation_mm=precip))

    return temperatures, precipitation


def _assert_no_gaps(
    ordered: list[DailyTemperature], start_date: date, end_date: date
) -> None:
    expected = start_date
    for entry in ordered:
        actual = date.fromisoformat(entry.date)
        if actual != expected:
            raise WeatherError(
                f"Gap or duplicate in daily temperature series: expected "
                f"{expected}, got {actual}."
            )
        expected += timedelta(days=1)
    if expected != end_date + timedelta(days=1):
        raise WeatherError(
            f"Daily temperature series incomplete: stops before {end_date}."
        )


def get_precipitation_forecast(
    lat: float,
    lon: float,
    start_date: date,
    end_date: date,
    *,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> list[ForecastDay]:
    """Daily precipitation forecast for [start_date, end_date], for
    scheduling/capacity.py's rain-window budget. Forward-looking only
    (Forecast API, up to the 16-day horizon) -- capacity planning is about
    the window ahead, not historical rain.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": PRECIPITATION_FIELD,
        "timezone": TIMEZONE,
    }
    key = _cache_key(
        "precipitation", lat, lon, start_date.isoformat(), end_date.isoformat()
    )
    data = _fetch(FORECAST_URL, params, cache_dir, key)
    daily = data["daily"]
    entries = list(zip(daily["time"], daily[PRECIPITATION_FIELD]))

    # Open-Meteo computes near-term forecast days first; the tail of a
    # long-horizon request can come back null simply because that day's
    # model run hasn't completed yet -- confirmed live requesting the
    # full 16-day FORECAST_MAX_HORIZON_DAYS window, where day 16 was
    # reproducibly null for several minutes while days 1-15 were real
    # data. Trimming only a TRAILING run of nulls treats "not computed
    # yet" as "don't rely on it" (a shorter, still-real usable window),
    # not fabricated data -- a null anywhere else in the series is a
    # genuine gap/anomaly, not this, and still fails loud below.
    trimmed = 0
    while entries and entries[-1][1] is None:
        entries.pop()
        trimmed += 1
    if trimmed:
        logger.warning(
            "FORECAST HORIZON TRUNCATED: %d trailing day(s) not yet "
            "computed by Open-Meteo, proceeding with %d real day(s) "
            "instead of the requested %s..%s window",
            trimmed, len(entries), start_date, end_date,
        )
    if not entries:
        raise WeatherError(
            f"Forecast API returned no usable precipitation data for "
            f"{start_date}..{end_date}"
        )

    result = []
    for d, precip in entries:
        if precip is None:
            raise WeatherError(f"Forecast API returned null precipitation for {d}")
        result.append(ForecastDay(date=d, precipitation_mm=precip))
    return result
