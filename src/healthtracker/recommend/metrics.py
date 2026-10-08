"""Metric resolver: the value of a recommendation metric on a given day.

Metrics are either daily_features columns or one of the parameterized
metrics below ("hard_sets_day:quads"). Grading compares these to the action's
target, so every metric a rule emits must resolve here.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import fetch_df, fetch_one
from healthtracker.features.daily import FEATURE_COLUMNS, estimate_hr_max, impute_rpe
from healthtracker.features.lifting import HARD_SET_SQL
from healthtracker.profile import load_profile
from healthtracker.rules.catalog import ACTIVITY_SPORTS

# Known on the morning of their own date (the night ending that morning).
OVERNIGHT_METRICS = {
    "sleep_total_min", "sleep_score", "sleep_midpoint_min", "hrv", "resting_hr", "hrv_z_30d",
    "rhr_z_30d", "sleep_score_z_30d", "hrv_7d_avg", "hrv_30d_avg", "bedtime_hour",
}

# Positive outcome_delta always means "better than my baseline".
OUTCOME_DIRECTION = {
    "hrv_z_30d": 1, "rhr_z_30d": -1, "sleep_score_z_30d": 1, "hrv": 1, "resting_hr": -1,
    "sleep_score": 1, "sleep_total_min": 1, "energy": 1, "mood": 1, "soreness": -1,
    "max_pain_activity": -1, "avg_stress": -1,
}

ZERO_WHEN_LOGGED = {"last_caffeine_hour", "caffeine_mg", "alcohol_drinks"}


def compare(actual: float | None, comparator: str, target: float | None, high: float | None = None) -> bool | None:
    if actual is None or target is None:
        return None
    eps = 1e-9
    match comparator:
        case "<":
            return actual < target - eps
        case "<=":
            return actual <= target + eps
        case ">":
            return actual > target + eps
        case ">=":
            return actual >= target - eps
        case "=":
            return abs(actual - target) <= 1e-6
        case "between":
            return target - eps <= actual <= (high if high is not None else target) + eps
    raise ValueError(comparator)


def split_outcome(outcome_metric: str | None) -> tuple[str, int] | None:
    if not outcome_metric:
        return None
    metric, _, lag = outcome_metric.partition("@")
    return metric, int(lag or 0)


class MetricResolver:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        self.profile = load_profile(conn)
        self.tz = self.profile.timezone
        f = fetch_df(conn, "SELECT * FROM analytics.daily_features ORDER BY date")
        self.features = f.set_index("date") if not f.empty else f
        self._workouts: pd.DataFrame | None = None
        self._sets: pd.DataFrame | None = None

    # ------------------------------------------------------------ availability
    def available(self, metric: str, d: date, as_of: date) -> bool:
        """Is the metric's data for `d` complete when the job runs on the morning of `as_of`?"""
        base = metric.split(":")[0]
        return d <= as_of if base in OVERNIGHT_METRICS else d < as_of

    # ------------------------------------------------------------ helpers
    def _feature(self, col: str, d: date) -> float | None:
        if self.features.empty or d not in self.features.index:
            return None
        v = self.features.at[d, col]
        if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
            if col in ZERO_WHEN_LOGGED and bool(self.features.at[d, "food_log_complete"]):
                return 0.0
            return None
        return float(v)

    def workouts(self) -> pd.DataFrame:
        if self._workouts is None:
            w = fetch_df(self.conn,
                         """SELECT sport, is_game, rpe, avg_hr, (start_ts AT TIME ZONE %s)::date AS date
                            FROM core.v_workout""", (self.tz,))
            if not w.empty:
                hr_max = estimate_hr_max(self.conn, self.profile, date.today())
                w["rpe_eff"] = [r if pd.notna(r) else impute_rpe(h, hr_max)
                                for r, h in zip(w["rpe"], w["avg_hr"], strict=False)]
            self._workouts = w
        return self._workouts

    def sets(self) -> pd.DataFrame:
        if self._sets is None:
            self._sets = fetch_df(
                self.conn,
                """SELECT s.id, s.exercise_id, s.is_warmup, coalesce(s.rir, 2) AS rir,
                          (w.start_ts AT TIME ZONE %s)::date AS date, e.movement_pattern
                   FROM core.lift_set s JOIN core.v_workout w ON w.id = s.workout_id
                   JOIN core.exercise e ON e.id = s.exercise_id""", (self.tz,))
        return self._sets

    def _hard(self, start: date, end: date) -> pd.DataFrame:
        return fetch_df(self.conn, f"SELECT * FROM ({HARD_SET_SQL}) h WHERE h.date BETWEEN %(s)s AND %(e)s",
                        {"tz": self.tz, "s": start, "e": end})

    # ------------------------------------------------------------ value
    def value(self, metric: str, d: date) -> float | None:
        name, _, arg = metric.partition(":")
        if name in FEATURE_COLUMNS and not arg:
            return self._feature(name, d)
        if name == "max_session_rpe":
            w = self.workouts()
            day = w[w["date"] == d] if not w.empty else w
            return float(day["rpe_eff"].max()) if not day.empty else 0.0
        if name == "activity_rpe":
            w = self.workouts()
            sports = ACTIVITY_SPORTS.get(arg, {arg})
            day = w[(w["date"] == d) & (w["sport"].isin(sports))] if not w.empty else w
            return float(day["rpe_eff"].max()) if not day.empty else 0.0
        if name == "hard_sets_day":
            h = self._hard(d, d)
            return float(h[h["muscle_group"] == arg]["set_credit"].sum()) if not h.empty else 0.0
        if name == "hard_sets_7d":
            h = self._hard(d - timedelta(days=6), d)
            return float(h[h["muscle_group"] == arg]["set_credit"].sum()) if not h.empty else 0.0
        if name == "lower_hard_sets_day":
            h = self._hard(d, d)
            if h.empty:
                return 0.0
            lower = h[(h["body_region"] == "lower") & (h["role"] == "primary")]
            return float(lower["set_id"].nunique())
        if name in ("exercise_sets_day", "pattern_sets_day"):
            s = self.sets()
            if s.empty:
                return 0.0
            day = s[(s["date"] == d) & (~s["is_warmup"])]
            if name == "exercise_sets_day":
                return float((day["exercise_id"] == int(arg)).sum())
            return float((day["movement_pattern"] == arg).sum())
        if name == "nutrient":
            if arg in FEATURE_COLUMNS:
                return self._feature(arg, d)
            from healthtracker.rules.context import nutrients_daily

            nd = nutrients_daily(self.conn, d, d)
            complete = self._feature("food_log_complete", d)
            if not complete:
                return None
            return float(nd[nd["nutrient"] == arg]["amount"].sum()) if not nd.empty else 0.0
        if name == "e1rm_best":
            row = fetch_one(self.conn,
                            """SELECT max(best_e1rm_kg) AS v FROM analytics.lift_progress
                               WHERE exercise_id = %s AND date BETWEEN %s AND %s""",
                            (int(arg), d - timedelta(days=6), d))
            return row["v"] if row else None
        if name == "injury_seen_professional":
            row = fetch_one(self.conn, "SELECT seen_professional FROM core.injury WHERE id = %s", (int(arg),))
            return None if row is None else float(bool(row["seen_professional"]))
        if name == "bedtime_hour":
            row = fetch_one(self.conn,
                            """SELECT extract(epoch FROM (sleep_start AT TIME ZONE %s)
                                      - (date - 1)::timestamp) / 3600.0 AS h
                               FROM core.sleep WHERE date = %s""", (self.tz, d))
            return round(float(row["h"]), 2) if row and row["h"] is not None else None
        raise KeyError(f"unknown metric {metric}")

    def baseline(self, metric: str, d: date) -> float | None:
        """Mean of the metric over the 30 eligible days before d (z-scores: 0)."""
        if metric.endswith("_z_30d"):
            return 0.0
        if self.features.empty or metric not in self.features.columns:
            return None
        f = self.features
        window = f[(f.index >= d - timedelta(days=30)) & (f.index < d)]
        window = window[~(window["exclude_from_baseline"].fillna(False).astype(bool)
                          | window["injury_active"].fillna(False).astype(bool))]
        vals = window[metric].dropna().astype(float)
        return float(vals.mean()) if len(vals) >= 5 else None
