"""Intensity distribution (D4): weekly time easy / moderate / hard across all endurance work.

Runs and other HR-tracked sessions count their time in the three zones; games and
sessions without heart rate count whole, at their effort (Seiler's session-goal
method). Lifting is left out. The aim is pyramidal: most time easy (75-80% to start),
then moderate, then hard. The pattern to catch is the recreational one: too much
"moderately hard".
"""

from __future__ import annotations

import pandas as pd
import psycopg

from healthtracker.db import execute, fetch_df, upsert


def pattern(low: float, mid: float, high: float, easy_target: float = 75, moderate_flag: float = 25,
            min_minutes: float = 60) -> str:
    total = low + mid + high
    if total < min_minutes:
        return "too_little"
    easy, moderate = low / total * 100, mid / total * 100
    if mid >= low and mid >= high:
        return "threshold"
    if easy < easy_target - 5 and moderate >= moderate_flag:
        return "moderate_heavy"
    return "polarized" if high > mid else "pyramidal"


def session_zones(conn: psycopg.Connection) -> pd.DataFrame:
    df = fetch_df(conn, """SELECT date, low_min, mid_min, high_min, tid_method FROM analytics.session_features
                           WHERE tid_method IS NOT NULL ORDER BY date""")
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        for c in ("low_min", "mid_min", "high_min"):
            df[c] = df[c].astype(float).fillna(0.0)
    return df


def weekly_rows(sessions: pd.DataFrame) -> list[dict]:
    if sessions.empty:
        return []
    s = sessions.copy()
    s["week_start"] = (s["date"] - pd.to_timedelta(s["date"].dt.weekday, unit="D")).dt.date
    s["total"] = s["low_min"] + s["mid_min"] + s["high_min"]
    rows = []
    for wk, g in s.groupby("week_start"):
        low, mid, high, total = (float(g[c].sum()) for c in ("low_min", "mid_min", "high_min", "total"))
        rows.append({"week_start": wk, "low_min": round(low, 1), "mid_min": round(mid, 1), "high_min": round(high, 1),
                     "easy_pct": round(low / total * 100, 1) if total else None,
                     "pattern": pattern(low, mid, high),
                     "hr_share": round(float(g.loc[g["tid_method"] == "hr", "total"].sum()) / total, 2) if total else None,
                     "n_sessions": len(g)})
    return rows


def build_intensity_weekly(conn: psycopg.Connection) -> int:
    rows = weekly_rows(session_zones(conn))
    execute(conn, "DELETE FROM analytics.intensity_weekly")
    return upsert(conn, "analytics.intensity_weekly", rows, conflict=["week_start"]) if rows else 0


def rolling_shares(sessions: pd.DataFrame, index: pd.DatetimeIndex, days: int = 14) -> pd.DataFrame:
    """Per day: endurance minutes and easy / moderate share over the trailing window."""
    out = pd.DataFrame(index=index, columns=["zone_min_14d", "easy_pct_14d", "mid_pct_14d"], dtype=float)
    if sessions.empty:
        return out
    daily = sessions.groupby("date")[["low_min", "mid_min", "high_min"]].sum().reindex(index, fill_value=0.0)
    roll = daily.rolling(days, min_periods=1).sum()
    total = roll.sum(axis=1)
    out["zone_min_14d"] = total.round(1)
    out["easy_pct_14d"] = (roll["low_min"] / total * 100).where(total > 0).round(1)
    out["mid_pct_14d"] = (roll["mid_min"] / total * 100).where(total > 0).round(1)
    return out
