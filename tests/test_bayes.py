"""B1-B3: Bayesian effect estimates. Calibrated, recover planted effects, find nothing in nulls."""

import numpy as np
import pandas as pd
import pytest

from healthtracker.stats.bayes import DEFAULT_PRIOR, days_to, estimate, p_beyond
from healthtracker.stats.hypothesis import Hypothesis

H = Hypothesis(1, "x >= 1", "y", 1)
DOSE = Hypothesis(2, "x", "y", 1, kind="dose")


def frame(n: int, beta: float, rho: float = 0.4, seed: int = 0, dose: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    e = rng.poisson(1.2, n).astype(float) if dose else (rng.random(n) < 0.35).astype(float)
    u = np.zeros(n)
    for t in range(1, n):
        u[t] = rho * u[t - 1] + rng.normal(0, 1)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame({"E": e, "Y": 10 + beta * e + u * np.sqrt(1 - rho**2)}, index=idx)


def test_eighty_percent_intervals_cover_about_eighty_percent():
    """Across simulated athletes whose true effects come from the prior, coverage matches the level."""
    rng = np.random.default_rng(11)
    hits80 = hits95 = 0
    sims = 300
    for k in range(sims):
        beta_sd = rng.normal(0, 0.5)  # true standardized effect drawn from the prior
        df = frame(60, beta_sd, seed=k)
        est = estimate(df, H, DEFAULT_PRIOR, 0.2)
        truth = beta_sd  # Y's noise SD is 1, so outcome units ~ SD units (sample SD differs a little)
        hits80 += est.lo80 <= truth * 1.0 <= est.hi80 or abs(truth) < 1e-9
        hits95 += est.lo95 <= truth <= est.hi95
    assert 0.72 <= hits80 / sims <= 0.88
    assert 0.90 <= hits95 / sims <= 0.99


def test_p_meaningful_rises_with_days_for_a_real_effect():
    p = [estimate(frame(n, 0.8, seed=3), H, DEFAULT_PRIOR, 0.2).p_meaningful for n in (14, 30, 60, 120)]
    assert p[-1] > 0.9 and p[-1] >= p[0]
    assert p == sorted(p) or p[-1] - p[0] > 0.3


def test_no_effect_stays_low():
    """Where nothing was planted, a "likely meaningful" call is rare and 95% intervals cover zero ~95% of the time."""
    ests = [estimate(frame(90, 0.0, seed=1000 + k), H, DEFAULT_PRIOR, 0.2) for k in range(200)]
    false_calls = np.mean([e.p_meaningful >= 0.8 for e in ests])
    covers = np.mean([e.lo95 < 0 < e.hi95 for e in ests])
    assert false_calls < 0.12 and 0.9 <= covers <= 0.99


def test_dose_per_unit_and_ar1():
    df = frame(120, 0.5, seed=5, dose=True)
    est = estimate(df, DOSE, DEFAULT_PRIOR, 0.2)
    assert est.estimate == pytest.approx(0.5, abs=0.2) and est.rho > 0.1  # per unit of dose; AR(1) found


def test_days_to_reach_08():
    assert days_to(0.5, 0.2, 50, 0.2, 40) == 0 or p_beyond(0.5, 0.2, 50, 0.2) < 0.8
    assert days_to(0.1, 0.3, 50, 0.2, 40) is None  # estimate inside the SWC: never
    n_more = days_to(0.35, 0.25, 50, 0.2, 30)
    assert n_more and n_more > 0
    assert p_beyond(0.35, 0.25 * np.sqrt(30 / (30 + n_more)), 50, 0.2) >= 0.8


@pytest.mark.db
def test_demo_planted_and_null_effects(seeded, conn):
    from healthtracker.stats.bayes import run_effects
    from healthtracker.stats.insights import load_features_frame

    f = load_features_frame(conn, seeded["end"])
    run_effects(conn, seeded["end"], f)
    from healthtracker.db import fetch_all

    rows = {(r["exposure"], r["outcome"], r["lag_days"]): r for r in fetch_all(conn, """
        SELECT h.exposure, h.outcome, h.lag_days, e.* FROM analytics.effect_estimate e
        JOIN analytics.hypothesis h ON h.id = e.hypothesis_id WHERE e.computed_on = %s""", (seeded["end"],))}
    caffeine = rows[("last_caffeine_hour >= 14", "sleep_score", 1)]
    assert caffeine["estimate"] < 0 and caffeine["p_meaningful"] > 0.8 and "Drake" in caffeine["prior_source"]
    drinks = rows[("alcohol_drinks", "hrv", 1)]  # dose (B3): planted ~6% per drink
    assert drinks["estimate"] < 0 and drinks["p_direction"] > 0.9
    null = rows[("last_caffeine_hour >= 14", "hrv", 1)]  # nothing planted
    assert null["p_meaningful"] < 0.8
    # More days, more certainty for the planted effect.
    early = load_features_frame(conn, seeded["start"] + pd.Timedelta(days=35))
    from healthtracker.stats.bayes import estimate as est_fn
    from healthtracker.stats.hypothesis import build_frame

    h = Hypothesis(0, "last_caffeine_hour >= 14", "sleep_score", 1)
    p_early = est_fn(build_frame(early, h)[0], h, (-0.2, 0.5, "x"), 0.2).p_meaningful
    assert p_early is None or p_early <= caffeine["p_meaningful"]
