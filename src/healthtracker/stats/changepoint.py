"""G3: Bayesian online change-point detection (Adams & MacKay 2007).

Series: resting HR, nightly ln HRV, pace at the reference HR (one value per qualifying run,
D3), scale weight, and the sleep midpoint. Each is standardized by its median and MAD and
prewhitened with its own AR(1) carry-over (daily physiology is autocorrelated, and without
this, ordinary runs of high or low days read as shifts; a real shift survives smaller); within
a segment the values are Normal with unknown mean and variance (Normal-Gamma conjugate, so
each step is exact and fast), and a constant hazard (one change per `expected_run`
measurements on average) sets how readily a new segment starts.

A change is reported at measurement s when, `lag` measurements later (20 days; 12 runs for
pace), the probability that a new segment began within `window` measurements of s is at least
`min_prob`, so a shift shows up about three weeks after it happened. It also needs at least
`min_side` measurements on each side, and segment means at least `min_shift_sd` series SDs
apart. Nearby candidates keep only the most probable. The report says "something shifted
around <date>", with the size and direction, and the candidate causes from the days around
it:
  * journal tags and A4 flags (sick, travel, alcohol, stress, time-zone change, DST week,
    hamstring, groin, shoulder, injury)
  * a season phase starting
  * logs whose two-week average moved by half an SD or more (weekly training load, training
    minutes, alcohol, caffeine, sleep duration, bedtime, last meal)
Candidates, not causes: the record says what changed nearby, never why.

Medication (sensitive): a dose change within two weeks of a shift is recorded only in
analytics.change_point_medication, never in the general record or anything the LLM sees. It
is not shown anywhere until a wording for it is approved (workstream H uses approved
templates only).

Shadow until pre-season (COMP_CHANGE_POINTS, brief section 14).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg
from scipy import special

from healthtracker.db import execute, fetch_all, fetch_df, insert

DEFAULTS = {"expected_run": 120, "lag": 20, "min_prob": 0.4, "min_side": 7, "min_shift_sd": 0.5, "window": 4}
# Runs come every two or three days: judge a shift in pace after 12 runs, not 20.
PER_METRIC = {"aerobic_pace": {"lag": 12, "window": 3}}
PRIOR = {"mu0": 0.0, "kappa0": 0.1, "alpha0": 3.0, "beta0": 2.0}  # standardized units: segment SD about 1
METRICS = {
    "resting_hr": ("daily_features", "resting_hr", "resting HR", "bpm"),
    "ln_hrv": ("daily_features", "ln_hrv", "HRV (log)", "ln ms"),
    "aerobic_pace": ("fitness_trend", "adjusted", "pace at the reference HR", "s/km"),
    "weight_kg": ("daily_features", "weight_kg", "weight", "kg"),
    "sleep_midpoint": ("daily_features", "sleep_midpoint_min", "sleep midpoint", "min"),
}
FLAGS = {"is_sick": "sick", "is_travel": "travel", "tag_alcohol": "alcohol tag", "tag_stress": "stress tag",
         "tz_change": "time-zone change", "dst_week": "daylight-saving change", "tag_hamstring": "tight hamstring",
         "tag_groin": "groin tweak", "tag_shoulder": "sore shoulders", "injury_active": "injury"}
LOGS = {"load_week": "weekly training load", "training_min": "training minutes", "alcohol_drinks": "alcohol",
        "caffeine_mg": "caffeine", "sleep_total_min": "sleep duration", "bedtime_hour": "bedtime",
        "last_meal_hour": "last meal time"}
CAUSE_DAYS_BEFORE, CAUSE_DAYS_AFTER, LOG_DAYS, MED_DAYS = 7, 3, 14, 14


# ------------------------------------------------------------------ the detector
def run_length_posterior(x: np.ndarray, hazard: float, prior: dict | None = None) -> np.ndarray:
    """R[t, r]: after t measurements, the probability that the current segment holds the last r
    of them (r = 0: a new segment starts with the next one)."""
    p = {**PRIOR, **(prior or {})}
    n = len(x)
    log_r = np.full((n + 1, n + 1), -np.inf)
    log_r[0, 0] = 0.0
    mu, kappa = np.array([p["mu0"]]), np.array([p["kappa0"]])
    alpha, beta = np.array([p["alpha0"]]), np.array([p["beta0"]])
    log_h, log_1h = np.log(hazard), np.log1p(-hazard)
    for t in range(1, n + 1):
        xt = x[t - 1]
        # Student-t predictive of xt under each current run length
        scale2 = beta * (kappa + 1) / (alpha * kappa)
        nu = 2 * alpha
        log_pred = (special.gammaln((nu + 1) / 2) - special.gammaln(nu / 2) - 0.5 * np.log(np.pi * nu * scale2)
                    - (nu + 1) / 2 * np.log1p((xt - mu) ** 2 / (nu * scale2)))
        prev = log_r[t - 1, :t]
        log_r[t, 1:t + 1] = prev + log_pred + log_1h
        log_r[t, 0] = special.logsumexp(prev + log_pred + log_h)
        log_r[t, :t + 1] -= special.logsumexp(log_r[t, :t + 1])
        mu_new = (kappa * mu + xt) / (kappa + 1)
        beta_new = beta + kappa * (xt - mu) ** 2 / (2 * (kappa + 1))
        mu = np.r_[p["mu0"], mu_new]
        kappa = np.r_[p["kappa0"], kappa + 1]
        alpha = np.r_[p["alpha0"], alpha + 0.5]
        beta = np.r_[p["beta0"], beta_new]
    return np.exp(log_r)


def standardize(v: np.ndarray) -> tuple[np.ndarray, float, float]:
    center = float(np.median(v))
    scale = float(1.4826 * np.median(np.abs(v - center)))
    if scale <= 1e-9:
        scale = float(np.std(v)) or 1.0
    return (v - center) / scale, center, scale


def persistence(z: np.ndarray) -> float:
    """Day-to-day carry-over (AR(1) coefficient) of deviations from a rolling median, so slow
    level changes and real shifts don't count as persistence. Clipped to [0, 0.9]."""
    dev = z - pd.Series(z).rolling(29, center=True, min_periods=7).median().to_numpy()
    a, b = dev[1:], dev[:-1]
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 10 or np.std(a[ok]) == 0 or np.std(b[ok]) == 0:
        return 0.0
    return float(np.clip(np.corrcoef(a[ok], b[ok])[0, 1], 0.0, 0.9))


