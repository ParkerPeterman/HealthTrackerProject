"""FIT files: the fallback when the Garmin API is down, and the source of per-second HR.

A FIT file is reduced to a JSON payload (session summary, laps, and compact
records) so it can live in raw.ingest like every other source.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import fitdecode

from healthtracker.db import connect
from healthtracker.ingest.raw import store_raw

SESSION_FIELDS = (
    "sport", "sub_sport", "start_time", "total_elapsed_time", "total_timer_time",
    "total_distance", "avg_heart_rate", "max_heart_rate", "avg_cadence",
    "avg_running_cadence", "total_ascent", "total_calories",
)
LAP_FIELDS = ("start_time", "total_elapsed_time", "total_timer_time", "total_distance",
              "avg_heart_rate", "avg_cadence", "avg_running_cadence")


def _jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=UTC)).isoformat()
    if isinstance(v, float | int | str) or v is None:
        return v
    return str(v)


def _fields(frame: fitdecode.FitDataMessage, wanted: tuple[str, ...]) -> dict:
    out = {}
    for name in wanted:
        if frame.has_field(name):
            out[name] = _jsonable(frame.get_value(name))
    return out


def parse_fit_bytes(blob: bytes) -> dict:
    session: dict = {}
    laps: list[dict] = []
    records: list[list] = []
    with fitdecode.FitReader(io.BytesIO(blob)) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "session" and not session:
                session = _fields(frame, SESSION_FIELDS)
            elif frame.name == "lap":
                laps.append(_fields(frame, LAP_FIELDS))
            elif frame.name == "record":
                ts = frame.get_value("timestamp") if frame.has_field("timestamp") else None
                hr = frame.get_value("heart_rate") if frame.has_field("heart_rate") else None
                speed = _first(frame, ("enhanced_speed", "speed"))
                cadence = _first(frame, ("cadence",))
                if cadence is not None and frame.has_field("fractional_cadence"):
                    cadence = float(cadence) + float(frame.get_value("fractional_cadence") or 0)
                altitude = _first(frame, ("enhanced_altitude", "altitude"))
                if ts is not None and hr is not None:
                    # [ts, hr, speed m/s, cadence (per leg for running, as FIT stores it), altitude m]
                    records.append([_jsonable(ts), int(hr), _round(speed, 3), _round(cadence, 1), _round(altitude, 1)])
    return {"session": session, "laps": laps, "records": records}


def _first(frame: fitdecode.FitDataMessage, names: tuple[str, ...]):
    for f in names:
        if frame.has_field(f) and frame.get_value(f) is not None:
            return frame.get_value(f)
    return None


def _round(v, nd: int) -> float | None:
    try:
        return None if v is None else round(float(v), nd)
    except (TypeError, ValueError):
        return None


def import_fit_files(paths: list[Path]) -> int:
    """Load FIT files (e.g. exported from Garmin Connect) into raw.ingest."""
    new = 0
    with connect() as conn:
        for path in paths:
            payload = parse_fit_bytes(path.read_bytes())
            m = re.search(r"(\d{6,})", path.stem)  # Garmin exports are named <activityId>_ACTIVITY.fit
            if m:
                payload["activityId"] = int(m.group(1))
            start = payload["session"].get("start_time")
            for_date = date.fromisoformat(start[:10]) if start else None
            new += store_raw(conn, "garmin_fit", "fit_activity", for_date, payload)
    return new
