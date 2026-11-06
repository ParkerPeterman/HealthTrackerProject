"""D2: the fitness-fatigue model recovers a simulated impulse-response and backtests against naive;
fitted time constants replace 42/7 only past the gate and when clearly better."""

import numpy as np
import pandas as pd
import pytest

from healthtracker.features import fitness_fatigue as ff


def simulate(days=200, seed=5, noise=2.0, taus=(42, 7)):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    load = pd.Series(np.where(rng.random(days) < 0.6, rng.uniform(40, 160, days), 0.0), index=idx)
    fit_, fat_ = ff.impulse(load, taus[0]).shift(1), ff.impulse(load, taus[1]).shift(1)
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


def test_forecast_intervals_are_conservative():
    """Section 13 calibration: one-run-ahead 80% intervals hold the next run at least 80% of the
    time. They hold it closer to 90%: the shared B1 priors (residual variance centred on the whole
    outcome variance, effects shrunk toward +-0.2 SD) overstate the noise by about a fifth."""
    from scipy import stats

    z, hits, n = stats.norm.ppf(0.9), 0, 0
    for seed in range(1, 5):
        a = ff._rows(ff.frame(*simulate(days=200, seed=seed)))
        for k in range(ff.MIN_RUNS, len(a), 2):
            mean, sd = ff.predict(ff._fit(a[:k]), a[k, 1], a[k, 2])
            hits += abs(a[k, 0] - mean) <= z * sd
            n += 1
    assert 0.8 <= hits / n <= 0.97


def test_impulse_decays():
    s = ff.impulse(pd.Series([100.0] + [0.0] * 42), 42)
    assert s.iloc[0] == 100 and s.iloc[42] == pytest.approx(100 * np.exp(-1), rel=1e-6)


def test_numpy_fit_matches_the_b1_design():
    from healthtracker.stats.bayes import VAGUE, design, fit

    df = ff.frame(*simulate())
    X, y, consecutive, y_sd, scales, _ = design(df, [("fitness", True), ("fatigue", True)])
    ref = fit(X, y, consecutive, np.r_[-0.2, 0.2, 0.0], np.r_[0.25, 0.25, VAGUE], ar=False)
    m = ff.fit_model(df)
    assert np.allclose(m["post"]["mn"], ref["mn"]) and m["post"]["log_ev"] == pytest.approx(ref["log_ev"])
    assert np.allclose(m["scales"], scales) and m["y_sd"] == pytest.approx(y_sd)


def test_tau_prior_is_centred_on_42_and_7():
    lp = ff.log_tau_prior()
    assert max(lp, key=lp.get) == (42.0, 7.0)
    assert np.exp(list(lp.values())).sum() == pytest.approx(1.0)


def test_grid_rows_are_the_same_runs_for_every_pair():
    frames = ff.grid_frames(*simulate())
    idx = frames[ff.FIXED].index
    assert len(frames) == len(ff.GRID_FITNESS) * len(ff.GRID_FATIGUE)
    assert all(df.index.equals(idx) for df in frames.values())  # so their evidences compare


def test_gate_keeps_42_and_7_with_too_few_runs():
    pace, load = simulate(days=120, taus=(60, 3))
    sel, rec = ff.choose_taus(pace, load, ff.backtest(pace, load))
    assert sel is None and not rec["gate"]["open"] and rec["used"] is False and "best" not in rec


def test_fitted_constants_adopted_when_clearly_better():
    """A short fatigue constant (3 days) far from 7: the grid finds it and forecasts clearly better."""
    pace, load = simulate(days=240, seed=1, taus=(60, 3))
    fixed = ff.backtest(pace, load)
    sel, rec = ff.choose_taus(pace, load, fixed)
    assert rec["gate"]["open"] and rec["used"] and sel is not None
    assert rec["best"]["fitness"] >= 50 and rec["best"]["fatigue"] <= 5
    assert rec["backtest"]["mae_model"] <= 0.95 * fixed["mae_model"] and rec["p_better"] >= 0.95
    assert sel["best"] == (rec["best"]["fitness"], rec["best"]["fatigue"]) and sel["backtest"]["beats_naive"]
    fc = ff.forecast(sel["model"], load, weekly_load=700, taus=sel["best"])
    assert fc["lo80"] < fc["pace_s_km"] < fc["hi80"]