def prewhiten(z: np.ndarray, phi: float) -> np.ndarray:
    """e_t = z_t - phi z_(t-1): autocorrelated noise would otherwise look like many small shifts.
    A level shift survives as a shift of (1 - phi) times its size."""
    if phi <= 0:
        return z
    e = z.copy()
    e[1:] = z[1:] - phi * z[:-1]
    e[0] = z[0] * np.sqrt(1 - phi ** 2)
    return e


def change_points(s: pd.Series, params: dict | None = None) -> list[dict]:
    """Shifts in a series (index: dates, one value per date), most recent last."""
    p = {**DEFAULTS, **(params or {})}
    s = s.dropna().groupby(level=0).mean().sort_index()
    v = s.to_numpy(dtype=float)
    n, lag, w, side = len(v), int(p["lag"]), int(p["window"]), int(p["min_side"])
    if n < 2 * side + lag:
        return []
    z, _, scale = standardize(v)
    e, _, _ = standardize(prewhiten(z, persistence(z)))
    R = run_length_posterior(e, 1.0 / p["expected_run"])
    # P(a segment began within w measurements of s), judged `lag` measurements after s (0-based s).
    prob = np.zeros(n)
    for s_i in range(1, n - lag + 1):
        t = s_i + lag
        r = t - s_i
        prob[s_i] = R[t, max(r - w, 0):r + w + 1].sum()
    cands = [i for i in np.argsort(-prob) if prob[i] >= p["min_prob"]]
    picked: list[int] = []
    for i in cands:
        if all(abs(i - j) > side for j in picked):
            picked.append(int(i))
    picked.sort()
    sd = float(np.std(v)) or scale
    out = []
    bounds = [0, *picked, n]
    for k, i in enumerate(picked):
        before, after = v[bounds[k]:i], v[i:bounds[k + 2]]
        if len(before) < side or len(after) < side:
            continue
        shift = float(after.mean() - before.mean())
        if abs(shift) < p["min_shift_sd"] * sd:
            continue
        out.append({"date": s.index[i].date(), "prob": round(float(prob[i]), 3), "mean_before": round(float(before.mean()), 3),
                    "mean_after": round(float(after.mean()), 3), "shift": round(shift, 3),
                    "shift_sd": round(shift / sd, 2), "n_before": len(before), "n_after": len(after)})
    return out


