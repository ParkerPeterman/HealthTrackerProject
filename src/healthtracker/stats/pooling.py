"""B5: partial pooling instead of a multiple-testing correction.

Hypotheses are grouped into families that should behave alike (evening habits and sleep;
evening habits and overnight recovery; training load and recovery; sleep and the next day).
Within a family the estimates share a hyperprior: a normal-normal model whose family mean and
between-hypothesis spread (tau) are estimated from the family itself (DerSimonian-Laird). Each
estimate is pulled toward the family mean in proportion to its uncertainty, which controls
false positives without throwing away what a small sample does show. The same function
shares information across session types in D5.
"""

from __future__ import annotations

import numpy as np
from scipy import stats

EVENING = {"last_caffeine_hour", "caffeine_mg", "caffeine_hours_before_bed", "alcohol_drinks", "last_meal_hour",
           "bedtime_hour", "water_late_ml"}
LOAD = {"had_game", "session_load", "load_internal", "training_min", "load_week_change_pct", "acwr", "load_z_28d"}
SLEEP_EXPOSURES = {"sleep_total_min", "sleep_score", "sleep_midpoint_min"}
FAMILIES = (
    ("evening->sleep", EVENING, {"sleep_score", "sleep_total_min", "sleep_midpoint_min", "sleep_quality"}),
    ("evening->overnight", EVENING, {"hrv", "resting_hr"}),
    ("load->recovery", LOAD, {"hrv", "resting_hr", "soreness", "energy"}),
    ("sleep->day", SLEEP_EXPOSURES, {"energy", "mood", "avg_stress", "kcal"}),
)


def family_of(exposure_col: str, outcome: str) -> str | None:
    for name, exps, outs in FAMILIES:
        if exposure_col in exps and outcome in outs:
            return name
    return None


def pool(est: np.ndarray, se: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Normal-normal partial pooling (DerSimonian-Laird). Returns pooled means and SDs, the family mean, and tau."""
    est, se = np.asarray(est, float), np.asarray(se, float)
    w = 1 / se**2
    mu = float(np.sum(w * est) / np.sum(w))
    k = len(est)
    q = float(np.sum(w * (est - mu) ** 2))
    denom = float(np.sum(w) - np.sum(w**2) / np.sum(w))
    tau2 = max(0.0, (q - (k - 1)) / denom) if denom > 0 else 0.0
    tau2 = max(tau2, 1e-4)  # never pool to a single point
    prec = w + 1 / tau2
    mean = (w * est + mu / tau2) / prec
    return mean, np.sqrt(1 / prec), mu, float(np.sqrt(tau2))


def p_beyond(mean: np.ndarray, sd: np.ndarray, swc: float) -> np.ndarray:
    return stats.norm.sf(swc, mean, sd) + stats.norm.cdf(-swc, mean, sd)
