from datetime import date

import numpy as np
import pandas as pd
import pytest
from statsmodels.stats.multitest import multipletests

from healthtracker.recommend.metrics import compare, split_outcome
from healthtracker.stats.experiments import assign_days, permutation_test
from healthtracker.stats.hypothesis import (
    Hypothesis,
    benjamini_hochberg,
    exposure_series,
    finalize,
    parse_exposure,
    run_test,
)


def synthetic(n=220, effect=-7.0, seed=3) -> pd.DataFrame:
    """Daily features where late caffeine lowers the next night's sleep score by `effect`."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    late = rng.random(n) < 0.35
    caffeine_hour = np.where(late, 15.5, 8.0)
    score = 78 + rng.normal(0, 6, n)
    score[1:] += effect * late[:-1]  # caffeine on day t -> sleep scored on day t+1
    return pd.DataFrame({
        "last_caffeine_hour": caffeine_hour, "sleep_score": score,
        "sleep_total_min": 450 + rng.normal(0, 25, n), "session_load": rng.choice([0, 250, 400, 700], n).astype(float),
        "exclude_from_baseline": False, "injury_active": False,
    }, index=idx)


def test_parse_exposure():
    assert parse_exposure("last_caffeine_hour >= 14") == ("last_caffeine_hour", ">=", 14.0)
    assert parse_exposure("had_game") == ("had_game", None, None)
    with pytest.raises(ValueError):
        parse_exposure("drop table; --")
    with pytest.raises(ValueError):
        parse_exposure("kcal >=")


def test_exposure_series_keeps_missing_as_missing():
    df = pd.DataFrame({"kcal": [1800.0, None, 2600.0]})
    assert exposure_series(df, "kcal < 2000").tolist()[0] == 1.0
    assert np.isnan(exposure_series(df, "kcal < 2000").tolist()[1])


def test_real_effect_is_supported():
    f = synthetic()
    h = Hypothesis(1, "last_caffeine_hour >= 14", "sleep_score", 1)
    res = finalize([run_test(f, h)], {1: h})[0]
    assert res.status == "supported", res.summary
    assert res.ci_low < -7 < res.ci_high
    assert res.boot_ci[1] < 0 and res.replicated


def test_no_effect_is_not_supported():
    f = synthetic(effect=0.0, seed=11)
    h = Hypothesis(1, "last_caffeine_hour >= 14", "sleep_score", 1)
    res = finalize([run_test(f, h)], {1: h})[0]
    assert res.status != "supported"


def test_too_few_days_is_insufficient():
    f = synthetic(n=40)
    h = Hypothesis(1, "last_caffeine_hour >= 14", "sleep_score", 1, min_n_per_group=20)
    assert run_test(f, h).status == "insufficient_data"


def test_bh_matches_statsmodels():
    p = [0.001, 0.02, 0.03, 0.2, 0.5, 0.04]
    ours = benjamini_hochberg(p)
    ref = multipletests(p, method="fdr_bh")[1]
    assert np.allclose(ours, ref)


def test_assignment_is_balanced_and_reproducible():
    a = assign_days(date(2026, 11, 2), 14, seed=5)
    assert len(a) == 14 and list(a.values()).count("treatment") == 7
    assert a == assign_days(date(2026, 11, 2), 14, seed=5)


def test_permutation_test_detects_difference():
    rng = np.random.default_rng(0)
    treat = np.array([True, False] * 10)
    y = rng.normal(70, 3, 20) + 6 * treat
    diff, p = permutation_test(y, treat, reps=2000)
    assert diff > 3 and p < 0.05


@pytest.mark.parametrize(("actual", "cmp", "target", "high", "expected"), [
    (5, "<=", 6, None, True), (7, "<=", 6, None, False), (140, ">=", 140, None, True),
    (2500, "between", 2400, 2800, True), (3000, "between", 2400, 2800, False), (None, "<=", 6, None, None),
    (1, "=", 1, None, True), (13.9, "<", 14, None, True),
])
def test_compare(actual, cmp, target, high, expected):
    assert compare(actual, cmp, target, high) is expected


def test_split_outcome():
    assert split_outcome("hrv_z_30d@1") == ("hrv_z_30d", 1)
    assert split_outcome(None) is None
