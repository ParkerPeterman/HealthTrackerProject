"""One internal load scale (A6) and the daily load channels.

The Venu 3 gives no Garmin training load, so load is computed here. Runs and other
HR-tracked sessions use Banister's TRIMP (computed in sessions.py). Games, lifts, and
sprint/jump work use session-RPE (minutes x effort, Foster et al. 2001), put on the
TRIMP scale by my own ratio between the two on sessions that have both: the median
TRIMP / sRPE over the last 120 days, once there are 5 such sessions. Until then a
default stands in (a rule parameter). A logged effort rating after runs makes the
calibration better.

Channels HR misses get their own daily totals: plyometric foot contacts, sprint
metres above 90% of max speed (logged; the watch's 1 Hz GPS is too coarse), and
shoulder minutes (games, pickup, throwing).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import execute, fetch_all, fetch_df, upsert

DEFAULTS = {"srpe_default_factor": 0.4, "min_calibration_pairs": 5, "calibration_days": 120}
THROWING_SPORTS = {"ultimate", "throwing"}
CHANNELS = ("internal", "internal_run", "internal_game", "internal_lift", "internal_other", "plyo_contacts",
            "sprint_m", "shoulder_min")


def load_params(conn: psycopg.Connection) -> dict[str, float]:
    rows = fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_LOAD'")
    return {**DEFAULTS, **{r["name"]: float(r["value"]) for r in rows}}


def calibration_factor(pairs: pd.DataFrame, params: dict) -> tuple[float, int, str]:
    """Median TRIMP per sRPE unit over sessions with both, or the default when there are too few."""
    ok = pairs.dropna(subset=["trimp", "srpe"])
    ok = ok[(ok["srpe"] > 0) & (ok["trimp"] > 0)]
    if len(ok) < params["min_calibration_pairs"]:
        return float(params["srpe_default_factor"]), len(ok), "default"
    return round(float(np.median(ok["trimp"] / ok["srpe"])), 3), len(ok), "median_ratio"


def calibrate(conn: psycopg.Connection, as_of: date) -> float:
    params = load_params(conn)
    pairs = fetch_df(conn, """SELECT f.trimp, f.srpe FROM analytics.session_features f
                              JOIN core.workout w ON w.id = f.workout_id
                              WHERE w.rpe IS NOT NULL AND f.trimp IS NOT NULL AND f.date BETWEEN %s AND %s""",
                     (as_of - timedelta(days=int(params["calibration_days"])), as_of))
    factor, n, method = calibration_factor(pairs, params)
    upsert(conn, "analytics.load_calibration", [{"computed_on": as_of, "factor": factor, "n_sessions": n,
                                                 "method": method, "details": {"window_days": params["calibration_days"]}}],
           conflict=["computed_on"])
    return factor


def channel_of(sport: str, session_type: str) -> str:
    if session_type == "game" or sport in THROWING_SPORTS:
        return "internal_game"
    if sport == "run":
        return "internal_run"
    if session_type == "lift":
        return "internal_lift"
    return "internal_other"


def daily_channels(sessions: pd.DataFrame) -> pd.DataFrame:
    """Long frame (date, channel, value, n_sessions) from session rows with internal_load."""
    if sessions.empty:
        return pd.DataFrame(columns=["date", "channel", "value", "n_sessions"])
    s = sessions.copy()
    s["channel"] = [channel_of(sp, t) for sp, t in zip(s["sport"], s["session_type"], strict=True)]
    s["shoulder"] = np.where(s["sport"].isin(THROWING_SPORTS) | (s["session_type"] == "game"), s["duration_min"], 0.0)
    out = []
    for d, g in s.groupby("date"):
        out.append({"date": d, "channel": "internal", "value": g["internal_load"].sum(), "n_sessions": len(g)})
        for ch, gg in g.groupby("channel"):
            out.append({"date": d, "channel": ch, "value": gg["internal_load"].sum(), "n_sessions": len(gg)})
        for col, ch in (("plyo_contacts", "plyo_contacts"), ("sprint_m", "sprint_m"), ("shoulder", "shoulder_min")):
            v = g[col].fillna(0).astype(float)
            if v.sum() > 0:
                out.append({"date": d, "channel": ch, "value": v.sum(), "n_sessions": int((v > 0).sum())})
    df = pd.DataFrame(out)
    df["value"] = df["value"].astype(float).round(1)
    return df


def current_factor(conn: psycopg.Connection) -> float:
    row = fetch_all(conn, "SELECT factor FROM analytics.load_calibration ORDER BY computed_on DESC LIMIT 1")
    return float(row[0]["factor"]) if row else float(load_params(conn)["srpe_default_factor"])


def apply_factor(conn: psycopg.Connection, factor: float, workout_id: int | None = None) -> int:
    """internal_load = TRIMP when there is one, else session-RPE x the calibration factor."""
    return execute(conn, """UPDATE analytics.session_features SET
                                load_factor = %(f)s::numeric,
                                load_method = CASE WHEN trimp IS NOT NULL THEN 'trimp' ELSE 'srpe_calibrated' END,
                                internal_load = round(coalesce(trimp, srpe * %(f)s::numeric), 1)
                            WHERE (%(w)s::bigint IS NULL OR workout_id = %(w)s)
                              AND (load_factor IS DISTINCT FROM %(f)s::numeric OR internal_load IS NULL
                                   OR internal_load IS DISTINCT FROM round(coalesce(trimp, srpe * %(f)s::numeric), 1))""",
                   {"f": factor, "w": workout_id})


def build_load(conn: psycopg.Connection, as_of: date) -> int:
    """Calibrate, put every session on the one scale, and rewrite the daily channels."""
    apply_factor(conn, calibrate(conn, as_of))
    sessions = fetch_df(conn, """SELECT date, sport, session_type, duration_min, internal_load, plyo_contacts, sprint_m
                                 FROM analytics.session_features""")
    rows = daily_channels(sessions)
    execute(conn, "DELETE FROM analytics.load_daily")
    return upsert(conn, "analytics.load_daily", rows.to_dict("records"), conflict=["date", "channel"]) if len(rows) else 0
