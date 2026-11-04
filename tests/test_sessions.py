"""A5: one row per session, with type, time in zones, pace at reference HR, GAP, and decoupling."""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.features.sessions import (
    DEFAULTS,
    classify_run,
    cost_of_running,
    decoupling,
    grade_adjusted_pace,
    minute_bins,
    tid_from_effort,
    time_in_zones,
    type_from_code,
)
from healthtracker.features.zones import Zones

Z = Zones(1, 200, "field_test", True, date(2026, 1, 1))
T0 = datetime(2026, 11, 10, 6, 0)


def run_samples(minutes: int, hr, speed, altitude=None) -> pd.DataFrame:
    n = minutes * 60
    ts = [T0 + timedelta(seconds=i) for i in range(n)]
    f = lambda v: np.asarray(v(np.arange(n)) if callable(v) else np.full(n, v), dtype=float)  # noqa: E731
    df = pd.DataFrame({"ts": ts, "hr": f(hr), "speed_mps": f(speed)})
    if altitude is not None:
        df["altitude_m"] = f(altitude)
    return df


def test_cost_of_running_and_gap():
    assert cost_of_running(0.0) == pytest.approx(3.6)
    assert cost_of_running(0.1) > cost_of_running(0.0) > cost_of_running(-0.05)
    flat = minute_bins(run_samples(30, 150, 3.0, altitude=100.0))
    climb = minute_bins(run_samples(30, 150, 3.0, altitude=lambda i: 100 + i * 0.15))  # 5% grade
    assert grade_adjusted_pace(flat) == pytest.approx(333.3, abs=0.5)
    assert grade_adjusted_pace(climb) < 300  # the same speed uphill is worth a faster flat pace


def test_decoupling_and_drift():
    steady = minute_bins(run_samples(60, 150, 3.0))
    drifting = minute_bins(run_samples(60, lambda i: 145 + i / 3600 * 15, 3.0))
    assert decoupling(steady, 60, DEFAULTS) == (0.0, 0.0)
    dec, drift = decoupling(drifting, 60, DEFAULTS)
    assert dec > 3 and drift > 3
    assert decoupling(steady, 35, DEFAULTS) == (None, None)  # too short


def test_classify_runs_from_hr():
    easy = minute_bins(run_samples(40, 140, 3.0))
    long = minute_bins(run_samples(85, 145, 3.0))
    tempo = minute_bins(run_samples(45, lambda i: np.where((i > 600) & (i < 2100), 170, 140), 3.3))
    intervals = minute_bins(run_samples(40, lambda i: np.where((i > 600) & ((i // 180) % 2 == 0), 185, 140), 3.5))
    assert classify_run(easy, Z, 40, 140, None, DEFAULTS) == ("easy", "hr")
    assert classify_run(long, Z, 85, 145, None, DEFAULTS) == ("long", "hr")
    assert classify_run(tempo, Z, 45, 160, None, DEFAULTS)[0] == "tempo"
    assert classify_run(intervals, Z, 40, 160, None, DEFAULTS)[0] == "vo2"  # 3-min bouts at 92.5% of max
    assert classify_run(None, Z, 40, 182, None, DEFAULTS) == ("tempo", "summary")


def test_time_in_zones_adds_up():
    s = run_samples(30, lambda i: np.where(i < 900, 130, 175), 3.0)
    z = time_in_zones(s, Z)
    assert sum(z[f"z{k}_min"] for k in range(1, 6)) == pytest.approx(30, abs=0.1)
    assert z["low_min"] == pytest.approx(15, abs=0.1) and z["mid_min"] == pytest.approx(15, abs=0.1)


def test_session_rpe_tid_and_library_codes():
    assert tid_from_effort(8, 90)["high_min"] == 90 and tid_from_effort(None, 30)["low_min"] == 30
    assert type_from_code("vo2_4x4") == "vo2" and type_from_code("accel_sprints") == "speed_jump"
    assert type_from_code(None) is None and type_from_code("mystery") is None


@pytest.mark.db
def test_every_session_has_a_row_and_rebuilds_identically(seeded, conn):
    from healthtracker.db import fetch_all
    from healthtracker.features.sessions import build_session_features

    sessions = fetch_all(conn, """SELECT w.id, w.sport, w.is_game, w.source, f.session_type, f.hr_source, f.tid_method,
                                         f.low_min, f.mid_min, f.high_min, f.duration_min
                                  FROM core.v_workout w LEFT JOIN analytics.session_features f ON f.workout_id = w.id""")
    assert sessions and all(s["session_type"] for s in sessions)
    games = [s for s in sessions if s["is_game"]]
    runs = [s for s in sessions if s["sport"] == "run"]
    assert games and all(s["session_type"] == "game" and s["tid_method"] == "session_rpe" for s in games)
    assert runs and all(s["hr_source"] == "samples" for s in runs)
    assert {"easy", "tempo"} <= {s["session_type"] for s in runs}
    for s in runs:
        assert s["low_min"] + s["mid_min"] + s["high_min"] == pytest.approx(s["duration_min"], abs=1.5)
    assert all(s["session_type"] == "lift" for s in sessions if s["sport"] == "strength")
    superseded = fetch_all(conn, """SELECT 1 FROM analytics.session_features f JOIN core.workout w ON w.id = f.workout_id
                                    WHERE w.superseded_by IS NOT NULL""")
    assert not superseded

    cols = "workout_id, session_type, z1_min, z3_min, low_min, high_min, pace_at_ref_hr_s_km, gap_s_km, decoupling_pct"
    before = fetch_all(conn, f"SELECT {cols} FROM analytics.session_features ORDER BY workout_id")
    build_session_features(conn, seeded["start"])
    after = fetch_all(conn, f"SELECT {cols} FROM analytics.session_features ORDER BY workout_id")
    assert before == after
    paced = [r for r in after if r["pace_at_ref_hr_s_km"] is not None]
    assert len(paced) > len(runs) / 2 and any(r["decoupling_pct"] is not None for r in after)
