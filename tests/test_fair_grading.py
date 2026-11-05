"""G5: followed vs not-followed advice compared only within the same decision-time state."""

import numpy as np
import pytest

from healthtracker.recommend.fair import decision_state, fair_compare, stratum


def test_decision_state_is_coarse_and_tolerates_missing_values():
    assert decision_state({"hrv_z_30d": -1.0, "sleep_total_min": 380, "is_travel": True}) == \
        {"hrv_low": True, "short_sleep": True, "busy": True}
    assert decision_state({"hrv_z_30d": 0.2, "sleep_total_min": 470, "had_game": False}) == \
        {"hrv_low": False, "short_sleep": False, "busy": False}
    assert decision_state(None) == {"hrv_low": None, "short_sleep": None, "busy": False}
    assert decision_state({"hrv_z_30d": float("nan")})["hrv_low"] is None
    assert stratum({"hrv_low": True, "short_sleep": False, "busy": True}) == (True, False, True)


def simulate(n=600, effect=1.0, seed=0):
    """Advice is skipped mostly on bad days, and bad days have worse outcomes on their own."""
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        st = {"hrv_low": bool(rng.random() < 0.3), "short_sleep": bool(rng.random() < 0.3),
              "busy": bool(rng.random() < 0.2)}
        bad = st["hrv_low"] + st["short_sleep"] + st["busy"]
        followed = bool(rng.random() < (0.85 if bad == 0 else 0.25))
        y = -2.0 * bad + (effect if followed else 0.0) + rng.normal(0, 1.0)
        rows.append({"followed": followed, "outcome_delta": y, "state": st})
    return rows


def test_matching_removes_the_bad_day_bias():
    g = fair_compare(simulate(effect=1.0))
    assert g["raw_diff"] > 2.0, "skipping on bad days inflates the naive comparison"
    assert abs(g["matched_diff"] - 1.0) < 0.3
    assert g["lo95"] < 1.0 < g["hi95"] and g["p_benefit"] > 0.99
    assert g["strata_used"] <= g["strata"] <= 8


def test_no_effect_looks_like_no_effect_once_matched():
    hits = 0
    for seed in range(30):
        g = fair_compare(simulate(n=300, effect=0.0, seed=seed))
        hits += g["lo80"] <= 0.0 <= g["hi80"]
    assert 18 <= hits <= 30, hits  # the 80% interval covers the truth about 80% of the time


def test_too_few_matched_days_is_too_early():
    rows = [{"followed": True, "outcome_delta": 1.0, "state": {"busy": False}} for _ in range(10)]
    rows += [{"followed": False, "outcome_delta": 0.0, "state": {"busy": True}} for _ in range(10)]
    g = fair_compare(rows)
    assert g["raw_diff"] == 1.0 and g["matched_diff"] is None and g["strata_used"] == 0


# ------------------------------------------------------------------ the database end
@pytest.mark.db
def test_grades_on_the_demo_and_state_logged_going_forward(seeded, tx):
    from healthtracker.db import fetch_all
    from healthtracker.recommend.fair import follow_through_candidates, grade_advice, graded_rows

    conn = tx
    end = seeded["end"]
    graded = graded_rows(conn)
    assert graded, "the demo has graded actions"
    assert all(set(r["state"]) == {"hrv_low", "short_sleep", "busy"} for rows in graded.values() for r in rows)
    with_state = fetch_all(conn, "SELECT count(*) AS n FROM analytics.recommendation_action WHERE state IS NOT NULL")
    assert with_state[0]["n"] > 0, "the builder logs the state at decision time"
    assert grade_advice(conn, end) == len(graded)
    grades = fetch_all(conn, "SELECT * FROM analytics.advice_grade WHERE computed_on = %s", (end,))
    for g in grades:
        assert g["n_matched_followed"] <= g["n_followed"] and g["n_matched_not"] <= g["n_not"]
        if g["matched_diff"] is not None and g["se"] is not None:
            assert g["lo95"] <= g["lo80"] <= g["matched_diff"] <= g["hi80"] <= g["hi95"]
    for c in follow_through_candidates(conn, end):
        assert c["kind"] == "follow_through" and c["text"].startswith("On comparable days")


@pytest.mark.db
def test_advice_grades_api(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import use_database

    use_database(db_url)
    data = TestClient(app).get("/api/advice-grades").json()
    assert isinstance(data, list)
    assert all({"raw_diff", "matched_diff", "n_matched_followed"} <= set(r) for r in data)
