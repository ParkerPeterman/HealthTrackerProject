"""raw Garmin payloads -> core tables.

Parsers are pure functions (payload in, row dict out) so they can be tested
against fixtures. Field names follow Garmin Connect's web API; every lookup is
defensive because the API is unofficial and fields come and go.
"""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg

from healthtracker.db import fetch_all, fetch_one, upsert
from healthtracker.ingest.raw import latest_payloads

SPORT_MAP = {
    "running": "run", "treadmill_running": "run", "trail_running": "run",
    "track_running": "run", "street_running": "run", "virtual_run": "run",
    "strength_training": "strength", "cardio_training": "cardio", "hiit": "hiit",
    "cycling": "bike", "road_biking": "bike", "indoor_cycling": "bike",
    "mountain_biking": "bike", "gravel_cycling": "bike", "virtual_ride": "bike",
    "walking": "walk", "hiking": "hike", "lap_swimming": "swim", "open_water_swimming": "swim",
    "yoga": "yoga", "pilates": "yoga", "soccer": "soccer", "basketball": "basketball",
}


# ------------------------------------------------------------------ helpers
def _get(d: Any, *path: str, default: Any = None) -> Any:
    for key in path:
        if not isinstance(d, dict):
            return default
        d = d.get(key)
        if d is None:
            return default
    return d


def _num(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    f = _num(v)
    return None if f is None else int(round(f))


def _minutes(seconds: Any) -> int | None:
    f = _num(seconds)
    return None if f is None else int(round(f / 60))


def ms_to_ts(ms: Any) -> datetime | None:
    f = _num(ms)
    return None if f is None else datetime.fromtimestamp(f / 1000, tz=UTC)


def gmt_str_to_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.replace("T", " ").split(".")[0]
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)


def _nonneg(v: Any) -> int | None:
    """Garmin uses -1/-2 as 'not enough data' sentinels for stress and similar."""
    i = _int(v)
    return None if i is None or i < 0 else i


def normalize_rpe(v: Any) -> int | None:
    """Garmin stores perceived effort as 10..100 (10 per step); some payloads use 1..10."""
    f = _num(v)
    if f is None or f <= 0:
        return None
    if f > 10:
        f = f / 10
    return max(1, min(10, int(round(f))))


def normalize_feel(v: Any) -> int | None:
    """Garmin 'feel' is 0, 25, 50, 75, 100 (very weak .. very strong) -> 1..5."""
    f = _num(v)
    if f is None or f < 0:
        return None
    if f <= 5 and f == int(f) and f >= 1:  # already 1..5
        return int(f)
    return max(1, min(5, int(round(f / 25)) + 1))


def map_sport(type_key: str | None, name: str | None = None) -> str:
    if name and "ultimate" in name.lower():
        return "ultimate"
    if not type_key:
        return "other"
    return SPORT_MAP.get(type_key, type_key)


# ------------------------------------------------------------------ parsers
def parse_sleep(payload: dict, for_date: date, hrv_payload: dict | None = None) -> dict | None:
    dto = payload.get("dailySleepDTO") or {}
    total_s = _num(dto.get("sleepTimeSeconds"))
    if not dto or not total_s:
        return None
    hrv_summary = (hrv_payload or {}).get("hrvSummary") or {}
    cal = dto.get("calendarDate")
    return {
        "date": date.fromisoformat(cal) if cal else for_date,
        "sleep_start": ms_to_ts(dto.get("sleepStartTimestampGMT")),
        "sleep_end": ms_to_ts(dto.get("sleepEndTimestampGMT")),
        "total_min": _minutes(total_s),
        "deep_min": _minutes(dto.get("deepSleepSeconds")),
        "rem_min": _minutes(dto.get("remSleepSeconds")),
        "light_min": _minutes(dto.get("lightSleepSeconds")),
        "awake_min": _minutes(dto.get("awakeSleepSeconds")),
        "sleep_score": _int(_get(dto, "sleepScores", "overall", "value")),
        "overnight_hrv": _num(payload.get("avgOvernightHrv")) or _num(hrv_summary.get("lastNightAvg")),
        "hrv_status": payload.get("hrvStatus") or hrv_summary.get("status"),
        "resting_hr": _int(payload.get("restingHeartRate")),
        "avg_spo2": _num(dto.get("averageSpO2Value")),
        "avg_resp_rate": _num(dto.get("averageRespirationValue")),
        "source": "garmin",
    }


