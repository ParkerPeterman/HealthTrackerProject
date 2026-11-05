"""H2: daily adherence features per medication, in analytics.medication_daily.

  med_status              taken / missed / unknown (no answer is unknown)
  consecutive_missed      recorded misses in a row; an unknown day breaks the run
  adherence_7d, _28d      taken / recorded days; unknown days are left out of both counts
  dose_mg                 the dose that day, from the dose changes
  days_since_dose_change  None until there has been a change
  dose_time_local         hours after local midnight of the day the dose was for
  dose_time_sd_14d        spread of dose times over the last 14 days (4 or more times)
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import psycopg

from healthtracker.db import execute, fetch_all, upsert
from healthtracker.profile import load_profile


def dose_on(med: dict, changes: list[dict], d: date) -> float | None:
    before = [c for c in changes if c["date"] <= d]
    if before:
        return before[-1]["new_dose_mg"]
    return changes[0]["old_dose_mg"] if changes and changes[0]["old_dose_mg"] is not None else med.get("dose_mg")


def feature_rows(med: dict, logs: dict[date, dict], changes: list[dict], start: date, end: date) -> list[dict]:
    """One row per day from start to end. `logs` maps date -> {status, hours}; `changes` sorted by date."""
    status = {d: r["status"] for d, r in logs.items()}
    hours = {d: r.get("hours") for d, r in logs.items() if r["status"] == "taken" and r.get("hours") is not None}
    rows, d = [], start
    while d <= end:
        streak, k = 0, d
        while status.get(k) == "missed":
            streak, k = streak + 1, k - timedelta(days=1)
        out = {"medication_id": med["id"], "date": d, "med_status": status.get(d, "unknown"),
               "consecutive_missed": streak}
        for n in (7, 28):
            window = [status.get(d - timedelta(days=i), "unknown") for i in range(n)]
            known = sum(s != "unknown" for s in window)
            out[f"known_{n}d"] = known
            out[f"adherence_{n}d"] = round(window.count("taken") / known, 3) if known else None
        last = [c for c in changes if c["date"] <= d]
        out["dose_mg"] = dose_on(med, changes, d)
        out["days_since_dose_change"] = (d - last[-1]["date"]).days if last else None
        out["dose_time_local"] = round(hours[d], 2) if d in hours else None
        recent = [hours[x] for x in (d - timedelta(days=i) for i in range(14)) if x in hours]
        out["dose_time_sd_14d"] = round(float(np.std(recent, ddof=1)), 2) if len(recent) >= 4 else None
        rows.append(out)
        d += timedelta(days=1)
    return rows


def load_logs(conn: psycopg.Connection, medication_id: int, tz: str) -> dict[date, dict]:
    rows = fetch_all(conn, """SELECT date, status,
                                     extract(epoch FROM (taken_at AT TIME ZONE %s) - date::timestamp) / 3600.0 AS hours
                              FROM core.medication_log WHERE medication_id = %s""", (tz, medication_id))
    return {r["date"]: {"status": r["status"], "hours": r["hours"]} for r in rows}


def build_medication_daily(conn: psycopg.Connection, end: date) -> int:
    """Recompute every medication's features from its start (or first answer) to `end`."""
    tz = load_profile(conn).timezone
    n = 0
    for med in fetch_all(conn, "SELECT * FROM core.medication ORDER BY id"):
        logs = load_logs(conn, med["id"], tz)
        changes = fetch_all(conn, "SELECT * FROM core.medication_change WHERE medication_id = %s ORDER BY date, id",
                            (med["id"],))
        start = min([med["start_date"], *logs.keys()])
        execute(conn, "DELETE FROM analytics.medication_daily WHERE medication_id = %s", (med["id"],))
        rows = feature_rows(med, logs, changes, start, end)
        n += upsert(conn, "analytics.medication_daily", rows, conflict=["medication_id", "date"]) if rows else 0
    return n


def usual_time(conn: psycopg.Connection, med: dict, d: date, tz: str, min_days: int = 5) -> float | None:
    """Hours after midnight: my set time, else the median of the last 28 days' logged times."""
    if med.get("usual_time") is not None:
        t = med["usual_time"]
        return t.hour + t.minute / 60
    rows = fetch_all(conn, """SELECT extract(epoch FROM (taken_at AT TIME ZONE %s) - date::timestamp) / 3600.0 AS h
                              FROM core.medication_log WHERE medication_id = %s AND status = 'taken'
                                AND taken_at IS NOT NULL AND date BETWEEN %s AND %s""",
                     (tz, med["id"], d - timedelta(days=28), d - timedelta(days=1)))
    hours = sorted(float(r["h"]) for r in rows)
    return round(float(np.median(hours)), 2) if len(hours) >= min_days else None
