"""G2: forecasts with honest intervals, shown only once they beat a naive forecast.

Each series gets the local-linear-trend state-space model D3 and E4 use (statsmodels
Kalman filter) on a daily index, with days that have no measurement left unobserved. From
the last filtered state the level is projected forward (level + slope, both uncertain), so
the 80% interval widens with the horizon; a single future measurement adds the model's
measurement noise on top.

  series          from                                        shown at   tested at
  weight_kg       scale weight                                28 days    14 days
  aerobic_pace    D3's adjusted pace at the reference HR      56 days    28 days
  vo2max          Garmin VO2 max (only the days it changed)   56 days    28 days
  time_trial_5k   performance tests (A8), in minutes          56 days    28 days
  sprint_10m, sprint_30m, cmj, broad_jump  performance tests  56 days    28 days

Aerobic pace is also forecast under the plan: D2's fitness-fatigue model at next week's
planned load, eight weeks out, backtested the same way with the load that actually followed.

Rolling-origin backtests: once a series has `min_obs` measurements, at origins every 7 days
fit on what was known then, forecast `test` days ahead, and compare with the first
measurement at least that far ahead (within two more weeks). The naive forecast is the last
measured value; for daily scale weight, the mean of the last 7 days. A forecast is shown
only with at least 3 tests and a lower mean absolute error than naive, and once the
component is live (COMP_FORECASTS: shadow until the season, brief section 14).

Goal arrival dates: from the projected level, the first day its mean reaches the target
(most likely), and the days the optimistic and pessimistic 80% bounds reach it. The result
is a date range, or "not within a year" when the pessimistic bound never gets there.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.components import component_status, is_live
from healthtracker.db import fetch_df, fetch_one, upsert

log = logging.getLogger(__name__)

Z80 = 1.2815515655446004
PATH_DAYS = 365
MIN_BACKTESTS = 3
STEP_DAYS = 7
MAX_EXTRA_DAYS = 14

SERIES = {
    # name: (show horizon, test horizon, min measurements, naive window in days)
    "weight_kg": (28, 14, 21, 7),
    "aerobic_pace": (56, 28, 10, 0),
    "vo2max": (56, 28, 8, 0),
    "time_trial_5k": (56, 28, 5, 0),
    "sprint_10m": (56, 28, 5, 0),
    "sprint_30m": (56, 28, 5, 0),
    "cmj": (56, 28, 5, 0),
    "broad_jump": (56, 28, 5, 0),
}
TESTS = ("time_trial_5k", "sprint_10m", "sprint_30m", "cmj", "broad_jump")
# goal metric (goals/metrics.py) -> forecast series, in the goal's units (the 5 km test is in minutes)
GOAL_SERIES = {"weight_kg": "weight_kg", "pace_at_ref_hr_s_km": "aerobic_pace", "vo2max": "vo2max",
               "run_5k_min": "time_trial_5k", "sprint_10m_s": "sprint_10m", "sprint_30m_s": "sprint_30m",
               "cmj_cm": "cmj", "broad_jump_cm": "broad_jump"}


# ------------------------------------------------------------------ the model
def fit_llt(s: pd.Series, end: pd.Timestamp):
    """Local linear trend on a daily index from the first measurement to `end`."""
    import statsmodels.api as sm

    s = s.groupby(level=0).mean().sort_index()
    idx = pd.date_range(s.index.min(), end, freq="D")
    y = s.reindex(idx).to_numpy(dtype=float)
    model = sm.tsa.UnobservedComponents(y, level="local linear trend")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return model.fit(disp=False, maxiter=200)


def project(res, days: int) -> dict[str, np.ndarray | float]:
    """Level mean and SD for 1..days ahead of the last filtered state, plus the measurement SD."""
    params = dict(zip(res.model.param_names, res.params, strict=True))
    q = np.diag([max(params.get("sigma2.level", 0.0), 0.0), max(params.get("sigma2.trend", 0.0), 0.0)])
    t = np.array([[1.0, 1.0], [0.0, 1.0]])
    m = np.asarray(res.filtered_state[:, -1], dtype=float)
    p = np.asarray(res.filtered_state_cov[:, :, -1], dtype=float)
    mean, sd = np.empty(days), np.empty(days)
    for h in range(days):
        m, p = t @ m, t @ p @ t.T + q
        mean[h], sd[h] = m[0], np.sqrt(max(p[0, 0], 0.0))
    return {"mean": mean, "sd": sd, "obs_sd": float(np.sqrt(max(params.get("sigma2.irregular", 0.0), 0.0))),
            "slope_week": float(np.asarray(res.filtered_state[:, -1])[1] * 7)}


def point(proj: dict, h: int) -> tuple[float, float, float]:
    """A single future measurement h days ahead: mean and 80% interval."""
    mean = float(proj["mean"][h - 1])
    sd = float(np.hypot(proj["sd"][h - 1], proj["obs_sd"]))
    return mean, mean - Z80 * sd, mean + Z80 * sd


def naive(train: pd.Series, window: int) -> float:
    if window:
        last = train.index.max()
        return float(train[train.index > last - pd.Timedelta(days=window)].mean())
    return float(train.iloc[-1])


def backtest(s: pd.Series, test: int, min_obs: int, window: int) -> dict:
    """Rolling origin: fit on what was known, forecast `test` days ahead, compare with naive."""
    s = s.groupby(level=0).mean().sort_index()
    errs, base, inside = [], [], []
    if s.size > min_obs:
        o = s.index[min_obs - 1]
        while o + pd.Timedelta(days=test) <= s.index.max():
            train = s[s.index <= o]
            ahead = s[s.index >= o + pd.Timedelta(days=test)]
            if not ahead.empty and (ahead.index[0] - o).days <= test + MAX_EXTRA_DAYS:
                when, actual = ahead.index[0], float(ahead.iloc[0])
                try:
                    proj = project(fit_llt(train, o), (when - o).days)
                except Exception:  # noqa: BLE001 - an origin that won't fit is skipped
                    log.debug("forecast backtest fit failed at %s", o)
                else:
                    mean, lo, hi = point(proj, (when - o).days)
                    errs.append(abs(mean - actual))
                    base.append(abs(naive(train, window) - actual))
                    inside.append(lo <= actual <= hi)
            o += pd.Timedelta(days=STEP_DAYS)
    return summarize(errs, base, inside)


def summarize(errs: list[float], base: list[float], inside: list[bool]) -> dict:
    if not errs:
        return {"backtest_n": 0, "mae_model": None, "mae_naive": None, "coverage80": None, "beats_naive": False}
    mae, mae0 = float(np.mean(errs)), float(np.mean(base))
    return {"backtest_n": len(errs), "mae_model": round(mae, 3), "mae_naive": round(mae0, 3),
            "coverage80": round(float(np.mean(inside)), 2), "beats_naive": len(errs) >= MIN_BACKTESTS and mae < mae0}


def forecast_series(metric: str, s: pd.Series, end: date) -> dict | None:
    horizon, test, min_obs, window = SERIES[metric]
    s = s.dropna()
    if s.empty:
        return None
    s = s[pd.DatetimeIndex(s.index) <= pd.Timestamp(end)]
    if s.index.nunique() < min_obs:
        return None
    try:
        proj = project(fit_llt(s, pd.Timestamp(end)), PATH_DAYS)
    except Exception:  # noqa: BLE001
        log.exception("forecast fit failed for %s", metric)
        return None
    mean, lo, hi = point(proj, horizon)
    path = {"start": (end + timedelta(days=1)).isoformat(), "mean": np.round(proj["mean"], 3).tolist(),
            "lo80": np.round(proj["mean"] - Z80 * proj["sd"], 3).tolist(),
            "hi80": np.round(proj["mean"] + Z80 * proj["sd"], 3).tolist()}
    return {"metric": metric, "model": "local_linear_trend", "n_obs": int(s.index.nunique()),
            "last_date": s.index.max().date(), "last_value": round(float(s.groupby(level=0).mean().iloc[-1]), 3),
            "horizon_days": horizon, "test_horizon_days": test,
            "headline": {"date": (end + timedelta(days=horizon)).isoformat(), "mean": round(mean, 3),
                         "lo80": round(lo, 3), "hi80": round(hi, 3), "slope_week": round(proj["slope_week"], 4)},
            "path": path, **backtest(s, test, min_obs, window)}


# ------------------------------------------------------------------ D2 under the plan
def plan_forecast(pace: pd.Series, load: pd.Series, weekly_load: float, test: int = 28) -> dict | None:
    """D2's pace at the reference HR in 8 weeks at the planned load, backtested at `test` days."""
    from healthtracker.features.fitness_fatigue import MIN_RUNS, fit_model, forecast, frame, predict

    df = frame(pace, load)
    if len(df) < MIN_RUNS:
        return None
    model = fit_model(df)
    if model is None:
        return None
    errs, base, inside = [], [], []
    last_origin = None
    for k in range(MIN_RUNS, len(df)):
        o = df.index[k - 1]
        if last_origin is not None and (o - last_origin).days < STEP_DAYS:
            continue
        ahead = df[df.index >= o + pd.Timedelta(days=test)]
        if ahead.empty:
            break
        if (ahead.index[0] - o).days > test + MAX_EXTRA_DAYS:
            continue
        last_origin = o
        m = fit_model(df.iloc[:k])
        if m is None:
            continue
        row = ahead.iloc[0]
        mean, sd = predict(m, row["fitness"], row["fatigue"])  # with the load that actually followed
        errs.append(abs(mean - row["Y"]))
        base.append(abs(df["Y"].iloc[k - 1] - row["Y"]))
        inside.append(mean - Z80 * sd <= row["Y"] <= mean + Z80 * sd)
    f = forecast(model, load, weekly_load)
    end = load.index.max().date()
    return {"metric": "aerobic_pace", "model": "fitness_fatigue", "n_obs": len(df), "last_date": df.index.max().date(),
            "last_value": round(float(df["Y"].iloc[-1]), 3), "horizon_days": 7 * f["weeks"], "test_horizon_days": test,
            "headline": {"date": (end + timedelta(days=7 * f["weeks"])).isoformat(), "mean": float(f["pace_s_km"]),
                         "lo80": float(f["lo80"]), "hi80": float(f["hi80"]), "weekly_load": int(f["weekly_load"])},
            "path": None, **summarize(errs, base, inside)}