def wear_minutes(hr_payload: dict | None) -> int | None:
    """Minutes with all-day HR data: samples present x sampling interval."""
    values = (hr_payload or {}).get("heartRateValues") or []
    ts = [v[0] for v in values if isinstance(v, list | tuple) and len(v) >= 2]
    if len(ts) < 2:
        return 0 if hr_payload is not None else None
    deltas = [b - a for a, b in zip(ts, ts[1:], strict=False) if b > a]
    interval_min = (statistics.median(deltas) / 60000) if deltas else 2
    present = sum(1 for v in values if len(v) >= 2 and v[1] is not None)
    return int(min(1440, round(present * interval_min)))


def parse_vo2max(max_metrics: Any) -> float | None:
    items = max_metrics if isinstance(max_metrics, list) else [max_metrics]
    for item in items:
        v = _get(item, "generic", "vo2MaxPreciseValue") or _get(item, "generic", "vo2MaxValue")
        if v:
            return round(float(v), 1)
    return None


def parse_daily_summary(
    payload: dict | None, for_date: date, hr_payload: dict | None = None, max_metrics: Any = None
) -> dict | None:
    if not payload and not hr_payload:
        return None
    p = payload or {}
    return {
        "date": for_date,
        "steps": _int(p.get("totalSteps")),
        "active_kcal": _int(p.get("activeKilocalories")),
        "total_kcal": _int(p.get("totalKilocalories")),
        "resting_hr": _int(p.get("restingHeartRate")) or _int((hr_payload or {}).get("restingHeartRate")),
        "avg_stress": _nonneg(p.get("averageStressLevel")),
        "body_battery_high": _nonneg(p.get("bodyBatteryHighestValue")),
        "body_battery_low": _nonneg(p.get("bodyBatteryLowestValue")),
        "intensity_min_mod": _int(p.get("moderateIntensityMinutes")),
        "intensity_min_vig": _int(p.get("vigorousIntensityMinutes")),
        "wear_minutes": wear_minutes(hr_payload),
        "vo2max": parse_vo2max(max_metrics) if max_metrics else None,
        "source": "garmin",
    }


def parse_activity(item: dict, detail: dict | None = None) -> dict:
    summary = (detail or {}).get("summaryDTO") or {}
    type_key = _get(item, "activityType", "typeKey")
    rpe = normalize_rpe(summary.get("directWorkoutRpe", item.get("directWorkoutRpe")))
    feel = normalize_feel(summary.get("directWorkoutFeel", item.get("directWorkoutFeel")))
    duration = _num(item.get("duration")) or _num(summary.get("duration")) or 0
    return {
        "source": "garmin",
        "source_id": str(item["activityId"]),
        "sport": map_sport(type_key, item.get("activityName")),
        "start_ts": gmt_str_to_ts(item.get("startTimeGMT")),
        "duration_s": max(1, int(round(duration))),
        "distance_m": _num(item.get("distance")),
        "avg_hr": _int(item.get("averageHR")),
        "max_hr": _int(item.get("maxHR")),
        "avg_cadence": _int(item.get("averageRunningCadenceInStepsPerMinute"))
        or _int(item.get("averageBikingCadenceInRevPerMinute")),
        "elev_gain_m": _num(item.get("elevationGain")),
        "kcal": _int(item.get("calories")),
        "device_load": _num(item.get("activityTrainingLoad")),
        "rpe": rpe,
        "rpe_source": "garmin" if rpe else None,
        "feel": feel,
    }


def parse_splits(payload: dict) -> list[dict]:
    rows = []
    for i, lap in enumerate(payload.get("lapDTOs") or [], start=1):
        dist, dur = _num(lap.get("distance")), _num(lap.get("duration"))
        if not dist or not dur:
            continue
        rows.append({
            "split_no": _int(lap.get("lapIndex")) or i,
            "distance_m": round(dist, 1),
            "duration_s": int(round(dur)),
            "avg_hr": _int(lap.get("averageHR")),
            "avg_cadence": _int(lap.get("averageRunCadence")),
        })
    return rows


