"""Is every day ingested, and which Garmin fields do the stored payloads really carry?

`healthtracker coverage` answers both after `garmin-backfill`. Missing overnight rows
mean the watch wasn't worn or hasn't synced; a field that never appears (Garmin
training load on a Venu 3, say) must not be relied on downstream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg

from healthtracker.db import fetch_all, fetch_one

# Fields worth knowing about, per raw endpoint. A payload "has" a field when it is
# present and not null; nested keys use dots.
FIELDS: dict[str, list[str]] = {
    "activity": ["activityTrainingLoad", "aerobicTrainingEffect", "anaerobicTrainingEffect",
                 "hrTimeInZone_1", "hrTimeInZone_5", "averageRunningCadenceInStepsPerMinute",
                 "startLatitude", "vO2MaxValue", "avgRespirationRate"],
    "sleep": ["avgOvernightHrv", "restingHeartRate", "dailySleepDTO.averageRespirationValue",
              "dailySleepDTO.sleepStartTimestampLocal", "dailySleepDTO.sleepScores"],
    "daily_summary": ["moderateIntensityMinutes", "vigorousIntensityMinutes", "averageStressLevel",
                      "bodyBatteryLowestValue", "restingHeartRate"],
    "hrv": ["hrvSummary.lastNightAvg", "hrvSummary.weeklyAvg", "hrvSummary.status"],
    "max_metrics": ["generic.vo2MaxPreciseValue"],
}


@dataclass
class Coverage:
    first: date | None
    last: date
    days: int = 0
    missing: dict[str, list[date]] = field(default_factory=dict)  # metric -> days without it

    @property
    def complete(self) -> bool:
        return not any(self.missing.values())


def ranges(days: list[date]) -> list[tuple[date, date]]:
    """Collapse sorted days into inclusive runs: [d1, d2, d3, d7] -> [(d1, d3), (d7, d7)]."""
    out: list[tuple[date, date]] = []
    for d in sorted(days):
        if out and d - out[-1][1] == timedelta(days=1):
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def daily_coverage(conn: psycopg.Connection, last: date, since: date | None = None) -> Coverage:
    """Days from the first watch data (or `since`) to `last` missing sleep, HRV, or resting HR."""
    first = fetch_one(conn, """SELECT least((SELECT min(date) FROM core.sleep),
                                            (SELECT min(date) FROM core.daily_wellness)) AS d""")["d"]
    if since is not None:
        first = max(first, since) if first else None
    if first is None or first > last:
        return Coverage(first, last)
    rows = fetch_all(conn, """SELECT d::date AS date, s.date IS NOT NULL AS sleep, s.overnight_hrv IS NOT NULL AS hrv,
                                     coalesce(s.resting_hr, w.resting_hr) IS NOT NULL AS rhr
                              FROM generate_series(%s::date, %s::date, interval '1 day') d
                              LEFT JOIN core.sleep s ON s.date = d::date
                              LEFT JOIN core.daily_wellness w ON w.date = d::date
                              ORDER BY 1""", (first, last))
    cov = Coverage(first, last, days=len(rows))
    for metric in ("sleep", "hrv", "rhr"):
        cov.missing[metric] = [r["date"] for r in rows if not r[metric]]
    return cov


def _has(payload, path: str) -> bool:
    node = payload
    for key in path.split("."):
        if isinstance(node, list):  # max_metrics comes as a one-item list
            node = node[0] if node else None
        if not isinstance(node, dict) or node.get(key) is None:
            return False
        node = node[key]
    return True


def field_presence(conn: psycopg.Connection, since: date | None = None) -> dict[str, dict]:
    """For each endpoint: payload count and how many carry each field of interest."""
    out: dict[str, dict] = {}
    for endpoint, fields in FIELDS.items():
        rows = fetch_all(conn, """SELECT DISTINCT ON (for_date, payload->>'activityId') payload FROM raw.ingest
                                  WHERE source = 'garmin' AND endpoint = %s AND (%s::date IS NULL OR for_date >= %s)
                                  ORDER BY for_date, payload->>'activityId', fetched_at DESC""",
                         (endpoint, since, since))
        out[endpoint] = {"payloads": len(rows),
                         "fields": {f: sum(1 for r in rows if _has(r["payload"], f)) for f in fields}}
    return out


def report(cov: Coverage, presence: dict[str, dict]) -> str:
    labels = {"sleep": "sleep", "hrv": "overnight HRV", "rhr": "resting HR"}
    if cov.first is None:
        return "No watch data yet. Run `healthtracker garmin-backfill --days 45` after logging in."
    lines = [f"Watch data {cov.first} to {cov.last}: {cov.days} days."]
    for metric, missing in cov.missing.items():
        if not missing:
            lines.append(f"  {labels[metric]}: every day")
            continue
        spans = ", ".join(str(a) if a == b else f"{a}..{b}" for a, b in ranges(missing))
        lines.append(f"  {labels[metric]}: missing {len(missing)} day(s): {spans}")
    lines.append("Stored Garmin payloads (fields present / payloads):")
    for endpoint, info in presence.items():
        n = info["payloads"]
        if not n:
            lines.append(f"  {endpoint}: none stored")
            continue
        fields = ", ".join(f"{f} {c}/{n}" for f, c in info["fields"].items())
        lines.append(f"  {endpoint} ({n}): {fields}")
    return "\n".join(lines)
