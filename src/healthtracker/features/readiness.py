"""G1 v1: an in-house readiness score (the Venu 3 has no Garmin readiness). Shadow mode.

A transparent weighted sum of standardized deviations, each signed so that positive means
more ready:

  hrv          7-day ln HRV vs its robust baseline (A2/A3)
  resting_hr   last night's resting HR vs baseline (higher is less ready)
  sleep        last night's sleep vs need (the SLEEP_SHORT minimum), in SDs of my nights
  regularity   7-day sleep regularity vs my 28-day value (E1)
  load         yesterday's load vs my 28-day baseline (higher is less ready)
  soreness     today's journal soreness vs my usual (higher is less ready)
  energy       today's journal energy vs my usual
  flag         a lower-readiness day flagged by code (missed doses plus symptoms, H4); it
               lowers the score, and its reason never leaves the medication tables

Weights start equal: with no published weighting, a unit-weighted composite is the robust
default (Dawes 1979, "The robust beauty of improper linear models"). They are parameters,
and v2 (about 90 days) will learn them and must beat v1 on held-out data first.

Score = 100 x Phi(composite / s), where s is the spread of my composites over the past 60
days, so 50 is a typical day for me. Components missing that day are left out; the score
needs at least three.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg
from scipy import stats

from healthtracker.db import execute, fetch_all, fetch_df, upsert

COMPONENTS = ("hrv", "resting_hr", "sleep", "regularity", "load", "soreness", "energy", "flag")
LABEL = {"hrv": ("HRV above normal", "HRV below normal"), "resting_hr": ("resting HR low", "resting HR up"),
         "sleep": ("slept enough", "short sleep"), "regularity": ("regular sleep", "irregular sleep"),
         "load": ("light recent load", "heavy recent load"), "soreness": ("not sore", "sore"),
         "energy": ("good energy", "low energy"), "flag": ("", "lower-readiness day")}
MIN_COMPONENTS = 3


def readiness_params(conn: psycopg.Connection) -> dict[str, float]:
    rows = fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_READINESS'")
    return {**{f"w_{c}": 1.0 for c in COMPONENTS}, "flag_z": 1.5, **{r["name"]: float(r["value"]) for r in rows}}


def _own_z(s: pd.Series, days: int = 28, min_n: int = 10) -> pd.Series:
    """Deviation from my own trailing mean and SD, the current day left out."""
    past = s.shift(1)
    m = past.rolling(days, min_periods=min_n).mean()
    sd = past.rolling(days, min_periods=min_n).std()
    return (s - m) / sd.where(sd > 0)


def component_frame(f: pd.DataFrame, status: pd.DataFrame, sleep_need: float, flags: pd.Series) -> pd.DataFrame:
    """Signed z per component and day (positive = more ready)."""
    out = pd.DataFrame(index=f.index)

    def band_z(metric: str, col: str) -> pd.Series:
        st = status[status["metric"] == metric].set_index("date") if not status.empty else pd.DataFrame()
        if st.empty:
            return pd.Series(np.nan, index=f.index)
        st.index = pd.to_datetime(st.index)
        st = st.reindex(f.index)
        return (st[col].astype(float) - st["base_center"].astype(float)) / st["base_scale"].astype(float).where(
            st["base_scale"].astype(float) > 0)

    out["hrv"] = band_z("ln_hrv", "acute")
    out["resting_hr"] = -band_z("resting_hr", "value")
    sleep = f["sleep_total_min"].astype(float)
    sleep_sd = sleep.shift(1).rolling(28, min_periods=10).std()
    out["sleep"] = ((sleep - sleep_need) / sleep_sd.where(sleep_sd > 0)).clip(upper=1.0)  # extra sleep helps a little
    sri7 = f["sri_7d"].astype(float) if "sri_7d" in f else pd.Series(np.nan, index=f.index)
    sri_sd = sri7.shift(1).rolling(60, min_periods=14).std()
    out["regularity"] = (sri7 - f.get("sri_28d", pd.Series(np.nan, index=f.index)).astype(float)) / sri_sd.where(
        sri_sd > 0)
    out["load"] = -f["load_z_28d"].astype(float).shift(1)
    out["soreness"] = -_own_z(f["soreness"].astype(float))
    out["energy"] = _own_z(f["energy"].astype(float))
    out["flag"] = flags.reindex(f.index).astype(float).fillna(0.0)
    return out.clip(-3, 3)


def scores(comp: pd.DataFrame, p: dict) -> pd.DataFrame:
    w = pd.Series({c: p[f"w_{c}"] for c in COMPONENTS})
    z = comp.copy()
    z["flag"] = -p["flag_z"] * z["flag"]  # a flagged day counts as flag_z SDs less ready
    have = z.drop(columns="flag").notna()
    wz = z.mul(w, axis=1)
    weight = have.mul(w.drop("flag"), axis=1).sum(axis=1) + np.where(comp["flag"] > 0, w["flag"], 0)
    composite = wz.sum(axis=1, min_count=1) / weight.where(weight > 0)
    composite[have.sum(axis=1) < MIN_COMPONENTS] = np.nan
    spread = composite.shift(1).rolling(60, min_periods=14).std()
    k = have.sum(axis=1).clip(lower=1)
    scale = spread.fillna(1 / np.sqrt(k)).where(spread.fillna(0) > 0.05, 1 / np.sqrt(k))
    out = pd.DataFrame({"composite": composite, "n_components": have.sum(axis=1)}, index=comp.index)
    out["score"] = (100 * stats.norm.cdf(composite / scale)).round()
    out.loc[composite.isna(), "score"] = np.nan
    return out


def drivers(row: pd.Series, p: dict, top: int = 3) -> list[dict]:
    items = []
    for c in COMPONENTS:
        z = row.get(c)
        if z is None or pd.isna(z) or (c == "flag" and not z):
            continue
        signed = -p["flag_z"] if c == "flag" else float(z)
        items.append({"component": c, "z": round(signed, 2), "label": LABEL[c][0 if signed > 0 else 1],
                      "weight": p[f"w_{c}"], "effect": "up" if signed > 0 else "down"})
    return sorted(items, key=lambda x: -abs(x["z"] * x["weight"]))[:top]


def build_readiness(conn: psycopg.Connection, end: date, days: int = 120) -> int:
    from healthtracker.stats.insights import load_features_frame

    f = load_features_frame(conn, end)
    if f.empty:
        return 0
    f = f[f.index >= pd.Timestamp(end - timedelta(days=days + 90))]
    status = fetch_df(conn, """SELECT date, metric, value, acute, base_center, base_scale FROM analytics.metric_status
                               WHERE metric IN ('ln_hrv', 'resting_hr') AND date >= %s""", (f.index.min().date(),))
    need = fetch_all(conn, """SELECT value FROM analytics.rule_parameter
                              WHERE rule_id = 'SLEEP_SHORT' AND name = 'min_minutes'""")
    flags = fetch_df(conn, """SELECT DISTINCT date FROM analytics.medication_message WHERE template = 'readiness'""")
    flag_s = pd.Series(1.0, index=pd.to_datetime(flags["date"])) if not flags.empty else pd.Series(dtype=float)
    p = readiness_params(conn)
    comp = component_frame(f, status, float(need[0]["value"]) if need else 420.0, flag_s)
    sc = scores(comp, p)
    rows = []
    for ts in comp.index[comp.index >= pd.Timestamp(end - timedelta(days=days))]:
        s = sc.loc[ts]
        if pd.isna(s["score"]):
            continue
        rows.append({"date": ts.date(), "score": int(s["score"]), "composite": round(float(s["composite"]), 3),
                     "n_components": int(s["n_components"]),
                     "components": {c: (None if pd.isna(v) else round(float(v), 2)) for c, v in comp.loc[ts].items()
                                    if c != "flag"},
                     "drivers": drivers(comp.loc[ts], p), "version": "v1"})
    execute(conn, "DELETE FROM analytics.readiness_daily WHERE date >= %s", (end - timedelta(days=days),))
    return upsert(conn, "analytics.readiness_daily", rows, conflict=["date"]) if rows else 0
