"""D2: Banister's impulse-response (fitness-fatigue) model. Shadow.

performance = p0 + k1 * fitness - k2 * fatigue, where fitness and fatigue are exponentially
weighted sums of daily internal load (A6) with time constants of about 42 and 7 days. The
performance marker is each steady run's pace at the reference HR at reference conditions
(D3), so lower is better: expect fitness to lower pace and fatigue to raise it.

k1 and k2 are fitted with priors (conjugate NIG, B1 machinery): direction from Banister et al.
1975, the conventional small effect, wide SDs. Without priors the model can't tell its
parameters apart.

Fitted time constants (once runs span ANALYSIS_D2.fit_taus_days, about 5 months, and number
fit_taus_runs): every pair on a grid (fitness 21-60 days, fatigue 3-14) is scored by its model
evidence on the same runs plus a log-normal prior centred on 42 and 7 days, the same idea as
B4's lag decay. The best pair replaces 42/7 only when its rolling-origin backtest, which
re-picks the pair from the runs before each origin, is clearly better on the same runs: a mean
absolute error at least 5% lower and P >= 0.95 in a paired comparison of the run-by-run errors.
A bare "lower error" rule swapped the constants on noise in most simulated histories whose true
constants were 42/7; this one in 8-15%, while still finding constants far from 42/7 (60/3) in 4
of 5 histories at the gate and all of them at 8 months. Otherwise 42/7 stay, and the grid's
verdict is stored for the record. The fitness constant is the hard one to pin down: in
simulation the fatigue constant lands within a grid step, the fitness constant often doesn't
(as in the Banister literature).

Uses: compare plans and forecast ("expected pace at the reference HR in 8 weeks if the
weekly load stays at this plan's"), never ground truth. Rolling-origin backtests (fit on the
runs before a date, predict the next run, move on) must beat the naive forecast (the last
run's value) before any forecast is shown.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg
from scipy import stats
from scipy.special import logsumexp

from healthtracker.db import fetch_all, fetch_df, upsert
from healthtracker.stats.bayes import VAGUE, fit
from healthtracker.stats.compare import clearly_better, p_smaller

TAU_FITNESS, TAU_FATIGUE = 42.0, 7.0
PRIOR = ((-0.2, 0.5), (0.2, 0.5))  # fitness lowers pace, fatigue raises it (standardized), Banister et al. 1975
MIN_RUNS = 15

# Fitted time constants: a grid (roughly even in log days) and log-normal priors on it.
GRID_FITNESS = (21.0, 28.0, 35.0, 42.0, 50.0, 60.0)
GRID_FATIGUE = (3.0, 5.0, 7.0, 10.0, 14.0)
TAU_PRIOR_SD = (0.35, 0.45)  # SD of ln(tau): 80% of the prior on about 27-66 and 4-12 days
FIT_TAUS_DAYS, FIT_TAUS_RUNS = 150, 40  # defaults for ANALYSIS_D2.fit_taus_days / fit_taus_runs
ADOPT_MARGIN, ADOPT_P = 0.05, 0.95  # fitted MAE 5% lower, and P(fitted better) >= 0.95 run by run
FIXED = (TAU_FITNESS, TAU_FATIGUE)


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


def grid_frames(pace: pd.Series, load: pd.Series) -> dict[tuple[float, float], pd.DataFrame]:
    """frame() for every pair on the grid. The rows are the same runs for every pair."""
    imp = {t: impulse(load, t).shift(1) for t in {*GRID_FITNESS, *GRID_FATIGUE}}
    base = pd.DataFrame({"Y": pace})
    return {(t1, t2): base.join(pd.DataFrame({"fitness": imp[t1], "fatigue": imp[t2]}), how="left").dropna()
            for t1 in GRID_FITNESS for t2 in GRID_FATIGUE}


def _fit(a: np.ndarray) -> dict | None:
    """The model on rows of [pace, fitness, fatigue]: what design() + fit() do, in plain numpy
    (the tau grid fits it thousands of times)."""
    if len(a) < 5:
        return None
    y_mean, y_sd = float(a[:, 0].mean()), float(a[:, 0].std(ddof=1)) or 1.0
    means, scales = a[:, 1:].mean(axis=0), a[:, 1:].std(axis=0, ddof=1)
    scales = np.where(scales > 0, scales, 1.0)
    X = np.column_stack([(a[:, 1:] - means) / scales, np.ones(len(a))])
    m0 = np.r_[PRIOR[0][0], PRIOR[1][0], 0.0]
    v0 = np.r_[PRIOR[0][1] ** 2, PRIOR[1][1] ** 2, VAGUE]
    # Runs are days apart: no AR(1) between them.
    post = fit(X, (a[:, 0] - y_mean) / y_sd, np.zeros(len(a), dtype=bool), m0, v0, ar=False)
    if post is None:
        return None
    return {"post": post, "y_mean": y_mean, "y_sd": y_sd,
            "means": {"fitness": float(means[0]), "fatigue": float(means[1])}, "scales": [float(x) for x in scales]}


def _rows(df: pd.DataFrame) -> np.ndarray:
    return df[["Y", "fitness", "fatigue"]].to_numpy(dtype=float)


def fit_model(df: pd.DataFrame) -> dict | None:
    return _fit(_rows(df))


def predict(model: dict, fitness: float, fatigue: float) -> tuple[float, float]:
    """Predicted pace and its predictive SD for given fitness and fatigue."""
    post = model["post"]
    z = np.r_[(fitness - model["means"]["fitness"]) / model["scales"][0],
              (fatigue - model["means"]["fatigue"]) / model["scales"][1], 1.0]
    mean = float(z @ post["mn"]) * model["y_sd"] + model["y_mean"]
    var = post["bn"] / post["an"] * (1 + float(z @ post["vn"] @ z))
    return mean, float(np.sqrt(var)) * model["y_sd"]


# ------------------------------------------------------------------ fitted time constants
def log_tau_prior() -> dict[tuple[float, float], float]:
    """Log-normal priors on the two time constants, normalized over the grid."""
    def one(grid: tuple[float, ...], centre: float, sd: float) -> dict[float, float]:
        lp = np.array([stats.norm.logpdf(np.log(t), np.log(centre), sd) for t in grid])
        return dict(zip(grid, lp - logsumexp(lp), strict=True))

    p1, p2 = one(GRID_FITNESS, TAU_FITNESS, TAU_PRIOR_SD[0]), one(GRID_FATIGUE, TAU_FATIGUE, TAU_PRIOR_SD[1])
    return {(a, b): p1[a] + p2[b] for a in GRID_FITNESS for b in GRID_FATIGUE}


def select_taus(frames: dict[tuple[float, float], pd.DataFrame | np.ndarray], upto: int | None = None) -> dict | None:
    """Posterior over the grid from the first `upto` runs: each pair's evidence times its prior."""
    prior = log_tau_prior()
    score, models = {}, {}
    for pair, rows in frames.items():
        a = _rows(rows) if isinstance(rows, pd.DataFrame) else rows
        m = _fit(a if upto is None else a[:upto])
        if m is not None:
            score[pair], models[pair] = m["post"]["log_ev"] + prior[pair], m
    if not score:
        return None
    pairs = list(score)
    s = np.array([score[p] for p in pairs])
    w = np.exp(s - logsumexp(s))
    best = pairs[int(np.argmax(s))]
    return {"best": best, "model": models[best], "weights": dict(zip(pairs, w.tolist(), strict=True)),
            "mean": (float(w @ [p[0] for p in pairs]), float(w @ [p[1] for p in pairs]))}


