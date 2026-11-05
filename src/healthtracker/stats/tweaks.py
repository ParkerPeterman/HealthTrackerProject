"""F2 / B6: what goes with a groin tweak. Rare events, so a Bayesian logistic model with
strong priors, and exploratory only.

Outcome: a one-tap groin tweak on day t. Candidate factors (brief F2), each over day t and the
day before, since a tweak follows the session that caused it:
  * change of direction: game minutes (most of the cutting in my week)
  * sprint metres and plyometric contacts (A6 channels)
  * cold: the lowest home temperature of the two days below 5 C (A7, once a home is set)
  * sleep: a night under 7 h before the tweak day
  * car travel: a travel day

Model: log-odds = a + sum b_k z_k with z the standardized factors (yes/no left as 0/1). The
priors are strong because events are few: Normal(0, 0.5) on each b (log-odds per SD), and
the intercept centred on my base rate with SD 1.5. The posterior is a Laplace approximation
(Newton steps to the mode, the Hessian for its spread). Each factor gets its odds ratio per
SD (or yes vs no) with an 80% interval and P(OR > 1).

Gate: at least 3 tweaks. Results are labeled exploratory, like the B6 screen: candidates for
a closer look or a physio conversation, never an active rule and never advice.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg
from scipy import stats

from healthtracker.db import fetch_all, fetch_df, upsert
from healthtracker.profile import load_profile

MIN_EVENTS = 3
PRIOR_SD = 0.5
FACTORS = {  # name: (words, yes/no)
    "game_min": ("game minutes", False), "sprint_m": ("sprint metres", False), "plyo": ("jump contacts", False),
    "cold": ("a cold day (under 5 C)", True), "short_sleep": ("a night under 7 h", True),
    "travel": ("a travel day", True),
}


def laplace_logistic(X: np.ndarray, y: np.ndarray, m0: np.ndarray, sd0: np.ndarray,
                     iters: int = 50) -> tuple[np.ndarray, np.ndarray]:
    """Posterior mode and covariance of a logistic regression with independent Normal priors."""
    beta = m0.copy()
    prec0 = 1 / sd0 ** 2
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ beta)))
        grad = X.T @ (y - p) - prec0 * (beta - m0)
        H = X.T @ (X * (p * (1 - p))[:, None]) + np.diag(prec0)
        step = np.linalg.solve(H, grad)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-8:
            break
    p = 1 / (1 + np.exp(-(X @ beta)))
    H = X.T @ (X * (p * (1 - p))[:, None]) + np.diag(prec0)
    return beta, np.linalg.inv(H)


def frame(f: pd.DataFrame) -> pd.DataFrame:
    """One row per day: the tweak, and each factor over that day and the one before."""
    two = lambda s: s.fillna(0).rolling(2, min_periods=1).sum()  # noqa: E731
    out = pd.DataFrame(index=f.index)
    out["tweak"] = f["tweak"].astype(float)
    out["game_min"] = two(f["game_min"].astype(float))
    out["sprint_m"] = two(f["sprint_m"].astype(float))
    out["plyo"] = two(f["plyo"].astype(float))
    if f["tmin_c"].notna().sum() >= 20:
        cold = f["tmin_c"].astype(float).rolling(2, min_periods=1).min() < 5
        out["cold"] = cold.astype(float).where(f["tmin_c"].rolling(2, min_periods=1).count() > 0)
    out["short_sleep"] = (f["sleep_total_min"].astype(float) < 420).astype(float).where(f["sleep_total_min"].notna())
    out["travel"] = f["is_travel"].astype("boolean").fillna(False).astype(float).rolling(2, min_periods=1).max()
    return out


def screen(df: pd.DataFrame) -> list[dict]:
    df = df.dropna()
    n_events = int(df["tweak"].sum())
    if n_events < MIN_EVENTS:
        return []
    used = [c for c in FACTORS if c in df and df[c].std() > 0]
    if not used:
        return []
    Z = []
    scale = {}
    for c in used:
        x = df[c].to_numpy(dtype=float)
        if FACTORS[c][1]:
            Z.append(x)
            scale[c] = 1.0
        else:
            sd = float(x.std()) or 1.0
            Z.append((x - x.mean()) / sd)
            scale[c] = sd
    X = np.column_stack([np.ones(len(df)), *Z])
    y = df["tweak"].to_numpy(dtype=float)
    rate = min(max(n_events / len(df), 1e-3), 0.5)
    m0 = np.r_[np.log(rate / (1 - rate)), np.zeros(len(used))]
    sd0 = np.r_[1.5, np.full(len(used), PRIOR_SD)]
    beta, cov = laplace_logistic(X, y, m0, sd0)
    z80 = stats.norm.ppf(0.9)
    out = []
    for k, c in enumerate(used, start=1):
        b, s = float(beta[k]), float(np.sqrt(cov[k, k]))
        out.append({"factor": c, "words": FACTORS[c][0], "per": "yes vs no" if FACTORS[c][1] else
                    f"per {scale[c]:.0f} more", "n_days": len(df), "n_events": n_events,
                    "log_or": round(b, 3), "log_or_sd": round(s, 3), "odds_ratio": round(float(np.exp(b)), 2),
                    "or_lo80": round(float(np.exp(b - z80 * s)), 2), "or_hi80": round(float(np.exp(b + z80 * s)), 2),
                    "p_higher": round(float(stats.norm.sf(0, b, s)), 3)})
    return out


# ------------------------------------------------------------------ from the database
def load_frame(conn: psycopg.Connection, end: date, days: int = 365) -> pd.DataFrame:
    from healthtracker.ingest.weather import home_point

    tz = load_profile(conn).timezone
    start = end - timedelta(days=days - 1)
    f = fetch_df(conn, """SELECT date, sleep_total_min, is_travel FROM analytics.daily_features
                          WHERE date BETWEEN %s AND %s ORDER BY date""", (start, end))
    if f.empty:
        return f
    f = f.set_index(pd.to_datetime(f["date"])).drop(columns="date").asfreq("D")
    loads = fetch_df(conn, """SELECT date, channel, value FROM analytics.load_daily
                              WHERE channel IN ('sprint_m', 'plyo_contacts') AND date BETWEEN %s AND %s""", (start, end))
    for ch, col in (("sprint_m", "sprint_m"), ("plyo_contacts", "plyo")):
        part = loads[loads["channel"] == ch] if not loads.empty else loads
        f[col] = pd.Series(part["value"].astype(float).to_numpy(), index=pd.to_datetime(part["date"])).reindex(f.index) \
            if not part.empty else np.nan
    games = fetch_df(conn, """SELECT (start_ts AT TIME ZONE %(tz)s)::date AS date, sum(duration_s) / 60.0 AS m
                              FROM core.v_workout WHERE is_game AND (start_ts AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s
                              GROUP BY 1""", {"tz": tz, "s": start, "e": end})
    f["game_min"] = pd.Series(games["m"].astype(float).to_numpy(), index=pd.to_datetime(games["date"])).reindex(f.index) \
        if not games.empty else np.nan
    home = home_point()
    wx = fetch_df(conn, """SELECT date, tmin_c FROM core.weather_daily WHERE lat = %s AND lon = %s
                           AND date BETWEEN %s AND %s""", (home[0], home[1], start, end)) if home else pd.DataFrame()
    f["tmin_c"] = pd.Series(wx["tmin_c"].astype(float).to_numpy(), index=pd.to_datetime(wx["date"])).reindex(f.index) \
        if not wx.empty else np.nan
    tweaks = {r["d"] for r in fetch_all(conn, """SELECT DISTINCT (ts AT TIME ZONE %s)::date AS d FROM core.behavior_event
                                                  WHERE type_code = 'groin_tweak'""", (tz,))}
    f["tweak"] = [d.date() in tweaks for d in f.index]
    return f


def run_tweak_screen(conn: psycopg.Connection, as_of: date) -> int:
    f = load_frame(conn, as_of)
    if f.empty:
        return 0
    rows = [{"computed_on": as_of, **r} for r in screen(frame(f))]
    for r in rows:
        r.pop("words")
        r.pop("per")
    return upsert(conn, "analytics.tweak_screen", rows, conflict=["computed_on", "factor"]) if rows else 0
