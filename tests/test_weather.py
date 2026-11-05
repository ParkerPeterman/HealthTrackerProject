"""A7: Open-Meteo weather. What is sent, how it is parsed and cached, and where it is used."""

from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from healthtracker.ingest import weather as wx

TZ = "America/Chicago"
HOME = (41.9, -87.6)


def payload(kind: str, point, start: datetime, hours: int, temp: float, daily_low=None) -> dict:
    t0 = int(start.timestamp())
    times = [t0 + 3600 * i for i in range(hours)]
    days = sorted({datetime.fromtimestamp(t, UTC).astimezone(ZoneInfo(TZ)).date() for t in times})
    midnights = [int(datetime.combine(d, time(0), ZoneInfo(TZ)).timestamp()) for d in days]
    return {"kind": kind, "point": list(point), "request": {},
            "response": {"hourly": {"time": times, "temperature_2m": [temp] * hours, "dew_point_2m": [temp - 4] * hours,
                                    "wind_speed_10m": [12.0] * hours, "precipitation": [0.0] * hours,
                                    "snowfall": [0.0] * hours, "weather_code": [3] * hours},
                         "daily": {"time": midnights, "temperature_2m_max": [temp + 3] * len(days),
                                   "temperature_2m_min": [daily_low if daily_low is not None else temp - 3] * len(days),
                                   "precipitation_sum": [0.0] * len(days), "snowfall_sum": [0.0] * len(days),
                                   "wind_speed_10m_max": [20.0] * len(days), "weather_code": [3] * len(days)}}}


def test_requests_send_only_a_rounded_point_and_dates():
    p = wx.request_params((41.8781, -87.6298), TZ, "forecast")
    assert (p["latitude"], p["longitude"]) == (41.9, -87.6)
    assert set(p) == {"latitude", "longitude", "hourly", "daily", "timezone", "timeformat", "wind_speed_unit",
                      "past_days", "forecast_days"}
    a = wx.request_params(HOME, TZ, "archive", date(2026, 10, 1), date(2026, 10, 5))
    assert a["start_date"] == "2026-10-01" and "past_days" not in a


def test_parse_hourly_utc_and_daily_local():
    start = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)  # 00:00 Chicago (CDT -> CST that day)
    hourly, daily = wx.parse(payload("forecast", HOME, start, 30, 8.0), TZ)
    assert len(hourly) == 30 and hourly[0]["ts"] == start and hourly[0]["source"] == "forecast"
    assert [d["date"] for d in daily] == [date(2026, 11, 1), date(2026, 11, 2)]


def test_outdoor_sessions_and_their_point():
    assert wx.session_point({"sport": "run", "start_lat": 41.93, "start_lon": -87.64}, HOME) == HOME
    travel = wx.session_point({"sport": "run", "start_lat": 45.53, "start_lon": -122.68, "travel": True}, HOME)
    assert travel == (45.5, -122.7)
    assert wx.session_point({"sport": "run", "start_lat": None, "start_lon": None}, HOME) is None  # treadmill
    assert wx.session_point({"sport": "ultimate", "start_lat": None, "start_lon": None}, HOME) == HOME
    assert wx.session_point({"sport": "strength", "start_lat": None, "start_lon": None}, HOME) is None


def test_icy_and_very_cold():
    p = {"very_cold_tmax_c": -10, "icy_tmin_c": 0, "icy_precip_mm": 1}
    assert wx.bad_for_outdoors({"tmin_c": -15, "tmax_c": -11}, p).startswith("very cold")
    assert wx.bad_for_outdoors({"tmin_c": -2, "tmax_c": 2, "precip_mm": 3}, p).startswith("icy")
    assert wx.bad_for_outdoors({"tmin_c": 4, "tmax_c": 9, "precip_mm": 3}, p) is None
    assert wx.bad_for_outdoors({"tmin_c": 1, "tmax_c": 3, "weather_code": 66}, p) == "icy (low of 1°C with precipitation)"