# ------------------------------------------------------------------ loaders
def load_wellness(conn: psycopg.Connection, start: date | None, end: date | None) -> int:
    def by_date(endpoint: str) -> dict[date, Any]:
        return {r["for_date"]: r["payload"] for r in latest_payloads(conn, "garmin", endpoint, start, end)}

    sleep, hrv = by_date("sleep"), by_date("hrv")
    summary, hr, maxm = by_date("daily_summary"), by_date("heart_rates"), by_date("max_metrics")

    sleep_rows = [r for d, p in sleep.items() if (r := parse_sleep(p, d, hrv.get(d)))]
    n = upsert(conn, "core.sleep", sleep_rows, conflict=["date"])

    days = set(summary) | set(hr)
    well_rows = [
        r for d in sorted(days)
        if (r := parse_daily_summary(summary.get(d), d, hr.get(d), maxm.get(d)))
    ]
    n += upsert(conn, "core.daily_wellness", well_rows, conflict=["date"])
    return n


def load_activities(conn: psycopg.Connection, start: date | None, end: date | None) -> int:
    acts = latest_payloads(conn, "garmin", "activity", start, end, key="activityId")
    details = {
        str(r["payload"].get("activityId")): r["payload"]
        for r in latest_payloads(conn, "garmin", "activity_detail", start, end, key="activityId")
    }
    splits = {
        str(r["payload"].get("activityId")): r["payload"]
        for r in latest_payloads(conn, "garmin", "activity_splits", start, end, key="activityId")
    }
    n = 0
    for r in acts:
        item = r["payload"]
        row = parse_activity(item, details.get(str(item.get("activityId"))))
        if row["start_ts"] is None:
            continue
        # Sport is set once (edits in the app win); RPE only when Garmin has one.
        keep = {"sport"} | ({"rpe", "rpe_source"} if row["rpe"] is None else set())
        update = [c for c in row if c not in keep]
        wid = upsert(conn, "core.workout", [row], conflict=["source", "source_id"],
                     update=update, returning="id")[0]
        n += 1
        sp = splits.get(row["source_id"])
        if sp:
            split_rows = [{"workout_id": wid, **s} for s in parse_splits(sp)]
            n += upsert(conn, "core.run_split", split_rows, conflict=["workout_id", "split_no"])
    return n


