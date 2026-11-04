"""Baselines that don't skew themselves (A3) and HRV / resting-HR status bands (A2).

A3: a baseline never includes the acute window it is compared against, it uses the
median and MAD so a few odd nights can't drag it, and it reports how many eligible
days it rests on and how mature it is ("still learning your normal").

A2 (Plews): single-night HRV is noisy, so the signal is a 7-day rolling mean of
ln(HRV), compared with a band of baseline +/- SWC, where SWC = 0.5 x the SD of the
7-day average over the baseline period (60 days, the latest 7 excluded). With five
or more nights in the week the highest and lowest are trimmed first, so one bad night
(a late game, a bad flight) moves nothing; a week of suppressed nights does. The
same method runs on resting HR, where "above" is the warning side.

A medication dose change (H3) is a known change point: maturity restarts from it,
the baseline switches to post-change days once there are enough, and bands are
widened for a while after it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import fetch_all, fetch_df, fetch_one, upsert

MAD_TO_SD = 1.4826
BAND_METRICS = {"ln_hrv": "below", "resting_hr": "above"}  # metric -> the side that is a warning
OTHER_METRICS = ("sleep_total_min", "sleep_midpoint_min", "resp_rate")
MIN_ACUTE_FOR_SWC = 14


@dataclass
class BaselineParams:
    acute_days: int = 7
    acute_min_nights: int = 4
    baseline_days: int = 60
    band_min_days: int = 28
    swc_factor: float = 0.5
    reset_widen_days: int = 28
    reset_widen_factor: float = 2.0
    trim: bool = True  # drop the highest and lowest night of the week (5+ nights)


def load_params(conn: psycopg.Connection) -> BaselineParams:
    rows = fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_BASELINE'")
    p = BaselineParams()
    for r in rows:
        if hasattr(p, r["name"]):
            cur = getattr(p, r["name"])
            setattr(p, r["name"], type(cur)(r["value"]))
    return p


def robust_center_scale(values: np.ndarray) -> tuple[float, float]:
    v = values[~np.isnan(values)]
    if len(v) == 0:
        return float("nan"), float("nan")
    med = float(np.median(v))
    return med, float(MAD_TO_SD * np.median(np.abs(v - med)))


def trimmed_mean(values: np.ndarray, trim: bool = True) -> float:
    """Mean after dropping the single highest and lowest value (when there are 5+)."""
    v = np.sort(values[~np.isnan(values)])
    if trim and len(v) >= 5:
        v = v[1:-1]
    return float(v.mean())


def status_frame(values: pd.Series, eligible: pd.Series, params: BaselineParams, warn_side: str | None = None,
                 resets: list[date] | tuple = ()) -> pd.DataFrame:
    """Per day: acute 7-day value, baseline (acute window excluded), maturity, and, for band
    metrics (`warn_side` set), the SWC band and status. `values` has a daily DatetimeIndex."""
    idx = values.index
    x = values.astype(float).where(eligible.reindex(idx).fillna(False).astype(bool)).to_numpy()
    n, A, B = len(x), params.acute_days, params.baseline_days
    acute, acute_n, cv = np.full(n, np.nan), np.zeros(n, dtype=int), np.full(n, np.nan)
    for i in range(n):
        w = x[max(0, i - A + 1): i + 1]
        ok = w[~np.isnan(w)]
        acute_n[i] = len(ok)
        if len(ok) >= params.acute_min_nights:
            acute[i] = trimmed_mean(ok, params.trim)
            m = ok.mean()
            if len(ok) > 1 and m != 0:
                cv[i] = 100 * ok.std(ddof=1) / abs(m)
    days = np.array([d.date() for d in idx])
    resets = sorted(resets)
    out = {k: np.full(n, np.nan) for k in ("base_center", "base_scale", "swc", "band_lo", "band_hi", "maturity")}
    base_n = np.zeros(n, dtype=int)
    status = np.array([None] * n, dtype=object)
    trend = np.array([None] * n, dtype=object)
    widened = np.zeros(n, dtype=bool)
    for i in range(n):
        hi, lo = i - A, max(0, i - A - B + 1)
        if hi < 0:
            out["maturity"][i] = 0.0
            continue
        window = np.arange(lo, hi + 1)
        reset = next((r for r in reversed(resets) if r <= days[i]), None)
        if reset is not None:
            post = window[days[window] >= reset]
            post_n = int(np.sum(~np.isnan(x[post])))
            if post_n >= params.band_min_days:
                window = post
            widened[i] = (days[i] - reset).days < params.reset_widen_days
        else:
            post_n = int(np.sum(~np.isnan(x[window])))
        b = x[window]
        base_n[i] = int(np.sum(~np.isnan(b)))
        out["maturity"][i] = round(min(1.0, post_n / params.band_min_days), 2)
        if base_n[i] >= 3:
            out["base_center"][i], out["base_scale"][i] = robust_center_scale(b)
        if warn_side is None:
            continue
        past_acute = acute[window]
        past_acute = past_acute[~np.isnan(past_acute)]
        if len(past_acute) >= MIN_ACUTE_FOR_SWC:
            _, sd7 = robust_center_scale(past_acute)
            swc = params.swc_factor * sd7 * (params.reset_widen_factor if widened[i] else 1.0)
            out["swc"][i] = swc
            if base_n[i] >= params.band_min_days and not np.isnan(acute[i]):
                c = out["base_center"][i]
                out["band_lo"][i], out["band_hi"][i] = c - swc, c + swc
                status[i] = "below" if acute[i] < c - swc else "above" if acute[i] > c + swc else "within"
        past_cv = cv[window]
        past_cv = past_cv[~np.isnan(past_cv)]
        if len(past_cv) >= MIN_ACUTE_FOR_SWC and not np.isnan(cv[i]):
            m, s = robust_center_scale(past_cv)
            trend[i] = "rising" if cv[i] > m + 0.5 * s else "falling" if cv[i] < m - 0.5 * s else "stable"
    return pd.DataFrame({"value": values.astype(float).to_numpy(), "acute": acute, "acute_n": acute_n, "cv_7d": cv,
                         **out, "base_n": base_n, "status": status, "cv_trend": trend, "widened": widened}, index=idx)


def reset_dates(conn: psycopg.Connection) -> list[date]:
    """Known change points that restart "normal": medication dose changes (H3), once that table exists."""
    if not fetch_one(conn, "SELECT to_regclass('core.medication_change') IS NOT NULL AS ok")["ok"]:
        return []
    return [r["date"] for r in fetch_all(conn, "SELECT DISTINCT date FROM core.medication_change ORDER BY date")]


def eligible_nights(f: pd.DataFrame) -> pd.Series:
    """Days that may enter a baseline: not sick, and no implausible overnight values."""
    sick = f["is_sick"].astype("boolean").fillna(False).astype(bool) if "is_sick" in f else False
    bad_night = f["sleep_plausible"].astype("boolean").eq(False).fillna(False).astype(bool) \
        if "sleep_plausible" in f else False
    return ~(sick | bad_night)


def compute_metric_status(features: pd.DataFrame, params: BaselineParams, resets: list[date] = ()) -> pd.DataFrame:
    """Long frame (date, metric, ...) for every baseline metric."""
    if features.empty:
        return pd.DataFrame()
    f = features.sort_index()
    f = f.reindex(pd.date_range(f.index.min(), f.index.max(), freq="D"))
    ok = eligible_nights(f)
    frames = []
    for metric in (*BAND_METRICS, *OTHER_METRICS):
        if metric not in f:
            continue
        s = status_frame(f[metric], ok & f[metric].notna(), params, BAND_METRICS.get(metric), resets)
        s["metric"] = metric
        frames.append(s)
    out = pd.concat(frames)
    out.index.name = "date"
    return out.reset_index()


def build_metric_status(conn: psycopg.Connection, end: date, start: date | None = None) -> int:
    f = fetch_df(conn, "SELECT * FROM analytics.daily_features WHERE date <= %s ORDER BY date", (end,))
    if f.empty:
        return 0
    f["date"] = pd.to_datetime(f["date"])
    out = compute_metric_status(f.set_index("date"), load_params(conn), reset_dates(conn))
    start = start or end - timedelta(days=14)
    out = out[out["date"] >= pd.Timestamp(start)]
    rows = []
    for r in out.to_dict("records"):
        row = {"date": r["date"].date(), "metric": r["metric"]}
        for k in ("value", "acute", "cv_7d", "base_center", "base_scale", "swc", "band_lo", "band_hi", "maturity"):
            v = r[k]
            row[k] = None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 3)
        row["acute_n"], row["base_n"] = int(r["acute_n"]), int(r["base_n"])
        row["status"], row["cv_trend"], row["widened"] = r["status"], r["cv_trend"], bool(r["widened"])
        rows.append(row)
    return upsert(conn, "analytics.metric_status", rows, conflict=["date", "metric"])


def latest_status(conn: psycopg.Connection, d: date) -> dict[str, dict]:
    rows = fetch_all(conn, "SELECT * FROM analytics.metric_status WHERE date = %s", (d,))
    return {r["metric"]: r for r in rows}
