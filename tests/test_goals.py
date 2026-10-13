"""Goals: energy estimates, calorie plans, progress, and the goal API on a real database."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.goals.energy import (
    KCAL_PER_KG,
    Expenditure,
    from_data,
    from_formula,
    plan_for,
    projected_date,
    trend_slope_kg_per_week,
)
from healthtracker.goals.metrics import daily_hits
from healthtracker.goals.progress import habit_progress, level_progress, weight_progress
from healthtracker.profile import Profile

D = date(2026, 10, 8)


def history(days: int = 28, intake: float = 2600, kg_per_week: float = -0.3, start_kg: float = 82.0,
            logged_every: int = 1) -> pd.DataFrame:
    """Synthetic daily_features: steady intake, a straight weight trend, every `logged_every`-th day complete."""
    idx = pd.to_datetime([D - timedelta(days=days - i) for i in range(days)])
    trend = start_kg + kg_per_week / 7 * np.arange(days)
    complete = np.arange(days) % logged_every == 0
    return pd.DataFrame({"food_log_complete": complete, "kcal": np.where(complete, intake, np.nan),
                         "weight_kg_smoothed": trend, "weight_kg": trend}, index=idx)


# ------------------------------------------------------------------ energy
def test_expenditure_reads_intake_and_weight_trend():
    exp = from_data(history(intake=2600, kg_per_week=-0.3), D)
    assert exp is not None and exp.source == "data"
    # Losing 0.3 kg/week on 2600 kcal means burning 2600 + 0.3 * 7700 / 7 = 2930 kcal/day.
    assert exp.kcal == pytest.approx(2600 + 0.3 * KCAL_PER_KG / 7, abs=1)
    assert exp.trend_kg_per_week == pytest.approx(-0.3, abs=1e-6)


def test_expenditure_needs_two_weeks_of_complete_logs():
    assert from_data(history(logged_every=3), D) is None  # ~9 complete days of 28
    assert from_data(history(days=10), D) is None


def test_formula_stands_in_without_data():
    p = Profile(sex="male", height_cm=180, birth_date=date(1999, 5, 1))
    exp = from_formula(p, 80, D, weekly_training_min=320)
    # Mifflin-St Jeor: 10*80 + 6.25*180 - 5*27 + 5 = 1795; x1.7 for 5-plus hours a week.
    assert exp.source == "formula" and exp.kcal == pytest.approx(1795 * 1.7)
    assert from_formula(Profile(sex="male"), 80, D, 300) is None  # no height or age


def test_plan_caps_pace_and_floors_calories():
    exp = Expenditure(kcal=2900, source="data")
    plan = plan_for(exp, -0.5, 80, "male")
    assert plan.kcal_mid == 2350 and (plan.kcal_min, plan.kcal_max) == (2250, 2450)
    assert not plan.capped and plan.protein_g_per_kg == 2.0
    fast = plan_for(exp, -1.5, 80, "male")  # 1% of 80 kg = 0.8 kg/week max
    assert fast.capped and fast.rate_kg_per_week == pytest.approx(-0.8)
    small = plan_for(Expenditure(kcal=1700, source="data"), -0.8, 60, "female")
    assert small.floor_applied and small.kcal_mid == 1200
    gain = plan_for(exp, 0.5, 80, "male")  # 0.5% of 80 kg = 0.4 kg/week max
    assert gain.capped and gain.rate_kg_per_week == pytest.approx(0.4) and gain.protein_g_per_kg == 1.6


def test_projection_only_when_heading_the_right_way():
    assert projected_date(D, 80, 78, -0.5) == D + timedelta(days=28)
    assert projected_date(D, 80, 78, 0.2) is None
    assert projected_date(D, 80, 78, -0.001) is None


# ------------------------------------------------------------------ progress
def weight_goal(**kw) -> dict:
    return {"direction": "decrease", "start_value": 82.0, "target_value": 79.0, "rate_per_week": -0.3,
            "start_date": D - timedelta(days=28), "target_date": None, **kw}


def test_weight_goal_on_track_behind_and_achieved():
    f = history(kg_per_week=-0.3)
    on = weight_progress(weight_goal(), f, 80.8, D)  # plan says 82 - 1.2 = 80.8
    assert on["status"] == "on_track" and on["pct"] == pytest.approx(0.4)
    assert on["projected_date"] is not None
    behind = weight_progress(weight_goal(), f, 81.9, D)
    assert behind["status"] == "behind"
    assert weight_progress(weight_goal(), f, 78.9, D)["status"] == "achieved"
    assert weight_progress(weight_goal(direction="maintain", target_value=80.0), f, 80.6, D)["status"] == "on_track"
    assert weight_progress(weight_goal(), f, None, D)["status"] == "no_data"


def test_trend_slope():
    assert trend_slope_kg_per_week(history(kg_per_week=-0.4), D) == pytest.approx(-0.4, abs=1e-6)


def test_level_goal_paces_against_its_date():
    goal = {"metric": "run_5k_min", "start_value": 24.0, "target_value": 21.0, "start_date": D - timedelta(days=30),
            "target_date": D + timedelta(days=30)}
    assert level_progress(goal, 22.5, D)["status"] == "on_track"   # halfway in time and in minutes
    assert level_progress(goal, 23.8, D)["status"] == "behind"
    assert level_progress(goal, 20.9, D)["status"] == "achieved"


def test_habit_counts_days_and_skips_missing_data():
    idx = pd.to_datetime([D - timedelta(days=6 - i) for i in range(7)])
    f = pd.DataFrame({"sleep_total_min": [480, 400, 470, np.nan, 455, 430, 465],
                      "food_log_complete": [True] * 7}, index=idx)
    days = daily_hits(f, "sleep_total_min", 450, D)
    assert [d["hit"] for d in days] == [True, False, True, None, True, False, True]
    goal = {"metric": "sleep_total_min", "target_value": 450, "days_per_week": 4, "start_date": D - timedelta(days=60)}
    assert habit_progress(goal, f, D)["status"] == "on_track"
    assert habit_progress({**goal, "days_per_week": 6}, f, D)["status"] == "behind"
    # Set two days ago: only asked for its share so far (ceil(6 * 3 / 7) = 3), and it has 2.
    assert habit_progress({**goal, "days_per_week": 6, "start_date": D - timedelta(days=2)}, f, D)["status"] == "behind"
    assert habit_progress({**goal, "days_per_week": 3, "start_date": D - timedelta(days=2)}, f, D)["status"] == "on_track"


# ------------------------------------------------------------------ database
@pytest.mark.db
def test_goal_api_sets_targets_and_weekly_check_proposes(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import connect, fetch_all
    from healthtracker.goals.targets import run_goal_check

    client = TestClient(app)
    day = {"day": seeded["end"].isoformat()}
    goals = client.get("/api/goals", params=day).json()
    assert {g["metric"] for g in goals} >= {"weight_kg", "run_5k_min", "sleep_total_min"}
    assert all("progress" in g for g in goals)

    metrics = client.get("/api/goals/metrics", params=day).json()
    assert next(m for m in metrics["metrics"] if m["key"] == "weight_kg")["current"] is not None

    plan = client.get("/api/goals/plan", params={"rate_kg_per_week": -0.4, "target_kg": 77, **day}).json()
    assert plan["expenditure"]["source"] in ("data", "formula") and plan["plan"]["kcal_min"] > 1200

    bad = client.post("/api/goals", json={"goal_type": "body_weight", "description": "x", "direction": "decrease"})
    assert bad.status_code == 422  # no target weight
    wrong_way = client.post("/api/goals", json={"goal_type": "body_weight", "description": "x", "direction": "decrease",
                                                "target_value": 77, "rate_per_week": 0.3})
    assert wrong_way.status_code == 422

    made = client.post("/api/goals", params=day, json={"goal_type": "body_weight", "description": "Cut to 77 kg",
                                           "direction": "decrease", "target_value": 77, "rate_per_week": -0.4,
                                           "apply_targets": True}).json()
    assert made["start_value"] is not None and made["applied_targets"]["plan"]["kcal_min"] > 1200
    active_weight = [g for g in client.get("/api/goals", params=day).json() if g["goal_type"] == "body_weight" and g["active"]]
    assert [g["id"] for g in active_weight] == [made["id"]]  # replaced the seeded weight goal

    assert client.get("/api/proposals").json()["targets"] == []  # setting targets cleared stale proposals
    with connect(db_url) as conn:
        kcal = fetch_all(conn, "SELECT * FROM core.nutrient_target WHERE active AND nutrient = 'kcal'")
        assert len(kcal) == 1 and kcal[0]["source_ref"] == "weight goal"
        # Move the target away from the plan so the weekly check has something to propose.
        conn.execute("UPDATE core.nutrient_target SET min_value = 3400, max_value = 3600 WHERE id = %s", (kcal[0]["id"],))
        run_goal_check(conn, seeded["end"])
    pending = client.get("/api/proposals").json()["targets"]
    kcal_prop = next(p for p in pending if p["nutrient"] == "kcal")
    assert kcal_prop["old_min"] == 3400 and kcal_prop["reason"].startswith("Over the last 28 days")
    assert client.get(f"/api/today?day={seeded['end']}").json()["pending_proposals"] >= 1
    assert client.post(f"/api/proposals/target/{kcal_prop['id']}", json={"approve": True}).json()["ok"]
    with connect(db_url) as conn:
        kcal = fetch_all(conn, "SELECT * FROM core.nutrient_target WHERE active AND nutrient = 'kcal'")
    assert kcal[0]["min_value"] == kcal_prop["new_min"]
    today = client.get(f"/api/today?day={seeded['end']}").json()
    assert today["goals"] and {"status", "pct"} <= set(today["goals"][0])