def load_fit(conn: psycopg.Connection, start: date | None, end: date | None) -> int:
    """FIT payloads -> hr_sample (and a workout row if the API never delivered one)."""
    n = 0
    for r in latest_payloads(conn, "garmin_fit", "fit_activity", start, end):
        p = r["payload"]
        sess = p.get("session") or {}
        start_ts = datetime.fromisoformat(sess["start_time"]) if sess.get("start_time") else None
        if start_ts is None:
            continue
        wid = None
        if p.get("activityId"):
            got = fetch_one(conn, "SELECT id FROM core.workout WHERE source='garmin' AND source_id=%s",
                            (str(p["activityId"]),))
            wid = got and got["id"]
        if wid is None:
            got = fetch_one(
                conn,
                """SELECT id FROM core.workout WHERE source='garmin'
                   AND abs(extract(epoch FROM start_ts - %s)) < 120 ORDER BY id LIMIT 1""",
                (start_ts,),
            )
            wid = got and got["id"]
        if wid is None:
            row = {
                "source": "garmin",
                "source_id": str(p.get("activityId") or f"fit:{int(start_ts.timestamp())}"),
                "sport": map_sport(sess.get("sport")),
                "start_ts": start_ts,
                "duration_s": max(1, int(round(_num(sess.get("total_timer_time")) or 1))),
                "distance_m": _num(sess.get("total_distance")),
                "avg_hr": _int(sess.get("avg_heart_rate")),
                "max_hr": _int(sess.get("max_heart_rate")),
                "avg_cadence": _int(sess.get("avg_running_cadence") or sess.get("avg_cadence")),
                "elev_gain_m": _num(sess.get("total_ascent")),
                "kcal": _int(sess.get("total_calories")),
            }
            wid = upsert(conn, "core.workout", [row], conflict=["source", "source_id"],
                         update=[], returning="id")
            wid = wid[0] if wid else fetch_one(
                conn, "SELECT id FROM core.workout WHERE source='garmin' AND source_id=%s",
                (row["source_id"],))["id"]
            n += 1
        samples = [(wid, datetime.fromisoformat(ts), hr, spd) for ts, hr, spd in p.get("records") or []]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO core.hr_sample (workout_id, ts, hr, speed_mps) VALUES (%s,%s,%s,%s)
                   ON CONFLICT (workout_id, ts) DO NOTHING""",
                samples,
            )
        n += len(samples)
    return n


def resolve_overlaps(conn: psycopg.Connection, since: date | None = None) -> int:
    """Mark manual workouts that overlap a Garmin workout as superseded (garmin > manual).

    Overlap = at least half of the shorter session. The kept Garmin row inherits
    what only the manual entry knows: lift sets, the game flag, RPE when Garmin
    has none, and notes. Planned-session links follow the kept row.
    """
    since_ts = datetime.combine(since, datetime.min.time(), UTC) - timedelta(days=1) if since else None
    pairs = fetch_all(
        conn,
        """SELECT m.id AS manual_id, g.id AS garmin_id
           FROM core.workout m
           JOIN core.workout g ON g.source = 'garmin' AND m.source = 'manual'
           WHERE (%s::timestamptz IS NULL OR m.start_ts >= %s::timestamptz)
             AND tstzrange(m.start_ts, m.start_ts + make_interval(secs => m.duration_s))
              && tstzrange(g.start_ts, g.start_ts + make_interval(secs => g.duration_s))
             AND extract(epoch FROM
                   least(m.start_ts + make_interval(secs => m.duration_s),
                         g.start_ts + make_interval(secs => g.duration_s))
                 - greatest(m.start_ts, g.start_ts))
                 >= 0.5 * least(m.duration_s, g.duration_s)
           ORDER BY m.id, abs(extract(epoch FROM m.start_ts - g.start_ts))""",
        (since_ts, since_ts),
    )
    chosen: dict[int, int] = {}
    for p in pairs:
        chosen.setdefault(p["manual_id"], p["garmin_id"])
    n = 0
    with conn.cursor() as cur:
        for manual_id, garmin_id in chosen.items():
            cur.execute(
                """UPDATE core.workout g SET
                       is_game = g.is_game OR m.is_game,
                       sport = CASE WHEN m.is_game OR g.sport IN ('other','cardio') THEN m.sport ELSE g.sport END,
                       rpe = COALESCE(g.rpe, m.rpe),
                       rpe_source = CASE WHEN g.rpe IS NULL AND m.rpe IS NOT NULL THEN 'manual' ELSE g.rpe_source END,
                       notes = CASE WHEN m.notes IS NULL THEN g.notes
                                    WHEN g.notes IS NULL THEN m.notes
                                    WHEN position(m.notes in g.notes) > 0 THEN g.notes
                                    ELSE g.notes || E'\n' || m.notes END
                   FROM core.workout m WHERE g.id = %s AND m.id = %s""",
                (garmin_id, manual_id),
            )
            cur.execute(
                """UPDATE core.lift_set s SET workout_id = %s
                   WHERE s.workout_id = %s AND NOT EXISTS (
                       SELECT 1 FROM core.lift_set k WHERE k.workout_id = %s
                       AND k.exercise_id = s.exercise_id AND k.set_no = s.set_no)""",
                (garmin_id, manual_id, garmin_id),
            )
            cur.execute("UPDATE core.planned_session SET linked_workout_id = %s WHERE linked_workout_id = %s",
                        (garmin_id, manual_id))
            cur.execute("UPDATE core.workout SET superseded_by = %s WHERE id = %s AND superseded_by IS DISTINCT FROM %s",
                        (garmin_id, manual_id, garmin_id))
            n += cur.rowcount
        # A manual row that no longer overlaps anything (edited times) counts again.
        cur.execute(
            """UPDATE core.workout SET superseded_by = NULL
               WHERE source = 'manual' AND superseded_by IS NOT NULL
                 AND NOT (id = ANY(%s))
                 AND (%s::timestamptz IS NULL OR start_ts >= %s::timestamptz)""",
            (list(chosen), since_ts, since_ts),
        )
        n += cur.rowcount
    return n


def load_all(conn: psycopg.Connection, start: date | None = None, end: date | None = None) -> int:
    n = load_wellness(conn, start, end)
    n += load_activities(conn, start, end)
    n += load_fit(conn, start - timedelta(days=1) if start else None, end + timedelta(days=1) if end else None)
    n += resolve_overlaps(conn, start)
    return n
