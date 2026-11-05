"""F6: WHO minutes and strength days, sleep regularity, and protein inside the weekly plan."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from healthtracker.planning.health import fixed_minutes, render_health, session_minutes, sleep_note

MONDAY = date(2026, 11, 16)


def entry(**kw):
    base = {"sport": "run", "hard": False, "hard_min": 0, "duration_min_lo": 30, "duration_min_hi": 45,
            "category": "endurance"}
    return {**base, **kw}


def test_minutes_are_counted_like_e4():
    assert session_minutes(entry(hard=True, hard_min=9), 40, []) == (31, 9)
    assert session_minutes(entry(), 30, [entry(duration_min_lo=5, duration_min_hi=8)]) == (35, 0)
    assert session_minutes(entry(sport="speed", hard=True), 30, []) == (0, 0)  # sprints: mostly rest
    assert session_minutes(entry(sport="strength", category="strength"), 45, []) == (0, 0)
    assert fixed_minutes({"session_type": "game", "sport": "ultimate", "planned_duration_min": 90}) == (0, 90)
    assert fixed_minutes({"session_type": "easy", "sport": "run", "planned_duration_min": 40, "planned_rpe": 3}) == (40, 0)
    assert fixed_minutes({"session_type": "hard", "sport": "run", "planned_duration_min": 40, "planned_rpe": 8}) == (0, 40)
    assert fixed_minutes({"session_type": "strength", "sport": "strength", "planned_duration_min": 60}) == (0, 0)


class _Inp:
    def __init__(self, sleep, params=None):
        self.sleep, self.params = sleep, params or {}


def test_sleep_note_only_when_regularity_is_low_or_weekends_drift():
    assert sleep_note(_Inp({})) is None
    assert sleep_note(_Inp({"sri": 82.0, "weekend_gap_min": 20.0}))["note"] is None
    low = sleep_note(_Inp({"sri": 61.0, "weekend_gap_min": 95.0}))
    assert low["low"] and "SRI 61" in low["note"] and "95 min later" in low["note"]
    assert "wake time" in low["note"]
    assert sleep_note(_Inp({"sri": 61.0}, {"ANALYSIS_PLAN": {"sri_low": 60}}))["note"] is None


def test_render_health():
    h = {"main": {"equivalent_min": 140, "strength_days": 2}, "with_optional": {"equivalent_min": 170, "strength_days": 2},
         "who_minutes": 150, "who_strength_days": 2, "notes": ["the optional sessions bring the week to the WHO 150 minutes"],
         "sleep": {"note": "sleep regularity is low (SRI 60 over 28 days). The same wake time every day."},
         "protein": {"g_lo": 150, "g_hi": None, "per_kg": [2.0, None], "basis": "per_kg_bodyweight"}}
    text = "\n".join(render_health(h))
    assert "140 aerobic minutes on the main days (170 with the optional sessions)" in text
    assert "- The optional sessions bring" in text and "Sleep: Sleep regularity is low" in text
    assert "Protein: at least 150 g a day (2 g/kg)." in text
    assert render_health(None) == []


# ------------------------------------------------------------------ in the planner
@pytest.fixture(scope="module")
def lib(db_url):
    from healthtracker.db import connect, use_database
    from healthtracker.planning.library import exercise_meta, load_library

    use_database(db_url)
    with connect() as c:
        return load_library(c), exercise_meta(c)


def inputs(phase="base", last=None, **kw):
    from healthtracker.planning.library import DayState
    from healthtracker.planning.weekly import PlanInputs

    states = {MONDAY + timedelta(days=i): DayState(MONDAY + timedelta(days=i), phase=phase) for i in range(7)}
    roomy = {"run": 900.0, "sprint": 2000.0, "plyo": 500.0, "total": 3000.0}
    return PlanInputs(week_start=MONDAY, phase=phase, states=states, last_week=last or roomy, **kw)


@pytest.mark.db
def test_easy_runs_lengthen_toward_150_minutes(lib):
    from healthtracker.planning.weekly import build_plan, render_plan

    library, meta = lib
    plan = build_plan(inputs(bonus_days=(), chronic_week=500.0), library, meta)
    h = plan["health"]
    assert h["main"]["equivalent_min"] >= 150 and h["main"]["meets_aerobic"]
    assert h["extended"], "an easy run was lengthened"
    grown = [s for s in plan["sessions"] if any("WHO 150" in c for c in s["reason"]["changes"])]
    assert grown and all(library[s["session_code"]]["category"] == "endurance" for s in grown)
    for s in grown:
        assert s["planned_duration_min"] <= library[s["session_code"]]["duration_min_hi"]
    assert h["main"]["strength_days"] >= 2 and h["main"]["meets_strength"]
    assert "Health:" in render_plan(plan)


@pytest.mark.db
def test_no_lengthening_past_the_caps_in_a_deload_or_after_flags(lib):
    from healthtracker.planning.ramp import ramp_cap
    from healthtracker.planning.weekly import build_plan

    library, meta = lib
    tight = {"run": 150.0, "sprint": 200.0, "plyo": 40.0, "total": 900.0}
    plan = build_plan(inputs(bonus_days=(), last=tight), library, meta)
    assert plan["planned"]["run"] <= ramp_cap(150.0, 10, 60) + 1e-6
    if not plan["health"]["main"]["meets_aerobic"]:
        assert any("ramp caps" in n for n in plan["health"]["notes"])
    for phase_week, caution, why in ((4, [], "deload"), (2, ["RECOV_ILLNESS_FLAG"], "recovery flags")):
        p = build_plan(inputs(bonus_days=(), chronic_week=500.0, phase_week=phase_week, caution=caution), library, meta)
        assert p["health"]["extended"] == []
        if not p["health"]["main"]["meets_aerobic"]:
            assert any(why in n and "on purpose" in n for n in p["health"]["notes"])


@pytest.mark.db
def test_transition_week_reports_its_shortfall(lib):
    from healthtracker.planning.weekly import build_plan

    library, meta = lib
    plan = build_plan(inputs("transition", chronic_week=300.0), library, meta)
    h = plan["health"]
    assert h["main"]["equivalent_min"] == 0  # everything in transition is optional
    assert any("strength day" in n for n in h["notes"])


# ------------------------------------------------------------------ the database end
@pytest.mark.db
def test_protein_and_sleep_inputs(tx):
    from healthtracker.db import execute, insert
    from healthtracker.planning.health import gather_protein, gather_sleep

    conn = tx
    day = date(2027, 7, 1)
    execute(conn, "UPDATE core.nutrient_target SET active = false WHERE nutrient = 'protein_g'")
    assert gather_protein(conn, day) is None
    insert(conn, "core.nutrient_target", {"nutrient": "protein_g", "basis": "per_kg_bodyweight", "min_value": 1.6,
                                          "max_value": 2.2, "valid_from": day}, returning=None)
    insert(conn, "core.body_measurement", {"ts": datetime.combine(day - timedelta(days=2), time(7), ZoneInfo("UTC")),
                                           "weight_kg": 80.0}, returning=None)
    p = gather_protein(conn, day)
    assert p == {"basis": "per_kg_bodyweight", "per_kg": [1.6, 2.2], "g_lo": 130, "g_hi": 175}
    execute(conn, "UPDATE analytics.rule SET status = 'active' WHERE rule_id = 'COMP_SLEEP_REGULARITY'")
    assert gather_sleep(conn, {"sri_28d": 66.5, "weekend_gap_min_28d": None}) == {"sri": 66.5, "weekend_gap_min": None}
    execute(conn, "UPDATE analytics.rule SET status = 'shadow' WHERE rule_id = 'COMP_SLEEP_REGULARITY'")
    assert gather_sleep(conn, {"sri_28d": 66.5}) == {}


@pytest.mark.db
def test_written_plan_carries_the_health_block(seeded, tx):
    from healthtracker.db import fetch_one
    from healthtracker.planning.weekly import write_week_plan

    conn = tx
    week = seeded["end"] + timedelta(days=(7 - seeded["end"].weekday()) % 7 or 7) + timedelta(days=21)
    assert write_week_plan(conn, week) > 0
    wp = fetch_one(conn, "SELECT summary FROM analytics.week_plan WHERE week_start = %s AND superseded_at IS NULL", (week,))
    h = wp["summary"]["health"]
    assert h["who_minutes"] == 150 and "equivalent_min" in h["main"]
    rec = fetch_one(conn, "SELECT rendered_text FROM analytics.recommendation WHERE date = %s AND kind = 'weekly'", (week,))
    assert "Health:" in rec["rendered_text"]