# ------------------------------------------------------------------ candidate causes
def causes(f: pd.DataFrame, phases: list[dict], d: date) -> list[dict]:
    """What else changed around `d`: tags and flags, a phase starting, logs that moved."""
    out: list[dict] = []
    if not f.empty:
        near = f[(f.index >= pd.Timestamp(d - timedelta(days=CAUSE_DAYS_BEFORE)))
                 & (f.index <= pd.Timestamp(d + timedelta(days=CAUSE_DAYS_AFTER)))]
        for col, label in FLAGS.items():
            if col in near and near[col].fillna(False).astype(bool).any():
                days = [x.date().isoformat() for x in near.index[near[col].fillna(False).astype(bool)]]
                out.append({"kind": "flag", "what": label, "days": days})
        before = f[(f.index >= pd.Timestamp(d - timedelta(days=LOG_DAYS))) & (f.index < pd.Timestamp(d))]
        after = f[(f.index >= pd.Timestamp(d)) & (f.index < pd.Timestamp(d + timedelta(days=LOG_DAYS)))]
        for col, label in LOGS.items():
            if col not in f:
                continue
            b, a = before[col].astype(float).dropna(), after[col].astype(float).dropna()
            sd = float(f[col].astype(float).std())
            if len(b) >= 5 and len(a) >= 5 and sd > 0 and abs(a.mean() - b.mean()) >= 0.5 * sd:
                out.append({"kind": "log", "what": label, "before": round(float(b.mean()), 1),
                            "after": round(float(a.mean()), 1), "direction": "up" if a.mean() > b.mean() else "down"})
    for ph in phases:
        if abs((ph["start_date"] - d).days) <= CAUSE_DAYS_BEFORE:
            out.append({"kind": "phase", "what": f"{ph['phase'].replace('_', ' ')} phase started",
                        "days": [ph["start_date"].isoformat()]})
    return out


def describe(metric: str, cp: dict) -> str:
    _, _, label, unit = METRICS[metric]
    way = "up" if cp["shift"] > 0 else "down"
    return (f"Something shifted around {cp['date']:%b %d}: {label} went {way} by about "
            f"{abs(cp['shift']):.1f} {unit} ({abs(cp['shift_sd']):.1f} SD)")


# ------------------------------------------------------------------ from the database
def load_series(conn: psycopg.Connection, end: date) -> tuple[dict[str, pd.Series], pd.DataFrame]:
    f = fetch_df(conn, "SELECT * FROM analytics.daily_features WHERE date <= %s ORDER BY date", (end,))
    if not f.empty:
        f = f.set_index(pd.to_datetime(f["date"]))
    out = {}
    for metric, (table, col, _, _) in METRICS.items():
        if table == "daily_features":
            out[metric] = f[col].astype(float).dropna() if not f.empty and col in f else pd.Series(dtype=float)
        else:
            runs = fetch_df(conn, """SELECT date, adjusted FROM analytics.fitness_trend WHERE metric = 'aerobic_pace'
                                     AND adjusted IS NOT NULL AND date <= %s ORDER BY date""", (end,))
            out[metric] = pd.Series(runs["adjusted"].astype(float).to_numpy(), index=pd.to_datetime(runs["date"])) \
                if not runs.empty else pd.Series(dtype=float)
    return out, f


def build_change_points(conn: psycopg.Connection, as_of: date, params: dict | None = None) -> int:
    from healthtracker.planning.season import phases as season_phases

    series, f = load_series(conn, as_of)
    ph = season_phases(conn)
    doses = fetch_all(conn, "SELECT id, medication_id, date FROM core.medication_change WHERE date <= %s", (as_of,))
    execute(conn, "DELETE FROM analytics.change_point_medication")
    execute(conn, "DELETE FROM analytics.change_point")
    n = 0
    for metric, s in series.items():
        for cp in change_points(s, {**PER_METRIC.get(metric, {}), **(params or {})}):
            cid = insert(conn, "analytics.change_point", {
                "computed_on": as_of, "metric": metric, **cp, "summary": describe(metric, cp),
                "causes": causes(f, ph, cp["date"])})
            n += 1
            for dc in doses:  # sensitive: only in its own table
                if abs((dc["date"] - cp["date"]).days) <= MED_DAYS:
                    insert(conn, "analytics.change_point_medication", {
                        "change_point_id": cid, "medication_change_id": dc["id"],
                        "days_from_change": (cp["date"] - dc["date"]).days}, returning=None)
    return n
