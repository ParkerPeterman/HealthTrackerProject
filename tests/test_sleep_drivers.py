"""E3: the multi-exposure sleep model doesn't double-count correlated exposures."""

import numpy as np
import pandas as pd
import pytest

from healthtracker.stats.bayes import VAGUE, design, fit, summarize


def test_correlated_exposures_are_not_double_counted():
    rng = np.random.default_rng(8)
    n = 120
    drinks = rng.poisson(0.8, n).astype(float)
    late_meal = 19 + 0.8 * drinks + rng.normal(0, 0.7, n)  # drinking nights run late; only drinks matter
    y = 60 - 4 * drinks + rng.normal(0, 5, n)
    df = pd.DataFrame({"Y": y, "drinks": drinks, "meal": late_meal}, index=pd.date_range("2026-06-01", periods=n))
    X, yy, consecutive, y_sd, scales, _ = design(df, [("drinks", True), ("meal", True)])
    post = fit(X, yy, consecutive, np.zeros(X.shape[1]), np.r_[0.25, 0.25, np.full(X.shape[1] - 2, VAGUE)])
    d = summarize(post, 0, y_sd / scales[0], 0.2)
    m = summarize(post, 1, y_sd / scales[1], 0.2)
    assert d["estimate"] == pytest.approx(-4, abs=1.2) and d["p_meaningful"] > 0.9
    assert m["lo95"] < 0 < m["hi95"] and abs(m["estimate"]) < 1.5
    alone = fit(X[:, [1, -1]], yy, consecutive, np.zeros(2), np.r_[0.25, VAGUE])  # the meal alone looks harmful
    assert summarize(alone, 0, y_sd / scales[1], 0.2)["estimate"] < m["estimate"] - 1


@pytest.mark.db
def test_demo_sleep_drivers(seeded, tx):
    from healthtracker.db import fetch_all
    from healthtracker.stats.sleep_drivers import run_sleep_drivers

    conn = tx
    assert run_sleep_drivers(conn, seeded["end"]) > 0
    rows = {(r["outcome"], r["exposure"]): r for r in fetch_all(
        conn, "SELECT * FROM analytics.sleep_driver WHERE computed_on = %s", (seeded["end"],))}
    drinks = rows[("hrv", "alcohol_drinks")]
    assert drinks["estimate"] < 0 and drinks["p_direction"] > 0.9 and "Pietil" in drinks["prior_source"]
    gap = rows[("sleep_total_min", "caffeine_hours_before_bed")]  # planted: late caffeine shortens sleep
    assert gap["estimate"] > 0  # a step at 14:00 seen through an hours dose: right direction, weaker certainty
    assert not any(e.startswith(("med_", "dose")) for _, e in rows)
