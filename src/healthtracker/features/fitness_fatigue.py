"""D2: Banister's impulse-response (fitness-fatigue) model. Shadow, fixed time constants.

performance = p0 + k1 * fitness - k2 * fatigue, where fitness and fatigue are exponentially
weighted sums of daily internal load (A6) with time constants of about 42 and 7 days. The
performance marker is each steady run's pace at the reference HR at reference conditions
(D3), so lower is better: expect fitness to lower pace and fatigue to raise it.

k1 and k2 are fitted with priors (conjugate NIG, B1 machinery): direction from Banister et al.
1975, the conventional small effect, wide SDs. Without priors the model can't tell its
parameters apart; the time constants stay fixed until 4-6 months of runs (then fitted with
priors too).

Uses: compare plans and forecast ("expected pace at the reference HR in 8 weeks if the
weekly load stays at this plan's"), never ground truth. Rolling-origin backtests (fit on the
runs before a date, predict the next run, move on) must beat the naive forecast (the last
run's value) before any forecast is shown.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg
from scipy import stats

from healthtracker.db import fetch_all, fetch_df, upsert
from healthtracker.stats.bayes import VAGUE, design, fit

TAU_FITNESS, TAU_FATIGUE = 42.0, 7.0
PRIOR = ((-0.2, 0.5), (0.2, 0.5))  # fitness lowers pace, fatigue raises it (standardized), Banister et al. 1975
MIN_RUNS = 15


def impulse(load: pd.Series, tau: float) -> pd.Series:
    """x_t = x_(t-1) * exp(-1/tau) + load_t on a daily index (missing days are zero load)."""
    decay = np.exp(-1.0 / tau)
    out, acc = [], 0.0
    for v in load.fillna(0.0).to_numpy(dtype=float):
        acc = acc * decay + v
        out.append(acc)
    return pd.Series(out, index=load.index)


def frame(pace: pd.Series, load: pd.Series, tau1: float = TAU_FITNESS, tau2: float = TAU_FATIGUE) -> pd.DataFrame:
    """One row per run: adjusted pace, and fitness/fatigue as of the day before (today's load comes after)."""
    fit_ = impulse(load, tau1).shift(1)
    fat_ = impulse(load, tau2).shift(1)
    df = pd.DataFrame({"Y": pace}).join(pd.DataFrame({"fitness": fit_, "fatigue": fat_}), how="left").dropna()
    return df


def fit_model(df: pd.DataFrame) -> dict | None:
    if len(df) < 5:
        return None
    X, y, consecutive, y_sd, scales, _ = design(df, [("fitness", True), ("fatigue", True)])
    m0 = np.r_[PRIOR[0][0], PRIOR[1][0], 0.0]
    v0 = np.r_[PRIOR[0][1] ** 2, PRIOR[1][1] ** 2, VAGUE]
    post = fit(X, y, consecutive, m0, v0, ar=False)  # runs are days apart: no AR(1) between them
    if post is None:
        return None
    return {"post": post, "y_mean": float(df["Y"].mean()), "y_sd": y_sd, "means": df[["fitness", "fatigue"]].mean(),
            "scales": scales}


def predict(model: dict, fitness: float, fatigue: float) -> tuple[float, float]:
    """Predicted pace and its predictive SD for given fitness and fatigue."""
    post = model["post"]
    z = np.r_[(fitness - model["means"]["fitness"]) / model["scales"][0],
              (fatigue - model["means"]["fatigue"]) / model["scales"][1], 1.0]
    mean = float(z @ post["mn"]) * model["y_sd"] + model["y_mean"]
    var = post["bn"] / post["an"] * (1 + float(z @ post["vn"] @ z))
    return mean, float(np.sqrt(var)) * model["y_sd"]


def backtest(pace: pd.Series, load: pd.Series, min_train: int = MIN_RUNS) -> dict:
    """Rolling origin: fit on runs before each one, predict it; compare with the last run's value."""
    df = frame(pace, load)
    errs, naive = [], []
    for k in range(min_train, len(df)):
        model = fit_model(df.iloc[:k])
        if model is None:
            continue
        row = df.iloc[k]
        pred, _ = predict(model, row["fitness"], row["fatigue"])
        errs.append(abs(pred - row["Y"]))
        naive.append(abs(df["Y"].iloc[k - 1] - row["Y"]))
    if not errs:
        return {"n": 0, "mae_model": None, "mae_naive": None, "beats_naive": False}
    return {"n": len(errs), "mae_model": round(float(np.mean(errs)), 2), "mae_naive": round(float(np.mean(naive)), 2),
            "beats_naive": float(np.mean(errs)) < float(np.mean(naive))}


def forecast(model: dict, load: pd.Series, weekly_load: float, weeks: int = 8) -> dict:
    """Pace at the reference HR after `weeks` at a steady weekly load, with an 80% interval."""
    future = pd.Series(weekly_load / 7.0, index=pd.date_range(load.index.max() + pd.Timedelta(days=1),
                                                               periods=7 * weeks, freq="D"))
    full = pd.concat([load, future])
    f1, f2 = impulse(full, TAU_FITNESS).iloc[-1], impulse(full, TAU_FATIGUE).iloc[-1]
    mean, sd = predict(model, f1, f2)
    z = stats.norm.ppf(0.9)
    return {"weeks": weeks, "weekly_load": round(weekly_load), "pace_s_km": round(mean, 1),
            "lo80": round(mean - z * sd, 1), "hi80": round(mean + z * sd, 1)}


def build_fitness_fatigue(conn: psycopg.Connection, as_of: date) -> int:
    runs = fetch_df(conn, """SELECT date, adjusted FROM analytics.fitness_trend
                             WHERE metric = 'aerobic_pace' AND adjusted IS NOT NULL AND date <= %s ORDER BY date""",
                    (as_of,))
    load = fetch_df(conn, """SELECT date, value FROM analytics.load_daily WHERE channel = 'internal' AND date <= %s
                             ORDER BY date""", (as_of,))
    if len(runs) < MIN_RUNS or load.empty:
        return 0
    idx = pd.date_range(min(pd.Timestamp(load["date"].min()), pd.Timestamp(runs["date"].min())), pd.Timestamp(as_of),
                        freq="D")
    load_s = pd.Series(load["value"].astype(float).to_numpy(), index=pd.to_datetime(load["date"])).reindex(idx, fill_value=0.0)
    pace = pd.Series(runs["adjusted"].astype(float).to_numpy(), index=pd.to_datetime(runs["date"]))
    model = fit_model(frame(pace, load_s))
    if model is None:
        return 0
    bt = backtest(pace, load_s)
    plan = fetch_all(conn, """SELECT summary FROM analytics.week_plan WHERE superseded_at IS NULL AND week_start <= %s
                              ORDER BY week_start DESC LIMIT 1""", (as_of + timedelta(days=7),))
    weekly = (plan[0]["summary"] or {}).get("planned_load") if plan else None
    weekly = float(weekly) if weekly else float(load_s.iloc[-28:].sum() / 4)
    k = model["post"]["mn"]
    row = {"computed_on": as_of, "tau_fitness": TAU_FITNESS, "tau_fatigue": TAU_FATIGUE,
           "k_fitness": round(float(k[0]) * model["y_sd"] / model["scales"][0], 5),
           "k_fatigue": round(float(k[1]) * model["y_sd"] / model["scales"][1], 5), "n_runs": len(pace),
           **{key: bt[key] for key in ("mae_model", "mae_naive", "beats_naive")}, "backtest_n": bt["n"],
           "forecast": forecast(model, load_s, weekly)}
    return upsert(conn, "analytics.fitness_fatigue", [row], conflict=["computed_on"])
