"""B1: Bayesian effect estimates in place of pass/fail tests.

For each hypothesis: outcome_t = a + b * exposure_(t - lag) + controls + AR(1) errors.

  1. The same rows, exclusions, and controls as the OLS cross-check (hypothesis.build_frame).
     Doses (B3) enter as numbers; yes/no exposures as 0/1.
  2. Outcome and dose are standardized, so priors (B2) are in outcome SDs (per SD of dose).
  3. AR(1) prewhitening: rho from the OLS residuals on consecutive days, then
     y*_t = y_t - rho y_(t-1) and the same for every column (Cochrane-Orcutt), consecutive
     days only.
  4. Conjugate Normal-Inverse-Gamma update: b | s2 ~ N(m0, s2 V0), s2 ~ IG(2, 1); the
     exposure's prior from knowledge.effect_priors (Normal(0, 0.5 SD) without a source),
     everything else vague. The marginal posterior of b is a Student t.

Outputs: posterior median, 80% and 95% intervals, P(direction), P(|b| > SWC) with the SWC
from rule_parameter (N3), days used, and the days until P(|b| > SWC) would reach 0.8 if the
estimate holds (the posterior SD shrinks with the square root of the days). Fast, exact, and
deterministic: no sampling.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd
import psycopg
from scipy import stats

from healthtracker.db import fetch_all, upsert
from healthtracker.stats.hypothesis import DEFAULT_EXCLUDES, Hypothesis, build_frame

DEFAULT_PRIOR = (0.0, 0.5, "no published prior: Normal(0, 0.5 SD) (brief B2 default)")
A0, B0 = 2.0, 1.0       # IG prior on the residual variance of the standardized outcome (mean 1)
VAGUE = 100.0           # prior variance for the intercept and controls
MIN_DAYS = 10


@dataclass
class Estimate:
    hypothesis_id: int
    kind: str
    n_days: int
    rho: float | None = None
    estimate: float | None = None
    lo80: float | None = None
    hi80: float | None = None
    lo95: float | None = None
    hi95: float | None = None
    estimate_sd: float | None = None
    p_direction: float | None = None
    swc: float | None = None
    p_meaningful: float | None = None
    days_to_80: int | None = None
    prior_mean: float = 0.0
    prior_sd: float = 0.5
    prior_source: str = DEFAULT_PRIOR[2]
    model: str = "nig_ar1"


def prewhiten(X: np.ndarray, y: np.ndarray, consecutive: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    e = y - X @ beta
    pairs = consecutive[1:]
    rho = 0.0
    if pairs.sum() >= 5:
        a, b = e[1:][pairs], e[:-1][pairs]
        rho = float(np.clip(np.dot(a, b) / max(np.dot(b, b), 1e-12), 0.0, 0.9))
    Xs = X[1:][pairs] - rho * X[:-1][pairs]
    ys = y[1:][pairs] - rho * y[:-1][pairs]
    return Xs, ys, rho


def nig_posterior(X: np.ndarray, y: np.ndarray, m0: np.ndarray, v0: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    v0_inv = np.diag(1 / v0)
    vn = np.linalg.inv(v0_inv + X.T @ X)
    mn = vn @ (v0_inv @ m0 + X.T @ y)
    an = A0 + len(y) / 2
    bn = B0 + 0.5 * float(y @ y + m0 @ v0_inv @ m0 - mn @ np.linalg.solve(vn, mn))
    return mn, vn, an, max(bn, 1e-9)


def p_beyond(loc: float, scale: float, dof: float, swc: float) -> float:
    """P(|b| > swc) under a Student t."""
    return float(stats.t.sf(swc, dof, loc, scale) + stats.t.cdf(-swc, dof, loc, scale))


def days_to(loc: float, scale: float, dof: float, swc: float, n: int, target: float = 0.8,
            horizon: int = 3650) -> int | None:
    """More days until P(|b| > swc) reaches the target if the estimate holds (SD shrinks as 1/sqrt(n))."""
    if p_beyond(loc, scale, dof, swc) >= target:
        return 0
    if abs(loc) <= swc:
        return None
    lo, hi = n, n + horizon
    if p_beyond(loc, scale * np.sqrt(n / hi), dof, swc) < target:
        return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if p_beyond(loc, scale * np.sqrt(n / mid), dof, swc) >= target:
            hi = mid
        else:
            lo = mid
    return hi - n


def estimate(df: pd.DataFrame, h: Hypothesis, prior: tuple[float, float, str], swc_sd: float) -> Estimate:
    out = Estimate(h.id, h.kind, len(df), prior_mean=prior[0], prior_sd=prior[1], prior_source=prior[2])
    if len(df) < MIN_DAYS or df["E"].std() == 0:
        return out
    y_sd = float(df["Y"].std()) or 1.0
    y = ((df["Y"] - df["Y"].mean()) / y_sd).to_numpy(dtype=float)
    e = df["E"].astype(float)
    e_scale = float(e.std()) if h.kind == "dose" else 1.0
    e = ((e - e.mean()) / e_scale) if h.kind == "dose" else e
    ctl = [c for c in df.columns if c.startswith(("dow_", "ctl_"))]
    C = df[ctl].astype(float)
    C = C.loc[:, C.std() > 0]
    C = (C - C.mean()) / C.std()
    X = np.column_stack([e.to_numpy(), C.to_numpy(), np.ones(len(df))])
    idx = pd.DatetimeIndex(df.index)
    consecutive = np.r_[False, (idx[1:] - idx[:-1]).days == 1]
    Xs, ys, rho = prewhiten(X, y, consecutive)
    if len(ys) < MIN_DAYS:
        return out
    m0 = np.zeros(X.shape[1])
    m0[0] = prior[0]
    v0 = np.full(X.shape[1], VAGUE)
    v0[0] = prior[1] ** 2
    mn, vn, an, bn = nig_posterior(Xs, ys, m0, v0)
    dof, loc, scale = 2 * an, float(mn[0]), float(np.sqrt(bn / an * vn[0, 0]))
    to_raw = y_sd / e_scale  # standardized -> outcome units per exposed day (or per dose unit)
    q = {k: float(stats.t.ppf(p, dof, loc, scale)) for k, p in
         (("lo95", 0.025), ("lo80", 0.10), ("hi80", 0.90), ("hi95", 0.975))}
    out.rho = round(rho, 3)
    out.estimate = round(loc * to_raw, 3)
    out.estimate_sd = round(loc, 3)
    for k, v in q.items():
        setattr(out, k, round(v * to_raw, 3))
    out.p_direction = round(float(stats.t.sf(0, dof, loc, scale) if loc > 0 else stats.t.cdf(0, dof, loc, scale)), 3)
    out.swc = round(swc_sd * to_raw, 3)
    out.p_meaningful = round(p_beyond(loc, scale, dof, swc_sd), 3)
    out.days_to_80 = days_to(loc, scale, dof, swc_sd, len(ys))
    return out


# ------------------------------------------------------------------ the weekly job
def priors(conn: psycopg.Connection) -> dict[tuple[str, str, int], tuple[float, float, str]]:
    return {(r["exposure"], r["outcome"], r["lag_days"]): (float(r["mean_sd"]), float(r["sd_sd"]), r["source"])
            for r in fetch_all(conn, "SELECT * FROM knowledge.effect_priors")}


def swc_params(conn: psycopg.Connection) -> dict[str, float]:
    return {r["name"]: float(r["value"]) for r in
            fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_SWC'")}


def run_effects(conn: psycopg.Connection, as_of: date, features: pd.DataFrame | None = None) -> int:
    from healthtracker.stats.insights import load_features_frame

    f = load_features_frame(conn, as_of) if features is None else features
    if f.empty:
        return 0
    pri, swc = priors(conn), swc_params(conn)
    rows = []
    for r in fetch_all(conn, "SELECT * FROM analytics.hypothesis WHERE status <> 'retired' ORDER BY id"):
        h = Hypothesis(r["id"], r["exposure"], r["outcome"], r["lag_days"], r["min_n_per_group"],
                       tuple(r.get("excludes") or DEFAULT_EXCLUDES), r.get("kind") or "binary")
        try:
            df, _ = build_frame(f, h)
        except KeyError:
            continue  # a feature the hypothesis needs isn't built yet
        prior = pri.get((h.exposure, h.outcome, h.lag_days), DEFAULT_PRIOR)
        est = estimate(df, h, prior, swc.get(h.outcome, swc.get("default", 0.2)))
        rows.append({"computed_on": as_of, **asdict(est)})
    return upsert(conn, "analytics.effect_estimate", rows, conflict=["computed_on", "hypothesis_id"]) if rows else 0
