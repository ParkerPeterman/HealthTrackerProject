"""G1 v2: learned readiness weights, kept only when they predict held-out sessions clearly better."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.features import readiness_v2 as r2
from healthtracker.features.readiness import COMPONENTS, scores, v2_scores
from healthtracker.stats.compare import clearly_better

P = {**{f"w_{c}": 1.0 for c in COMPONENTS}, "flag_z": 1.5}


def history(betas: dict[str, float], days=240, seed=1, missing=0.1):
    """Daily components, sessions on 60% of days, outcome = sum(beta x component) + noise."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    comp = pd.DataFrame(rng.normal(0, 1, (days, len(r2.LEARNED))), index=idx, columns=r2.LEARNED)
    comp = comp.mask(rng.random(comp.shape) < missing)
    comp["flag"] = 0.0
    beta = np.array([betas.get(c, 0.0) for c in r2.LEARNED])
    y = np.nan_to_num(comp[list(r2.LEARNED)].to_numpy()) @ beta + rng.normal(0, 1, days)
    out = pd.DataFrame({"outcome": y}, index=idx)[rng.random(days) < 0.6]
    return comp, r2.training_rows(comp, scores(comp, P)["composite"], out)


def arrays(df):
    return df[list(r2.LEARNED)].to_numpy(), df["v1"].to_numpy(), df["outcome"].to_numpy()


# ------------------------------------------------------------------ outcomes
def test_effort_against_plan_then_my_usual_for_the_type():
    s = pd.DataFrame({"date": pd.to_datetime(["2026-03-01", "2026-03-03", "2026-03-05", "2026-03-07", "2026-03-08"]),
                      "session_type": ["easy"] * 4 + ["lift"],
                      "rpe": [3, 4, 3, 6, 7], "planned_rpe": [np.nan, np.nan, np.nan, np.nan, 8.0]})
    ref = r2.effort_reference(s)
    assert np.isnan(ref.iloc[0]) and np.isnan(ref.iloc[2])  # fewer than 3 earlier easy runs
    assert ref.iloc[3] == 3.0   # median of 3, 4, 3: the RPE 6 easy run was 3 harder than usual
    assert ref.iloc[4] == 8.0   # the plan wins when one is linked


def test_outcome_parts_are_signed_so_higher_is_better():
    n = 20
    d = pd.date_range("2026-03-01", periods=n, freq="D")
    rng = np.random.default_rng(0)
    s = pd.DataFrame({"date": d, "session_type": "lift", "rpe": rng.integers(5, 9, n).astype(float),
                      "planned_rpe": 7.0, "feel": rng.integers(1, 6, n).astype(float)})
    pace = pd.DataFrame({"date": d, "residual": rng.normal(0, 5, n)})
    out = r2.day_outcomes(s, pace)
    assert len(out) == n and (out["n_parts"] == 3).all()
    assert np.corrcoef(out["effort"], -s["rpe"])[0, 1] == pytest.approx(1.0)       # harder than planned: worse
    assert np.corrcoef(out["pace"], -pace["residual"])[0, 1] == pytest.approx(1.0)  # slower than trend: worse
    assert np.corrcoef(out["feel"], s["feel"])[0, 1] == pytest.approx(1.0)
    assert out["outcome"].to_numpy() == pytest.approx(out[list(r2.PARTS)].mean(axis=1).to_numpy())


def test_a_part_with_too_few_sessions_is_left_out():
    d = pd.date_range("2026-03-01", periods=15, freq="D")
    s = pd.DataFrame({"date": d, "session_type": "lift", "rpe": np.r_[[6.0, 7.0] * 7, 6.0], "planned_rpe": 7.0,
                      "feel": [np.nan] * 12 + [3.0, 4.0, 2.0]})
    out = r2.day_outcomes(s, pd.DataFrame(columns=["date", "residual"]))
    assert out["feel"].isna().all() and out["pace"].isna().all() and out["effort"].notna().all()


