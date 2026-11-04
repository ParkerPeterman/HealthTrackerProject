"""F2: the session library, its fit checks and swaps, shoulder/groin/hamstring handling, physio prompt."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from healthtracker.features.sessions import type_from_code
from healthtracker.planning.library import DayState, check, exercises_for
from healthtracker.rules import catalog
from healthtracker.rules.base import RuleContext

D = date(2026, 11, 16)
META = {
    "Back squat": {"pattern": "squat", "primary": {"quads", "glutes"}},
    "Trap-bar deadlift": {"pattern": "hinge", "primary": {"quads", "glutes"}},
    "Romanian deadlift": {"pattern": "hinge", "primary": {"hamstrings"}},
    "Overhead press": {"pattern": "vertical_push", "primary": {"shoulders"}},
    "Landmine press": {"pattern": "other", "primary": {"shoulders"}},
    "Bench press": {"pattern": "horizontal_push", "primary": {"chest"}},
}
VO2 = {"code": "vo2_4x4", "category": "endurance", "rpe": 8, "activities": ["running"], "phases": None,
       "prerequisites": {"hrv_band": "not_below"}, "exercises": []}
SPRINTS = {"code": "maxv_sprints", "category": "speed_jump", "rpe": 7, "activities": ["running", "sprinting"],
           "phases": None, "prerequisites": {"no_tags_days": {"hamstring_tight": 2}}, "exercises": []}
DEPTH = {"code": "depth_jumps", "category": "speed_jump", "rpe": 7, "activities": ["jumping"],
         "phases": ["preseason"], "prerequisites": {}, "exercises": []}
EASY = {"code": "easy_run", "category": "endurance", "rpe": 3, "activities": ["running"], "phases": None,
        "prerequisites": {}, "exercises": []}
LOWER = {"code": "strength_lower_heavy", "category": "strength", "rpe": 7, "activities": ["lifting"], "phases": None,
         "prerequisites": {}, "exercises": [{"exercise": "Back squat", "sets": 4, "reps": "3-5", "alt": "Trap-bar deadlift"},
                                            {"exercise": "Romanian deadlift", "sets": 3, "reps": "6-8"}]}
UPPER = {"code": "strength_upper", "category": "strength", "rpe": 6, "activities": ["lifting"], "phases": None,
         "prerequisites": {}, "exercises": [{"exercise": "Bench press", "sets": 3, "reps": "5-8"},
                                            {"exercise": "Overhead press", "sets": 3, "reps": "6-8",
                                             "swap": {"shoulder": "Landmine press"}}]}


def restriction(**kw) -> dict:
    base = {"target_type": "activity", "activity": None, "muscle_group": None, "movement_pattern": None,
            "exercise_name": None, "max_rpe": 0, "start_date": D - timedelta(days=3), "end_date": None}
    return {**base, **kw}


def test_prerequisites_phases_and_tags():
    assert check(VO2, DayState(D), META) == []
    assert check(VO2, DayState(D, hrv_status="below"), META) == ["7-day HRV is below its normal band"]
    assert check(VO2, DayState(D, hrv_status="within"), META) == []
    assert check(DEPTH, DayState(D, phase="base"), META) == ["only in pre-season"]
    assert check(DEPTH, DayState(D, phase="preseason"), META) == []
    assert check(SPRINTS, DayState(D, tag_days={"hamstring_tight": D - timedelta(days=1)}), META) == \
        ["tight hamstring yesterday"]
    assert check(SPRINTS, DayState(D, tag_days={"hamstring_tight": D - timedelta(days=2)}), META) == []


def test_restrictions_block_hard_sessions_but_not_easy_ones():
    capped_running = DayState(D, restrictions=[restriction(activity="running", max_rpe=4)])
    assert check(EASY, capped_running, META) == []
    assert check(VO2, capped_running, META) == ["injury restriction (running: max RPE 4)"]
    no_sprinting = DayState(D, restrictions=[restriction(activity="sprinting")])
    assert check(SPRINTS, no_sprinting, META) and check(EASY, no_sprinting, META) == []


def test_exercise_swaps():
    sore = DayState(D, shoulder_flagged=True)
    kept, notes = exercises_for(UPPER, sore, META)
    assert [e["exercise"] for e in kept] == ["Bench press", "Landmine press"]
    assert "swap" not in kept[1] and "sore" in notes[0]
    assert [e["exercise"] for e in exercises_for(UPPER, DayState(D), META)[0]] == ["Bench press", "Overhead press"]

    no_squat = DayState(D, restrictions=[restriction(target_type="movement_pattern", movement_pattern="squat")])
    kept, notes = exercises_for(LOWER, no_squat, META)
    assert [e["exercise"] for e in kept] == ["Trap-bar deadlift", "Romanian deadlift"]
    no_quads = DayState(D, restrictions=[restriction(target_type="muscle_group", muscle_group="quads")])
    assert [e["exercise"] for e in exercises_for(LOWER, no_quads, META)[0]] == ["Romanian deadlift"]
    no_legs = DayState(D, restrictions=[restriction(target_type="muscle_group", muscle_group=m)
                                        for m in ("quads", "hamstrings")])
    assert check(LOWER, no_legs, META) == ["every exercise in it is restricted"]


def _ctx(features: dict, body: dict) -> RuleContext:
    return RuleContext(date=D, features=pd.DataFrame.from_dict(features, orient="index"), params={}, body=body)


PHYSIO = {"window_days": 28, "min_events": 2, "min_days_between": 28}


def test_physio_prompt_once_then_only_when_more_frequent():
    two = {D - timedelta(days=20): {"tag_groin": True, "tag_shoulder": False},
           D - timedelta(days=2): {"tag_groin": True, "tag_shoulder": False}}
    r = catalog.inj_physio_prompt(_ctx(two, {"groin": {}}), PHYSIO)
    assert r.fired and r.details["prompted"] == [{"area": "groin", "count": 2}]
    assert "physiotherapist" in r.actions[0].action_text and r.actions[0].metric == "area_seen_professional:groin"
    assert "painkiller" not in r.actions[0].action_text.lower()

    once = {"groin": {"last_prompt": {"date": D - timedelta(days=30), "count": 2}}}
    assert not catalog.inj_physio_prompt(_ctx(two, once), PHYSIO).fired  # same frequency: not again
    three = {**two, D - timedelta(days=1): {"tag_groin": True, "tag_shoulder": False}}
    r = catalog.inj_physio_prompt(_ctx(three, once), PHYSIO)
    assert r.fired and "up from 2" in r.actions[0].action_text
    recent = {"groin": {"last_prompt": {"date": D - timedelta(days=10), "count": 2}}}
    assert not catalog.inj_physio_prompt(_ctx(three, recent), PHYSIO).fired  # too soon after the last one
    assert not catalog.inj_physio_prompt(_ctx(two, {"groin": {"seen_professional": True}}), PHYSIO).fired
    one = {D - timedelta(days=2): {"tag_groin": True, "tag_shoulder": False}}
    assert not catalog.inj_physio_prompt(_ctx(one, {}), PHYSIO).fired


def test_shoulder_swap_only_on_pressing_days():
    lift = {"date": D, "session_type": "strength", "sport": "strength", "status": "planned",
            "focus_muscles": ["chest", "shoulders"], "session_code": None}
    legs = {**lift, "focus_muscles": ["quads", "hamstrings"]}
    sore = {"shoulder": {"flagged": True, "source": "tag", "overhead_codes": ["strength_upper"]}}

    def run(planned, body):
        return catalog.inj_shoulder_swap(RuleContext(date=D, features=pd.DataFrame(), params={}, planned=planned,
                                                     body=body), {})

    r = run([lift], sore)
    assert r.fired and r.actions[0].metric == "pattern_sets_day:vertical_push" and r.actions[0].target_value == 0
    assert not run([legs], sore).fired
    assert run([{**legs, "session_code": "strength_upper"}], sore).fired
    assert not run([lift], {"shoulder": {"flagged": False}}).fired


# ------------------------------------------------------------------ on the database


@pytest.mark.db
def test_library_rows_are_consistent(conn, db_url):
    from healthtracker.planning.library import exercise_meta, load_library

    lib, meta = load_library(conn), exercise_meta(conn)
    assert len(lib) >= 25
    for code, e in lib.items():
        for ex in e["exercises"]:
            for name in (ex["exercise"], ex.get("alt"), (ex.get("swap") or {}).get("shoulder")):
                assert name is None or name in meta, (code, name)
        if e["category"] in ("endurance", "speed_jump", "ultimate") and e["sport"] != "throwing":
            assert type_from_code(code) is not None, code  # session features know the type
        if e["travel_ok"]:
            assert set(e["equipment"]) <= {"band"} and not e["hard"], code
        assert e["duration_min_lo"] <= e["duration_min_hi"]
    vo2 = sorted((e["level"], c) for c, e in lib.items() if e["progression_group"] == "vo2")
    assert [c for _, c in vo2] == ["vo2_3x3", "vo2_4x3", "vo2_3x4", "vo2_4x4"]
    assert [lib[f"copenhagen_l{k}"]["level"] for k in (1, 2, 3)] == [1, 2, 3]
    # Sprints and jumps come before lifting on the same day.
    assert max(e["day_order"] for e in lib.values() if e["category"] == "speed_jump") < \
        min(e["day_order"] for e in lib.values() if e["category"] == "strength")


@pytest.mark.db
def test_copenhagen_level_steps_up_and_down(conn, db_url):
    from healthtracker.db import fetch_one, insert
    from healthtracker.planning.library import copenhagen_level

    tz = "America/Chicago"
    start = date(2027, 6, 1)
    params = {"copenhagen_sessions_per_level": 3, "copenhagen_tweak_days": 14}
    assert copenhagen_level(conn, start, tz, params)[0] == 1
    ex = fetch_one(conn, "SELECT id FROM core.exercise WHERE name = 'Copenhagen adduction L1'")["id"]
    for k in range(3):
        w = insert(conn, "core.workout", {"source": "manual", "sport": "strength",
                                          "start_ts": datetime.combine(start + timedelta(days=2 * k), time(18), ZoneInfo(tz)),
                                          "duration_s": 600})
        insert(conn, "core.lift_set", {"workout_id": w, "exercise_id": ex, "set_no": 1, "reps": 8, "weight_kg": 0},
               returning=None)
    day = start + timedelta(days=7)
    level, why = copenhagen_level(conn, day, tz, params)
    assert level == 2 and "3 sessions at level 1" in why
    insert(conn, "core.behavior_event", {"ts": datetime.combine(day - timedelta(days=1), time(12), ZoneInfo(tz)),
                                         "type_code": "groin_tweak"}, returning=None)
    assert copenhagen_level(conn, day, tz, params)[0] == 1  # a tweak after the last session: back to level 1
    conn.rollback()


@pytest.mark.db
def test_shoulder_flag_checkin_and_api(seeded, conn, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import execute

    client = TestClient(app)
    day = seeded["end"]

    def tag(d):  # add the sore-shoulder tag, keeping the day's other tags
        have = client.get(f"/api/tags/{d}").json()["tags"]
        client.put(f"/api/tags/{d}", json={"tags": [*have, "shoulder_sore"]})

    assert client.get(f"/api/shoulder/{day}").json()["flagged"] is False
    tag(day - timedelta(days=2))
    s = client.get(f"/api/shoulder/{day}").json()
    assert s["flagged"] and s["checkin_needed"] and s["source"] == "tag"
    s = client.put(f"/api/shoulder/{day - timedelta(days=1)}", json={"pain": 1}).json()
    s = client.put(f"/api/shoulder/{day}", json={"pain": 2}).json()
    assert not s["flagged"] and s["pain_today"] == 2  # two low check-ins after the sore day clear it
    assert client.put(f"/api/shoulder/{day}", json={"pain": 11}).status_code == 422

    tag(day)
    lib = client.get("/api/session-library", params={"day": str(day)}).json()
    by = {x["code"]: x for x in lib["sessions"]}
    assert lib["state"]["shoulder_flagged"]
    assert "Landmine press" in [e["exercise"] for e in by["strength_upper"]["exercises"]]
    assert by["vo2_4x4"]["hr_target"]["bpm"][0] < by["vo2_4x4"]["hr_target"]["bpm"][1]
    assert by["vo2_4x4"]["expected_load"]["internal"] > 0 and by["repeated_sprints"]["expected_load"]["sprint_m"] == 360
    assert not by["depth_jumps"]["fits"]  # the demo season puts this day outside pre-season
    assert client.get("/api/session-library/nope").status_code == 404
    bad = client.post("/api/planned", json={"date": str(day), "sport": "run", "session_type": "hard",
                                            "session_code": "nope"})
    assert bad.status_code == 400
    execute(conn, """DELETE FROM core.behavior_event WHERE type_code IN ('shoulder_sore', 'shoulder_pain')""")
