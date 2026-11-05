"""D5: learned response by session type."""

import numpy as np
import pandas as pd
import pytest

from healthtracker.features.session_response import recovery_hours, responses


def test_recovers_a_costly_session_type():
    rng = np.random.default_rng(2)
    n = 150
    idx = pd.date_range("2026-03-01", periods=n, freq="D")
    types = rng.choice(["easy", "vo2", "lift", "none"], n, p=[0.3, 0.15, 0.25, 0.3])
    sessions = pd.DataFrame({"date": idx[types != "none"].date, "session_type": types[types != "none"]})
    lnhrv = 4.0 + rng.normal(0, 0.05, n)
    vo2 = types == "vo2"
    lnhrv[1:] -= 0.06 * vo2[:-1]  # planted: VO2 lowers the next mornings
    lnhrv[2:] -= 0.06 * vo2[:-2]
    status = pd.DataFrame({"date": np.r_[idx, idx], "metric": ["ln_hrv"] * n + ["resting_hr"] * n,
                           "value": np.r_[lnhrv, 50 + rng.normal(0, 1, n)], "base_center": np.r_[[4.0] * n, [50] * n],
                           "swc": np.r_[[0.03] * n, [0.5] * n]})
    f = pd.DataFrame({"soreness": rng.integers(1, 5, n), "energy": rng.integers(1, 5, n), "is_sick": False}, index=idx)
    rows = {(r["session_type"], r["outcome"]): r for r in responses(f, sessions, status)}
    assert rows[("vo2", "hrv_swc")]["estimate"] < -1.0 and rows[("vo2", "hrv_swc")]["p_direction"] > 0.95
    assert abs(rows[("easy", "hrv_swc")]["estimate"]) < 0.8
    assert rows[("vo2", "hrv_swc")]["pooled_estimate"] < rows[("easy", "hrv_swc")]["pooled_estimate"]


def test_recovery_hours():
    assert recovery_hours(None, True) == 48 and recovery_hours(-1.2, True) == 72 and recovery_hours(0.1, False) == 0


@pytest.mark.db
def test_demo_session_response(seeded, tx):
    from healthtracker.db import fetch_all, fetch_one
    from healthtracker.features.session_response import build_session_response

    assert build_session_response(tx, seeded["end"]) > 0
    rows = fetch_all(tx, "SELECT * FROM analytics.session_response WHERE computed_on = %s", (seeded["end"],))
    game = [r for r in rows if r["session_type"] == "game" and r["outcome"] == "hrv_swc"]
    assert game and game[0]["estimate"] < 0  # planted: games lower next-day HRV
    lib = fetch_one(tx, "SELECT recovery_source FROM knowledge.session_library WHERE code = 'vo2_4x4'")
    assert lib["recovery_source"] == "prior"  # shadow: nothing written back yet
