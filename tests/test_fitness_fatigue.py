"""D2: the fitness-fatigue model recovers a simulated impulse-response and backtests against naive."""

import numpy as np
import pandas as pd
import pytest

from healthtracker.features import fitness_fatigue as ff


def simulate(days=200, seed=5, noise=2.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    load = pd.Series(np.where(rng.random(days) < 0.6, rng.uniform(40, 160, days), 0.0), index=idx)
    fit_, fat_ = ff.impulse(load, 42).shift(1), ff.impulse(load, 7).shift(1)
    pace = 330 - 0.012 * fit_ + 0.03 * fat_ + rng.normal(0, noise, days)  # fitness lowers pace, fatigue raises it
    run_days = idx[(rng.random(days) < 0.35) & (np.arange(days) > 10)]
    return pace.loc[run_days], load


def test_recovers_directions_and_beats_naive():
    pace, load = simulate()
    model = ff.fit_model(ff.frame(pace, load))
    k = model["post"]["mn"]
    assert k[0] < 0 < k[1]
    bt = ff.backtest(pace, load)
    assert bt["n"] > 20 and bt["beats_naive"]
    fc = ff.forecast(model, load, weekly_load=700)
    assert fc["lo80"] < fc["pace_s_km"] < fc["hi80"] and fc["weeks"] == 8


def test_impulse_decays():
    s = ff.impulse(pd.Series([100.0] + [0.0] * 42), 42)
    assert s.iloc[0] == 100 and s.iloc[42] == pytest.approx(100 * np.exp(-1), rel=1e-6)


@pytest.mark.db
def test_demo_fitness_fatigue(seeded, tx):
    from healthtracker.db import fetch_one

    assert ff.build_fitness_fatigue(tx, seeded["end"]) == 1
    row = fetch_one(tx, "SELECT * FROM analytics.fitness_fatigue WHERE computed_on = %s", (seeded["end"],))
    assert row["n_runs"] >= 15 and row["forecast"]["pace_s_km"] > 0 and row["mae_naive"] is not None