# ------------------------------------------------------------------ learning and the comparison
def test_learns_which_components_matter():
    _, df = history({"hrv": 0.4, "energy": 0.4})
    Z, _, y = arrays(df)
    w = r2.weights_record(r2.learn(Z, y, r2.prior_means(P)))
    others = [w[c]["beta"] for c in r2.LEARNED if c not in ("hrv", "energy")]
    assert min(w["hrv"]["beta"], w["energy"]["beta"]) > max(others)
    assert w["hrv"]["lo80"] < w["hrv"]["beta"] < w["hrv"]["hi80"]
    assert np.mean([v["relative"] for v in w.values()]) == pytest.approx(1.0, abs=0.01)


def test_no_weight_goes_negative():
    """A component that predicts the wrong way (as if being sore helped) is dropped, not inverted."""
    _, df = history({"hrv": 0.3, "soreness": -0.4})
    Z, _, y = arrays(df)
    m = r2.learn(Z, y, r2.prior_means(P))
    w = r2.weights_record(m)
    assert w["soreness"]["beta"] == 0 and w["soreness"]["dropped"] and w["soreness"]["lo80"] is None
    assert all(v["beta"] >= 0 for v in w.values()) and w["hrv"]["beta"] > 0
    assert m["coef"][r2.LEARNED.index("soreness")] == 0


def test_weight_intervals_cover_about_eighty_percent():
    """Section 13 calibration: true weights near the prior (0-0.3), 80% intervals hold the truth
    about 80% of the time (a dropped weight counts as a miss)."""
    rng = np.random.default_rng(0)
    hits = tot = 0
    for s in range(60):
        true = {c: float(rng.uniform(0.0, 0.3)) for c in r2.LEARNED}
        _, df = history(true, days=150, seed=1000 + s)
        Z, _, y = arrays(df)
        w = r2.weights_record(r2.learn(Z, y, r2.prior_means(P)))
        for c in r2.LEARNED:
            tot += 1
            hits += (not w[c].get("dropped")) and w[c]["lo80"] <= true[c] / y.std(ddof=1) <= w[c]["hi80"]
    assert 0.7 <= hits / tot <= 0.9


def test_priors_keep_v2_near_v1_with_little_data():
    _, df = history({}, days=50, seed=3)
    Z, _, y = arrays(df)
    w = r2.weights_record(r2.learn(Z, y, r2.prior_means(P)))
    assert all(abs(v["beta"] - r2.PRIOR_EFFECT) < 0.25 for v in w.values())


def test_v2_replaces_v1_when_it_predicts_clearly_better():
    _, df = history({"hrv": 0.4, "energy": 0.4}, seed=1)
    Z, c1, y = arrays(df)
    bt = r2.backtest(Z, c1, y, r2.prior_means(P))
    assert bt["n"] == len(y) - r2.MIN_TRAIN and bt["mae_v2"] < bt["mae_v1"]
    assert clearly_better(bt["mae_v2"], bt["mae_v1"], bt["p_better"], r2.V2_MARGIN, r2.V2_P)


@pytest.mark.parametrize("betas", [{}, {c: 0.1 for c in r2.LEARNED}])
def test_v2_does_not_replace_v1_when_equal_weights_are_right_or_nothing_matters(betas):
    _, df = history(betas, seed=1)
    Z, c1, y = arrays(df)
    bt = r2.backtest(Z, c1, y, r2.prior_means(P))
    assert not clearly_better(bt["mae_v2"], bt["mae_v1"], bt["p_better"], r2.V2_MARGIN, r2.V2_P)


# ------------------------------------------------------------------ v2 scores
def test_v2_composite_flag_and_minimum_components():
    comp, _ = history({}, days=30)
    betas = {c: 0.1 for c in r2.LEARNED}
    base = r2.v2_composite(comp, betas, P)
    flagged = comp.copy()
    flagged.iloc[-1, flagged.columns.get_loc("flag")] = 1.0
    assert r2.v2_composite(flagged, betas, P).iloc[-1] == pytest.approx(base.iloc[-1] - 1.5 * 0.1)
    thin = comp.copy()
    thin.iloc[-1, 2:7] = np.nan
    assert np.isnan(r2.v2_composite(thin, betas, P).iloc[-1])


