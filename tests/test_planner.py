"""F3: the weekly plan generator. Templates, spacing, games, restrictions, ramp caps, versions, grading."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from healthtracker.planning.library import DayState
from healthtracker.planning.weekly import PlanInputs, build_plan, kind, spacing_ok

pytestmark = pytest.mark.db

MONDAY = date(2026, 11, 16)
ROOMY = {"run": 900.0, "sprint": 2000.0, "plyo": 500.0, "total": 3000.0}


@pytest.fixture(scope="module")
def lib(db_url):
    from healthtracker.db import connect, use_database
    from healthtracker.planning.library import exercise_meta, load_library

    use_database(db_url)
    with connect() as c:
        return load_library(c), exercise_meta(c)


def inputs(phase="base", week_start=MONDAY, last=None, restrictions=(), **kw) -> PlanInputs:
    states = {week_start + timedelta(days=i): DayState(week_start + timedelta(days=i), phase=phase,
                                                       restrictions=list(restrictions)) for i in range(7)}
    return PlanInputs(week_start=week_start, phase=phase, states=states, last_week=last or ROOMY,
                      chronic_week=kw.pop("chronic_week", None), **kw)


def by_day(plan) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for s in plan["sessions"]:
        out.setdefault(s["date"].weekday(), []).append(s["session_code"])
    return out


def hard_days(plan, lib) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for s in plan["sessions"]:
        k = kind(lib[s["session_code"]])
        if k:
            out.setdefault(s["date"].weekday(), set()).add(k)
    return out


def test_base_week_is_the_brief_template(lib):
    library, meta = lib
    plan = build_plan(inputs(), library, meta)
    days = by_day(plan)
    assert days[0][0] in ("accel_sprints", "maxv_sprints") and days[0][1:] == ["jump_cmj_broad", "strength_lower_heavy"]
    assert days[1] == ["easy_run", "strength_upper"]
    assert days[2] == ["vo2_3x3"]
    assert days[3] == ["long_run", "strength_single_leg"]
    assert days[5] == ["easy_run"] and days[6] == ["mobility"]
    s = {(x["date"].weekday(), x["session_code"]): x for x in plan["sessions"]}
    assert s[(0, "strength_lower_heavy")]["addons"] == ["copenhagen_l1", "nordics"]
    assert s[(1, "strength_upper")]["addons"] == ["shoulder_bands"] and s[(3, "long_run")]["addons"] == ["strides"]
    assert all(x["optional"] == (x["date"].weekday() >= 4) for x in plan["sessions"])
    assert all(x["reason"]["why"] and x["reason"]["day"] == "as planned" for x in plan["sessions"])
    assert "VO2 level 1 of 4" in s[(2, "vo2_3x3")]["reason"]["progression"]
    assert set(s[(0, "strength_lower_heavy")]["focus_muscles"]) >= {"quads", "hamstrings"}
    assert plan["dropped"] == [] and plan["intensity"]["easy_pct"] >= 75


def test_main_days_only(lib):
    library, meta = lib
    plan = build_plan(inputs(bonus_days=()), library, meta)
    assert {s["date"].weekday() for s in plan["sessions"]} == {0, 1, 2, 3}
    assert {"vo2_3x3", "long_run", "strength_lower_heavy"} <= {s["session_code"] for s in plan["sessions"]}


def test_vo2_build_keeps_the_two_vo2_sessions_apart(lib):
    library, meta = lib
    plan = build_plan(inputs("vo2_build", vo2_level=(2, "test")), library, meta)
    vo2 = [s["date"].weekday() for s in plan["sessions"] if s["session_code"].startswith("vo2_")]
    assert vo2 == [1, 3]
    long = [s for s in plan["sessions"] if s["session_code"] == "long_run"]
    assert long and long[0]["optional"] and long[0]["date"].weekday() >= 4
    assert "strength_power" in by_day(plan)[0]


@pytest.mark.parametrize("phase", ["base", "deload", "vo2_build", "preseason", "in_season", "transition"])
@pytest.mark.parametrize("strict", [0, 1])
def test_hard_days_are_spaced(lib, phase, strict):
    library, meta = lib
    plan = build_plan(inputs(phase, params={"ANALYSIS_PLAN": {"strict_spacing": strict}}), library, meta)
    hard = hard_days(plan, lib[0])
    for d, kinds in hard.items():
        assert len(kinds) == 1, (phase, d, kinds)
        nxt = hard.get(d + 1, set())
        if strict:
            assert not nxt
        else:  # only a sprint/strength day followed by a hard endurance day
            assert all(k == "N" and n == "E" for k in kinds for n in nxt), (phase, hard)


def test_spacing_rule():
    assert spacing_ok("E", {"N"}, set(), set(), strict=False)
    assert not spacing_ok("N", {"E"}, set(), set(), strict=False)
    assert not spacing_ok("E", set(), set(), {"E"}, strict=False)
    assert not spacing_ok("E", {"N"}, set(), set(), strict=True)
    assert not spacing_ok("E", set(), {"N"}, set(), strict=False)
    assert spacing_ok(None, {"E", "N"}, {"E"}, {"E"}, strict=True)


def test_games_shape_the_week(lib):
    library, meta = lib
    thursday = MONDAY + timedelta(days=3)
    game = {"date": thursday, "sport": "ultimate", "session_type": "game", "planned_duration_min": 90, "planned_rpe": 8}
    plan = build_plan(inputs(fixed=[game]), library, meta)
    for s in plan["sessions"]:
        e = library[s["session_code"]]
        assert s["date"] != thursday
        if s["date"] in (thursday - timedelta(days=1), thursday + timedelta(days=1)):
            assert not e["hard"] and e["plan_type"] != "moderate"
            assert not set(s["focus_muscles"] or []) & {"quads", "hamstrings", "glutes"}
            assert not {"copenhagen_l1", "nordics"} & set(s["addons"] or [])
    assert plan["games"] == [str(thursday)]


def test_weekend_game_turns_monday_sprints_into_technique(lib):
    library, meta = lib
    plan = build_plan(inputs(prev_game=True), library, meta)
    monday = by_day(plan)[0]
    assert monday[0] == "speed_technique" and "jump_cmj_broad" not in monday
    first = [s for s in plan["sessions"] if s["session_code"] == "speed_technique"][0]
    assert "the day after a game" in first["reason"]["changes"][0]


def restriction(**kw):
    base = {"target_type": "activity", "activity": None, "muscle_group": None, "movement_pattern": None,
            "exercise_name": None, "max_rpe": 0, "start_date": MONDAY - timedelta(days=10), "end_date": None}
    return {**base, **kw}


@pytest.mark.parametrize("phase", ["base", "vo2_build", "preseason", "in_season"])
def test_no_hard_session_under_a_restriction(lib, phase):
    library, meta = lib
    rs = [restriction(activity="running", max_rpe=3), restriction(activity="sprinting"),
          restriction(target_type="muscle_group", muscle_group="hamstrings")]
    plan = build_plan(inputs(phase, restrictions=rs, vo2_level=(3, "test")), library, meta)
    assert plan["sessions"], "something is still planned"
    for s in plan["sessions"]:
        e = library[s["session_code"]]
        if "running" in e["activities"]:
            assert e["rpe"] <= 3, s["session_code"]
        assert "sprinting" not in e["activities"], s["session_code"]
        assert "hamstrings" not in (s["focus_muscles"] or []), s["session_code"]
        assert "nordics" not in (s["addons"] or [])


@pytest.mark.parametrize("phase", ["base", "deload", "vo2_build", "preseason", "in_season"])
@pytest.mark.parametrize("last", [
    {"run": 0.0, "sprint": 0.0, "plyo": 0.0, "total": 0.0},
    {"run": 120.0, "sprint": 60.0, "plyo": 10.0, "total": 400.0},
    {"run": 250.0, "sprint": 300.0, "plyo": 40.0, "total": 700.0},
    {"run": None, "sprint": None, "plyo": None, "total": None},
])
def test_no_plan_exceeds_a_ramp_cap(lib, phase, last):
    from healthtracker.planning.ramp import ramp_cap

    library, meta = lib
    params = {"TRAIN_RUN_RAMP": {"max_weekly_increase_pct": 10, "low_base_allowance": 60},
              "TRAIN_CHANNEL_RAMP": {"sprint_increase_pct": 15, "sprint_allowance_m": 100,
                                     "plyo_increase_pct": 15, "plyo_allowance": 40},
              "TRAIN_LOAD_RAMP": {"max_weekly_increase_pct": 10}, "ANALYSIS_PLAN": {"total_allowance": 100}}
    game = {"date": MONDAY + timedelta(days=5), "sport": "ultimate", "session_type": "game",
            "planned_duration_min": 60, "planned_rpe": 7}
    plan = build_plan(inputs(phase, last=last, params=params, fixed=[game], chronic_week=900.0, vo2_level=(4, "t")),
                      library, meta)
    tot, fixed = plan["planned"], plan["fixed_load"]
    assert tot["run"] + fixed["run"] <= ramp_cap(last["run"], 10, 60) + 1e-6
    assert tot["sprint_m"] <= ramp_cap(last["sprint"], 15, 100)
    assert tot["plyo_contacts"] <= ramp_cap(last["plyo"], 15, 40)
    assert tot["total"] <= max(0.0, ramp_cap(last["total"], 10, 100) - fixed["total"]) + 1e-6
    # The plan's per-session numbers add up to its totals.
    assert sum(s["planned_sprint_m"] or 0 for s in plan["sessions"]) == tot["sprint_m"]
    assert round(sum(s["planned_internal_load"] for s in plan["sessions"]), 1) == pytest.approx(tot["total"], abs=0.2)


def test_ramp_caps_shrink_before_they_drop(lib):
    library, meta = lib
    tight = {"run": 150.0, "sprint": 0.0, "plyo": 40.0, "total": 900.0}
    params = {"ANALYSIS_PLAN": {"min_sprint_m": 40}, "TRAIN_CHANNEL_RAMP": {"sprint_allowance_m": 60}}
    plan = build_plan(inputs(last=tight, params=params), library, meta)
    codes = [s["session_code"] for s in plan["sessions"]]
    assert "vo2_3x3" in codes, "the key session survives"
    sprint = [s for s in plan["sessions"] if s["date"].weekday() == 0][0]
    assert library[sprint["session_code"]]["sprint_m"] > 60 and sprint["planned_sprint_m"] == 60
    assert "held to 60 m" in sprint["reason"]["changes"][0]
    # A cap below the useful minimum turns the sprints into technique.
    plan = build_plan(inputs(last=tight, params={**params, "TRAIN_CHANNEL_RAMP": {"sprint_allowance_m": 20}}), library, meta)
    assert by_day(plan)[0][0] == "speed_technique"
    # Running load shrinks the optional and non-key runs first.
    runs = {s["session_code"]: s for s in plan["sessions"] if library[s["session_code"]]["sport"] == "run"}
    assert plan["planned"]["run"] <= 165 + 1e-6 and "vo2_3x3" in runs


# ------------------------------------------------------------------ the database end
def test_versions_progression_and_grading(seeded, tx):
    conn = tx
    from healthtracker.db import fetch_all, fetch_one, insert
    from healthtracker.planning.weekly import grade_week_plan, write_week_plan

    week = seeded["end"] + timedelta(days=(7 - seeded["end"].weekday()) % 7 or 7) + timedelta(days=14)
    n = write_week_plan(conn, week)
    v1 = fetch_one(conn, "SELECT * FROM analytics.week_plan WHERE week_start = %s AND superseded_at IS NULL", (week,))
    assert n > 0 and v1["version"] >= 1
    assert write_week_plan(conn, week) == n  # same inputs: same version
    assert fetch_one(conn, "SELECT count(*) AS n FROM analytics.week_plan WHERE week_start = %s", (week,))["n"] == 1
    rows = fetch_all(conn, "SELECT * FROM core.planned_session WHERE plan_id = %s", (v1["id"],))
    assert all(r["reason"]["why"] for r in rows) and len(rows) == n

    # A new game changes the plan: version 2, version 1 superseded.
    insert(conn, "core.planned_session", {"date": week + timedelta(days=2), "sport": "ultimate", "session_type": "game",
                                          "planned_duration_min": 90, "planned_rpe": 8}, returning=None)
    write_week_plan(conn, week)
    plans = fetch_all(conn, "SELECT * FROM analytics.week_plan WHERE week_start = %s ORDER BY version", (week,))
    assert [p["version"] for p in plans][-2:] == [v1["version"], v1["version"] + 1] and plans[-2]["superseded_at"]
    current = plans[-1]

    # Do the first main session, then grade the week.
    first = fetch_one(conn, """SELECT * FROM core.planned_session WHERE plan_id = %s AND sport IN ('run', 'strength')
                               AND NOT optional ORDER BY date LIMIT 1""", (current["id"],))
    tz = ZoneInfo("America/Chicago")
    insert(conn, "core.workout", {"source": "manual", "sport": first["sport"], "duration_s": 1800, "rpe": 5,
                                  "start_ts": datetime.combine(first["date"], time(18), tz)}, returning=None)
    assert grade_week_plan(conn, week) == 1
    graded = fetch_one(conn, "SELECT * FROM analytics.week_plan WHERE id = %s", (current["id"],))
    assert graded["graded_at"] and 0 < graded["adherence_pct"] < 100
    assert fetch_one(conn, "SELECT status FROM core.planned_session WHERE id = %s", (first["id"],))["status"] == "done"


def test_vo2_level_steps_up_after_a_completed_week(tx):
    conn = tx
    from healthtracker.db import insert
    from healthtracker.planning.weekly import vo2_level

    week = date(2027, 9, 6)
    assert vo2_level(conn, week, "base", False)[0] == 1
    pid = insert(conn, "analytics.week_plan", {"week_start": week - timedelta(days=7), "version": 1, "phase": "base",
                                               "inputs": {}, "summary": {}, "plan_hash": "x"})
    insert(conn, "core.planned_session", {"date": week - timedelta(days=5), "sport": "run", "session_type": "hard",
                                          "session_code": "vo2_3x3", "status": "done", "plan_id": pid,
                                          "origin": "generated"}, returning=None)
    assert vo2_level(conn, week, "base", False) == (2, "last week's VO2 sessions done")
    assert vo2_level(conn, week, "base", True)[0] == 1 and vo2_level(conn, week, "deload", False)[0] == 1