@pytest.mark.db
def test_archive_wins_and_sessions_get_weather(seeded, tx, monkeypatch):
    from healthtracker.db import fetch_all, fetch_one
    from healthtracker.features.sessions import session_rows
    from healthtracker.ingest.raw import store_raw

    conn = tx
    conn.execute("DELETE FROM raw.ingest WHERE source = 'open_meteo'")  # the demo's synthetic weather
    monkeypatch.setattr(wx, "get_settings", lambda: SimpleNamespace(home_lat=41.88, home_lon=-87.63, weather_enabled=True))
    run = fetch_one(conn, """SELECT w.* FROM core.v_workout w WHERE w.sport = 'run' AND w.start_lat IS NOT NULL
                             ORDER BY start_ts DESC LIMIT 1""")
    if run is None:  # the demo's Garmin runs may carry no start point; give one a point near home
        run = fetch_one(conn, "SELECT * FROM core.v_workout WHERE sport = 'run' ORDER BY start_ts DESC LIMIT 1")
        conn.execute("UPDATE core.workout SET start_lat = 41.9, start_lon = -87.6 WHERE id = %s", (run["id"],))
    start = run["start_ts"].astimezone(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=6)
    store_raw(conn, "open_meteo", "archive", start.date(), payload("archive", HOME, start, 24, 5.0))
    store_raw(conn, "open_meteo", "forecast", start.date(), payload("forecast", HOME, start, 24, 9.0))  # newer, but forecast
    n = wx.load_weather(conn)
    assert n > 0
    rows = fetch_all(conn, "SELECT DISTINCT source, temp_c FROM core.weather_hourly")
    assert rows == [{"source": "archive", "temp_c": 5.0}]
    row = session_rows(conn, [run["id"]])[0]
    assert row["temp_c"] == 5.0 and row["dew_point_c"] == 1.0 and row["weather_source"] == "archive"
    assert wx.load_weather(conn) == n  # rebuilt from the cache: same result


@pytest.mark.db
def test_pull_calls_only_open_meteo(seeded, tx, monkeypatch):
    from healthtracker.db import fetch_one

    conn = tx
    conn.execute("DELETE FROM raw.ingest WHERE source = 'open_meteo'")  # the demo's synthetic weather
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        start = datetime(2026, 6, 23, 5, tzinfo=UTC)
        body = payload("forecast", HOME, start, 24 * 14, 20.0)["response"]
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(wx, "get_settings", lambda: SimpleNamespace(home_lat=None, home_lon=None, weather_enabled=True))
    assert wx.pull_weather(conn, seeded["end"], client=client) == 0 and not seen  # no home location: no call
    monkeypatch.setattr(wx, "get_settings", lambda: SimpleNamespace(home_lat=41.88, home_lon=-87.63, weather_enabled=True))
    assert wx.pull_weather(conn, seeded["end"], client=client) > 0
    assert seen and {r.url.host for r in seen} <= {"api.open-meteo.com", "archive-api.open-meteo.com"}
    for r in seen:
        assert float(r.url.params["latitude"]) == 41.9 and float(r.url.params["longitude"]) == -87.6
    assert fetch_one(conn, "SELECT count(*) AS n FROM raw.ingest WHERE source = 'open_meteo'")["n"] == len(seen)


@pytest.mark.db
def test_planner_moves_quality_work_indoors(db_url):
    from healthtracker.db import connect, use_database
    from healthtracker.planning.library import DayState, exercise_meta, load_library
    from healthtracker.planning.weekly import PlanInputs, build_plan, render_plan

    use_database(db_url)
    with connect() as c:
        lib, meta = load_library(c), exercise_meta(c)
    monday = date(2026, 11, 16)
    states = {monday + timedelta(days=i): DayState(monday + timedelta(days=i)) for i in range(7)}
    roomy = {"run": 900.0, "sprint": 900.0, "plyo": 200.0, "total": 3000.0}
    weather = {monday: {"tmin_c": -18, "tmax_c": -12}, monday + timedelta(days=2): {"tmin_c": -1, "tmax_c": 1, "precip_mm": 4}}
    plan = build_plan(PlanInputs(week_start=monday, phase="base", states=states, last_week=roomy, weather=weather),
                      lib, meta)
    codes = {(s["date"].weekday(), s["session_code"]) for s in plan["sessions"]}
    assert not any(d == 0 and c in ("accel_sprints", "maxv_sprints") for d, c in codes)  # very cold: no sprints
    assert (0, "strength_lower_heavy") in codes  # Monday stays a strength day
    vo2 = [s for s in plan["sessions"] if s["session_code"] == "vo2_3x3"][0]
    assert "treadmill or bike" in vo2["reason"]["changes"][-1]
    text = render_plan(plan)
    assert "Weather data by Open-Meteo.com" in text and "very cold" in text