# ------------------------------------------------------------------ goal arrival dates
def goal_eta(row: dict, target: float, better: str) -> dict:
    """When the projected level reaches `target`: most likely, earliest, latest (80%)."""
    path = row.get("path")
    if not path:
        return {"likely": None, "earliest": None, "latest": None}
    start = date.fromisoformat(path["start"])
    sign = 1 if better == "higher" else -1

    def first(values: list[float]) -> str | None:
        for i, v in enumerate(values):
            if (v - target) * sign >= 0:
                return (start + timedelta(days=i)).isoformat()
        return None

    opt, pess = (path["hi80"], path["lo80"]) if better == "higher" else (path["lo80"], path["hi80"])
    return {"likely": first(path["mean"]), "earliest": first(opt), "latest": first(pess)}


def goal_forecast(conn: psycopg.Connection, goal: dict, as_of: date, better: str) -> dict | None:
    """A goal's arrival dates, when its series has a forecast that is live and beats naive."""
    series = GOAL_SERIES.get(goal.get("metric") or "")
    if not series or goal.get("target_value") is None or not is_live(component_status(conn), "COMP_FORECASTS"):
        return None
    row = fetch_one(conn, """SELECT * FROM analytics.forecast WHERE metric = %s AND model = 'local_linear_trend'
                             AND computed_on <= %s ORDER BY computed_on DESC LIMIT 1""", (series, as_of))
    if not row or not row["beats_naive"]:
        return None
    return {**goal_eta(row, float(goal["target_value"]), better), "computed_on": row["computed_on"],
            "mae_model": row["mae_model"], "mae_naive": row["mae_naive"], "backtest_n": row["backtest_n"]}


