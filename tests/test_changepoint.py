"""G3: Bayesian online change points, candidate causes, and the sensitive medication link."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.stats.changepoint import causes, change_points, describe, run_length_posterior

IDX = pd.date_range("2026-01-01", periods=200, freq="D")


def ar1(n, phi, sd, seed):
    rng = np.random.default_rng(seed)
    e = rng.normal(0, sd * np.sqrt(1 - phi ** 2), n)
    x = np.zeros(n)
    x[0] = rng.normal(0, sd)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


def test_run_length_posterior_is_a_distribution_and_resets_at_a_jump():
    x = np.r_[np.zeros(30), np.full(30, 4.0)] + np.random.default_rng(0).normal(0, 0.3, 60)
    R = run_length_posterior(x, 1 / 100)
    assert np.allclose(R[1:].sum(axis=1), 1.0)
    assert R[60].argmax() in range(28, 33), "the current segment began at the jump"


def test_a_clear_shift_is_found_near_its_day():
    x = 60 + ar1(200, 0.3, 2.0, 7)
    x[120:] += 4.0
    cps = change_points(pd.Series(x, index=IDX))
    assert len(cps) == 1
    cp = cps[0]
    assert abs((cp["date"] - IDX[120].date()).days) <= 5
    assert 3.0 < cp["shift"] < 5.0 and cp["shift_sd"] > 1 and cp["n_before"] >= 100
    assert "went up by about" in describe("resting_hr", cp)


def test_autocorrelated_noise_without_a_shift_stays_quiet():
    found = sum(len(change_points(pd.Series(60 + ar1(200, phi, 2.0, seed), index=IDX)))
                for phi in (0.0, 0.3, 0.5) for seed in range(10))
    assert found <= 2, found


def test_small_shifts_and_short_series_are_not_reported():
    x = 60 + ar1(200, 0.0, 2.0, 3)
    x[120:] += 0.6  # 0.3 SD: below the minimum shift
    assert change_points(pd.Series(x, index=IDX)) == []
    assert change_points(pd.Series(x[:30], index=IDX[:30])) == []


def test_candidate_causes():
    d = date(2026, 3, 1)
    idx = pd.date_range(d - timedelta(days=30), d + timedelta(days=30), freq="D")
    f = pd.DataFrame(index=idx, data={"is_travel": False, "is_sick": False, "load_week": 400.0, "alcohol_drinks": 0.5})
    f.loc[pd.Timestamp(d - timedelta(days=2)), "is_sick"] = True
    f.loc[f.index >= pd.Timestamp(d), "load_week"] = 650.0
    f.loc[pd.Timestamp(d - timedelta(days=20)), "is_travel"] = True  # too early to count
    phases = [{"phase": "vo2_build", "start_date": d - timedelta(days=1)},
              {"phase": "base", "start_date": d - timedelta(days=60)}]
    out = causes(f, phases, d)
    kinds = {(c["kind"], c["what"]) for c in out}
    assert ("flag", "sick") in kinds and ("flag", "travel") not in kinds
    assert ("log", "weekly training load") in kinds and ("log", "alcohol") not in kinds
    assert ("phase", "vo2 build phase started") in kinds and len([k for k in kinds if k[0] == "phase"]) == 1
    load = next(c for c in out if c["what"] == "weekly training load")
    assert load["direction"] == "up" and load["before"] == 400.0 and load["after"] == 650.0


# ------------------------------------------------------------------ the database end
@pytest.mark.db
def test_change_points_on_a_planted_shift_with_the_medication_link_kept_apart(seeded, tx):
    from healthtracker.db import execute, fetch_all, insert
    from healthtracker.stats.changepoint import build_change_points

    conn = tx
    end = seeded["end"]
    # Plant a 6 bpm resting-HR rise 60 days before the end (clear of the demo's illness week), and a
    # dose change four days before it. The demo itself has no shift.
    assert not [r for r in fetch_all(conn, "SELECT * FROM analytics.change_point") if r["metric"] == "resting_hr"]
    shift_day = end - timedelta(days=60)
    execute(conn, "UPDATE analytics.daily_features SET resting_hr = resting_hr + 6 WHERE date >= %s", (shift_day,))
    med = insert(conn, "core.medication", {"name": "Testamine", "dose_mg": 10, "start_date": end - timedelta(days=200)})
    insert(conn, "core.medication_change", {"medication_id": med, "date": shift_day - timedelta(days=4),
                                            "old_dose_mg": 10, "new_dose_mg": 15}, returning=None)
    assert build_change_points(conn, end) >= 1
    rows = fetch_all(conn, "SELECT * FROM analytics.change_point WHERE metric = 'resting_hr'")
    hit = [r for r in rows if abs((r["date"] - shift_day).days) <= 5]
    assert hit and float(hit[0]["shift"]) > 3 and hit[0]["summary"].startswith("Something shifted around")
    # The general record never mentions medication; the link lives only in the sensitive table.
    for r in fetch_all(conn, "SELECT summary, causes::text AS c FROM analytics.change_point"):
        assert "medic" not in (r["summary"] + r["c"]).lower() and "dose" not in (r["summary"] + r["c"]).lower()
    links = fetch_all(conn, "SELECT * FROM analytics.change_point_medication WHERE change_point_id = %s", (hit[0]["id"],))
    assert len(links) == 1 and links[0]["days_from_change"] == (hit[0]["date"] - (shift_day - timedelta(days=4))).days


@pytest.mark.db
def test_change_points_api_and_sensitive_tables(seeded, conn):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import fetch_all
    from healthtracker.medication import SENSITIVE_TABLES

    data = TestClient(app).get("/api/change-points").json()
    assert data["live"] is False and isinstance(data["change_points"], list)
    marked = {f"{r['s']}.{r['t']}" for r in fetch_all(conn, """
        SELECT n.nspname AS s, c.relname AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE obj_description(c.oid, 'pg_class') = 'sensitive'""")}
    assert marked == set(SENSITIVE_TABLES)