def test_fitted_constants_not_adopted_when_42_and_7_are_true():
    pace, load = simulate(days=240, seed=1)
    sel, rec = ff.choose_taus(pace, load, ff.backtest(pace, load))
    assert rec["gate"]["open"] and sel is None and rec["used"] is False and rec["reason"] == "not clearly better than 42/7"


def test_paired_comparison():
    a = {"at": list(range(30)), "errors": [1.0] * 30}
    b = {"at": list(range(30)), "errors": [1.5 + 0.1 * (i % 3) for i in range(30)]}
    assert ff.p_better(a, b) > 0.99 and ff.p_better(b, a) < 0.01
    assert ff.p_better(a, a) == 0.0 and ff.p_better(a, {"at": [], "errors": []}) == 0.0


@pytest.mark.db
def test_demo_fitness_fatigue(seeded, tx):
    from healthtracker.db import fetch_one

    assert ff.build_fitness_fatigue(tx, seeded["end"]) == 1
    row = fetch_one(tx, "SELECT * FROM analytics.fitness_fatigue WHERE computed_on = %s", (seeded["end"],))
    assert row["n_runs"] >= 15 and row["forecast"]["pace_s_km"] > 0 and row["mae_naive"] is not None
    # 90 demo days: the gate stays shut, 42/7 are used, and the record says why.
    assert row["tau_source"] == "fixed" and float(row["tau_fitness"]) == 42 and float(row["tau_fatigue"]) == 7
    assert row["tau_fit"]["gate"]["open"] is False and row["tau_fit"]["gate"]["needs_days"] == 150


@pytest.mark.db
def test_demo_fitted_taus_with_the_gate_lowered(seeded, tx):
    """The demo has no planted time constants, so the grid gives a verdict but 42/7 are kept
    unless the grid's pick is clearly better; either way the row and G2 agree on the constants."""
    from healthtracker.db import execute, fetch_one
    from healthtracker.stats.forecast import _plan_row

    execute(tx, "UPDATE analytics.rule_parameter SET min_allowed = 15, value = 30 WHERE rule_id = 'ANALYSIS_D2'")
    assert ff.build_fitness_fatigue(tx, seeded["end"]) == 1
    row = fetch_one(tx, "SELECT * FROM analytics.fitness_fatigue WHERE computed_on = %s", (seeded["end"],))
    rec = row["tau_fit"]
    assert rec["gate"]["open"] and rec["best"]["fitness"] in ff.GRID_FITNESS and 0 < rec["best_weight"] <= 1
    assert sum(t["weight"] for t in rec["top"]) <= 1.001 and rec["backtest"]["n"] > 0
    assert row["tau_source"] == ("fitted" if rec["used"] else "fixed")
    used = (rec["best"]["fitness"], rec["best"]["fatigue"]) if rec["used"] else ff.FIXED
    assert (float(row["tau_fitness"]), float(row["tau_fatigue"])) == used
    plan = _plan_row(tx, seeded["end"])
    assert plan is not None and plan["model"] == "fitness_fatigue"


@pytest.mark.db
def test_fixed_constants_still_written_when_fitting_fails(seeded, tx, monkeypatch):
    from healthtracker.db import fetch_one

    def boom(*_a, **_k):
        raise RuntimeError("grid broke")

    monkeypatch.setattr(ff, "choose_taus", boom)
    assert ff.build_fitness_fatigue(tx, seeded["end"]) == 1
    row = fetch_one(tx, "SELECT * FROM analytics.fitness_fatigue WHERE computed_on = %s", (seeded["end"],))
    assert row["tau_source"] == "fixed" and row["tau_fit"]["reason"] == "fitting failed"
