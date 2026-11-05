"""E4: long-term trends and weekly activity against the WHO guidelines."""

import pytest


@pytest.mark.db
def test_long_trends_and_who_weeks(seeded, conn):
    from healthtracker.db import fetch_all

    trends = fetch_all(conn, "SELECT * FROM analytics.long_trend ORDER BY date")
    metrics = {r["metric"] for r in trends}
    assert {"resting_hr", "hrv", "weight_kg"} <= metrics
    rhr = [r for r in trends if r["metric"] == "resting_hr"]
    assert rhr[-1]["level"] < rhr[0]["level"]  # planted: resting HR drifts down about 2 bpm over the demo
    assert all(r["lo80"] <= r["level"] <= r["hi80"] for r in trends)
    weeks = fetch_all(conn, "SELECT * FROM analytics.health_weekly ORDER BY week_start")
    assert len(weeks) >= 10
    for w in weeks:
        assert w["equivalent_min"] == w["moderate_min"] + 2 * w["vigorous_min"]
        assert w["meets_aerobic"] == (w["equivalent_min"] >= 150) and w["meets_strength"] == (w["strength_days"] >= 2)
    assert any(w["strength_days"] >= 2 for w in weeks)