def test_each_day_uses_the_model_fitted_before_it():
    comp, _ = history({}, days=120)
    w = {c: {"beta": 0.1} for c in r2.LEARNED}
    w_sleep = {c: {"beta": 0.5 if c == "sleep" else 0.0} for c in r2.LEARNED}
    models = [{"computed_on": comp.index[60].date(), "weights": w, "adopted": False},
              {"computed_on": comp.index[90].date(), "weights": w_sleep, "adopted": True}]
    out = v2_scores(comp, models, P)
    assert out.index.min() > comp.index[60]           # nothing before the first model, nor on its day
    assert out.loc[comp.index[90], "model_on"] == comp.index[60].date()  # the day a model is fitted uses the one before
    assert out.loc[comp.index[91], "model_on"] == comp.index[90].date() and out.loc[comp.index[91], "adopted"]
    assert out["score"].between(0, 100).all()
    d = out.loc[comp.index[100], "drivers"]
    assert d and all(x["component"] == "sleep" for x in d)  # only sleep carries weight in that model


# ------------------------------------------------------------------ the demo
@pytest.mark.db
def test_demo_readiness_model_in_shadow(seeded, tx):
    from healthtracker.db import fetch_all, fetch_one

    assert r2.build_readiness_model(tx, seeded["end"]) == 1
    m = fetch_one(tx, "SELECT * FROM analytics.readiness_model WHERE computed_on = %s", (seeded["end"],))
    assert m["gate"]["needs_days"] == 90 and m["n_sessions"] >= r2.MIN_TRAIN
    assert set(m["weights"]) == set(r2.LEARNED) and m["parts"]["feel"] > 0 and m["backtest"]["n"] > 0
    assert m["adopted"] is False or m["gate"]["open"]
    # The weekly job fitted models on the Sundays; daily rows carry v2 next to v1, v1 still in use.
    rows = fetch_all(tx, "SELECT * FROM analytics.readiness_daily WHERE v2 IS NOT NULL ORDER BY date")
    assert rows and all(r["version"] == "v1" and r["score"] == r["score_v1"] for r in rows)
    assert all(0 <= r["v2"]["score"] <= 100 and len(r["v2"]["drivers"]) <= 3 for r in rows)


@pytest.mark.db
def test_demo_v2_takes_over_only_when_adopted_and_live(seeded, tx):
    from datetime import timedelta

    from healthtracker.db import execute, fetch_all
    from healthtracker.features.readiness import build_readiness

    end = seeded["end"]
    execute(tx, "UPDATE analytics.rule SET status = 'active' WHERE rule_id = 'COMP_READINESS_V2'")
    assert build_readiness(tx, end, days=30) > 0
    rows = fetch_all(tx, "SELECT * FROM analytics.readiness_daily WHERE v2 IS NOT NULL AND date >= %s",
                     (end - timedelta(days=30),))  # the days rebuilt
    assert all((r["version"] == "v2") == r["v2"]["adopted"] for r in rows)
    execute(tx, "UPDATE analytics.readiness_model SET adopted = TRUE")  # as if v2 had predicted better
    assert build_readiness(tx, end, days=30) > 0
    rows = fetch_all(tx, "SELECT * FROM analytics.readiness_daily WHERE v2 IS NOT NULL AND date >= %s",
                     (end - timedelta(days=30),))  # the days rebuilt
    assert rows and all(r["version"] == "v2" and r["score"] == r["v2"]["score"] for r in rows)
    assert all(r["score_v1"] is not None for r in rows)  # v1 kept alongside for the shadow log


@pytest.mark.db
def test_v1_is_written_when_v2_fails(seeded, tx, monkeypatch):
    from healthtracker.db import fetch_all
    from healthtracker.features import readiness

    def boom(*_a, **_k):
        raise RuntimeError("v2 broke")

    monkeypatch.setattr(readiness, "v2_scores", boom)
    assert readiness.build_readiness(tx, seeded["end"], days=14) > 0
    rows = fetch_all(tx, "SELECT * FROM analytics.readiness_daily WHERE date > %s", (seeded["end"] - timedelta(days=14),))
    assert rows and all(r["version"] == "v1" and r["v2"] is None and r["score"] == r["score_v1"] for r in rows)
