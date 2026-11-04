"""A3 baselines and A2 HRV / resting-HR bands."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.features.baselines import (
    BaselineParams,
    compute_metric_status,
    status_frame,
    trimmed_mean,
)

P = BaselineParams()


def series(values, start="2026-01-01") -> pd.Series:
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="D"), dtype=float)


def noisy(n: int, level: float = 4.1, sd: float = 0.08, seed: int = 4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out, e = np.empty(n), 0.0
    for i in range(n):
        e = 0.5 * e + rng.normal(0, sd)
        out[i] = level + e
    return out


def all_days(s: pd.Series) -> pd.Series:
    return pd.Series(True, index=s.index)


def test_trimmed_mean_drops_one_extreme_each_side():
    assert trimmed_mean(np.array([4.0, 4.0, 4.0, 4.0, 3.0])) == 4.0
    assert trimmed_mean(np.array([4.0, 3.0, 5.0, np.nan])) == 4.0  # 3 nights: plain mean


def test_baseline_leaves_out_the_acute_week():
    s = series(list(noisy(80)) + [3.7] * 7)
    out = status_frame(s, all_days(s), P, "below")
    last = out.iloc[-1]
    assert abs(last["base_center"] - 4.1) < 0.05  # the low week isn't in its own baseline
    assert last["acute"] == pytest.approx(3.7) and last["status"] == "below"
    assert last["base_n"] == 60 and last["maturity"] == 1.0


def test_odd_days_barely_move_a_robust_baseline():
    v = noisy(80)
    v[20:23] = 2.0  # three absurd nights inside the baseline window
    s = series(v)
    out = status_frame(s, all_days(s), P, "below")
    clean = status_frame(series(noisy(80)), all_days(s), P, "below")
    assert abs(out["base_center"].iloc[-1] - clean["base_center"].iloc[-1]) < 0.02


def _new_warnings(a: pd.Series, b: pd.Series) -> int:
    """Days that are 'below' in `b` (with the bad night) but not in `a` (without it)."""
    return int(((b == "below") & (a != "below")).sum())


def flip_rate(params: BaselineParams, runs: int = 120) -> float:
    flips = 0
    for seed in range(runs):
        v = noisy(90, seed=seed)
        bad = v.copy()
        bad[82] -= np.log(1 / 0.7)  # one night 30% down
        a = status_frame(series(v), all_days(series(v)), params, "below")["status"].iloc[82:89]
        b = status_frame(series(bad), all_days(series(bad)), params, "below")["status"].iloc[82:89]
        flips += _new_warnings(a, b) > 0
    return flips / runs


def test_trimming_halves_the_pull_of_one_bad_night():
    # A +/-0.5 SD band is narrow by design (about a third of ordinary weeks sit below it), so one
    # night can tip a borderline week either way. Trimming the week's extremes is what keeps a
    # single night from deciding it: here it roughly halves those flips versus a plain mean.
    trimmed = flip_rate(P)
    plain = flip_rate(BaselineParams(trim=False))
    assert trimmed <= 0.6 * plain, (trimmed, plain)


def test_bad_night_in_an_ordinary_week_changes_nothing():
    v = noisy(110)
    v[82:95] = np.median(v[22:82]) + np.tile([0.02, -0.03, 0.01, 0.0, -0.01, 0.03, -0.02], 2)[:13]  # ordinary days
    bad = v.copy()
    bad[88] -= np.log(1 / 0.6)  # one night 40% down
    a = status_frame(series(v), all_days(series(v)), P, "below")["status"].iloc[88:95]
    b = status_frame(series(bad), all_days(series(bad)), P, "below")["status"].iloc[88:95]
    assert _new_warnings(a, b) == 0


def test_overreaching_block_pushes_the_week_below_the_band():
    v = noisy(110)
    v[90:102] -= np.linspace(0.015, 0.18, 12)
    s = series(v)
    st = status_frame(s, all_days(s), P, "below")["status"]
    assert (st.iloc[95:103] == "below").mean() >= 0.75


def test_needs_enough_nights_and_history():
    s = series(noisy(20))
    out = status_frame(s, all_days(s), P, "below")
    assert out["status"].isna().all() and out["maturity"].iloc[-1] < 1  # still learning
    few = s.copy()
    few.iloc[-7:-3] = np.nan  # only 3 nights in the last week
    assert np.isnan(status_frame(few, few.notna(), P, "below")["acute"].iloc[-1])


def test_resting_hr_warns_above():
    s = series(list(noisy(80, level=50, sd=1.0)) + [54.0] * 7)
    assert status_frame(s, all_days(s), P, "above")["status"].iloc[-1] == "above"


def test_dose_change_resets_maturity_and_widens_band():
    s = series(noisy(120))
    reset = s.index[100].date()
    plain = status_frame(s, all_days(s), P, "below")
    after = status_frame(s, all_days(s), P, "below", resets=[reset])
    assert plain["maturity"].iloc[110] == 1.0 and after["maturity"].iloc[110] < 0.2
    assert after["widened"].iloc[110] and after["swc"].iloc[110] == pytest.approx(2 * plain["swc"].iloc[110])
    assert not after["widened"].iloc[100 + 28 + 1 :].any()


def test_metric_status_covers_every_metric_and_skips_sick_days():
    idx = pd.date_range("2026-01-01", periods=70, freq="D")
    f = pd.DataFrame({"ln_hrv": noisy(70), "resting_hr": 50.0, "sleep_total_min": 450.0, "sleep_midpoint_min": 200.0,
                      "resp_rate": 14.0, "is_sick": False, "sleep_plausible": True}, index=idx)
    f.loc[idx[-3:], "is_sick"] = True
    f.loc[idx[-3:], "ln_hrv"] = 3.0
    out = compute_metric_status(f, P)
    assert set(out["metric"]) == {"ln_hrv", "resting_hr", "sleep_total_min", "sleep_midpoint_min", "resp_rate"}
    last = out[(out["metric"] == "ln_hrv")].iloc[-1]
    assert last["acute_n"] == 4 and last["acute"] > 3.9  # sick nights left out


@pytest.mark.db
def test_demo_overreaching_block_and_bad_nights(seeded, conn):
    from healthtracker.db import fetch_df
    from healthtracker.features.baselines import load_params

    start, days = seeded["start"], 90
    over = start + timedelta(days=int(days * 0.40))
    st = fetch_df(conn, "SELECT date, status, acute, maturity FROM analytics.metric_status WHERE metric = 'ln_hrv' ORDER BY date")
    st["date"] = pd.to_datetime(st["date"])
    late_block = st[(st["date"] >= pd.Timestamp(over + timedelta(days=5))) & (st["date"] < pd.Timestamp(over + timedelta(days=13)))]
    assert len(late_block) == 8 and (late_block["status"] == "below").mean() >= 0.75

    f = fetch_df(conn, "SELECT * FROM analytics.daily_features ORDER BY date")
    f["date"] = pd.to_datetime(f["date"])
    f = f.set_index("date")
    params = load_params(conn)
    base = compute_metric_status(f, params)
    fixed = f.copy()
    for frac in (0.70, 0.85, 0.95):
        d = pd.Timestamp(start + timedelta(days=int(days * frac)))
        assert f.loc[d, "ln_hrv"] < f.loc[d - pd.Timedelta(days=1), "ln_hrv"] - 0.2  # the planted night
        fixed.loc[d, "ln_hrv"] = (f.loc[d - pd.Timedelta(days=1), "ln_hrv"] + f.loc[d + pd.Timedelta(days=1), "ln_hrv"]) / 2 \
            if d + pd.Timedelta(days=1) in f.index else f.loc[d - pd.Timedelta(days=1), "ln_hrv"]
    without = compute_metric_status(fixed, params)
    for frac in (0.70, 0.85, 0.95):
        d = pd.Timestamp(start + timedelta(days=int(days * frac)))
        a, b = (x[(x["metric"] == "ln_hrv") & (x["date"] >= d) & (x["date"] < d + pd.Timedelta(days=7))]
                .set_index("date")["status"] for x in (without, base))
        assert _new_warnings(a, b) == 0, (d, a.tolist(), b.tolist())
