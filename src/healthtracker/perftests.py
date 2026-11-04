"""Performance tests (A8): what each test is, how to run it, and how often.

Results live in core.performance_test in base units (bpm, seconds, centimetres).
Garmin's VO2 max estimate is listed for completeness but never logged by hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import psycopg

from healthtracker.db import fetch_all, fetch_one

WHEN = "Test when rested, on a Monday or Tuesday, never right after a travel weekend."


@dataclass(frozen=True)
class PerfTestSpec:
    key: str
    label: str
    unit: str
    better: str | None      # "higher", "lower", or None (a measurement, not a score)
    every_days: int | None  # how often to repeat it
    how: str
    best_of: int = 1        # attempts per session; the best one counts
    lo: float = 0
    hi: float = 1e9

    def best(self, trials: list[float]) -> float:
        return min(trials) if self.better == "lower" else max(trials)


TESTS: dict[str, PerfTestSpec] = {t.key: t for t in [
    PerfTestSpec("max_hr", "Max HR", "bpm", None, 365,
             "After a warm-up, run 3 × 3 minutes hard uphill, finishing the last one all-out. "
             "Use a chest strap if you have one.", lo=120, hi=240),
    PerfTestSpec("time_trial_5k", "5 km time trial", "s", "lower", 49,
             "5 km on a flat route or track after a warm-up, same route each time. "
             "Temperature is filled in from the weather data.", lo=600, hi=3600),
    PerfTestSpec("sprint_10m", "10 m sprint", "s", "lower", 28,
             "Time it with phone slow-motion video or a partner. Same surface and warm-up each time; best of 3.",
             best_of=3, lo=1.0, hi=5.0),
    PerfTestSpec("sprint_30m", "30 m sprint", "s", "lower", 28,
             "Time it with phone slow-motion video or a partner. Same surface and warm-up each time; best of 3.",
             best_of=3, lo=2.5, hi=10.0),
    PerfTestSpec("cmj", "Countermovement jump", "cm", "higher", 28,
             "Jump height from a phone video app (flight time) or a wall vertical reach; best of 3.",
             best_of=3, lo=5, hi=120),
    PerfTestSpec("broad_jump", "Standing broad jump", "cm", "higher", 28,
             "Two-foot take-off, stick the landing, measure to the back heel; best of 3.",
             best_of=3, lo=50, hi=400),
]}

GARMIN_VO2 = ("VO2 max (Garmin)", "Estimated automatically from outdoor runs. Garmin's estimate depends on the max HR "
              "in your Garmin Connect profile, so set it there once your max HR test confirms it.")


def protocols() -> list[dict]:
    rows = [{"key": t.key, "label": t.label, "unit": t.unit, "better": t.better, "every_days": t.every_days,
             "how": t.how, "best_of": t.best_of, "min": t.lo, "max": t.hi} for t in TESTS.values()]
    rows.append({"key": "vo2max_garmin", "label": GARMIN_VO2[0], "unit": "ml/kg/min", "better": "higher",
                 "every_days": None, "how": GARMIN_VO2[1], "best_of": 1, "automatic": True})
    return rows


def latest(conn: psycopg.Connection, test: str, as_of: date, within_days: int | None = None) -> dict | None:
    """The most recent result on or before `as_of` (optionally only within `within_days`)."""
    since = as_of - timedelta(days=within_days) if within_days else None
    return fetch_one(conn, """SELECT * FROM core.performance_test WHERE test = %s AND date <= %s
                              AND (%s::date IS NULL OR date >= %s) ORDER BY date DESC, id DESC LIMIT 1""",
                     (test, as_of, since, since))


def summary(conn: psycopg.Connection, as_of: date) -> dict[str, dict]:
    """Per test: the latest result, the best ever, how many, and when it is next due."""
    out = {}
    rows = fetch_all(conn, "SELECT * FROM core.performance_test WHERE date <= %s ORDER BY date, id", (as_of,))
    for key, spec in TESTS.items():
        mine = [r for r in rows if r["test"] == key]
        last = mine[-1] if mine else None
        best = spec.best([float(r["value"]) for r in mine]) if mine and spec.better else None
        due = None
        if spec.every_days:
            due = as_of if last is None else last["date"] + timedelta(days=spec.every_days)
        out[key] = {"last": last, "best": best, "count": len(mine), "due": due,
                    "overdue": due is not None and due <= as_of}
    return out
