"""E2 / N5: quality tiers. Poor-tier measures can't drive an active rule or an insight alone."""

import pytest

from healthtracker.quality import insight_problem, poor_only

TIER = {"body_battery_low": "poor", "deep_min": "poor", "hrv": "good", "sleep_total_min": "good",
        "sleep_quality": "fair"}


def test_poor_only():
    assert poor_only(["body_battery_low", "deep_min"], TIER)
    assert not poor_only(["body_battery_low", "hrv"], TIER)
    assert not poor_only(["something_new"], TIER) and not poor_only([], TIER)


def test_no_insight_rests_on_stages_alone():
    assert "deep_min" in insight_problem("alcohol_drinks >= 1", "deep_min", TIER)
    assert "body_battery_low" in insight_problem("body_battery_low < 30", "hrv", TIER)
    assert insight_problem("sleep_total_min", "sleep_quality", TIER) is None


@pytest.mark.db
def test_tiers_seeded_and_activation_guard(tx):
    from healthtracker.db import fetch_one, insert
    from healthtracker.quality import tiers
    from healthtracker.stats.promotion import set_status

    conn = tx
    t = tiers(conn)
    assert t["deep_min"] == "poor" and t["avg_stress"] == "poor" and t["sleep_total_min"] == "good"
    assert t["sleep_midpoint_min"] == "fair" and t["sleep_quality"] == "fair"
    assert fetch_one(conn, "SELECT inputs FROM analytics.rule WHERE rule_id = 'SLEEP_SHORT'")["inputs"] == ["sleep_total_min"]
    insert(conn, "analytics.rule", {"rule_id": "TEST_BB_ONLY", "domain": "recovery", "description": "test",
                                    "evidence_level": "heuristic", "inputs": ["body_battery_low"]}, returning=None)
    with pytest.raises(ValueError, match="poor-quality"):
        set_status(conn, "TEST_BB_ONLY", "active")
    set_status(conn, "TEST_BB_ONLY", "retired")  # anything but active is fine


@pytest.mark.db
def test_journal_sleep_quality_keeps_motivation_history(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import connect, execute

    client = TestClient(app)
    day = "2026-12-01"
    try:
        client.put(f"/api/journal/{day}", json={"motivation": 4, "mood": 3})
        out = client.put(f"/api/journal/{day}", json={"sleep_quality": 2, "energy": 3}).json()
        assert out["motivation"] == 4 and out["sleep_quality"] == 2 and out["mood"] == 3
        assert client.put(f"/api/journal/{day}", json={"sleep_quality": 6}).status_code == 422
    finally:
        with connect(db_url) as c:
            execute(c, "DELETE FROM core.journal WHERE date = %s", (day,))
