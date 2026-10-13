"""Where each goal stands: current value, progress, whether it's on pace, and when it lands.

Statuses: achieved | ahead | on_track | behind | in_progress (no date to pace
against) | no_data. Weight goals are judged against the planned path from the
start weight at the planned rate, using the smoothed trend so a salty dinner
never reads as failure.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import psycopg

from healthtracker.goals.energy import projected_date, trend_slope_kg_per_week
from healthtracker.goals.metrics import METRICS, current_value, daily_hits

MAINTAIN_BAND_KG = 1.0       # ±1 kg (about 2 lb) counts as holding
MIN_TOLERANCE_KG = 0.45      # about 1 lb: never "behind" for less than this


def planned_rate(goal: dict, current: float | None, as_of: date) -> float | None:
    """kg/week: the goal's own rate, else what reaching the target by its date needs."""
    if goal.get("direction") == "maintain":
        return 0.0
    if goal.get("rate_per_week") is not None:
        return float(goal["rate_per_week"])
    if goal.get("target_date") and goal.get("target_value") is not None and current is not None:
        weeks = max((goal["target_date"] - as_of).days / 7, 1)
        return (float(goal["target_value"]) - current) / weeks
    return None


def _direction(goal: dict, start: float | None, target: float | None) -> str:
    if goal.get("direction"):
        return goal["direction"]
    if start is None or target is None or abs(target - start) < 1e-6:
        return "maintain"
    return "decrease" if target < start else "increase"


def weight_progress(goal: dict, features: pd.DataFrame, current: float | None, as_of: date) -> dict:
    start = goal.get("start_value")
    target = goal.get("target_value")
    out: dict = {"current": current, "start": start, "target": target, "pct": None, "expected": None,
                 "rate_per_week": trend_slope_kg_per_week(features, as_of), "projected_date": None}
    if current is None:
        return {**out, "status": "no_data"}
    direction = _direction(goal, start, target)
    if direction == "maintain":
        ref = target if target is not None else start
        if ref is None:
            return {**out, "status": "no_data"}
        off = abs(current - ref)
        return {**out, "target": ref, "pct": max(0.0, 1 - off / MAINTAIN_BAND_KG) if off < MAINTAIN_BAND_KG else 0.0,
                "status": "on_track" if off <= MAINTAIN_BAND_KG else "behind"}
    if start is None or target is None:
        return {**out, "status": "no_data"}
    sign = -1 if direction == "decrease" else 1
    total, done = target - start, current - start
    out["pct"] = min(1.0, max(0.0, done / total)) if total else 1.0
    if (current - target) * sign >= 0:
        return {**out, "status": "achieved", "projected_date": as_of.isoformat()}
    rate = planned_rate(goal, current, as_of)
    actual = out["rate_per_week"]
    if actual is not None:
        when = projected_date(as_of, current, target, actual)
        out["projected_date"] = when.isoformat() if when else None
    if rate is None:
        return {**out, "status": "in_progress"}
    weeks = max((as_of - goal["start_date"]).days, 0) / 7
    expected = start + rate * weeks
    expected = max(expected, target) if sign < 0 else min(expected, target)
    out["expected"] = expected
    tolerance = max(MIN_TOLERANCE_KG, abs(rate))  # a week's worth of progress
    gap = (current - expected) * sign
    status = "ahead" if gap > tolerance else "behind" if gap < -tolerance else "on_track"
    return {**out, "status": status}


def level_progress(goal: dict, current: float | None, as_of: date) -> dict:
    spec = METRICS[goal["metric"]]
    start, target = goal.get("start_value"), goal.get("target_value")
    out: dict = {"current": current, "start": start, "target": target, "pct": None, "expected": None}
    if current is None or target is None:
        return {**out, "status": "no_data"}
    better = 1 if spec.better == "higher" else -1
    if (current - target) * better >= 0:
        return {**out, "pct": 1.0, "status": "achieved"}
    if start is None or abs(target - start) < 1e-9:
        return {**out, "status": "in_progress"}
    out["pct"] = min(1.0, max(0.0, (current - start) / (target - start)))
    if not goal.get("target_date") or goal["target_date"] <= goal["start_date"]:
        return {**out, "status": "in_progress"}
    share = (as_of - goal["start_date"]).days / (goal["target_date"] - goal["start_date"]).days
    expected = start + (target - start) * min(1.0, max(0.0, share))
    out["expected"] = expected
    gap = (current - expected) * better
    tolerance = 0.1 * abs(target - start)
    return {**out, "status": "ahead" if gap > tolerance else "behind" if gap < -tolerance else "on_track"}


def habit_progress(goal: dict, features: pd.DataFrame, as_of: date) -> dict:
    """Rolling last 7 days. A goal set this week is only asked for its share of the target so far."""
    days = daily_hits(features, goal["metric"], goal.get("target_value"), as_of)
    started = goal.get("start_date") or as_of - timedelta(days=6)
    counted = [d for d in days if date.fromisoformat(d["date"]) >= started]
    hits = sum(1 for d in days if d["hit"])          # shown: the whole week, matching the day dots
    hits_since = sum(1 for d in counted if d["hit"])  # judged: only days since the goal was set
    needed = int(goal.get("days_per_week") or 7)
    needed_so_far = math.ceil(needed * len(counted) / 7) if counted else needed
    out = {"current": hits, "start": None, "target": goal.get("target_value"), "days": days, "hits": hits,
           "needed": needed, "pct": min(1.0, hits / needed), "expected": None}
    if not any(d["hit"] is not None for d in counted):
        return {**out, "status": "no_data"}
    return {**out, "status": "on_track" if hits_since >= needed_so_far else "behind"}


def goal_progress(conn: psycopg.Connection, goal: dict, features: pd.DataFrame, as_of: date) -> dict:
    metric = goal.get("metric")
    if not metric or metric not in METRICS:
        return {"status": "not_tracked", "current": None, "pct": None}
    spec = METRICS[metric]
    if spec.kind == "daily":
        return {"metric": metric, **habit_progress(goal, features, as_of)}
    current = current_value(conn, features, metric, as_of, goal.get("exercise_id"))
    if metric == "weight_kg":
        return {"metric": metric, **weight_progress(goal, features, current, as_of)}
    return {"metric": metric, **level_progress(goal, current, as_of)}
