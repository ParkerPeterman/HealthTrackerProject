"""Profile access: time zone, units, and the reference values rules rely on."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

import psycopg

from healthtracker.db import fetch_one

DEFAULT_PREFS = {
    "runs_per_week": 3,
    "lifts_per_week": 2,
    "min_rest_days": 1,
    "long_run_day": "sun",
    "generate_plan": True,
}


@dataclass
class Profile:
    timezone: str = "UTC"
    display_units: str = "imperial"
    birth_date: date | None = None
    sex: str | None = None
    height_cm: float | None = None
    ref_hr_bpm: int | None = None
    training_prefs: dict | None = None

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def prefs(self) -> dict:
        return {**DEFAULT_PREFS, **(self.training_prefs or {})}

    def age_on(self, d: date) -> int | None:
        if not self.birth_date:
            return None
        b = self.birth_date
        return d.year - b.year - ((d.month, d.day) < (b.month, b.day))


def load_profile(conn: psycopg.Connection) -> Profile:
    row = fetch_one(conn, "SELECT * FROM core.profile WHERE id = 1")
    if not row:
        return Profile()
    return Profile(
        timezone=row["timezone"] or "UTC",
        display_units=row.get("display_units") or "imperial",
        birth_date=row["birth_date"],
        sex=row["sex"],
        height_cm=float(row["height_cm"]) if row["height_cm"] is not None else None,
        ref_hr_bpm=row["ref_hr_bpm"],
        training_prefs=row.get("training_prefs"),
    )
