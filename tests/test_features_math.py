import numpy as np
import pandas as pd

from healthtracker.features.daily import _days_since, _prior_baseline, impute_rpe, pace_at_ref_hr


def test_pace_at_ref_hr_recovers_linear_relationship():
    hr = np.linspace(130, 170, 20)
    speed = 0.9 + 0.0155 * hr  # m/s
    pts = pd.DataFrame({"hr": hr, "speed": speed})
    pace = pace_at_ref_hr(pts, 145)
    assert pace == round(1000 / (0.9 + 0.0155 * 145), 1)


def test_pace_at_ref_hr_refuses_to_extrapolate_or_guess():
    pts = pd.DataFrame({"hr": [140, 141, 142, 143, 141, 140], "speed": [3.0] * 6})
    assert pace_at_ref_hr(pts, 170) is None  # far outside, no spread
    assert pace_at_ref_hr(pts.head(3), 141) is None  # too few points


def test_impute_rpe_scales_with_heart_rate():
    assert impute_rpe(None, 190) == 5.0
    assert impute_rpe(100, 190) == 2.0
    assert impute_rpe(150, 190) == 5.0
    assert impute_rpe(180, 190) == 9.0


def test_days_since():
    flag = pd.Series([False, True, False, False, True, False])
    assert _days_since(flag).tolist()[1:] == [0, 1, 2, 0, 1]
    assert np.isnan(_days_since(flag).iloc[0])


def test_baseline_skips_excluded_days_and_survives_long_gaps():
    idx = pd.date_range("2026-01-01", periods=80, freq="D")
    values = pd.Series(60.0, index=idx)
    values.iloc[40:65] = 30.0  # an injury period with odd values
    eligible = pd.Series(True, index=idx)
    eligible.iloc[40:65] = False
    mean, std = _prior_baseline(values, eligible, window=30, min_periods=10)
    # odd values never enter the baseline, and it doesn't blank out after the gap
    assert mean.iloc[50] == 60.0 and mean.iloc[70] == 60.0
    assert mean.iloc[5] != mean.iloc[5]  # NaN: not enough history yet
