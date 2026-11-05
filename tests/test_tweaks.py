"""F2 / B6: the groin-tweak screen, a Bayesian logistic model with strong priors for rare events."""

import numpy as np
import pandas as pd
import pytest

from healthtracker.stats.tweaks import FACTORS, frame, laplace_logistic, screen

IDX = pd.date_range("2026-01-01", periods=300, freq="D")


def features(seed=0, game_effect=2.0):
    rng = np.random.default_rng(seed)
    game = (rng.random(300) < 0.15) * 90.0
    logit = -4.2 + game_effect * (np.r_[0.0, game[:-1]] > 0)  # a tweak the day after a game
    tweak = rng.random(300) < 1 / (1 + np.exp(-logit))
    return pd.DataFrame(index=IDX, data={
        "tweak": tweak, "game_min": np.where(game > 0, game, np.nan), "sprint_m": rng.gamma(2, 60, 300),
        "plyo": np.where(rng.random(300) < 0.3, 30.0, np.nan), "tmin_c": rng.normal(8, 6, 300),
        "sleep_total_min": rng.normal(450, 30, 300), "is_travel": rng.random(300) < 0.05})


def test_laplace_matches_a_known_logistic_fit():
    rng = np.random.default_rng(1)
    x = rng.normal(size=4000)
    y = (rng.random(4000) < 1 / (1 + np.exp(-(-1.0 + 0.8 * x)))).astype(float)
    X = np.column_stack([np.ones(4000), x])
    beta, cov = laplace_logistic(X, y, np.zeros(2), np.array([10.0, 10.0]))
    assert beta[0] == pytest.approx(-1.0, abs=0.12) and beta[1] == pytest.approx(0.8, abs=0.1)
    assert 0 < cov[1, 1] < 0.01


def test_frame_spans_the_tweak_day_and_the_one_before():
    f = features()
    df = frame(f)
    assert set(FACTORS) <= set(df.columns)
    g = f["game_min"].fillna(0)
    assert (df["game_min"] == g + g.shift(1).fillna(0)).all()
    assert df["travel"].max() == 1.0 and set(df["short_sleep"].dropna().unique()) <= {0.0, 1.0}


def test_games_stand_out_and_the_rest_stays_calm():
    rows = {r["factor"]: r for r in screen(frame(features(seed=3)))}
    assert rows["game_min"]["p_higher"] > 0.9 and rows["game_min"]["odds_ratio"] > 1.3
    for k in ("sprint_m", "travel"):
        assert rows[k]["or_lo80"] < 1 < rows[k]["or_hi80"], rows[k]
    assert all(r["n_events"] >= 3 for r in rows.values())


def test_strong_priors_with_few_events():
    f = features(seed=5, game_effect=0.0)
    f["tweak"] = False
    f.iloc[[40, 150, 260], f.columns.get_loc("tweak")] = True
    rows = screen(frame(f))
    assert rows and all(0.3 < r["odds_ratio"] < 3 for r in rows), "three events can't make a big claim"
    f.iloc[150, f.columns.get_loc("tweak")] = False
    assert screen(frame(f)) == []  # under three events: nothing


@pytest.mark.db
def test_tweak_screen_on_the_demo(seeded, tx, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import fetch_all, use_database
    from healthtracker.stats.tweaks import run_tweak_screen

    conn = tx
    n_events = fetch_all(conn, "SELECT count(*) AS n FROM core.behavior_event WHERE type_code = 'groin_tweak'")[0]["n"]
    n = run_tweak_screen(conn, seeded["end"])
    if n_events >= 3:
        assert n > 0
        rows = fetch_all(conn, "SELECT * FROM analytics.tweak_screen WHERE computed_on = %s", (seeded["end"],))
        assert all(r["or_lo80"] <= r["odds_ratio"] <= r["or_hi80"] for r in rows)
    use_database(db_url)
    data = TestClient(app).get("/api/tweak-screen").json()
    assert data["exploratory"] is True and isinstance(data["factors"], list)