# ------------------------------------------------------------------ from the database
def _empty() -> pd.Series:
    return pd.Series(dtype=float, index=pd.DatetimeIndex([]))


def _series(df: pd.DataFrame, col: str, scale: float = 1.0) -> pd.Series:
    if df.empty:
        return _empty()
    return pd.Series(df[col].astype(float).to_numpy() * scale, index=pd.to_datetime(df["date"])).sort_index()


def load_series(conn: psycopg.Connection, end: date) -> dict[str, pd.Series]:
    out = {"weight_kg": _series(fetch_df(conn, """SELECT date, weight_kg FROM analytics.daily_features
                                                   WHERE weight_kg IS NOT NULL AND date <= %s""", (end,)), "weight_kg"),
           "aerobic_pace": _series(fetch_df(conn, """SELECT date, adjusted FROM analytics.fitness_trend
                                                      WHERE metric = 'aerobic_pace' AND adjusted IS NOT NULL
                                                        AND date <= %s""", (end,)), "adjusted")}
    v = _series(fetch_df(conn, """SELECT date, vo2max FROM core.daily_wellness WHERE vo2max IS NOT NULL
                                  AND date <= %s""", (end,)), "vo2max")
    out["vo2max"] = v[v.ne(v.shift())] if not v.empty else v  # Garmin repeats its last estimate
    tests = fetch_df(conn, """SELECT date, test, value FROM core.performance_test WHERE test = ANY(%s) AND date <= %s""",
                     (list(TESTS), end))
    for t in TESTS:
        part = tests[tests["test"] == t] if not tests.empty else tests
        out[t] = _series(part, "value", 1 / 60 if t == "time_trial_5k" else 1.0) if not part.empty else _empty()
    return out


