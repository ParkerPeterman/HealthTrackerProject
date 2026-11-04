"""Ramp guards (D1): how much a weekly load may grow. Shared by the rules and the planner,
so no plan can exceed a cap the rules would flag."""

from __future__ import annotations


def ramp_cap(last_week: float | None, pct: float, allowance: float) -> float:
    """Most this week may hold: last week plus `pct`%, or plus a small absolute allowance,
    whichever is larger (so a low winter base can still grow)."""
    base = max(float(last_week or 0.0), 0.0)
    return max(base * (1 + pct / 100.0), base + allowance)


CHANNELS = {
    # name: (daily_features this-week column, last-week column, pct param, allowance param, unit label)
    "run": ("run_load_week", "run_load_week_prev", "max_weekly_increase_pct", "low_base_allowance", "load"),
    "sprint": ("sprint_week", "sprint_week_prev", "sprint_increase_pct", "sprint_allowance_m", "m of sprinting"),
    "plyo": ("plyo_week", "plyo_week_prev", "plyo_increase_pct", "plyo_allowance", "jump contacts"),
}
