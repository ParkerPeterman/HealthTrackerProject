"""E3: what drives my sleep, from one multi-exposure model per outcome.

Each row is a day's exposures and the night that follows (the night ending the next
morning): caffeine dose and its timing, drinks, last meal, internal training load and when
the last session ended, water from 20:00 on, and travel. Outcomes: sleep duration, sleep
midpoint, overnight HRV and resting HR, and my own sleep rating. Putting the exposures in
one model means a late-caffeine day that was also a drinking day isn't counted twice.

Same machinery as B1: standardized columns, AR(1) prewhitening, a conjugate
Normal-Inverse-Gamma update, cited priors where they exist (B2) and Normal(0, 0.5 SD)
otherwise, plus day-of-week and the previous night's sleep as controls. Food-log exposures
exist only on complete days, so only those days enter. An exposure logged on too few days
(water, for example) is left out rather than guessed. Medication is not here: dose time
is analyzed only in the sensitive H3 model.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import upsert
from healthtracker.stats.bayes import (
    DEFAULT_PRIOR,
    VAGUE,
    design,
    fit,
    priors,
    summarize,
    swc_params,
)
from healthtracker.stats.hypothesis import excluded_days

OUTCOMES = ("sleep_total_min", "sleep_midpoint_min", "hrv", "resting_hr", "sleep_quality")
EXPOSURES = (("caffeine_mg", True), ("caffeine_hours_before_bed", True), ("alcohol_drinks", True),
             ("last_meal_hour", True), ("load_internal", True), ("train_end_hour", True), ("water_late_ml", True),
             ("is_travel", False))
MIN_COVERAGE = 0.7
MIN_DAYS = 20


def frame(f: pd.DataFrame, outcome: str) -> tuple[pd.DataFrame, list[tuple[str, bool]]]:
    f = f.sort_index()
    f = f.reindex(pd.date_range(f.index.min(), f.index.max(), freq="D"))
    df = pd.DataFrame(index=f.index)
    df["Y"] = f[outcome].astype(float).shift(-1)  # the night after the exposure day
    df["ctl_sleep"] = f["sleep_total_min"].astype(float)  # the night before
    dow = pd.Series(f.index.dayofweek, index=f.index).shift(-1)
    for k in range(1, 7):
        df[f"dow_{k}"] = (dow == k).astype(float)
    complete = f["food_log_complete"].astype("boolean").fillna(False).astype(bool)
    use: list[tuple[str, bool]] = []
    for col, dose in EXPOSURES:
        if col not in f:
            continue
        x = f[col].astype(float) if dose else f[col].astype("boolean").astype(float)
        if col == "caffeine_hours_before_bed":  # no caffeine at all: as long a gap as I ever have
            x = x.where(~((f["caffeine_mg"].astype(float) == 0) & complete), x.max())
        if col in ("train_end_hour",):  # rest days have no end time; they sit at my usual
            x = x.fillna(x.median())
        if x[complete].notna().mean() < MIN_COVERAGE or x.std() == 0 or np.isnan(x.std()):
            continue
        df[col] = x
        use.append((col, dose))
    bad = excluded_days(f, ("sick", "injury", "implausible"))
    df = df[complete & ~bad & ~bad.shift(-1, fill_value=False)]
    df = df[["Y", *[c for c, _ in use], *[c for c in df.columns if c.startswith(("dow_", "ctl_"))]]].dropna()
    # An exposure that never varies on the days that enter (travel days rarely have a complete
    # food log) can't be estimated here; leave it out rather than report its prior.
    use = [(c, d) for c, d in use if df[c].std() > 0 and df[c].nunique() > 1]
    return df, use


def run_sleep_drivers(conn: psycopg.Connection, as_of: date, features: pd.DataFrame | None = None) -> int:
    from healthtracker.stats.insights import load_features_frame

    f = load_features_frame(conn, as_of) if features is None else features
    if f.empty:
        return 0
    pri, swc = priors(conn), swc_params(conn)
    rows = []
    for outcome in OUTCOMES:
        if outcome not in f or f[outcome].notna().sum() < MIN_DAYS:
            continue
        df, use = frame(f, outcome)
        if len(df) < MIN_DAYS or not use:
            continue
        X, y, consecutive, y_sd, scales, _ = design(df, use)
        m0, v0 = np.zeros(X.shape[1]), np.full(X.shape[1], VAGUE)
        sources = []
        for j, (col, _dose) in enumerate(use):
            mean, sd, src = pri.get((col, outcome, 1), DEFAULT_PRIOR)
            m0[j], v0[j] = mean, sd**2
            sources.append(src)
        post = fit(X, y, consecutive, m0, v0)
        if post is None:
            continue
        swc_sd = swc.get(outcome, swc.get("default", 0.2))
        for j, (col, _dose) in enumerate(use):
            s = summarize(post, j, y_sd / scales[j], swc_sd)
            rows.append({"computed_on": as_of, "outcome": outcome, "exposure": col, "n_days": post["n"],
                         **{k: s[k] for k in ("estimate", "lo80", "hi80", "lo95", "hi95", "p_direction",
                                              "p_meaningful")}, "prior_source": sources[j]})
    return upsert(conn, "analytics.sleep_driver", rows, conflict=["computed_on", "outcome", "exposure"]) if rows else 0