def build_forecasts(conn: psycopg.Connection, as_of: date) -> int:
    rows = []
    for metric, s in load_series(conn, as_of).items():
        r = forecast_series(metric, s, as_of)
        if r:
            rows.append(r)
    plan = _plan_row(conn, as_of)
    if plan:
        rows.append(plan)
    for r in rows:
        r["computed_on"] = as_of
    return upsert(conn, "analytics.forecast", rows, conflict=["computed_on", "metric", "model"]) if rows else 0


def _plan_row(conn: psycopg.Connection, as_of: date) -> dict | None:
    runs = fetch_df(conn, """SELECT date, adjusted FROM analytics.fitness_trend
                             WHERE metric = 'aerobic_pace' AND adjusted IS NOT NULL AND date <= %s ORDER BY date""",
                    (as_of,))
    load = fetch_df(conn, """SELECT date, value FROM analytics.load_daily WHERE channel = 'internal' AND date <= %s
                             ORDER BY date""", (as_of,))
    if runs.empty or load.empty:
        return None
    idx = pd.date_range(min(pd.Timestamp(load["date"].min()), pd.Timestamp(runs["date"].min())), pd.Timestamp(as_of),
                        freq="D")
    load_s = _series(load, "value").reindex(idx, fill_value=0.0)
    plan = fetch_one(conn, """SELECT summary FROM analytics.week_plan WHERE superseded_at IS NULL AND week_start <= %s
                              ORDER BY week_start DESC LIMIT 1""", (as_of + timedelta(days=7),))
    weekly = ((plan or {}).get("summary") or {}).get("planned_load")
    weekly = float(weekly) if weekly else float(load_s.iloc[-28:].sum() / 4)
    return plan_forecast(_series(runs, "adjusted"), load_s, weekly)