# ------------------------------------------------------------------ backtests and forecasts
def backtest(pace: pd.Series, load: pd.Series, min_train: int = MIN_RUNS, fitted: bool = False,
             frames: dict | None = None) -> dict:
    """Rolling origin: fit on runs before each one, predict it; compare with the last run's value.
    `fitted` re-picks the time constants from the runs before each origin (no peeking ahead)."""
    frames = (frames or grid_frames(pace, load)) if fitted else {FIXED: frame(pace, load)}
    arrays = {pair: _rows(df) for pair, df in frames.items()}
    a = arrays[FIXED]
    errs, naive, at = [], [], []
    for k in range(min_train, len(a)):
        if fitted:
            sel = select_taus(arrays, k)
            model, row = (sel["model"], arrays[sel["best"]][k]) if sel else (None, None)
        else:
            model, row = _fit(a[:k]), a[k]
        if model is None:
            continue
        pred, _ = predict(model, row[1], row[2])
        errs.append(abs(pred - row[0]))
        naive.append(abs(a[k - 1, 0] - row[0]))
        at.append(k)
    if not errs:
        return {"n": 0, "mae_model": None, "mae_naive": None, "beats_naive": False}
    return {"n": len(errs), "mae_model": round(float(np.mean(errs)), 2), "mae_naive": round(float(np.mean(naive)), 2),
            "beats_naive": float(np.mean(errs)) < float(np.mean(naive)), "errors": errs, "at": at}


def p_better(bt: dict, other: dict) -> float:
    """One-sided P that `bt`'s errors are smaller than `other`'s, paired run by run (t test)."""
    a, b = dict(zip(bt.get("at", []), bt.get("errors", []), strict=True)), \
        dict(zip(other.get("at", []), other.get("errors", []), strict=True))
    common = sorted(a.keys() & b.keys())
    return p_smaller([a[k] for k in common], [b[k] for k in common])


