"""B6: the exploratory screen. Where non-obvious findings come from.

Every week: each exposure x outcome x lag (0-3 days) gets the B1 model with a strong
shrinkage prior, Normal(0, 0.2 SD), in place of a horseshoe (conjugate, exact, fast). With
that much shrinkage only effects with a lot of support clear the smallest worthwhile change,
which keeps the false-positive rate down across hundreds of combinations.

A combination is flagged when P(|effect| > SWC) reaches a threshold (0.5 by default). Flagged
results are labeled exploratory; they can become candidate self-experiments (B7) but never
an active rule directly. Medication and poor-tier measures are never scanned, and nothing
here is shown as advice.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import psycopg

from healthtracker.db import fetch_all, upsert
from healthtracker.medication import is_sensitive
from healthtracker.quality import tiers
from healthtracker.stats.bayes import estimate, swc_params
from healthtracker.stats.hypothesis import (
    DEFAULT_EXCLUDES,
    OVERNIGHT_OUTCOMES,
    Hypothesis,
    excluded_days,
    parse_exposure,
)

EXPOSURES = {  # column: dose (True) or yes/no
    "caffeine_mg": True, "last_caffeine_hour": True, "caffeine_hours_before_bed": True, "alcohol_drinks": True,
    "last_meal_hour": True, "kcal": True, "protein_g": True, "carbs_g": True, "fiber_g": True,
    "training_min": True, "load_internal": True, "train_end_hour": True, "bedtime_hour": True,
    "water_late_ml": True, "sleep_total_min": True, "is_travel": False, "tag_stress": False, "had_game": False,
}
OUTCOMES = ("hrv", "resting_hr", "sleep_total_min", "sleep_midpoint_min", "sleep_quality", "energy", "mood", "soreness")
TESTABLE = {"caffeine_mg", "last_caffeine_hour", "caffeine_hours_before_bed", "last_meal_hour", "train_end_hour"}
DEFAULTS = {"prior_sd": 0.2, "flag_p": 0.5, "max_lag": 3}


def screen_params(conn: psycopg.Connection) -> dict[str, float]:
    rows = fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_SCREEN'")
    return {**DEFAULTS, **{r["name"]: float(r["value"]) for r in rows}}


def combos(f: pd.DataFrame, max_lag: int, tier: dict[str, str]) -> list[tuple[str, bool, str, int]]:
    out = []
    for col, dose in EXPOSURES.items():
        if col not in f or is_sensitive(col) or tier.get(col) == "poor" or f[col].notna().sum() < 20:
            continue
        for outcome in OUTCOMES:
            if outcome == col or outcome not in f or tier.get(outcome) == "poor":
                continue
            # An overnight outcome on day d is the night that ended that morning: a daytime
            # exposure can only reach it from the next day on.
            first = 0 if (outcome not in OVERNIGHT_OUTCOMES and outcome != "sleep_quality") or col == "bedtime_hour" else 1
            for lag in range(first, max_lag + 1):
                out.append((col, dose, outcome, lag))
    return out


def frames(f: pd.DataFrame, outcome: str, lag: int) -> pd.DataFrame:
    """Outcome, controls, and exclusions for one outcome and lag, built once for every exposure
    (the same rows and controls as hypothesis.build_frame)."""
    f = f.sort_index()
    f = f.reindex(pd.date_range(f.index.min(), f.index.max(), freq="D"))
    df = pd.DataFrame(index=f.index)
    df["Y"] = f[outcome].astype(float).shift(-lag)
    dow = pd.Series(f.index.dayofweek, index=f.index).shift(-lag)
    for k in range(1, 7):
        df[f"dow_{k}"] = (dow == k).astype(float)
    shift = 1 - lag if outcome in OVERNIGHT_OUTCOMES else -lag
    df["ctl_sleep"] = f["sleep_total_min"].astype(float).shift(shift)
    df["ctl_load"] = f["session_load"].astype(float).shift(1 - lag) if "session_load" in f else 0.0
    bad = excluded_days(f, DEFAULT_EXCLUDES)
    df["_bad"] = bad | bad.shift(-lag, fill_value=False)
    return df, f


def run_screen(conn: psycopg.Connection, as_of: date, features: pd.DataFrame | None = None) -> int:
    from healthtracker.stats.insights import load_features_frame

    f = load_features_frame(conn, as_of) if features is None else features
    if f.empty:
        return 0
    p, swc, tier = screen_params(conn), swc_params(conn), tiers(conn)
    known = {(parse_exposure(r["exposure"])[0], r["outcome"], r["lag_days"])
             for r in fetch_all(conn, "SELECT exposure, outcome, lag_days FROM analytics.hypothesis")}
    rows = []
    cache: dict[tuple[str, int], tuple[pd.DataFrame, pd.DataFrame]] = {}
    for col, dose, outcome, lag in combos(f, int(p["max_lag"]), tier):
        if (outcome, lag) not in cache:
            cache[(outcome, lag)] = frames(f, outcome, lag)
        base, full = cache[(outcome, lag)]
        df = base.copy()
        df["E"] = full[col].astype(float) if dose else full[col].astype("boolean").astype(float)
        if col in ("sleep_total_min",):
            df = df.drop(columns="ctl_sleep")  # the exposure itself
        if col in ("session_load", "load_internal", "training_min", "had_game"):
            df = df.drop(columns="ctl_load")
        df = df[~df.pop("_bad").astype(bool)].dropna()
        h = Hypothesis(0, col, outcome, lag, kind="dose" if dose else "binary")
        e = estimate(df, h, (0.0, p["prior_sd"], "screen shrinkage"), swc.get(outcome, swc.get("default", 0.2)))
        if e.p_meaningful is None:
            continue
        rows.append({"computed_on": as_of, "exposure": col, "outcome": outcome, "lag_days": lag, "n_days": e.n_days,
                     "estimate": e.estimate, "lo80": e.lo80, "hi80": e.hi80, "p_meaningful": e.p_meaningful,
                     "flagged": e.p_meaningful >= p["flag_p"], "known": (col, outcome, lag) in known,
                     "candidate": e.p_meaningful >= p["flag_p"] and col in TESTABLE})
    return upsert(conn, "analytics.screen_result", rows,
                  conflict=["computed_on", "exposure", "outcome", "lag_days"]) if rows else 0
