"""D3: the aerobic fitness trend, from pace at the reference HR on steady runs.

1. Qualifying runs: easy, long, and tempo runs of 20 minutes or more with a pace at the
   reference HR (A5).
2. Conditions. Pace at a given HR is slower when it is hot and humid, on climbs, and later in
   long runs. Each run is adjusted to reference conditions (10 °C, dew point at or below
   10 °C, flat, 45 minutes) with a small Bayesian regression whose coefficients start at
   "no effect", Normal(0, 0.5 SD) per SD of each covariate (the brief's default for effects
   without a published prior), so with few runs the adjustment stays near zero. A covariate
   joins only once enough runs carry it (weather needs A7).
3. Trend. The adjusted paces (irregular days; missing days are simply unobserved) feed a
   local-linear-trend state-space model (statsmodels, Kalman smoother): the underlying
   fitness level and its weekly change, each with an 80% interval. With too few runs for
   that, a local-level model; below the gate the trend is stored but labeled learning.
4. Garmin's VO2 max estimate is smoothed the same way as a secondary marker.
"""

from __future__ import annotations

import logging
import warnings
from datetime import date

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import execute, fetch_all, fetch_df, upsert

log = logging.getLogger("healthtracker.fitness")
Z80 = 1.2816
DEFAULTS = {"min_runs": 10, "min_run_minutes": 20, "decoupling_good_pct": 5, "ref_temp_c": 10, "ref_dew_c": 10,
            "ref_minutes": 45, "prior_sd": 0.5}
STEADY = ("easy", "long", "tempo")


def fitness_params(conn: psycopg.Connection) -> dict[str, float]:
    rows = fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_FITNESS'")
    return {**DEFAULTS, **{r["name"]: float(r["value"]) for r in rows}}


