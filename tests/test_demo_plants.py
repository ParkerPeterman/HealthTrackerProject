"""Section 13: the demo athlete's newer planted effects are there, and the methods see them."""

from datetime import timedelta

import pytest

pytestmark = pytest.mark.db


def test_schedule_change_is_found_as_a_change_point(seeded, conn):
    from healthtracker.db import fetch_all

    planted = seeded["start"] + timedelta(days=int(90 * 0.72))
    rows = fetch_all(conn, "SELECT * FROM analytics.change_point WHERE metric = 'sleep_midpoint'")
    hit = [r for r in rows if abs((r["date"] - planted).days) <= 5]
    assert hit, rows
    assert 25 < float(hit[0]["shift"]) < 60, "45 minutes later"


def test_runs_get_the_synthetic_home_weather(seeded, conn):
    from healthtracker.db import fetch_one

    r = fetch_one(conn, """SELECT count(*) AS n, count(temp_c) AS with_temp, count(*) FILTER (WHERE weather_source = 'archive')
                                  AS archive FROM analytics.session_features WHERE sport = 'run'""")
    assert r["n"] > 0 and r["with_temp"] == r["n"] and r["archive"] == r["n"]
    m = fetch_one(conn, """SELECT covariates FROM analytics.fitness_model WHERE metric = 'aerobic_pace'
                           ORDER BY computed_on DESC LIMIT 1""")
    assert {"temp", "humid"} <= set(m["covariates"]["used"]), "D3 adjusts for the weather once it's there"


def test_heat_raises_run_hr_at_the_same_pace(seeded, conn):
    """Raw check of the plant: run HR at a given pace rises 0.4 bpm per degree above 12 C. Spring
    warms up while the athlete gets fitter (temperature and date correlate at about 0.8), so the
    date has to be in the model too, the same reason D3 keeps a time trend in its adjustment."""
    import numpy as np

    from healthtracker.db import fetch_all

    rows = fetch_all(conn, """SELECT f.date, f.temp_c, w.avg_hr, w.distance_m / w.duration_s AS speed
                              FROM analytics.session_features f JOIN core.v_workout w ON w.id = f.workout_id
                              WHERE f.sport = 'run' AND f.temp_c IS NOT NULL AND w.avg_hr IS NOT NULL
                              ORDER BY f.date""")
    t = np.array([float(r["temp_c"]) for r in rows])
    hr = np.array([float(r["avg_hr"]) for r in rows])
    v = np.array([float(r["speed"]) for r in rows])
    day = np.array([(r["date"] - rows[0]["date"]).days for r in rows], dtype=float)
    X = np.column_stack([np.ones(len(t)), v, day, np.clip(t - 12, 0, None)])
    beta, *_ = np.linalg.lstsq(X, hr, rcond=None)
    assert 0.15 < beta[3] < 0.8, beta


def test_occasional_groin_tweaks(seeded, conn):
    from healthtracker.db import fetch_one

    n = fetch_one(conn, "SELECT count(*) AS n FROM core.behavior_event WHERE type_code = 'groin_tweak'")["n"]
    assert 1 <= n <= 15