def forecast(model: dict, load: pd.Series, weekly_load: float, weeks: int = 8,
             taus: tuple[float, float] = FIXED) -> dict:
    """Pace at the reference HR after `weeks` at a steady weekly load, with an 80% interval."""
    future = pd.Series(weekly_load / 7.0, index=pd.date_range(load.index.max() + pd.Timedelta(days=1),
                                                               periods=7 * weeks, freq="D"))
    full = pd.concat([load, future])
    f1, f2 = impulse(full, taus[0]).iloc[-1], impulse(full, taus[1]).iloc[-1]
    mean, sd = predict(model, f1, f2)
    z = stats.norm.ppf(0.9)
    return {"weeks": weeks, "weekly_load": round(weekly_load), "pace_s_km": round(mean, 1),
            "lo80": round(mean - z * sd, 1), "hi80": round(mean + z * sd, 1)}


def choose_taus(pace: pd.Series, load: pd.Series, fixed_bt: dict, need_days: float = FIT_TAUS_DAYS,
                need_runs: float = FIT_TAUS_RUNS) -> tuple[dict | None, dict]:
    """The grid's verdict, and the selection to use instead of 42/7 (None: keep them).
    Fitted constants are used only past the gate and when their honest backtest is clearly better."""
    span = int((pace.index.max() - pace.index.min()).days) if len(pace) else 0
    record: dict = {"gate": {"span_days": span, "n_runs": len(pace), "needs_days": int(need_days),
                             "needs_runs": int(need_runs), "open": span >= need_days and len(pace) >= need_runs}}
    if not record["gate"]["open"]:
        return None, {**record, "used": False, "reason": "not enough runs yet"}
    frames = grid_frames(pace, load)
    sel = select_taus(frames)
    if sel is None:
        return None, {**record, "used": False, "reason": "no fit"}
    bt = backtest(pace, load, fitted=True, frames=frames)
    top = sorted(sel["weights"].items(), key=lambda kv: -kv[1])[:5]
    p = p_better(bt, fixed_bt)
    used = clearly_better(bt["mae_model"], fixed_bt["mae_model"], p, ADOPT_MARGIN, ADOPT_P)
    record |= {"best": {"fitness": sel["best"][0], "fatigue": sel["best"][1]},
               "best_weight": round(sel["weights"][sel["best"]], 3),
               "mean": {"fitness": round(sel["mean"][0], 1), "fatigue": round(sel["mean"][1], 1)},
               "top": [{"fitness": p[0], "fatigue": p[1], "weight": round(w, 3)} for p, w in top],
               "backtest": {k: bt[k] for k in ("n", "mae_model", "mae_naive", "beats_naive")},
               "mae_fixed": fixed_bt["mae_model"], "p_better": round(p, 3), "used": used,
               "reason": "clearly better than 42/7 in the backtest" if used else "not clearly better than 42/7"}
    return ({**sel, "backtest": bt} if used else None), record


def _params(conn: psycopg.Connection) -> dict[str, float]:
    return {r["name"]: float(r["value"]) for r in
            fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_D2'")}


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
    p = _params(conn)
    try:  # fitted constants are optional: if they fail, 42/7 still give a row
        sel, tau_fit = choose_taus(pace, load_s, bt, p.get("fit_taus_days", FIT_TAUS_DAYS),
                                   p.get("fit_taus_runs", FIT_TAUS_RUNS))
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("fitting D2 time constants failed; keeping 42/7")
        sel, tau_fit = None, {"used": False, "reason": "fitting failed"}
    taus = FIXED
    if sel is not None:
        model, bt, taus = sel["model"], sel["backtest"], sel["best"]
    plan = fetch_all(conn, """SELECT summary FROM analytics.week_plan WHERE superseded_at IS NULL AND week_start <= %s
                              ORDER BY week_start DESC LIMIT 1""", (as_of + timedelta(days=7),))
    weekly = (plan[0]["summary"] or {}).get("planned_load") if plan else None
    weekly = float(weekly) if weekly else float(load_s.iloc[-28:].sum() / 4)
    k = model["post"]["mn"]
    row = {"computed_on": as_of, "tau_fitness": taus[0], "tau_fatigue": taus[1],
           "tau_source": "fitted" if sel is not None else "fixed", "tau_fit": tau_fit,
           "k_fitness": round(float(k[0]) * model["y_sd"] / model["scales"][0], 5),
           "k_fatigue": round(float(k[1]) * model["y_sd"] / model["scales"][1], 5), "n_runs": len(pace),
           **{key: bt[key] for key in ("mae_model", "mae_naive", "beats_naive")}, "backtest_n": bt["n"],
           "forecast": forecast(model, load_s, weekly, taus=taus)}
    return upsert(conn, "analytics.fitness_fatigue", [row], conflict=["computed_on"])
