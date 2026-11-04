"""A6: TRIMP, session-RPE calibrated to it, and the daily load channels."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from healthtracker.features.load import DEFAULTS, calibration_factor, channel_of, daily_channels
from healthtracker.features.sessions import banister_trimp


def test_banister_trimp():
    # 60 min at 75% of heart-rate reserve (rest 50, max 190 -> HR 155): 60 x 0.75 x 0.64 x e^(1.92 x 0.75)
    assert banister_trimp(np.array([155.0]), np.array([60.0]), 50, 190, "male") == pytest.approx(121.6, abs=0.1)
    assert banister_trimp(np.array([155.0]), np.array([60.0]), 50, 190, "female") == pytest.approx(
        60 * 0.75 * 0.86 * np.exp(1.67 * 0.75), abs=0.1)
    assert banister_trimp(np.array([40.0, 230.0]), np.array([1.0, 1.0]), 50, 190, None) == pytest.approx(
        0.64 * np.exp(1.92))  # below rest counts 0, above max counts as max


def test_calibration_needs_enough_pairs():
    few = pd.DataFrame({"trimp": [60.0, 80.0], "srpe": [150.0, 200.0]})
    assert calibration_factor(few, DEFAULTS) == (0.4, 2, "default")
    many = pd.DataFrame({"trimp": [60.0, 80.0, 90.0, 70.0, 100.0, 300.0], "srpe": [150.0, 200, 200, 175, 250, 300]})
    factor, n, method = calibration_factor(many, DEFAULTS)
    assert method == "median_ratio" and n == 6 and factor == pytest.approx(0.4, abs=0.01)


def test_daily_channels():
    d = date(2026, 11, 10)
    s = pd.DataFrame({"date": [d, d, d], "sport": ["run", "ultimate", "speed"],
                      "session_type": ["easy", "game", "speed_jump"], "duration_min": [40.0, 90.0, 30.0],
                      "internal_load": [55.0, 300.0, 40.0], "plyo_contacts": [None, None, 60],
                      "sprint_m": [None, None, 180.0]})
    out = {r["channel"]: r["value"] for r in daily_channels(s).to_dict("records")}
    assert out == {"internal": 395.0, "internal_run": 55.0, "internal_game": 300.0, "internal_other": 40.0,
                   "plyo_contacts": 60.0, "sprint_m": 180.0, "shoulder_min": 90.0}
    assert channel_of("strength", "lift") == "internal_lift"


@pytest.mark.db
def test_one_scale_in_the_demo(seeded, conn):
    from healthtracker.db import fetch_all, fetch_one

    cal = fetch_one(conn, "SELECT * FROM analytics.load_calibration ORDER BY computed_on DESC LIMIT 1")
    assert cal["method"] == "median_ratio" and 0.2 < cal["factor"] < 0.8
    rows = fetch_all(conn, "SELECT session_type, sport, trimp, srpe, internal_load, load_method FROM analytics.session_features")
    assert all(r["internal_load"] is not None for r in rows)
    runs = [r for r in rows if r["sport"] == "run"]
    assert runs and all(r["load_method"] == "trimp" for r in runs)
    games = [r for r in rows if r["session_type"] == "game"]
    assert games and all(r["load_method"] == "srpe_calibrated" and r["trimp"] is None for r in games)
    assert all(r["internal_load"] == pytest.approx(round(r["srpe"] * cal["factor"], 1)) for r in games)
    check = fetch_one(conn, """SELECT count(*) AS bad FROM analytics.daily_features f
                               LEFT JOIN analytics.load_daily l ON l.date = f.date AND l.channel = 'internal'
                               WHERE abs(f.load_internal - coalesce(l.value, 0)) > 0.05""")
    assert check["bad"] == 0
    shoulder = fetch_one(conn, "SELECT count(*) AS n FROM analytics.load_daily WHERE channel = 'shoulder_min'")
    assert shoulder["n"] > 0
