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

B4, lagged effects ('lagged' hypotheses): the exposure on the first lag and the three days
before it enters as one exponentially decaying sum, sum_j exp(-j / tau) x_(t-j), so an effect
spread over four days (a hard session lowering HRV for two mornings) costs one coefficient
instead of four. tau comes from a small grid (0 = the first lag only, 0.5, 1, 2, 4 days) by
model evidence on identical rows (the same AR(1) rho for every tau, so the evidences
compare). The estimate is the effect at the first lag; the lag profile (the effect at each
lag, the total, and every tau's evidence and weight) goes alongside.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd
import psycopg
from scipy import stats

from healthtracker.db import fetch_all, upsert
from healthtracker.stats.hypothesis import DEFAULT_EXCLUDES, Hypothesis, build_frame, parse_exposure
from healthtracker.stats.pooling import family_of, pool
from healthtracker.stats.pooling import p_beyond as pooled_p_beyond

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
    se_sd: float | None = None
    p_direction: float | None = None
    swc: float | None = None
    p_meaningful: float | None = None
    days_to_80: int | None = None
    prior_mean: float = 0.0
    prior_sd: float = 0.5
    prior_source: str = DEFAULT_PRIOR[2]
    model: str = "nig_ar1"


def prewhiten(X: np.ndarray, y: np.ndarray, consecutive: np.ndarray,
              rho: float | None = None) -> tuple[np.ndarray, np.ndarray, float]:
    pairs = consecutive[1:]
    if rho is None:
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        e = y - X @ beta
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


def fit(X: np.ndarray, y: np.ndarray, consecutive: np.ndarray, m0: np.ndarray, v0: np.ndarray,
        ar: bool = True, rho: float | None = None) -> dict | None:
    """Prewhiten (unless the rows aren't a daily series), then the conjugate update.
    Columns of X are standardized (plus a constant). A given `rho` is used as is."""
    Xs, ys, rho = prewhiten(X, y, consecutive, rho) if ar else (X, y, 0.0)
    if len(ys) < MIN_DAYS:
        return None
    mn, vn, an, bn = nig_posterior(Xs, ys, m0, v0)
    # Log marginal likelihood (model evidence), for comparing models of the same rows.
    _, logdet_n = np.linalg.slogdet(vn)
    log_ev = (-len(ys) / 2 * np.log(2 * np.pi) + 0.5 * (logdet_n - np.sum(np.log(v0)))
              + A0 * np.log(B0) - an * np.log(bn) + math.lgamma(an) - math.lgamma(A0))
    return {"mn": mn, "vn": vn, "dof": 2 * an, "an": an, "bn": bn, "rho": rho, "n": len(ys), "log_ev": float(log_ev)}


def summarize(post: dict, j: int, to_raw: float, swc_sd: float) -> dict:
    """Coefficient j of a fit, in outcome units: median, intervals, P(direction), P(|b| > SWC), days to 0.8."""
    dof, loc = post["dof"], float(post["mn"][j])
    scale = float(np.sqrt(post["bn"] / post["an"] * post["vn"][j, j]))
    q = {k: float(stats.t.ppf(p, dof, loc, scale)) for k, p in
         (("lo95", 0.025), ("lo80", 0.10), ("hi80", 0.90), ("hi95", 0.975))}
    out = {"estimate": round(loc * to_raw, 3), "estimate_sd": round(loc, 3), "se_sd": round(scale, 4),
           **{k: round(v * to_raw, 3) for k, v in q.items()},
           "p_direction": round(float(stats.t.sf(0, dof, loc, scale) if loc > 0 else stats.t.cdf(0, dof, loc, scale)), 3),
           "swc": round(swc_sd * to_raw, 3), "p_meaningful": round(p_beyond(loc, scale, dof, swc_sd), 3),
           "days_to_80": days_to(loc, scale, dof, swc_sd, post["n"])}
    return out


def design(df: pd.DataFrame, exposures: list[tuple[str, bool]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, float,
                                                                         list[float], list[str]]:
    """Standardized outcome; exposures (doses standardized, 0/1 kept), controls, constant."""
    y_sd = float(df["Y"].std()) or 1.0
    y = ((df["Y"] - df["Y"].mean()) / y_sd).to_numpy(dtype=float)
    cols, scales = [], []
    for col, dose in exposures:
        e = df[col].astype(float)
        sc = float(e.std()) if dose else 1.0
        cols.append(((e - e.mean()) / sc).to_numpy() if dose else e.to_numpy())
        scales.append(sc or 1.0)
    ctl = [c for c in df.columns if c.startswith(("dow_", "ctl_"))]
    C = df[ctl].astype(float)
    C = C.loc[:, C.std() > 0]
    C = (C - C.mean()) / C.std()
    X = np.column_stack([*cols, C.to_numpy(), np.ones(len(df))]) if cols else np.ones((len(df), 1))
    idx = pd.DatetimeIndex(df.index)
    consecutive = np.r_[False, (idx[1:] - idx[:-1]).days == 1]
    return X, y, consecutive, y_sd, scales, list(C.columns)


def estimate(df: pd.DataFrame, h: Hypothesis, prior: tuple[float, float, str], swc_sd: float) -> Estimate:
    out = Estimate(h.id, h.kind, len(df), prior_mean=prior[0], prior_sd=prior[1], prior_source=prior[2])
    if len(df) < MIN_DAYS or df["E"].std() == 0:
        return out
    X, y, consecutive, y_sd, scales, _ = design(df, [("E", h.kind == "dose")])
    m0 = np.zeros(X.shape[1])
    m0[0] = prior[0]
    v0 = np.full(X.shape[1], VAGUE)
    v0[0] = prior[1] ** 2
    post = fit(X, y, consecutive, m0, v0)
    if post is None:
        return out
    out.rho = round(post["rho"], 3)
    for k, v in summarize(post, 0, y_sd / scales[0], swc_sd).items():
        setattr(out, k, v)
    return out


# ------------------------------------------------------------------ B4: lagged effects
TAUS = (0.0, 0.5, 1.0, 2.0, 4.0)   # 0: the first lag only
LAG_SPAN = 3                        # the first lag and the three days before it
SHAPE_CLEAR = 0.5                   # the best tau needs this much of the evidence weight to claim a shape


def decay(tau: float) -> np.ndarray:
    return np.array([1.0] + [math.exp(-j / tau) if tau > 0 else 0.0 for j in range(1, LAG_SPAN + 1)])


def lagged_frame(f: pd.DataFrame, h: Hypothesis) -> tuple[pd.DataFrame, bool]:
    """build_frame at the first lag, plus the exposure 1-3 days earlier (E1..E3), on the same rows
    for every tau. Returns the frame and whether the exposure is a dose."""
    from dataclasses import replace as dc_replace

    from healthtracker.stats.hypothesis import exposure_series

    col, op, _ = parse_exposure(h.exposure)
    raw = f[col]
    vals = set(pd.Series(raw).dropna().astype(float).unique()) if len(raw) else set()
    dose = op is None and not (str(raw.dtype) in ("bool", "boolean") or vals <= {0.0, 1.0})
    df, _ = build_frame(f, dc_replace(h, kind="dose" if dose else "binary"))
    ff = f.sort_index()
    ff = ff.reindex(pd.date_range(ff.index.min(), ff.index.max(), freq="D"))
    x = ff[col].astype(float) if dose else exposure_series(ff, h.exposure)
    for j in range(1, LAG_SPAN + 1):
        df[f"E{j}"] = x.shift(j).reindex(df.index)
    return df.dropna(), dose


def estimate_lagged(df: pd.DataFrame, h: Hypothesis, prior: tuple[float, float, str], swc_sd: float,
                    dose: bool) -> tuple[Estimate, dict | None]:
    out = Estimate(h.id, h.kind, len(df), prior_mean=prior[0], prior_sd=prior[1], prior_source=prior[2],
                   model="nig_ar1_decay")
    if len(df) < MIN_DAYS or df["E"].std() == 0:
        return out, None
    lags = df[["E", *[f"E{j}" for j in range(1, LAG_SPAN + 1)]]].to_numpy(dtype=float)
    mean, sd = (float(df["E"].mean()), float(df["E"].std()) or 1.0) if dose else (0.0, 1.0)
    lags = (lags - mean) / sd  # doses per SD of the daily dose, the same scale for every tau
    fits, rho = {}, None
    for tau in TAUS:
        d2 = df.drop(columns=[f"E{j}" for j in range(1, LAG_SPAN + 1)]).copy()
        d2["E"] = lags @ decay(tau)
        X, y, consecutive, y_sd, _, _ = design(d2, [("E", False)])
        m0, v0 = np.zeros(X.shape[1]), np.full(X.shape[1], VAGUE)
        m0[0], v0[0] = prior[0], prior[1] ** 2
        if rho is None:  # from the first-lag model, then fixed so the evidences compare
            _, _, rho = prewhiten(X, y, consecutive)
        post = fit(X, y, consecutive, m0, v0, rho=rho)
        if post is not None:
            fits[tau] = post
    if not fits:
        return out, None
    ev = np.array([fits[t]["log_ev"] for t in fits])
    weights = np.exp(ev - ev.max()) / np.exp(ev - ev.max()).sum()
    best = max(fits, key=lambda t: fits[t]["log_ev"])
    post = fits[best]
    to_raw = float(df["Y"].std() or 1.0) / sd
    out.rho = round(post["rho"], 3)
    for k, v in summarize(post, 0, to_raw, swc_sd).items():
        setattr(out, k, v)
    w = decay(best)
    per = summarize(post, 0, to_raw * sd, swc_sd)  # per SD of a dose (per occurrence for yes/no), unrounded scale
    profile = {"tau": best, "first_lag": h.lag_days, "per_sd": round(sd, 3) if dose else None,
               "shape_clear": bool(weights.max() >= SHAPE_CLEAR),
               "log_ev": {str(t): round(fits[t]["log_ev"], 3) for t in fits},
               "weights": {str(t): round(float(x), 3) for t, x in zip(fits, weights, strict=True)},
               "lags": [{"lag": h.lag_days + j, "weight": round(float(w[j]), 3),
                         **{k: round(per[k] * float(w[j]), 3) for k in ("estimate", "lo80", "hi80", "lo95", "hi95")}}
                        for j in range(LAG_SPAN + 1) if w[j] >= 0.05],
               "total": {k: round(per[k] * float(w.sum()), 3) for k in ("estimate", "lo80", "hi80")}}
    return out, profile


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
        prior = pri.get((h.exposure, h.outcome, h.lag_days), DEFAULT_PRIOR)
        swc_sd = swc.get(h.outcome, swc.get("default", 0.2))
        profile = None
        try:
            if h.kind == "lagged":
                df, dose = lagged_frame(f, h)
                est, profile = estimate_lagged(df, h, prior, swc_sd, dose)
            else:
                df, _ = build_frame(f, h)
                est = estimate(df, h, prior, swc_sd)
        except KeyError:
            continue  # a feature the hypothesis needs isn't built yet
        rows.append({"computed_on": as_of, **asdict(est), "family": family_of(parse_exposure(h.exposure)[0], h.outcome),
                     "pooled_sd": None, "pooled_p_meaningful": None, "lag_profile": profile, "_swc_sd": swc_sd})
    pool_families(rows)
    for r in rows:
        r.pop("_swc_sd")
    return upsert(conn, "analytics.effect_estimate", rows, conflict=["computed_on", "hypothesis_id"]) if rows else 0


def pool_families(rows: list[dict]) -> None:
    """B5: within each family of three or more estimates, pull each toward the family mean."""
    fams: dict[str, list[dict]] = {}
    for r in rows:
        if r["family"] and r["estimate_sd"] is not None and r["se_sd"]:
            fams.setdefault(r["family"], []).append(r)
    for members in fams.values():
        if len(members) < 3:
            continue
        mean, sd, _, _ = pool([m["estimate_sd"] for m in members], [m["se_sd"] for m in members])
        for m, mu, s in zip(members, mean, sd, strict=True):
            m["pooled_sd"] = round(float(mu), 4)
            m["pooled_p_meaningful"] = round(float(pooled_p_beyond(np.array([mu]), np.array([s]), m["_swc_sd"])[0]), 3)
