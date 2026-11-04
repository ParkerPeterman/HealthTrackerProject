"""A8: performance tests and the sprint/jump goals they feed."""

from datetime import date, timedelta

import pytest

from healthtracker.perftests import TESTS, protocols


def test_best_trial_depends_on_direction():
    assert TESTS["sprint_10m"].best([1.91, 1.88, 1.95]) == 1.88
    assert TESTS["cmj"].best([44.0, 47.5, 46.0]) == 47.5
    keys = [p["key"] for p in protocols()]
    assert keys[-1] == "vo2max_garmin" and "max_hr" in keys


@pytest.mark.db
def test_tests_api_and_goal(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app

    client = TestClient(app)
    day = seeded["end"]
    out = client.get(f"/api/tests?day={day}").json()
    s = out["summary"]
    assert s["max_hr"]["last"]["chest_strap"] is True and s["max_hr"]["last"]["value"] == 194
    assert s["sprint_10m"]["count"] >= 2 and s["sprint_10m"]["best"] <= 1.95
    assert date.fromisoformat(s["sprint_10m"]["due"]) > date.fromisoformat(s["sprint_10m"]["last"]["date"])

    made = client.post("/api/tests", json={"test": "sprint_10m", "date": str(day), "trials": [1.85, 1.79, 1.83],
                                           "method": "partner stopwatch"}).json()
    assert made["value"] == 1.79 and made["unit"] == "s" and made["trials"] == [1.85, 1.79, 1.83]
    assert client.post("/api/tests", json={"test": "sprint_10m", "value": 9.5}).status_code == 422
    assert client.post("/api/tests", json={"test": "max_hr"}).status_code == 422

    goal = client.post(f"/api/goals?day={day}", json={"goal_type": "performance", "description": "Faster 10 m",
                                                        "metric": "sprint_10m_s", "target_value": 1.7,
                                                        "target_date": str(day + timedelta(days=90))}).json()
    assert goal["progress"]["current"] == 1.79 and goal["unit"] == "s"
    assert client.delete(f"/api/goals/{goal['id']}").json()["ok"]
    assert client.delete(f"/api/tests/{made['id']}").json()["ok"]
    assert client.delete(f"/api/tests/{made['id']}").status_code == 404
