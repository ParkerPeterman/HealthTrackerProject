"""A7: weather from Open-Meteo, the third and last approved outbound call.

Open-Meteo is free for non-commercial use and needs no API key; it asks for attribution
("Weather data by Open-Meteo.com") on any view that uses it, and asks apps above 10,000
requests a day to get in touch (this makes a handful a day).

What leaves the machine: a latitude and longitude rounded to 0.1 degree (about 11 km),
dates, and the list of variables. Nothing else. The point is my home location from .env,
or, on a day I tagged as travel, the session's start point (already rounded on ingest).

  * forecast endpoint, past 7 days + next 7: recent sessions (the archive lags several
    days) and the planner's week
  * archive endpoint (ERA5 reanalysis): sessions older than the lag, replacing the
    forecast's stand-in values

Every response is stored in raw.ingest; core.weather_hourly and core.weather_daily are
rebuilt from those payloads alone, so recomputing gives the same result. Archive rows
always win over forecast rows; among the same kind, the newest response wins.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg

from healthtracker.config import get_settings
from healthtracker.db import execute, fetch_all
from healthtracker.ingest.raw import store_raw
from healthtracker.profile import load_profile

log = logging.getLogger("healthtracker.weather")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = ("temperature_2m", "dew_point_2m", "wind_speed_10m", "precipitation", "snowfall", "weather_code")
DAILY = ("temperature_2m_max", "temperature_2m_min", "precipitation_sum", "snowfall_sum", "wind_speed_10m_max",
         "weather_code")
ATTRIBUTION = "Weather data by Open-Meteo.com"
OUTDOOR_SPORTS = {"run", "ultimate", "speed", "sprints", "plyometrics", "bike", "throwing", "walk", "hike"}


def rounded(lat: float | None, lon: float | None) -> tuple[float, float] | None:
    if lat is None or lon is None:
        return None
    return round(float(lat), 1), round(float(lon), 1)


def home_point() -> tuple[float, float] | None:
    s = get_settings()
    return rounded(s.home_lat, s.home_lon)


def request_params(point: tuple[float, float], tz: str, kind: str, start: date | None = None,
                   end: date | None = None) -> dict:
    """The complete request: a rounded point, dates, variable names, units. Nothing else."""
    lat, lon = rounded(*point)
    p = {"latitude": lat, "longitude": lon, "hourly": ",".join(HOURLY), "daily": ",".join(DAILY),
         "timezone": tz, "timeformat": "unixtime", "wind_speed_unit": "kmh"}
    if kind == "forecast":
        p.update({"past_days": 7, "forecast_days": 7})
    else:
        p.update({"start_date": start.isoformat(), "end_date": end.isoformat()})
    return p


def fetch(client: httpx.Client, point: tuple[float, float], tz: str, kind: str, start: date | None = None,
          end: date | None = None) -> dict:
    params = request_params(point, tz, kind, start, end)
    r = client.get(FORECAST_URL if kind == "forecast" else ARCHIVE_URL, params=params, timeout=30)
    r.raise_for_status()
    return {"request": params, "kind": kind, "point": list(rounded(*point)), "response": r.json()}


# ------------------------------------------------------------------ parsing (pure)
def parse(payload: dict, tz: str) -> tuple[list[dict], list[dict]]:
    """Hourly and daily rows from one stored payload, keyed by the rounded point that was asked for."""
    from zoneinfo import ZoneInfo

    lat, lon = payload["point"]
    source = "archive" if payload["kind"] == "archive" else "forecast"
    res = payload["response"]
    hourly, daily = [], []
    h = res.get("hourly") or {}
    for i, t in enumerate(h.get("time") or []):
        def v(key: str, i: int = i):
            vals = h.get(key) or []
            return vals[i] if i < len(vals) else None
        if v("temperature_2m") is None:
            continue
        hourly.append({"lat": lat, "lon": lon, "ts": datetime.fromtimestamp(int(t), UTC),
                       "temp_c": v("temperature_2m"), "dew_point_c": v("dew_point_2m"),
                       "wind_kmh": v("wind_speed_10m"), "precip_mm": v("precipitation"),
                       "snowfall_cm": v("snowfall"), "weather_code": v("weather_code"), "source": source})
    dd = res.get("daily") or {}
    zone = ZoneInfo(tz)
    for i, t in enumerate(dd.get("time") or []):
        def v(key: str, i: int = i):
            vals = dd.get(key) or []
            return vals[i] if i < len(vals) else None
        if v("temperature_2m_max") is None:
            continue
        local = datetime.fromtimestamp(int(t), UTC).astimezone(zone).date()
        daily.append({"lat": lat, "lon": lon, "date": local, "tmin_c": v("temperature_2m_min"),
                      "tmax_c": v("temperature_2m_max"), "precip_mm": v("precipitation_sum"),
                      "snowfall_cm": v("snowfall_sum"), "wind_max_kmh": v("wind_speed_10m_max"),
                      "weather_code": v("weather_code"), "source": source})
    return hourly, daily


HOURLY_UPSERT = """
    INSERT INTO core.weather_hourly (lat, lon, ts, temp_c, dew_point_c, wind_kmh, precip_mm, snowfall_cm,
                                     weather_code, source)
    VALUES (%(lat)s, %(lon)s, %(ts)s, %(temp_c)s, %(dew_point_c)s, %(wind_kmh)s, %(precip_mm)s, %(snowfall_cm)s,
            %(weather_code)s, %(source)s)
    ON CONFLICT (lat, lon, ts) DO UPDATE SET temp_c = EXCLUDED.temp_c, dew_point_c = EXCLUDED.dew_point_c,
        wind_kmh = EXCLUDED.wind_kmh, precip_mm = EXCLUDED.precip_mm, snowfall_cm = EXCLUDED.snowfall_cm,
        weather_code = EXCLUDED.weather_code, source = EXCLUDED.source
    WHERE core.weather_hourly.source = 'forecast' OR EXCLUDED.source = 'archive'"""
DAILY_UPSERT = """
    INSERT INTO core.weather_daily (lat, lon, date, tmin_c, tmax_c, precip_mm, snowfall_cm, wind_max_kmh,
                                    weather_code, source)
    VALUES (%(lat)s, %(lon)s, %(date)s, %(tmin_c)s, %(tmax_c)s, %(precip_mm)s, %(snowfall_cm)s, %(wind_max_kmh)s,
            %(weather_code)s, %(source)s)
    ON CONFLICT (lat, lon, date) DO UPDATE SET tmin_c = EXCLUDED.tmin_c, tmax_c = EXCLUDED.tmax_c,
        precip_mm = EXCLUDED.precip_mm, snowfall_cm = EXCLUDED.snowfall_cm, wind_max_kmh = EXCLUDED.wind_max_kmh,
        weather_code = EXCLUDED.weather_code, source = EXCLUDED.source
    WHERE core.weather_daily.source = 'forecast' OR EXCLUDED.source = 'archive'"""


def load_weather(conn: psycopg.Connection) -> int:
    """Rebuild the weather tables from every cached response: forecasts first, then the archive."""
    tz = load_profile(conn).timezone
    rows = fetch_all(conn, """SELECT payload FROM raw.ingest WHERE source = 'open_meteo'
                              ORDER BY endpoint = 'archive', fetched_at, id""")
    execute(conn, "DELETE FROM core.weather_hourly")
    execute(conn, "DELETE FROM core.weather_daily")
    n = 0
    with conn.cursor() as cur:
        for r in rows:
            hourly, daily = parse(r["payload"], tz)
            if hourly:
                cur.executemany(HOURLY_UPSERT, hourly)
            if daily:
                cur.executemany(DAILY_UPSERT, daily)
            n += len(hourly) + len(daily)
    return n


# ------------------------------------------------------------------ what to fetch
def session_points(conn: psycopg.Connection, since: date, home: tuple[float, float] | None) -> dict:
    """{point: [dates]} for outdoor sessions: home, or the rounded start point on a travel day."""
    tz = load_profile(conn).timezone
    rows = fetch_all(conn, """SELECT (w.start_ts AT TIME ZONE %(tz)s)::date AS date, w.sport, w.start_lat, w.start_lon,
                                     EXISTS (SELECT 1 FROM core.behavior_event e WHERE e.type_code = 'travel'
                                             AND (e.ts AT TIME ZONE %(tz)s)::date = (w.start_ts AT TIME ZONE %(tz)s)::date)
                                       AS travel
                              FROM core.v_workout w WHERE w.start_ts >= %(since)s::date""", {"tz": tz, "since": since})
    out: dict[tuple[float, float], set[date]] = {}
    for r in rows:
        point = session_point(r, home)
        if point:
            out.setdefault(point, set()).add(r["date"])
    return out


def session_point(w: dict, home: tuple[float, float] | None) -> tuple[float, float] | None:
    """Outdoor sessions only: GPS runs, and manual games and field sessions (assumed outdoors).
    A run with no start point was on a treadmill."""
    gps = rounded(w.get("start_lat"), w.get("start_lon"))
    if w["sport"] not in OUTDOOR_SPORTS or (w["sport"] in ("run", "bike", "walk", "hike") and gps is None):
        return None
    return gps if (w.get("travel") and gps) else home


def archive_needed(conn: psycopg.Connection, point: tuple[float, float], dates: set[date], before: date) -> list[date]:
    have = {r["d"] for r in fetch_all(conn, """SELECT DISTINCT (ts AT TIME ZONE 'UTC')::date AS d FROM core.weather_hourly
                                              WHERE lat = %s AND lon = %s AND source = 'archive'""", point)}
    return sorted(d for d in dates if d < before and d not in have)


def pull_weather(conn: psycopg.Connection, today: date, client: httpx.Client | None = None, days: int = 60) -> int:
    """Nightly: the forecast for home (and recent travel points), then archive gaps for sessions."""
    s = get_settings()
    home = home_point()
    if not s.weather_enabled or home is None:
        log.info("weather off or HOME_LAT/HOME_LON not set; skipping Open-Meteo")
        return 0
    from healthtracker.rules.context import load_params

    lag = int(load_params(conn).get("ANALYSIS_WEATHER", {}).get("archive_lag_days", 6))
    tz = load_profile(conn).timezone
    points = session_points(conn, today - timedelta(days=days), home)
    recent = {p for p, ds in points.items() if max(ds) >= today - timedelta(days=lag)}
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": "healthtracker (personal, non-commercial)"})
    n = 0
    try:
        for point in sorted({home} | recent):
            n += store_raw(conn, "open_meteo", "forecast", today, fetch(client, point, tz, "forecast"))
        for point, ds in sorted(points.items()):
            gaps = archive_needed(conn, point, ds, today - timedelta(days=lag))
            if gaps:
                n += store_raw(conn, "open_meteo", "archive", gaps[0],
                               fetch(client, point, tz, "archive", gaps[0], gaps[-1]))
    finally:
        if own:
            client.close()
    return n + load_weather(conn)


# ------------------------------------------------------------------ lookups
def weather_at(conn: psycopg.Connection, point: tuple[float, float], ts: datetime) -> dict | None:
    """The hour nearest `ts` at a point (within 90 minutes)."""
    rows = fetch_all(conn, """SELECT temp_c, dew_point_c, wind_kmh, precip_mm, source FROM core.weather_hourly
                              WHERE lat = %s AND lon = %s AND ts BETWEEN %s - interval '90 minutes'
                                AND %s + interval '90 minutes'
                              ORDER BY abs(extract(epoch FROM ts - %s)) LIMIT 1""", (*point, ts, ts, ts))
    return rows[0] if rows else None


def daily_forecast(conn: psycopg.Connection, start: date, end: date) -> dict[date, dict]:
    home = home_point()
    if home is None:
        return {}
    rows = fetch_all(conn, """SELECT * FROM core.weather_daily WHERE lat = %s AND lon = %s AND date BETWEEN %s AND %s""",
                     (*home, start, end))
    return {r["date"]: r for r in rows}


# WMO weather codes for freezing drizzle/rain, snow, snow grains, snow showers.
ICY_CODES = {56, 57, 66, 67, 71, 73, 75, 77, 85, 86}


def bad_for_outdoors(day: dict, p: dict) -> str | None:
    """Why a day's forecast moves outdoor quality work inside, or None."""
    tmin, tmax = day.get("tmin_c"), day.get("tmax_c")
    if tmax is not None and tmax <= p.get("very_cold_tmax_c", -10):
        return f"very cold (high of {tmax:.0f}°C)"
    wet = (day.get("precip_mm") or 0) >= p.get("icy_precip_mm", 1) or (day.get("snowfall_cm") or 0) > 0
    if (day.get("weather_code") in ICY_CODES) or (wet and tmin is not None and tmin <= p.get("icy_tmin_c", 0)):
        return f"icy (low of {tmin:.0f}°C with precipitation)" if tmin is not None else "icy"
    return None
