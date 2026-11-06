"""Section 14: when each shadow part is due for a look, against my own season."""

from datetime import date, timedelta

import pytest

from healthtracker.components import SCHEDULE, planned_on

STARTS = {"base": date(2026, 11, 9), "vo2_build": date(2027, 1, 4), "preseason": date(2027, 2, 8),
          "in_season": date(2027, 4, 1)}


def test_planned_dates_follow_the_season():
    base = STARTS["base"]
    assert planned_on("phase", "vo2_build", STARTS, base) == date(2027, 1, 4)
    assert planned_on("base_plus", 22, STARTS, base) == date(2026, 12, 1)  # "about Dec 1" with the default start
    assert planned_on("phase", "base", {}, base) == base
    assert planned_on("phase", "preseason", {}, base) is None and planned_on("evidence", "x", STARTS, base) is None
    moved = {**STARTS, "preseason": date(2027, 2, 22)}  # a later first game moves the pre-season parts with it
    assert planned_on("phase", "preseason", moved, base) == date(2027, 2, 22)


def test_every_scheduled_code_is_a_known_kind():
    assert all(kind in ("phase", "base_plus", "evidence") for kind, _, _ in SCHEDULE.values())
    assert "COMP_READINESS_V2" in SCHEDULE and SCHEDULE["COMP_READINESS_V2"][0] == "evidence"


@pytest.mark.db
def test_schedule_on_the_demo(seeded, tx):
    from healthtracker.components import schedule
    from healthtracker.db import execute

    later = seeded["end"] + timedelta(days=400)
    rows = {r["code"]: r for r in schedule(tx, later)}
    assert {"COMP_HRV_BANDS", "COMP_FORECASTS", "TRAIN_DAILY_ADJUST", "COMP_READINESS_V2"} <= set(rows)
    for r in rows.values():
        assert not (r["due"] and r["status"] != "shadow")       # only shadow parts are ever due
        assert not (r["due"] and not r["gate_met"])               # never before the data gate
    v2 = rows["COMP_READINESS_V2"]
    assert v2["planned_on"] is None and v2["due"] is False      # the demo's v2 hasn't beaten v1
    execute(tx, "UPDATE analytics.readiness_model SET adopted = TRUE")
    assert v2["gate_met"] and {r["code"]: r for r in schedule(tx, later)}["COMP_READINESS_V2"]["due"]
    early = {r["code"]: r for r in schedule(tx, date(2000, 1, 1))}
    assert not any(r["due"] for c, r in early.items() if c != "COMP_READINESS_V2")  # nothing dated is due yet


@pytest.mark.db
def test_rules_api_carries_the_schedule(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import use_database

    use_database(db_url)
    rows = {r["rule_id"]: r for r in TestClient(app).get("/api/rules").json()}
    assert rows["COMP_HRV_BANDS"]["schedule"]["note"].startswith("A2")
    assert rows["COMP_READINESS_V2"]["schedule"]["planned_on"] is None
    assert rows["SLEEP_SHORT"]["schedule"] is None  # ordinary rules aren't scheduled
