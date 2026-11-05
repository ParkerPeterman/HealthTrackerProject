"""F5: progression only after a completed, well-recovered week; scheduled deloads; lift steps from RIR."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from healthtracker.planning.progression import (
    judge_last_week,
    phase_week,
    scheduled_deload,
    strength_step,
)
from healthtracker.units import KG_PER_LB

MONDAY = date(2026, 11, 16)
ROWS = [{"phase": "base", "start_date": date(2026, 11, 9), "end_date": date(2026, 12, 20)},
        {"phase": "deload", "start_date": date(2026, 12, 21), "end_date": date(2027, 1, 3)},
        {"phase": "in_season", "start_date": date(2027, 4, 1), "end_date": None}]


def test_phase_week_and_deload_schedule():
    assert phase_week(ROWS, date(2026, 11, 9)) == 1
    assert phase_week(ROWS, date(2026, 11, 30)) == 4
    assert phase_week(ROWS, date(2026, 12, 21)) == 1
    assert phase_week(ROWS, date(2026, 11, 2)) is None
    assert phase_week(ROWS, date(2027, 4, 5)) == 2  # the phase started on a Thursday: that week was week 1
    assert scheduled_deload("base", 4, 4) and scheduled_deload("preseason", 8, 4)
    assert not scheduled_deload("base", 3, 4) and not scheduled_deload("in_season", 4, 4)
    assert scheduled_deload("vo2_build", 3, 3) and not scheduled_deload("base", 4, 0)
    assert not scheduled_deload("base", None, 4)


def graded(**kw):
    base = {"graded_at": datetime(2026, 11, 15, 20), "key_done": 3, "key_planned": 3,
            "response": {"hrv_status": "within", "resting_hr_status": "within", "recovery_flags": 0}}
    return {**base, **kw}


def test_progress_only_after_a_completed_well_recovered_week():
    ok, why = judge_last_week(graded())
    assert ok and "3 of 3" in why
    assert judge_last_week(None)[0] is False
    assert judge_last_week(graded(graded_at=None))[0] is False
    assert judge_last_week(graded(key_planned=0, key_done=0))[0] is False
    ok, why = judge_last_week(graded(key_done=2))
    assert not ok and "2 of 3" in why
    assert judge_last_week(graded(key_done=2), key_pct=60)[0]
    assert not judge_last_week(graded(response={"recovery_flags": 1}))[0]
    assert "HRV" in judge_last_week(graded(response={"hrv_status": "below"}))[1]
    assert "resting HR" in judge_last_week(graded(response={"resting_hr_status": "above"}))[1]
    assert judge_last_week(graded(response=None))[0]  # no response recorded: nothing flagged


@pytest.mark.parametrize("rir,pct,kg", [(3, 5, 105.0), (5, 5, 105.0), (2, 2.5, 102.5), (1, 0, 100.0),
                                        (0, -5, 95.0), (None, 2.5, 102.5)])
def test_strength_steps_follow_rir(rir, pct, kg):
    s = strength_step(100.0, 5, rir, "metric")
    assert s["step_pct"] == pct and s["next_kg"] == kg and s["next_reps"] == 5
    assert s["rir_assumed"] == (rir is None)


def test_strength_rounding_bodyweight_and_deload():
    # 20 kg x 1.025 rounds back to 20 kg: one more rep instead.
    s = strength_step(20.0, 8, 2, "metric")
    assert s["next_kg"] == 20.0 and s["next_reps"] == 9 and "one more rep" in s["why"]
    # Imperial plates: 225 lb at RIR 3 -> 236.25 -> 235 lb.
    s = strength_step(225 * KG_PER_LB, 5, 3, "imperial")
    assert round(s["next_kg"] / KG_PER_LB, 1) == 235.0
    # Light dumbbells step by 1 kg; a cut always goes down at least one step.
    assert strength_step(12.0, 10, 0, "metric")["next_kg"] == 11.0
    assert strength_step(0.0, 8, 3, "metric")["next_reps"] == 9
    assert strength_step(0.0, 8, 0, "metric")["next_reps"] == 7
    d = strength_step(100.0, 5, 3, "metric", deload=True)
    assert d["next_kg"] == 100.0 and d["fewer_sets"] == 1 and "deload" in d["why"]


# ------------------------------------------------------------------ in the planner
@pytest.fixture(scope="module")
def lib(db_url):
    from healthtracker.db import connect, use_database
    from healthtracker.planning.library import exercise_meta, load_library

    use_database(db_url)
    with connect() as c:
        return load_library(c), exercise_meta(c)


def inputs(phase="base", **kw):
    from healthtracker.planning.library import DayState
    from healthtracker.planning.weekly import PlanInputs

    states = {MONDAY + timedelta(days=i): DayState(MONDAY + timedelta(days=i), phase=phase) for i in range(7)}
    roomy = {"run": 900.0, "sprint": 2000.0, "plyo": 500.0, "total": 3000.0}
    return PlanInputs(week_start=MONDAY, phase=phase, states=states, last_week=kw.pop("last", roomy), **kw)


@pytest.mark.db
def test_load_target_holds_without_progress(lib):
    from healthtracker.planning.weekly import build_plan

    library, meta = lib
    up = build_plan(inputs(chronic_week=1000.0, phase_week=2), library, meta)
    held = build_plan(inputs(chronic_week=1000.0, phase_week=2,
                             progress=(False, "2 of 3 key sessions done last week, so the load holds")), library, meta)
    assert up["target_load"] == 1050 and held["target_load"] == 1000
    assert "2 of 3" in held["reason"] and held["progression"]["progress"] is False
    # Recovery flags still take precedence over a completed week.
    flagged = build_plan(inputs(chronic_week=1000.0, caution=["RECOV_ILLNESS_FLAG"]), library, meta)
    assert flagged["target_load"] == 850


@pytest.mark.db
def test_every_fourth_week_is_a_deload(lib):
    from healthtracker.planning.weekly import build_plan, render_plan

    library, meta = lib
    normal = build_plan(inputs(chronic_week=700.0, phase_week=3), library, meta)
    deload = build_plan(inputs(chronic_week=700.0, phase_week=4), library, meta)
    assert not normal["progression"]["deload_week"] and deload["progression"]["deload_week"]
    assert deload["target_load"] == round(700 * 0.65) and "every 4th week" in deload["reason"]
    assert deload["planned"]["total"] <= 0.75 * normal["planned"]["total"]
    assert {s["session_code"] for s in deload["sessions"] if s["key"]} == \
        {s["session_code"] for s in normal["sessions"] if s["key"]}, "the key sessions stay"
    for s in deload["sessions"]:
        lo = library[s["session_code"]]["duration_min_lo"]
        if s["key"]:
            assert s["planned_duration_min"] == lo, s["session_code"]
        else:
            assert s["planned_duration_min"] < lo and "deload week" in s["reason"]["changes"][0], s["session_code"]
    assert 0 < deload["planned"]["sprint_m"] < normal["planned"]["sprint_m"]
    assert 0 < deload["planned"]["plyo_contacts"] < normal["planned"]["plyo_contacts"]
    # Optional sessions go while the week is over its target.
    assert not any(s["optional"] for s in deload["sessions"])
    assert {x["slot"] for x in deload["dropped"]} == {"bonus_run", "bonus_prehab"}
    assert "a deload week" in render_plan(deload)
    # A roomy target doesn't lengthen anything back.
    roomy = build_plan(inputs(chronic_week=3000.0, phase_week=4), library, meta)
    assert [(s["session_code"], s["planned_duration_min"]) for s in roomy["sessions"] if not s["optional"]] == \
        [(s["session_code"], s["planned_duration_min"]) for s in deload["sessions"]]
    # The setting moves it; in-season has none.
    assert build_plan(inputs(chronic_week=700.0, phase_week=3, params={"ANALYSIS_PLAN": {"deload_every": 3}}),
                      library, meta)["progression"]["deload_week"]
    assert not build_plan(inputs("in_season", chronic_week=700.0, phase_week=4), library, meta)["progression"]["deload_week"]


# ------------------------------------------------------------------ the database end
@pytest.mark.db
def test_progression_check_reads_last_weeks_grade(tx):
    from healthtracker.db import insert
    from healthtracker.planning.progression import progression_check

    conn = tx
    week = date(2027, 10, 4)
    assert progression_check(conn, week) == (False, "no plan last week to check, so the load holds")
    insert(conn, "analytics.week_plan", {"week_start": week - timedelta(days=7), "version": 1, "phase": "base",
                                         "inputs": {}, "summary": {}, "plan_hash": "x", "graded_at": datetime(2027, 10, 3),
                                         "key_done": 2, "key_planned": 2,
                                         "response": {"hrv_status": "within", "recovery_flags": 0}})
    ok, why = progression_check(conn, week)
    assert ok and "2 of 2" in why


@pytest.mark.db
def test_vo2_level_holds_in_a_scheduled_deload(tx):
    from healthtracker.db import insert
    from healthtracker.planning.weekly import vo2_level

    conn = tx
    week = date(2027, 9, 13)
    pid = insert(conn, "analytics.week_plan", {"week_start": week - timedelta(days=7), "version": 1, "phase": "base",
                                               "inputs": {}, "summary": {}, "plan_hash": "x"})
    insert(conn, "core.planned_session", {"date": week - timedelta(days=5), "sport": "run", "session_type": "hard",
                                          "session_code": "vo2_4x3", "status": "done", "plan_id": pid,
                                          "origin": "generated"}, returning=None)
    assert vo2_level(conn, week, "base", False)[0] == 3
    assert vo2_level(conn, week, "base", False, deload=True) == (2, "holding the level through the deload")


@pytest.mark.db
def test_strength_hints_from_the_last_session(tx):
    from healthtracker.db import fetch_one, insert
    from healthtracker.planning.progression import strength_hints

    conn = tx
    tz = "America/Chicago"
    day = date(2027, 8, 10)
    ex = fetch_one(conn, "SELECT id FROM core.exercise WHERE name = 'Back squat'")["id"]

    def session(d, sets):
        w = insert(conn, "core.workout", {"source": "manual", "sport": "strength", "duration_s": 3600,
                                          "start_ts": datetime.combine(d, time(18), ZoneInfo(tz))})
        for k, (kg, reps, rir, warm) in enumerate(sets, 1):
            insert(conn, "core.lift_set", {"workout_id": w, "exercise_id": ex, "set_no": k, "reps": reps,
                                           "weight_kg": kg, "rir": rir, "is_warmup": warm}, returning=None)

    session(day - timedelta(days=9), [(90, 5, 0, False)])
    # The last session: a warm-up, then top sets at 110 kg whose hardest had 2 in reserve.
    session(day - timedelta(days=3), [(60, 5, None, True), (110, 5, 3, False), (110, 5, 2, False), (100, 8, 0, False)])
    h = strength_hints(conn, ["Back squat", "Nope"], day, tz, "metric")
    assert set(h) == {"Back squat"}
    b = h["Back squat"]
    assert b["last_kg"] == 110 and b["last_rir"] == 2 and b["next_kg"] == 112.5 and b["last_date"] == day - timedelta(days=3)
    assert strength_hints(conn, ["Back squat"], day, tz, "metric", deload=True)["Back squat"]["fewer_sets"] == 1
    # A day before the last session sees the one before it.
    assert strength_hints(conn, ["Back squat"], day - timedelta(days=4), tz, "metric")["Back squat"]["next_kg"] == 85.0


@pytest.mark.db
def test_library_api_shows_lift_suggestions(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import use_database

    use_database(db_url)
    client = TestClient(app)
    lib = client.get("/api/session-library", params={"day": str(seeded["end"])}).json()
    assert lib["state"]["deload_week"] in (True, False)
    by = {x["code"]: x for x in lib["sessions"]}
    squat = next(e for e in by["strength_lower_heavy"]["exercises"] if e["exercise"] == "Back squat")
    assert squat["suggestion"]["last_kg"] > 0 and squat["suggestion"]["why"]
    assert all("suggestion" not in e for e in by["shoulder_bands"]["exercises"])
