"""B4: lagged effects with an exponential-decay shape, tau chosen by model evidence."""

import numpy as np
import pandas as pd
import pytest

from healthtracker.stats.bayes import decay, estimate_lagged, lagged_frame
from healthtracker.stats.hypothesis import Hypothesis

IDX = pd.date_range("2026-01-01", periods=240, freq="D")


def features(tau: float, beta: float = -6.0, seed: int = 0, binary: bool = False) -> pd.DataFrame:
    """Daily load (or yes/no sessions), and next-morning HRV that carries the effect for days."""
    rng = np.random.default_rng(seed)
    x = (rng.random(240) < 0.35).astype(float) if binary else rng.gamma(2.0, 150.0, 240) * (rng.random(240) < 0.7)
    xs = (x - x.mean()) / (1.0 if binary else x.std())
    w = decay(tau)
    effect = sum(w[j] * np.r_[np.zeros(1 + j), xs[:-1 - j]] for j in range(4))  # outcome_t from x_(t-1-j)
    hrv = 60 + beta * effect + rng.normal(0, 3.0, 240)
    return pd.DataFrame(index=IDX, data={
        "session_load": x, "had_game": x.astype(bool) if binary else False, "hrv": hrv,
        "sleep_total_min": rng.normal(440, 25, 240), "is_sick": False, "is_travel": False})


def fit(f: pd.DataFrame, exposure: str):
    h = Hypothesis(99, exposure, "hrv", 1, kind="lagged")
    df, dose = lagged_frame(f, h)
    return estimate_lagged(df, h, (0.0, 0.5, "test"), 0.2, dose)


def test_decay_weights():
    assert decay(0.0).tolist() == [1.0, 0.0, 0.0, 0.0]
    w = decay(1.0)
    assert w[0] == 1.0 and w[1] == pytest.approx(np.exp(-1)) and np.all(np.diff(w) < 0)


def test_lagged_frame_lines_up_the_earlier_days():
    f = features(1.0)
    df, dose = lagged_frame(f, Hypothesis(1, "session_load", "hrv", 1, kind="lagged"))
    assert dose and {"E", "E1", "E2", "E3", "Y"} <= set(df.columns)
    t = df.index[10]
    assert df.loc[t, "E2"] == f.loc[t - pd.Timedelta(days=2), "session_load"]
    assert df.loc[t, "Y"] == f.loc[t + pd.Timedelta(days=1), "hrv"]
    _, dose = lagged_frame(features(1.0, binary=True), Hypothesis(2, "had_game", "hrv", 1, kind="lagged"))
    assert not dose


def test_a_fading_effect_is_found_with_its_shape():
    est, lp = fit(features(tau=2.0), "session_load")
    assert lp["tau"] in (1.0, 2.0, 4.0) and lp["shape_clear"]
    lags = lp["lags"]
    assert lags[0]["estimate"] == pytest.approx(-6.0, abs=1.5)
    assert all(x["estimate"] < 0 for x in lags) and abs(lags[1]["estimate"]) < abs(lags[0]["estimate"])
    assert lp["total"]["estimate"] < lags[0]["estimate"], "the effect adds up over the days"
    assert est.p_meaningful > 0.9 and est.model == "nig_ar1_decay"
    assert abs(sum(lp["weights"].values()) - 1.0) < 0.01


def test_a_next_morning_only_effect_prefers_no_fade():
    _, lp = fit(features(tau=0.0, binary=True, seed=4), "had_game")
    assert lp["tau"] in (0.0, 0.5)
    assert lp["weights"]["4.0"] < 0.2


def test_no_effect_gives_no_confident_estimate():
    est, lp = fit(features(tau=1.0, beta=0.0, seed=2), "session_load")
    assert est.p_meaningful < 0.5
    assert lp["lags"][0]["lo80"] < 0 < lp["lags"][0]["hi80"]


# ------------------------------------------------------------------ the database end
@pytest.mark.db
def test_lagged_hypotheses_on_the_demo(seeded, tx):
    from healthtracker.db import fetch_all
    from healthtracker.stats.bayes import run_effects
    from healthtracker.stats.ranking import effect_candidates

    conn = tx
    end = seeded["end"]
    run_effects(conn, end)
    rows = fetch_all(conn, """SELECT e.*, h.exposure FROM analytics.effect_estimate e
                              JOIN analytics.hypothesis h ON h.id = e.hypothesis_id
                              WHERE h.kind = 'lagged' AND e.computed_on = %s""", (end,))
    assert len(rows) == 5
    for r in rows:
        lp = r["lag_profile"]
        assert r["model"] == "nig_ar1_decay" and lp["first_lag"] == 1 and lp["lags"][0]["lag"] == 1
        assert set(lp["log_ev"]) == {"0.0", "0.5", "1.0", "2.0", "4.0"}
    cands = {c["key"]: c for c in effect_candidates(conn, end)}
    texts = [cands[f"effect:{r['hypothesis_id']}"]["text"] for r in rows if f"effect:{r['hypothesis_id']}" in cands]
    assert texts and all("next morning" in t for t in texts)
