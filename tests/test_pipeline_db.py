"""End-to-end on a real Postgres: synthetic data through every layer, plus the API."""

from datetime import date, timedelta

import pytest

from healthtracker.db import fetch_all, fetch_one

pytestmark = pytest.mark.db


def counts(conn) -> dict:
    tables = ["core.sleep", "core.daily_wellness", "core.workout", "core.run_split", "core.hr_sample",
              "analytics.daily_features", "analytics.daily_flag", "analytics.recommendation",
              "analytics.recommendation_action", "analytics.weekly_muscle_volume", "analytics.lift_progress"]
    return {t: fetch_one(conn, f"SELECT count(*) AS n FROM {t}")["n"] for t in tables}


def test_every_layer_is_populated(seeded, conn):
    c = counts(conn)
    assert c["core.sleep"] == 90 and c["analytics.daily_features"] >= 90
    assert all(v > 0 for v in c.values()), c
    errors = fetch_all(conn, "SELECT * FROM analytics.daily_flag WHERE details->>'eval_status' = 'error'")
    assert errors == []


def test_overlapping_workouts_merge_into_garmin_row(seeded, conn):
    merged = fetch_all(conn, """SELECT m.id, g.source, g.rpe, g.rpe_source,
                                       (SELECT count(*) FROM core.lift_set s WHERE s.workout_id = m.id) AS left_behind,
                                       (SELECT count(*) FROM core.lift_set s WHERE s.workout_id = g.id) AS moved
                                FROM core.workout m JOIN core.workout g ON g.id = m.superseded_by""")
    assert merged, "the generator records some strength sessions on the watch too"
    for r in merged:
        assert r["source"] == "garmin" and r["left_behind"] == 0 and r["moved"] > 0
        assert r["rpe"] is not None and r["rpe_source"] == "manual"


def test_features_are_sane(seeded, conn):
    f = fetch_all(conn, "SELECT * FROM analytics.daily_features ORDER BY date")
    games = [r for r in f if r["had_game"]]
    assert games and all(r["is_partial_day"] for r in games)  # watch is off during games
    assert all(r["kcal"] is None for r in f if not r["food_log_complete"])  # missing != zero
    late = f[-1]
    assert late["acwr"] is not None and late["hrv_z_30d"] is not None and late["pace_at_ref_hr_s_km"] is not None
    sick = [r for r in f if r["exclude_from_baseline"]]
    assert sick, "illness days are flagged"


def test_actions_are_graded(seeded, conn):
    graded = fetch_one(conn, """SELECT count(*) FILTER (WHERE followed IS NOT NULL) AS graded,
                                       count(*) FILTER (WHERE outcome_delta IS NOT NULL) AS with_outcome,
                                       count(*) FILTER (WHERE NOT visible) AS hidden
                                FROM analytics.recommendation_action""")
    assert graded["graded"] > 20 and graded["with_outcome"] > 10 and graded["hidden"] > 0


def test_insights_find_planted_effects(seeded, conn):
    rows = fetch_all(conn, """SELECT DISTINCT ON (h.id) h.exposure, h.outcome, h.lag_days, i.status, i.effect_size
                              FROM analytics.insight i JOIN analytics.hypothesis h ON h.id = i.hypothesis_id
                              ORDER BY h.id, i.computed_at DESC""")
    by = {(r["exposure"], r["outcome"], r["lag_days"]): r for r in rows}
    caffeine = by[("last_caffeine_hour >= 14", "sleep_score", 1)]
    assert caffeine["effect_size"] < -3
    alcohol = by[("alcohol_drinks >= 1", "hrv", 1)]  # ~1-2 drinking days a week: 90 days may be too few
    assert alcohol["status"] == "insufficient_data" or alcohol["effect_size"] < 0


def test_rerunning_a_day_is_idempotent(seeded, conn):
    from healthtracker.db import connect
    from healthtracker.pipeline.jobs import nightly

    d = seeded["end"]
    with connect() as c:
        before = counts(c)
    report = nightly(d, skip_ingest=True, use_llm=False)
    assert report.ok, report.summary()
    with connect() as c:
        after = counts(c)
    assert before == after


def test_weekly_plan_respects_games(seeded, conn):
    from healthtracker.planning.weekly import plan_week

    monday = seeded["end"] + timedelta(days=(7 - seeded["end"].weekday()) % 7 or 7)
    plan = plan_week(conn, monday)
    games = {date.fromisoformat(g) for g in plan["games"]}
    assert games, "the generator schedules future Tuesday games"
    for s in plan["sessions"]:
        if s["date"] + timedelta(days=1) in games:
            assert s["session_type"] != "strength" or not set(s["focus_muscles"] or []) & {"quads", "hamstrings"}
            assert s["session_type"] not in ("hard",)
    assert plan["planned_load"] > 0


def test_api_smoke(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app

    client = TestClient(app)
    today = client.get(f"/api/today?day={seeded['end']}").json()
    assert today["recommendation"]["rendered_text"]
    w = client.post("/api/workouts", json={"sport": "ultimate", "is_game": True, "duration_min": 80, "rpe": 8}).json()
    ex = client.get("/api/exercises").json()[0]
    s = client.post(f"/api/workouts/{w['id']}/sets", json={"exercise_id": ex["id"], "reps": 5, "weight_kg": 60}).json()
    assert s["set_no"] == 1
    assert client.delete(f"/api/workouts/{w['id']}").json()["ok"]
    bad = client.post("/api/injuries/1/restrictions", json={"target_type": "muscle_group"})
    assert bad.status_code == 422
    assert client.get("/api/hypotheses").status_code == 200
