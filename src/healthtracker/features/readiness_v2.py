"""G1 v2: readiness weights learned from how my sessions went. Shadow.

v1 (features/readiness.py) weighs its components equally. v2 learns the weights from the
next session's outcome, the session on the day of the morning reading, combining three parts,
each signed so that higher means the session went better, and standardized:

  effort   RPE vs plan: the linked planned session's RPE, else my usual RPE for that session
           type (median of the 90 days before, 3 or more sessions); harder than planned is worse
  pace     a steady run's pace at the reference HR vs D3's smoothed level (slower is worse)
  feel     my rating of the session (Garmin feel, 1-5)

A day's outcome is the mean of the parts it has, over that day's sessions. The weights come
from a conjugate Normal-Inverse-Gamma regression (B1) of the outcome on that morning's v1
components (a missing component counts as a typical day, 0), with priors centred on v1's
weights scaled to a modest effect, so with little data v2 stays close to v1. No weight may be
negative: every component is signed so that higher means more ready, and on the 180-day demo
an unconstrained fit gave soreness a negative weight (sore = more ready), which is noise or
confounding. The most negative component is dropped and the rest refitted until none is left.

The medication flag isn't learned: it keeps v1's fixed weight relative to the others, and
flagged days are left out of the learning and of the comparison (medication stays apart).

v2 replaces v1 only past the gate (ANALYSIS_READINESS v2_min_days, v2_min_sessions) and when it
predicts held-out sessions clearly better than v1 (v1's composite calibrated on the same
training days): a rolling-origin backtest, paired day by day, with v2's mean absolute error at
least V2_MARGIN lower and P >= V2_P. Even then it is shown only once COMP_READINESS_V2 is live.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import fetch_all, fetch_df, upsert
from healthtracker.features.readiness import COMPONENTS, LABEL, MIN_COMPONENTS
from healthtracker.stats.bayes import VAGUE, fit
from healthtracker.stats.compare import clearly_better, p_smaller

LEARNED = tuple(c for c in COMPONENTS if c != "flag")
PARTS = ("effort", "pace", "feel")
PRIOR_EFFECT = 0.1   # per component SD, in outcome SDs: v1's equal weights at a modest size
PRIOR_SD = 0.15
MIN_TRAIN = 30       # session days before the first backtest origin
MIN_PART = 10        # a part needs this many sessions to be used
V2_MIN_DAYS, V2_MIN_SESSIONS = 90, 40  # defaults for the gate parameters
V2_MARGIN, V2_P = 0.02, 0.95
REF_DAYS, REF_MIN = 90, 3


# ------------------------------------------------------------------ outcomes
def effort_reference(sessions: pd.DataFrame) -> pd.Series:
    """Planned RPE where a plan is linked, else my median RPE for the type over the 90 days before."""
    ref = sessions["planned_rpe"].astype(float).copy()
    for i, r in sessions.iterrows():
        if not pd.isna(ref.loc[i]):
            continue
        prior = sessions[(sessions["session_type"] == r["session_type"]) & (sessions["date"] < r["date"])
                         & (sessions["date"] >= r["date"] - pd.Timedelta(days=REF_DAYS))]["rpe"].dropna()
        if len(prior) >= REF_MIN:
            ref.loc[i] = float(prior.median())
    return ref


def _standardize(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    if s.notna().sum() < MIN_PART or not s.std() > 0:
        return pd.Series(np.nan, index=s.index)
    return (s - s.mean()) / s.std()


def day_outcomes(sessions: pd.DataFrame, pace: pd.DataFrame) -> pd.DataFrame:
    """One row per session day: each part (standardized, higher = better) and their mean.

    sessions: date, session_type, rpe, planned_rpe, feel (one row per workout)
    pace: date, residual (adjusted pace minus D3's level, s/km; one row per run day)"""
    s = sessions.copy()
    s["date"] = pd.to_datetime(s["date"])
    s = s.sort_values("date")
    s["effort"] = -(s["rpe"].astype(float) - effort_reference(s))
    s["feel"] = s["feel"].astype(float)
    per_day = s.groupby("date")[["effort", "feel"]].mean()
    if not pace.empty:
        p = pace.assign(date=pd.to_datetime(pace["date"])).groupby("date")["residual"].mean()
        per_day = per_day.join((-p).rename("pace"), how="outer")
    else:
        per_day["pace"] = np.nan
    per_day = per_day[list(PARTS)]
    z = per_day.apply(_standardize)
    z["outcome"] = z.mean(axis=1)  # NaN where no part is available
    z["n_parts"] = z[list(PARTS)].notna().sum(axis=1)
    return z.dropna(subset=["outcome"])


# ------------------------------------------------------------------ learning
def prior_means(p: dict) -> np.ndarray:
    w = np.array([p.get(f"w_{c}", 1.0) for c in LEARNED], dtype=float)
    return PRIOR_EFFECT * w / (w.mean() or 1.0)


def _fit_linear(X: np.ndarray, y: np.ndarray, m0: np.ndarray, v0: np.ndarray) -> dict | None:
    """NIG on a standardized y (constant added last). `coef`/`intercept` predict y in its units."""
    y_mean, y_sd = float(y.mean()), float(y.std(ddof=1)) or 1.0
    Xc = np.column_stack([X, np.ones(len(X))])
    post = fit(Xc, (y - y_mean) / y_sd, np.zeros(len(y), dtype=bool), np.r_[m0, 0.0], np.r_[v0, VAGUE], ar=False)
    if post is None:
        return None
    return {"post": post, "coef": post["mn"][:-1] * y_sd, "intercept": float(post["mn"][-1]) * y_sd + y_mean}


def _predict(m: dict, X: np.ndarray) -> np.ndarray:
    return X @ m["coef"] + m["intercept"]


def learn(Z: np.ndarray, y: np.ndarray, m0: np.ndarray) -> dict | None:
    """v2's weights: y on the learned components (NaN -> 0), priors Normal(m0, PRIOR_SD), and no
    negative weights. Each component is signed so that higher means more ready, so a negative
    weight (sore = more ready) is noise or confounding: the most negative one is dropped and the
    rest refitted until none is left (an active set)."""
    Z = np.nan_to_num(Z)
    active = list(range(Z.shape[1]))
    while True:
        m = _fit_linear(Z[:, active], y, m0[active], np.full(len(active), PRIOR_SD ** 2))
        if m is None:
            return None
        b = m["post"]["mn"][:len(active)]
        if not active or (b >= 0).all():
            break
        active.pop(int(np.argmin(b)))
    coef = np.zeros(Z.shape[1])
    coef[active] = m["coef"]
    return {**m, "coef": coef, "active": active}


def calibrate_v1(c1: np.ndarray, y: np.ndarray) -> dict | None:
    """v1's composite mapped to the outcome (intercept and slope, vague): v1's fair stand-in."""
    return _fit_linear(c1.reshape(-1, 1), y, np.zeros(1), np.full(1, VAGUE))


def backtest(Z: np.ndarray, c1: np.ndarray, y: np.ndarray, m0: np.ndarray, min_train: int = MIN_TRAIN) -> dict:
    """Rolling origin over session days in date order: fit both on the days before, predict the next."""
    e1, e2 = [], []
    for k in range(min_train, len(y)):
        m2, m1 = learn(Z[:k], y[:k], m0), calibrate_v1(c1[:k], y[:k])
        if m2 is None or m1 is None:
            continue
        e2.append(abs(float(_predict(m2, np.nan_to_num(Z[k:k + 1]))[0]) - y[k]))
        e1.append(abs(float(_predict(m1, c1[k:k + 1].reshape(-1, 1))[0]) - y[k]))
    if not e2:
        return {"n": 0, "mae_v1": None, "mae_v2": None, "p_better": 0.0}
    return {"n": len(e2), "mae_v1": round(float(np.mean(e1)), 4), "mae_v2": round(float(np.mean(e2)), 4),
            "p_better": round(p_smaller(e2, e1), 3)}


def weights_record(m: dict) -> dict:
    """Learned weights in outcome SDs per component SD, with 80% intervals (none for a dropped
    component) and v1-comparable relative weights (mean 1)."""
    from scipy import stats

    post, active = m["post"], m["active"]
    z = stats.t.ppf(0.9, post["dof"])
    beta = np.zeros(len(LEARNED))
    beta[active] = post["mn"][:len(active)]
    mean = float(beta.mean())
    out = {}
    for i, c in enumerate(LEARNED):
        rec = {"beta": round(float(beta[i]), 4), "relative": round(float(beta[i] / mean), 2) if mean > 0 else 0.0}
        if i in active:
            j = active.index(i)
            s = float(np.sqrt(post["bn"] / post["an"] * post["vn"][j, j]))
            rec |= {"lo80": round(float(beta[i] - z * s), 4), "hi80": round(float(beta[i] + z * s), 4)}
        else:
            rec |= {"lo80": None, "hi80": None, "dropped": True}
        out[c] = rec
    return out


# ------------------------------------------------------------------ v2 scores
def v2_composite(comp: pd.DataFrame, betas: dict[str, float], p: dict) -> pd.Series:
    """Sum of learned weight x component (missing = 0); the flag at v1's relative weight."""
    b = pd.Series(betas)
    z = comp[list(LEARNED)].fillna(0.0)
    out = z.mul(b, axis=1).sum(axis=1)
    w_rest = np.mean([p.get(f"w_{c}", 1.0) for c in LEARNED]) or 1.0
    flag_beta = float(b.mean()) * p.get("w_flag", 1.0) / w_rest
    out -= p["flag_z"] * flag_beta * comp["flag"].fillna(0.0)
    have = comp[list(LEARNED)].notna().sum(axis=1)
    return out.where(have >= MIN_COMPONENTS)


def v2_drivers(row: pd.Series, betas: dict[str, float], p: dict, top: int = 3) -> list[dict]:
    mean = float(np.mean(list(betas.values()))) or 1.0
    items = []
    for c in LEARNED:
        z = row.get(c)
        if z is None or pd.isna(z):
            continue
        contrib = betas[c] * float(z)
        if contrib == 0:
            continue
        items.append({"component": c, "z": round(float(z), 2), "label": LABEL[c][0 if contrib > 0 else 1],
                      "weight": round(betas[c] / mean, 2), "effect": "up" if contrib > 0 else "down"})
    if row.get("flag"):
        items.append({"component": "flag", "z": -p["flag_z"], "label": LABEL["flag"][1],
                      "weight": p.get("w_flag", 1.0), "effect": "down"})
    return sorted(items, key=lambda x: -abs(x["z"] * x["weight"]))[:top]


# ------------------------------------------------------------------ from the database
def load_outcomes(conn: psycopg.Connection, end: date) -> pd.DataFrame:
    sessions = fetch_df(conn, """
        SELECT sf.date, sf.session_type, w.rpe, w.feel,
               (SELECT p.planned_rpe FROM core.planned_session p WHERE p.linked_workout_id = w.id
                  AND p.planned_rpe IS NOT NULL ORDER BY p.id LIMIT 1) AS planned_rpe
        FROM analytics.session_features sf JOIN core.v_workout w ON w.id = sf.workout_id
        WHERE sf.date <= %s AND (w.rpe IS NOT NULL OR w.feel IS NOT NULL)""", (end,))
    pace = fetch_df(conn, """SELECT date, adjusted - level AS residual FROM analytics.fitness_trend
                             WHERE metric = 'aerobic_pace' AND adjusted IS NOT NULL AND level IS NOT NULL
                               AND date <= %s""", (end,))
    if sessions.empty and pace.empty:
        return pd.DataFrame(columns=[*PARTS, "outcome", "n_parts"])
    if sessions.empty:
        sessions = pd.DataFrame(columns=["date", "session_type", "rpe", "feel", "planned_rpe"])
    return day_outcomes(sessions, pace)


def training_rows(comp: pd.DataFrame, composite_v1: pd.Series, out: pd.DataFrame) -> pd.DataFrame:
    """Session days with an outcome and a v1 score, flagged days left out, in date order."""
    df = comp.join(composite_v1.rename("v1"), how="inner").join(out[["outcome"]], how="inner")
    df = df[(df["flag"].fillna(0) == 0) & df["v1"].notna()]
    return df.sort_index()


def build_readiness_model(conn: psycopg.Connection, as_of: date) -> int:
    from healthtracker.features.readiness import load_components, scores

    comp, p = load_components(conn, as_of, days=None)
    if comp.empty:
        return 0
    out = load_outcomes(conn, as_of)
    df = training_rows(comp, scores(comp, p)["composite"], out)
    if len(df) < MIN_TRAIN:
        return 0
    span = int((df.index.max() - df.index.min()).days)
    need_days, need_n = p.get("v2_min_days", V2_MIN_DAYS), p.get("v2_min_sessions", V2_MIN_SESSIONS)
    gate = {"span_days": span, "n_sessions": len(df), "needs_days": int(need_days), "needs_sessions": int(need_n),
            "open": span >= need_days and len(df) >= need_n}
    Z, c1, y, m0 = df[list(LEARNED)].to_numpy(dtype=float), df["v1"].to_numpy(dtype=float), \
        df["outcome"].to_numpy(dtype=float), prior_means(p)
    m = learn(Z, y, m0)
    if m is None:
        return 0
    bt = backtest(Z, c1, y, m0)
    better = clearly_better(bt["mae_v2"], bt["mae_v1"], bt["p_better"], V2_MARGIN, V2_P)
    adopted = gate["open"] and better
    reason = ("not enough session days yet" if not gate["open"] else
              "predicts sessions clearly better than v1" if better else "not clearly better than v1")
    parts = {k: int(out.loc[out.index <= pd.Timestamp(as_of), k].notna().sum()) for k in PARTS}
    row = {"computed_on": as_of, "n_sessions": len(df), "span_days": span, "gate": gate,
           "weights": weights_record(m), "parts": parts,
           "backtest": bt, "adopted": adopted, "reason": reason}
    return upsert(conn, "analytics.readiness_model", [row], conflict=["computed_on"])


def models_before(conn: psycopg.Connection, end: date) -> list[dict]:
    return fetch_all(conn, """SELECT computed_on, weights, adopted FROM analytics.readiness_model
                              WHERE computed_on < %s ORDER BY computed_on""", (end + timedelta(days=1),))
