"""D5: how I respond to each session type, learned from my own data. Shadow; gate about 90 days.

For every session type (easy, long, tempo, intervals, VO2, speed/jump, game, lift), one model
per outcome with an indicator for each type on the day (so a lift-and-run day isn't counted
twice) plus day-of-week:

  hrv_swc        mean of the next two mornings' ln HRV minus its baseline, in SWC units
  rhr_swc        the same for resting HR
  soreness_next  next-day journal soreness
  energy_next    next-day journal energy

Estimates are then shared across types with the B5 partial pooling, so a type with few
sessions borrows from the others. "Intervals cost about one SWC of HRV for two days" is what
this measures; once live, it becomes the library's recovery cost and the planner keeps that
many days between such a session and a game.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import psycopg

from healthtracker.components import component_status, is_live
from healthtracker.db import execute, fetch_all, fetch_df, upsert
from healthtracker.stats.bayes import VAGUE, design, fit, summarize
from healthtracker.stats.pooling import pool

TYPES = ("easy", "long", "tempo", "intervals", "vo2", "speed_jump", "game", "lift")
MIN_SESSIONS = 5
# Library codes per session type (the planner's recovery cost is per code).
CODES = {"easy": ("easy_run",), "long": ("long_run",), "tempo": ("tempo_run",),
         "intervals": ("hill_repeats", "repeated_sprints", "game_like_intervals"),
         "vo2": ("vo2_3x3", "vo2_4x3", "vo2_3x4", "vo2_4x4"),
         "speed_jump": ("accel_sprints", "maxv_sprints", "jump_cmj_broad", "depth_jumps", "cod_drills"),
         "lift": ("strength_lower_heavy", "strength_power", "strength_upper", "strength_single_leg",
                  "strength_maintenance")}


def outcome_frame(f: pd.DataFrame, status: pd.DataFrame) -> pd.DataFrame:
    idx = f.index
    out = pd.DataFrame(index=idx)
    for metric, name in (("ln_hrv", "hrv_swc"), ("resting_hr", "rhr_swc")):
        st = status[status["metric"] == metric].set_index("date")
        st.index = pd.to_datetime(st.index)
        st = st.reindex(idx)
        dev = (st["value"].astype(float) - st["base_center"].astype(float)) / st["swc"].astype(float).where(
            st["swc"].astype(float) > 0)
        out[name] = (dev.shift(-1) + dev.shift(-2)) / 2  # the next two mornings
    out["soreness_next"] = f["soreness"].astype(float).shift(-1)
    out["energy_next"] = f["energy"].astype(float).shift(-1)
    return out


def responses(f: pd.DataFrame, sessions: pd.DataFrame, status: pd.DataFrame) -> list[dict]:
    f = f.sort_index()
    f = f.reindex(pd.date_range(f.index.min(), f.index.max(), freq="D"))
    ind = pd.DataFrame(0.0, index=f.index, columns=list(TYPES))
    for t, g in sessions.groupby("session_type"):
        if t in ind:
            ind.loc[ind.index.isin(pd.to_datetime(g["date"])), t] = 1.0
    use = [t for t in TYPES if ind[t].sum() >= MIN_SESSIONS]
    if not use:
        return []
    outs = outcome_frame(f, status)
    sick = f["is_sick"].astype("boolean").fillna(False).astype(bool) if "is_sick" in f else False
    rows = []
    for outcome in outs.columns:
        df = pd.concat([outs[[outcome]].rename(columns={outcome: "Y"}), ind[use]], axis=1)
        dow = pd.Series(f.index.dayofweek, index=f.index)
        for k in range(1, 7):
            df[f"dow_{k}"] = (dow == k).astype(float)
        df = df[~sick].dropna()
        if len(df) < 30:
            continue
        X, y, consecutive, y_sd, scales, _ = design(df, [(t, False) for t in use])
        m0, v0 = np.zeros(X.shape[1]), np.full(X.shape[1], VAGUE)
        v0[: len(use)] = 0.5**2
        post = fit(X, y, consecutive, m0, v0)
        if post is None:
            continue
        summ = [summarize(post, j, y_sd, 0.2) for j in range(len(use))]
        pooled, _, _, _ = pool([s["estimate_sd"] for s in summ], [s["se_sd"] for s in summ]) if len(use) >= 3 \
            else (np.array([s["estimate_sd"] for s in summ]), None, None, None)
        for j, t in enumerate(use):
            s = summ[j]
            rows.append({"session_type": t, "outcome": outcome, "n_sessions": int(ind[t].sum()),
                         "estimate": s["estimate"], "lo80": s["lo80"], "hi80": s["hi80"],
                         "pooled_estimate": round(float(pooled[j]) * y_sd, 3), "p_direction": s["p_direction"]})
    return rows


def recovery_hours(cost_swc: float | None, hard: bool) -> int:
    """Hours to keep before a game: a session that costs 1+ SWC of HRV over two days needs three days."""
    if cost_swc is None:
        return 48 if hard else 0
    if cost_swc <= -1.0:
        return 72
    if cost_swc <= -0.5:
        return 48
    return 24 if hard else 0


def build_session_response(conn: psycopg.Connection, as_of: date) -> int:
    from healthtracker.stats.insights import load_features_frame

    f = load_features_frame(conn, as_of)
    if f.empty:
        return 0
    sessions = fetch_df(conn, "SELECT date, session_type FROM analytics.session_features WHERE date <= %s", (as_of,))
    status = fetch_df(conn, """SELECT date, metric, value, base_center, swc FROM analytics.metric_status
                               WHERE metric IN ('ln_hrv', 'resting_hr') AND date <= %s""", (as_of,))
    if sessions.empty or status.empty:
        return 0
    rows = [{"computed_on": as_of, **r} for r in responses(f, sessions, status)]
    n = upsert(conn, "analytics.session_response", rows, conflict=["computed_on", "session_type", "outcome"]) if rows else 0
    if rows and is_live(component_status(conn), "COMP_SESSION_RESPONSE"):
        for r in rows:
            if r["outcome"] != "hrv_swc":
                continue
            for code in CODES.get(r["session_type"], ()):
                execute(conn, """UPDATE knowledge.session_library SET recovery_hrv_swc = %s, recovery_source = 'learned',
                                 recovery_n = %s, recovery_hours = %s WHERE code = %s""",
                        (r["pooled_estimate"], r["n_sessions"],
                         recovery_hours(r["pooled_estimate"], code not in ("easy_run", "long_run")), code))
    return n


def library_costs(conn: psycopg.Connection) -> dict[str, dict]:
    return {r["code"]: r for r in fetch_all(conn, """SELECT code, recovery_hours, recovery_hrv_swc, recovery_source
                                                     FROM knowledge.session_library""")}
