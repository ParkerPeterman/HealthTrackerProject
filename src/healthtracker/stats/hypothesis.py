"""Testing one exposure -> outcome hypothesis on my own daily data.

A finding is "supported" only when all of these hold:
  * at least `min_n_per_group` days exposed and unexposed
  * Benjamini-Hochberg adjusted p < 0.05 across everything tested this run
  * the 95% CI excludes zero under both HAC (Newey-West) errors and a
    moving-block bootstrap (consecutive days are not independent)
  * the effect is at least 0.2 SD of the outcome (big enough to matter)
  * the same direction shows up in the later half of the data (replication)
Regressions control for day of week, prior night's sleep, and training load,
except where a control is the exposure itself or sits on its causal path.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import statsmodels.api as sm

from healthtracker.units import fmt_hour

EXPOSURE_RE = re.compile(r"^\s*([a-z][a-z0-9_]*)\s*(>=|<=|>|<|=)?\s*(-?\d+(?:\.\d+)?)?\s*$")

LOAD_FAMILY = {"session_load", "had_game", "acwr", "load_z_28d", "days_since_hard", "days_since_game",
               "acute_load_7d", "chronic_load_28d", "run_km_7d", "n_workouts"}
SLEEP_FAMILY = {"sleep_total_min", "sleep_score", "sleep_midpoint_min", "sleep_score_z_30d"}
OVERNIGHT_OUTCOMES = SLEEP_FAMILY | {"hrv", "resting_hr", "hrv_z_30d", "rhr_z_30d"}

MIN_EFFECT_SD = 0.2
ALPHA = 0.05

LABELS = {
    "last_caffeine_hour": "last caffeine", "caffeine_mg": "caffeine", "alcohol_drinks": "drinks",
    "last_meal_hour": "last meal", "had_game": "a game", "session_load": "session load",
    "acwr": "acute:chronic load", "sleep_total_min": "sleep", "sleep_score": "sleep score",
    "sleep_midpoint_min": "sleep midpoint", "protein_g_per_kg": "protein (g/kg)", "kcal": "calories",
    "carbs_g": "carbs (g)", "fiber_g": "fiber (g)", "hrv_z_30d": "HRV vs baseline",
    "rhr_z_30d": "resting HR vs baseline", "days_since_hard": "days since a hard session",
    "run_km_7d": "7-day running km", "injury_active": "an active injury", "hrv": "HRV",
    "resting_hr": "resting HR", "energy": "energy", "mood": "mood", "soreness": "soreness",
    "avg_stress": "Garmin stress",
}
UNITS = {"hrv": "ms", "resting_hr": "bpm", "sleep_total_min": "min", "kcal": "kcal"}
HOUR_COLS = {"last_caffeine_hour", "last_meal_hour"}


@dataclass
class Hypothesis:
    id: int
    exposure: str
    outcome: str
    lag_days: int
    min_n_per_group: int = 20


@dataclass
class TestResult:
    hypothesis_id: int
    n_exposed: int
    n_unexposed: int
    effect: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None
    boot_ci: tuple[float, float] | None = None
    std_effect: float | None = None
    replicated: bool | None = None
    controlled_for: list[str] = field(default_factory=list)
    window: tuple | None = None
    p_adjusted: float | None = None
    status: str = "insufficient_data"
    summary: str = ""


def parse_exposure(expr: str) -> tuple[str, str | None, float | None]:
    m = EXPOSURE_RE.match(expr)
    if not m:
        raise ValueError(f"bad exposure expression {expr!r}")
    col, op, val = m.group(1), m.group(2), m.group(3)
    if (op is None) != (val is None):
        raise ValueError(f"bad exposure expression {expr!r}")
    return col, op, float(val) if val is not None else None


def exposure_series(df: pd.DataFrame, expr: str) -> pd.Series:
    col, op, val = parse_exposure(expr)
    s = df[col]
    if op is None:
        out = s.astype("boolean").astype("Float64")
    else:
        x = s.astype(float)
        ops = {">=": x >= val, "<=": x <= val, ">": x > val, "<": x < val, "=": x == val}
        out = ops[op].astype(float).where(x.notna())
    return out.astype(float)


def describe(h: Hypothesis) -> tuple[str, str]:
    col, op, val = parse_exposure(h.exposure)
    name = LABELS.get(col, col.replace("_", " "))
    if op is None:
        exp = name
    elif col in HOUR_COLS:
        exp = f"{name} {'at or after' if op in ('>=', '>') else 'before'} {fmt_hour(val)}"
    elif col == "sleep_total_min":
        exp = f"sleep {'under' if op in ('<', '<=') else 'over'} {val / 60:g}h"
    else:
        exp = f"{name} {op} {val:g}"
    when = {0: "same day", 1: "next day"}.get(h.lag_days, f"{h.lag_days} days later")
    if h.outcome in SLEEP_FAMILY | {"hrv", "resting_hr"} and h.lag_days == 1:
        when = "that night" if h.outcome in SLEEP_FAMILY else "next morning"
    return exp, f"{LABELS.get(h.outcome, h.outcome)} ({when})"


def build_frame(features: pd.DataFrame, h: Hypothesis) -> tuple[pd.DataFrame, list[str]]:
    """Rows = exposure days with E, Y (shifted by lag), and controls."""
    f = features.sort_index()
    f = f.reindex(pd.date_range(f.index.min(), f.index.max(), freq="D"))
    col, _, _ = parse_exposure(h.exposure)
    lag = h.lag_days
    df = pd.DataFrame(index=f.index)
    df["E"] = exposure_series(f, h.exposure)
    df["Y"] = f[h.outcome].astype(float).shift(-lag)

    bad = f["exclude_from_baseline"].fillna(False).astype(bool)
    if col != "injury_active":
        bad = bad | f["injury_active"].fillna(False).astype(bool)
    df["bad"] = bad | bad.shift(-lag, fill_value=False)

    controls = []
    out_date_dow = pd.Series(f.index.dayofweek, index=f.index).shift(-lag)
    for k in range(1, 7):
        df[f"dow_{k}"] = (out_date_dow == k).astype(float)
    controls.append("day_of_week")
    if col not in SLEEP_FAMILY:
        # Prior night's sleep: for overnight outcomes, the night *before* the one measured
        # (the same night would be a mediator); for daytime outcomes, the night ending that day.
        shift = 1 - lag if h.outcome in OVERNIGHT_OUTCOMES else -lag
        df["ctl_sleep"] = f["sleep_total_min"].astype(float).shift(shift)
        controls.append("prior_sleep")
    if col not in LOAD_FAMILY:
        # Training load on the day before the outcome day.
        df["ctl_load"] = f["session_load"].astype(float).shift(1 - lag)
        controls.append("training_load")
    df = df[~df["bad"]].drop(columns="bad").dropna()
    return df, controls


def _design(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    cols = ["E"] + [c for c in df.columns if c.startswith(("dow_", "ctl_"))]
    X = df[cols].to_numpy(dtype=float)
    keep = X.std(axis=0) > 0
    keep[0] = True
    X = sm.add_constant(X[:, keep], has_constant="add")
    # Drop controls (never the constant or E) until the design has full rank.
    while X.shape[1] > 2 and np.linalg.matrix_rank(X) < X.shape[1]:
        X = X[:, :-1]
    return X, df["Y"].to_numpy(dtype=float)


def fit_hac(df: pd.DataFrame) -> tuple[float, float, float, float]:
    X, y = _design(df)
    n = len(y)
    maxlags = max(3, int(round(4 * (n / 100) ** (2 / 9))))
    res = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    lo, hi = res.conf_int(alpha=ALPHA)[1]
    return float(res.params[1]), float(lo), float(hi), float(res.pvalues[1])


def block_bootstrap_ci(df: pd.DataFrame, block: int = 7, reps: int = 400, seed: int = 7) -> tuple[float, float]:
    """Moving-block bootstrap CI for the exposure coefficient."""
    X, y = _design(df)
    n = len(y)
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(n / block)
    coefs = []
    for _ in range(reps):
        starts = rng.integers(0, max(1, n - block + 1), n_blocks)
        idx = np.concatenate([np.arange(s, min(s + block, n)) for s in starts])[:n]
        Xb, yb = X[idx], y[idx]
        if Xb[:, 1].std() == 0:
            continue
        beta, *_ = np.linalg.lstsq(Xb, yb, rcond=None)
        coefs.append(beta[1])
    if len(coefs) < reps // 2:
        return (float("nan"), float("nan"))
    return float(np.percentile(coefs, 2.5)), float(np.percentile(coefs, 97.5))


def run_test(features: pd.DataFrame, h: Hypothesis) -> TestResult:
    df, controls = build_frame(features, h)
    n_e, n_u = int((df["E"] == 1).sum()), int((df["E"] == 0).sum())
    res = TestResult(h.id, n_e, n_u, controlled_for=controls)
    if df.empty:
        return res
    res.window = (df.index.min().date(), df.index.max().date())
    if min(n_e, n_u) < h.min_n_per_group:
        res.summary = f"Needs {h.min_n_per_group} days in each group (have {n_e} exposed, {n_u} not)."
        return res
    res.effect, res.ci_low, res.ci_high, res.p_value = fit_hac(df)
    res.boot_ci = block_bootstrap_ci(df)
    sd = float(df["Y"].std())
    res.std_effect = res.effect / sd if sd > 0 else 0.0

    half = len(df) // 2
    late = df.iloc[half:]
    if min((late["E"] == 1).sum(), (late["E"] == 0).sum()) >= 5:
        e2, _, _, p2 = fit_hac(late)
        res.replicated = bool(np.sign(e2) == np.sign(res.effect) and p2 < 0.10)
    res.status = "weak"
    return res


def benjamini_hochberg(pvals: list[float]) -> list[float]:
    n = len(pvals)
    if n == 0:
        return []
    order = np.argsort(pvals)
    ranked = np.array(pvals, dtype=float)[order] * n / np.arange(1, n + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.minimum(adj, 1.0)
    return out.tolist()


def finalize(results: list[TestResult], hyps: dict[int, Hypothesis]) -> list[TestResult]:
    """Apply BH across tested hypotheses, then the support criteria and summaries."""
    tested = [r for r in results if r.p_value is not None]
    for r, p in zip(tested, benjamini_hochberg([r.p_value for r in tested]), strict=True):
        r.p_adjusted = p
    for r in tested:
        boot_ok = r.boot_ci is not None and not any(math.isnan(x) for x in r.boot_ci) and (
            r.boot_ci[0] > 0 or r.boot_ci[1] < 0)
        hac_ok = r.ci_low > 0 or r.ci_high < 0
        if (r.p_adjusted < ALPHA and hac_ok and boot_ok and abs(r.std_effect) >= MIN_EFFECT_SD
                and r.replicated):
            r.status = "supported"
        exp, out = describe(hyps[r.hypothesis_id])
        unit = UNITS.get(hyps[r.hypothesis_id].outcome, "")
        direction = "higher" if r.effect > 0 else "lower"
        r.summary = (f"After {exp}: {out} {abs(r.effect):.1f}{(' ' + unit) if unit else ''} {direction} "
                     f"(95% CI {r.ci_low:.1f} to {r.ci_high:.1f}; {r.n_exposed} vs {r.n_unexposed} days; "
                     f"adj. p={r.p_adjusted:.3f})")
        if r.status != "supported":
            why = []
            if r.p_adjusted >= ALPHA:
                why.append("not significant after correction")
            if abs(r.std_effect) < MIN_EFFECT_SD:
                why.append("effect too small")
            if not boot_ok:
                why.append("bootstrap CI crosses zero")
            if r.replicated is False:
                why.append("did not replicate in later data")
            if r.replicated is None:
                why.append("not enough later data to replicate")
            r.summary += " - " + ", ".join(why) if why else ""
    return results
