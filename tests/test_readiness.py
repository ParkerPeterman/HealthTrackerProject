"""G1 v1: the readiness score is transparent, falls on bad days, and names its drivers."""

import numpy as np
import pandas as pd
import pytest

from healthtracker.features.readiness import COMPONENTS, drivers, scores

P = {**{f"w_{c}": 1.0 for c in COMPONENTS}, "flag_z": 1.5}


def comp(n=80, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-08-01", periods=n, freq="D")
    df = pd.DataFrame(rng.normal(0, 1, (n, len(COMPONENTS) - 1)), index=idx, columns=COMPONENTS[:-1])
    df["flag"] = 0.0
    return df


def test_score_is_mostly_mid_and_moves_with_components():
    c = comp()
    s = scores(c, P)
    assert 35 <= s["score"].iloc[30:].median() <= 65
    bad = c.copy()
    bad.iloc[-1, :7] = -2.0
    good = c.copy()
    good.iloc[-1, :7] = 2.0
    assert scores(bad, P)["score"].iloc[-1] < 15 < 85 < scores(good, P)["score"].iloc[-1]


def test_flag_lowers_the_score_and_shows_as_a_driver():
    c = comp()
    c.iloc[-1, :7] = 0.0
    flagged = c.copy()
    flagged.iloc[-1, -1] = 1.0
    assert scores(flagged, P)["score"].iloc[-1] < scores(c, P)["score"].iloc[-1]
    d = drivers(flagged.iloc[-1], P)
    assert d[0]["label"] == "lower-readiness day" and "dose" not in str(d).lower()


def test_needs_three_components():
    c = comp()
    c.iloc[-1, 2:7] = np.nan
    assert np.isnan(scores(c, P)["score"].iloc[-1])


@pytest.mark.db
def test_demo_readiness(seeded, conn):
    from healthtracker.db import fetch_all

    rows = fetch_all(conn, "SELECT * FROM analytics.readiness_daily ORDER BY date")
    assert len(rows) > 40 and all(0 <= r["score"] <= 100 for r in rows)
    assert all(len(r["drivers"]) <= 3 for r in rows) and rows[-1]["version"] == "v1"
    # The demo has good and bad stretches (illness, an overreaching block): readiness spans them.
    import statistics

    scores_ = [r["score"] for r in rows]
    assert min(scores_) < 35 and max(scores_) > 65
    assert statistics.pstdev(scores_) > 8