def qualifying_runs(conn: psycopg.Connection, end: date, p: dict) -> pd.DataFrame:
    df = fetch_df(conn, """SELECT f.date, f.workout_id, f.session_type, f.duration_min, f.pace_at_ref_hr_s_km AS pace,
                                  f.decoupling_pct, f.temp_c, f.dew_point_c, w.elev_gain_m, w.distance_m
                           FROM analytics.session_features f JOIN core.workout w ON w.id = f.workout_id
                           WHERE f.sport = 'run' AND f.session_type = ANY(%s) AND f.pace_at_ref_hr_s_km IS NOT NULL
                             AND f.duration_min >= %s AND f.date <= %s ORDER BY f.date, f.workout_id""",
                  (list(STEADY), p["min_run_minutes"], end))
    if df.empty:
        return df
    for c in ("duration_min", "pace", "decoupling_pct", "temp_c", "dew_point_c", "elev_gain_m", "distance_m"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    df["climb_m_km"] = (df["elev_gain_m"] / (df["distance_m"] / 1000)).where(df["distance_m"] > 0)
    return df


def covariates(runs: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Deviations from reference conditions; unknown values sit at the reference (no adjustment)."""
    return pd.DataFrame({
        "temp": (runs["temp_c"] - p["ref_temp_c"]).fillna(0.0),
        "humid": (runs["dew_point_c"] - p["ref_dew_c"]).clip(lower=0).fillna(0.0),
        "climb": runs["climb_m_km"].fillna(0.0),
        "duration": runs["duration_min"] - p["ref_minutes"],
    }, index=runs.index)


def adjust(runs: pd.DataFrame, p: dict) -> tuple[pd.Series, dict]:
    """Pace at reference conditions, from a ridge-like Bayesian regression centered on no effect."""
    y = runs["pace"].to_numpy(dtype=float)
    if len(y) < 5:
        return pd.Series(y, index=runs.index), {"used": [], "note": "too few runs to adjust"}
    x = covariates(runs, p)
    present = {"temp": runs["temp_c"].notna(), "humid": runs["dew_point_c"].notna(),
               "climb": runs["climb_m_km"].notna(), "duration": pd.Series(True, index=runs.index)}
    used = [c for c in x if present[c].sum() >= p["min_runs"] and x[c].std() > 1e-6]
    if not used:
        return pd.Series(y, index=runs.index), {"used": [], "note": "no covariate with enough runs"}
    # Linear time trend in the design so fitness gains aren't credited to the weather.
    t = (pd.to_datetime(runs["date"]) - pd.to_datetime(runs["date"]).min()).dt.days.to_numpy(dtype=float)
    sd_x = x[used].std().to_numpy()
    z = (x[used].to_numpy() - x[used].mean().to_numpy()) / sd_x
    design = np.column_stack([np.ones(len(y)), (t - t.mean()) / max(t.std(), 1.0), z])
    sd_y = float(np.std(y, ddof=1)) or 1.0
    tau = np.r_[1e6, 1e6, np.full(len(used), p["prior_sd"] * sd_y)]  # flat on intercept and time
    sigma2 = sd_y**2
    for _ in range(3):  # a couple of passes to settle the noise variance
        prec = design.T @ design / sigma2 + np.diag(1 / tau**2)
        beta = np.linalg.solve(prec, design.T @ y / sigma2)
        sigma2 = float(np.mean((y - design @ beta) ** 2)) or sigma2
    effect = z @ beta[2:]
    centered = x[used].mean().to_numpy() / sd_x @ beta[2:]  # the adjustment at reference (all zeros)
    adjusted = y - (effect + centered)
    coefs = {c: round(float(b / s), 3) for c, b, s in zip(used, beta[2:], sd_x, strict=True)}
    return pd.Series(adjusted, index=runs.index), {"used": used, "per_unit": coefs,
                                                   "reference": {k: p[k] for k in ("ref_temp_c", "ref_dew_c",
                                                                                   "ref_minutes")}}


def smooth(series: pd.Series, end: date, trend: bool) -> tuple[pd.DataFrame, dict]:
    """Kalman-smoothed level (and slope) on a daily index; days without data are unobserved."""
    import statsmodels.api as sm

    s = series.groupby(level=0).mean().sort_index()
    idx = pd.date_range(s.index.min(), pd.Timestamp(end), freq="D")
    y = s.reindex(idx)
    model = sm.tsa.UnobservedComponents(y.to_numpy(dtype=float), level="local linear trend" if trend else "local level")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = model.fit(disp=False, maxiter=200)
    lvl = res.smoothed_state[0]
    lvl_var = np.clip(res.smoothed_state_cov[0, 0], 0, None)  # tiny negatives are rounding
    out = pd.DataFrame({"level": lvl, "level_lo80": lvl - Z80 * np.sqrt(lvl_var), "level_hi80": lvl + Z80 * np.sqrt(lvl_var)},
                       index=idx)
    if trend:
        slope, slope_var = res.smoothed_state[1] * 7, np.clip(res.smoothed_state_cov[1, 1], 0, None) * 49
        out["slope_week"] = slope
        out["slope_lo80"] = slope - Z80 * np.sqrt(slope_var)
        out["slope_hi80"] = slope + Z80 * np.sqrt(slope_var)
    else:
        out["slope_week"] = out["slope_lo80"] = out["slope_hi80"] = np.nan
    return out, {k: round(float(v), 6) for k, v in zip(res.model.param_names, res.params, strict=True)}


def trend_rows(metric: str, observed: pd.Series, adjusted: pd.Series, end: date, min_trend: int) -> tuple[list[dict], dict]:
    if adjusted.dropna().empty:
        return [], {}
    adj = pd.Series(adjusted.to_numpy(), index=pd.to_datetime(observed.index)).dropna()
    n = int(adj.groupby(level=0).size().size)
    trend = n >= min_trend
    try:
        sm_df, params = smooth(adj, end, trend)
    except Exception:  # noqa: BLE001 - a failed fit just means no trend today
        log.exception("fitness smoothing failed for %s", metric)
        return [], {}
    obs = pd.Series(observed.to_numpy(), index=pd.to_datetime(observed.index)).groupby(level=0).mean()
    adj_day = adj.groupby(level=0).mean()
    counts = adj_day.reindex(sm_df.index).notna().cumsum()
    rows = []
    for d, r in sm_df.iterrows():
        def num(v):
            return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 2)
        rows.append({"date": d.date(), "metric": metric, "observed": num(obs.get(d)), "adjusted": num(adj_day.get(d)),
                     "level": num(r["level"]), "level_lo80": num(r["level_lo80"]), "level_hi80": num(r["level_hi80"]),
                     "slope_week": num(r["slope_week"]), "slope_lo80": num(r["slope_lo80"]),
                     "slope_hi80": num(r["slope_hi80"]), "n_obs": int(counts.loc[d]),
                     "model": "local_linear_trend" if trend else "local_level"})
    return rows, params


def build_fitness(conn: psycopg.Connection, end: date) -> int:
    p = fitness_params(conn)
    out = []
    runs = qualifying_runs(conn, end, p)
    if not runs.empty:
        adjusted, cov = adjust(runs, p)
        rows, params = trend_rows("aerobic_pace", runs.set_index("date")["pace"],
                                  pd.Series(adjusted.to_numpy(), index=runs["date"]), end, int(p["min_runs"]))
        out += rows
        upsert(conn, "analytics.fitness_model", [{"computed_on": end, "metric": "aerobic_pace", "n_obs": len(runs),
                                                  "covariates": cov, "params": params}],
               conflict=["computed_on", "metric"])
    vo2 = fetch_df(conn, """SELECT date, vo2max FROM core.daily_wellness WHERE vo2max IS NOT NULL AND date <= %s
                            ORDER BY date""", (end,))
    if not vo2.empty:
        v = pd.Series(vo2["vo2max"].astype(float).to_numpy(), index=vo2["date"])
        v = v[v.ne(v.shift())]  # Garmin repeats the last estimate; only changes are new information
        rows, params = trend_rows("vo2max", v, v, end, int(p["min_runs"]))
        out += rows
        upsert(conn, "analytics.fitness_model", [{"computed_on": end, "metric": "vo2max", "n_obs": len(v),
                                                  "covariates": None, "params": params}],
               conflict=["computed_on", "metric"])
    execute(conn, "DELETE FROM analytics.fitness_trend")
    return upsert(conn, "analytics.fitness_trend", out, conflict=["date", "metric"]) if out else 0


def decoupling_summary(conn: psycopg.Connection, end: date, p: dict | None = None, n: int = 6) -> dict:
    """Durability: decoupling on recent long steady runs against the threshold (a parameter)."""
    p = p or fitness_params(conn)
    rows = fetch_all(conn, """SELECT date, decoupling_pct FROM analytics.session_features
                              WHERE decoupling_pct IS NOT NULL AND date <= %s ORDER BY date DESC LIMIT %s""", (end, n))
    good = [r for r in rows if r["decoupling_pct"] < p["decoupling_good_pct"]]
    return {"threshold_pct": p["decoupling_good_pct"], "recent": rows[::-1],
            "share_good": round(len(good) / len(rows), 2) if rows else None}


def latest_trend(conn: psycopg.Connection, metric: str, end: date) -> dict | None:
    rows = fetch_all(conn, """SELECT * FROM analytics.fitness_trend WHERE metric = %s AND date <= %s
                              ORDER BY date DESC LIMIT 1""", (metric, end))
    return rows[0] if rows else None
