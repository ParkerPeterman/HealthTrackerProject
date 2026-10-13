"""What a goal can track, and how to read its current value from my data.

Level metrics (weight, 5K time, e1RM, resting HR...) compare today's value with a
target. Daily metrics (habits) count the days in the last week that met it.
Values are stored metric: kg, minutes, seconds per km, bpm.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import fetch_all, fetch_one
from healthtracker.goals.energy import _window


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    better: str          # "higher" or "lower" (weight follows the goal's direction instead)
    kind: str            # "level" or "daily"
    goal_types: tuple[str, ...]


METRICS: dict[str, Metric] = {m.key: m for m in [
    Metric("weight_kg", "Body weight", "kg", "lower", "level", ("body_weight",)),
    Metric("run_5k_min", "5K time", "min", "lower", "level", ("performance",)),
    Metric("e1rm_kg", "Estimated 1RM", "kg", "higher", "level", ("performance",)),
    Metric("pace_at_ref_hr_s_km", "Pace at reference HR", "s/km", "lower", "level", ("performance",)),
    Metric("vo2max", "VO2 max", "ml/kg/min", "higher", "level", ("performance", "health")),
    Metric("resting_hr", "Resting HR, 7-day average", "bpm", "lower", "level", ("health",)),
    Metric("hrv", "HRV, 7-day average", "ms", "higher", "level", ("health",)),
    Metric("sleep_total_min", "Sleep", "min", "higher", "daily", ("habit",)),
    Metric("protein_g_per_kg", "Protein", "g/kg", "higher", "daily", ("habit",)),
    Metric("food_log_complete", "Complete food log", "day", "higher", "daily", ("habit",)),
    Metric("training_day", "Train", "day", "higher", "daily", ("habit",)),
    Metric("alcohol_drinks", "Alcohol", "drinks", "lower", "daily", ("habit",)),
]}


def _last(features: pd.DataFrame, col: str, as_of: date, within: int = 30) -> float | None:
    f = _window(features, as_of - timedelta(days=within), as_of + timedelta(days=1))
    if f.empty or col not in f:
        return None
    s = f[col].astype(float).dropna()
    return float(s.iloc[-1]) if len(s) else None


def _mean(features: pd.DataFrame, col: str, as_of: date, days: int = 7, min_n: int = 3) -> float | None:
    f = _window(features, as_of - timedelta(days=days - 1), as_of + timedelta(days=1))
    if f.empty or col not in f:
        return None
    s = f[col].astype(float).dropna()
    return float(s.mean()) if len(s) >= min_n else None


def best_5k_minutes(conn: psycopg.Connection, as_of: date, days: int = 60) -> float | None:
    """Fastest stretch of at least 5 km inside one run (from its splits), scaled to exactly 5 km.

    Runs without splits count by their whole-run average pace if they were 5 km or longer.
    """
    since = as_of - timedelta(days=days)
    splits = fetch_all(conn, """SELECT s.workout_id, s.split_no, s.distance_m, s.duration_s
                                FROM core.run_split s JOIN core.v_workout w ON w.id = s.workout_id
                                WHERE w.sport = 'run' AND w.start_ts::date BETWEEN %s AND %s
                                ORDER BY s.workout_id, s.split_no""", (since, as_of))
    best: float | None = None
    by_run: dict[int, list[tuple[float, float]]] = {}
    for r in splits:
        by_run.setdefault(r["workout_id"], []).append((float(r["distance_m"]), float(r["duration_s"])))
    for parts in by_run.values():
        lo, dist, secs = 0, 0.0, 0.0
        for d, t in parts:  # two pointers: shortest-time window covering 5 km
            dist, secs = dist + d, secs + t
            while lo < len(parts) and dist - parts[lo][0] >= 5000:
                dist, secs = dist - parts[lo][0], secs - parts[lo][1]
                lo += 1
            if dist >= 5000:
                pace = secs * 5000 / dist
                best = pace if best is None else min(best, pace)
    whole = fetch_one(conn, """SELECT min(duration_s * 5000.0 / distance_m) AS s FROM core.v_workout
                               WHERE sport = 'run' AND distance_m >= 5000 AND start_ts::date BETWEEN %s AND %s
                                 AND id NOT IN (SELECT workout_id FROM core.run_split)""", (since, as_of))
    if whole and whole["s"] is not None:
        best = whole["s"] if best is None else min(best, whole["s"])
    return None if best is None else best / 60


def best_e1rm_kg(conn: psycopg.Connection, exercise_id: int | None, as_of: date, days: int = 42) -> float | None:
    if exercise_id is None:
        return None
    row = fetch_one(conn, """SELECT max(best_e1rm_kg) AS e FROM analytics.lift_progress
                             WHERE exercise_id = %s AND date BETWEEN %s AND %s""",
                    (exercise_id, as_of - timedelta(days=days), as_of))
    return row["e"] if row else None


def current_value(conn: psycopg.Connection, features: pd.DataFrame, metric: str, as_of: date,
                  exercise_id: int | None = None) -> float | None:
    if metric == "weight_kg":
        return _last(features, "weight_kg_smoothed", as_of, 21) or _last(features, "weight_kg", as_of, 21)
    if metric == "run_5k_min":
        return best_5k_minutes(conn, as_of)
    if metric == "e1rm_kg":
        return best_e1rm_kg(conn, exercise_id, as_of)
    if metric in ("pace_at_ref_hr_s_km", "vo2max"):
        return _last(features, metric, as_of)
    if metric in ("resting_hr", "hrv"):
        return _mean(features, metric, as_of)
    if metric in METRICS and METRICS[metric].kind == "daily":
        return None  # habits are counted per day, see daily_hits
    raise ValueError(f"unknown goal metric {metric!r}")


def day_value(row: pd.Series, metric: str) -> float | None:
    """A habit metric's value on one day; None when the data needed isn't there."""
    def val(col: str) -> float | None:
        v = row.get(col)
        return None if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA else float(v)

    if metric == "food_log_complete":
        return 1.0 if bool(row.get("food_log_complete")) else 0.0
    if metric == "training_day":
        return 1.0 if (val("n_workouts") or 0) > 0 or bool(row.get("had_game")) else 0.0
    if metric in ("protein_g_per_kg", "alcohol_drinks"):
        return val(metric) if bool(row.get("food_log_complete")) else None
    return val(metric)


def daily_hits(features: pd.DataFrame, metric: str, target: float | None, as_of: date, days: int = 7) -> list[dict]:
    """The last `days` days (oldest first): value and whether it met the target."""
    spec = METRICS[metric]
    f = _window(features, as_of - timedelta(days=days - 1), as_of + timedelta(days=1))
    out = []
    for i in range(days - 1, -1, -1):
        d = as_of - timedelta(days=i)
        ts = pd.Timestamp(d)
        v = day_value(f.loc[ts], metric) if not f.empty and ts in f.index else None
        if v is None:
            hit = None
        elif metric in ("food_log_complete", "training_day"):
            hit = v >= 1
        elif target is None:
            hit = None
        else:
            hit = v >= target if spec.better == "higher" else v <= target
        out.append({"date": d.isoformat(), "value": None if v is None else round(v, 2), "hit": hit})
    return out
