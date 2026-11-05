"""E4: long-term trends and the WHO activity guidelines.

Trends: resting HR, nightly HRV, Garmin VO2 max, and weight, each through the same
local-linear-trend state-space model as D3 (Kalman smoother, 80% intervals), reported as
the level and the change per month on the first of each month and the latest day. Gate:
60 days of the metric.

WHO 2020: 150-300 minutes of moderate or 75-150 of vigorous aerobic activity a week (or an
equivalent mix, moderate + 2 x vigorous >= 150), plus muscle strengthening on at least two
days. Minutes come from Garmin's intensity minutes plus sessions logged without the watch
(games and field sessions at RPE 7+ count as vigorous, otherwise moderate); a strength day
is a day with lift sets or a strength session.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import execute, fetch_df, upsert
from healthtracker.features.fitness import smooth
from healthtracker.profile import load_profile

MIN_DAYS = 60
WHO_MIN, WHO_STRENGTH = 150, 2


def build_long_trends(conn: psycopg.Connection, end: date) -> int:
    f = fetch_df(conn, """SELECT date, resting_hr, hrv, weight_kg FROM analytics.daily_features
                          WHERE date <= %s ORDER BY date""", (end,))
    vo2 = fetch_df(conn, "SELECT date, vo2max FROM core.daily_wellness WHERE vo2max IS NOT NULL AND date <= %s",
                   (end,))
    series = {c: pd.Series(f[c].astype(float).to_numpy(), index=pd.to_datetime(f["date"])).dropna()
              for c in ("resting_hr", "hrv", "weight_kg")} if not f.empty else {}
    if not vo2.empty:
        v = pd.Series(vo2["vo2max"].astype(float).to_numpy(), index=pd.to_datetime(vo2["date"]))
        series["vo2max"] = v[v.ne(v.shift())]
    rows = []
    for metric, s in series.items():
        if s.index.nunique() < MIN_DAYS and (s.index.max() - s.index.min()).days < MIN_DAYS:
            continue
        try:
            sm, _ = smooth(s, end, trend=True)
        except Exception:  # noqa: BLE001 - a failed fit just means no trend this time
            continue
        counts = s.groupby(level=0).size().reindex(sm.index).fillna(0).cumsum()
        keep = sm.index[(sm.index.day == 1) | (sm.index == sm.index.max())]
        for ts in keep:
            r = sm.loc[ts]

            def num(v, k=30 / 7):
                return None if pd.isna(v) else round(float(v) * k, 3)
            rows.append({"date": ts.date(), "metric": metric, "level": round(float(r["level"]), 2),
                         "lo80": round(float(r["level_lo80"]), 2), "hi80": round(float(r["level_hi80"]), 2),
                         "slope_month": num(r["slope_week"]), "slope_lo80": num(r["slope_lo80"]),
                         "slope_hi80": num(r["slope_hi80"]), "n_obs": int(counts.loc[ts])})
    execute(conn, "DELETE FROM analytics.long_trend")
    return upsert(conn, "analytics.long_trend", rows, conflict=["date", "metric"]) if rows else 0


def weekly_activity(conn: psycopg.Connection, end: date, weeks: int = 26) -> list[dict]:
    tz = load_profile(conn).timezone
    start = end - timedelta(days=7 * weeks)
    well = fetch_df(conn, """SELECT date, intensity_min_mod AS mod, intensity_min_vig AS vig FROM core.daily_wellness
                             WHERE date BETWEEN %s AND %s""", (start, end))
    manual = fetch_df(conn, """SELECT (start_ts AT TIME ZONE %(tz)s)::date AS date, duration_s, rpe FROM core.v_workout
                               WHERE source = 'manual' AND sport <> 'strength'
                                 AND (start_ts AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s""",
                      {"tz": tz, "s": start, "e": end})
    strength = fetch_df(conn, """SELECT DISTINCT (w.start_ts AT TIME ZONE %(tz)s)::date AS date FROM core.v_workout w
                                 WHERE (w.sport = 'strength' OR EXISTS (SELECT 1 FROM core.lift_set s WHERE s.workout_id = w.id))
                                   AND (w.start_ts AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s""",
                        {"tz": tz, "s": start, "e": end})
    idx = pd.date_range(start, end, freq="D")
    d = pd.DataFrame(index=idx)
    w = well.set_index(pd.to_datetime(well["date"])) if not well.empty else pd.DataFrame(columns=["mod", "vig"])
    d["mod"] = w["mod"].astype(float).reindex(idx) if not w.empty else np.nan
    d["vig"] = w["vig"].astype(float).reindex(idx) if not w.empty else np.nan
    d["data"] = d["mod"].notna() | d["vig"].notna()
    d[["mod", "vig"]] = d[["mod", "vig"]].fillna(0.0)
    for r in manual.itertuples():
        ts = pd.Timestamp(r.date)
        if ts in d.index:
            d.loc[ts, "vig" if (r.rpe or 0) >= 7 else "mod"] += r.duration_s / 60
            d.loc[ts, "data"] = True
    d["strength"] = d.index.isin(pd.to_datetime(strength["date"])) if not strength.empty else False
    d["week"] = d.index - pd.to_timedelta(d.index.weekday, unit="D")
    rows = []
    for wk, g in d.groupby("week"):
        if len(g) < 7:
            continue
        mod, vig = int(round(g["mod"].sum())), int(round(g["vig"].sum()))
        days = int(g["strength"].sum())
        rows.append({"week_start": wk.date(), "moderate_min": mod, "vigorous_min": vig, "equivalent_min": mod + 2 * vig,
                     "strength_days": days, "meets_aerobic": mod + 2 * vig >= WHO_MIN, "meets_strength": days >= WHO_STRENGTH,
                     "days_with_data": int(g["data"].sum())})
    return rows


def build_health_weekly(conn: psycopg.Connection, end: date) -> int:
    rows = weekly_activity(conn, end)
    return upsert(conn, "analytics.health_weekly", rows, conflict=["week_start"]) if rows else 0
