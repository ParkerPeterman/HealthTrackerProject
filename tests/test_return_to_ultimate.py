"""F7: pre-season builds max-velocity sprinting and cutting back up before game-like work."""

from datetime import date, timedelta

import pytest

from healthtracker.planning.progression import return_stage, return_weeks

PRE = date(2027, 2, 8)  # the default pre-season start with a first game on April 1
ROOMY = {"run": 900.0, "sprint": 2000.0, "plyo": 500.0, "total": 3000.0}


def test_stage_by_week():
    assert [return_stage("preseason", w) for w in range(1, 8)] == [1, 1, 2, 2, 3, 3, 3]
    assert return_stage("base", 1) is None and return_stage("in_season", 1) is None
    assert return_stage("preseason", None) is None
    p = {"ANALYSIS_PLAN": {"return_maxv_week": 2, "return_full_week": 4}}
    assert [return_stage("preseason", w, p) for w in range(1, 5)] == [1, 2, 2, 3]
    assert return_weeks({"ANALYSIS_PLAN": {"return_maxv_week": 4, "return_full_week": 3}}) == (4, 4)


@pytest.fixture(scope="module")
def lib(db_url):
    from healthtracker.db import connect, use_database
    from healthtracker.planning.library import exercise_meta, load_library

    use_database(db_url)
    with connect() as c:
        return load_library(c), exercise_meta(c)


@pytest.mark.db
def test_library_check_follows_the_stage(lib):
    from healthtracker.planning.library import DayState, check

    library, meta = lib
    later = {"maxv_sprints", "repeated_sprints", "cod_drills", "game_like_intervals"}

    def blocked(stage):
        st = DayState(PRE, phase="preseason", return_stage=stage)
        return {c for c, e in library.items() if any("return to ultimate" in r for r in check(e, st, meta))}

    assert blocked(1) == later
    assert blocked(2) == {"game_like_intervals"}
    assert blocked(3) == set() and blocked(None) == set()
    st = DayState(PRE, phase="preseason", return_stage=1)
    assert "max-velocity sprinting and cutting from week 3 of pre-season" in check(library["maxv_sprints"], st, meta)[0]
    assert "game-like work from week 5" in check(library["game_like_intervals"], st, meta)[0]


def inputs(week_no, fixed=(), restrictions=(), last=None, params=None):
    from healthtracker.planning.library import DayState
    from healthtracker.planning.weekly import PlanInputs

    ws = PRE + timedelta(days=7 * (week_no - 1))
    states = {ws + timedelta(days=i): DayState(ws + timedelta(days=i), phase="preseason",
                                               restrictions=list(restrictions)) for i in range(7)}
    return PlanInputs(week_start=ws, phase="preseason", states=states, last_week=last or ROOMY, chronic_week=900.0,
                      phase_week=week_no, fixed=list(fixed), vo2_level=(4, "t"), params=params or {})


@pytest.mark.db
@pytest.mark.parametrize("week_no", range(1, 8))
def test_no_session_ahead_of_its_stage(lib, week_no):
    from healthtracker.planning.weekly import build_plan

    library, meta = lib
    plan = build_plan(inputs(week_no), library, meta)
    stage = return_stage("preseason", week_no)
    assert plan["return_to_play"]["stage"] == stage and plan["return_to_play"]["week"] == week_no
    for s in plan["sessions"]:
        assert library[s["session_code"]]["return_stage"] <= stage, (week_no, s["session_code"])
    codes = {s["session_code"] for s in plan["sessions"]}
    if stage == 1:
        assert "accel_sprints" in codes and "hill_repeats" in codes, "the key slots fall back, not out"
    assert not [x for x in plan["dropped"] if not x["optional"]], plan["dropped"]


@pytest.mark.db
def test_sprint_volume_scales_before_the_caps(lib):
    from healthtracker.planning.weekly import build_plan, render_plan

    library, meta = lib
    full = {"maxv_sprints": 125, "repeated_sprints": 360, "accel_sprints": 120, "cod_drills": 80}
    for week_no, k in ((1, 0.5), (3, 0.75), (5, 1.0)):
        plan = build_plan(inputs(week_no), library, meta)
        for s in plan["sessions"]:
            if s["planned_sprint_m"]:
                assert s["planned_sprint_m"] == int(5 * round(full[s["session_code"]] * k / 5)), (week_no, s)
                if k < 1:
                    assert any("of the sprint volume" in c for c in s["reason"]["changes"])
        assert plan["return_to_play"]["sprint_scale"] == k
        text = render_plan(plan)
        assert f"week {week_no} of pre-season" in text and "(pre-season phase" in text
    # A tight ramp cap still binds after the scaling.
    tight = {**ROOMY, "sprint": 0.0}
    params = {"TRAIN_CHANNEL_RAMP": {"sprint_allowance_m": 50}}
    plan = build_plan(inputs(3, last=tight, params=params), library, meta)
    assert plan["planned"]["sprint_m"] <= 50


@pytest.mark.db
def test_restrictions_and_games_still_count(lib):
    from healthtracker.planning.weekly import build_plan, render_plan

    library, meta = lib
    r = {"target_type": "activity", "activity": "sprinting", "muscle_group": None, "movement_pattern": None,
         "exercise_name": None, "max_rpe": 0, "start_date": PRE - timedelta(days=30), "end_date": None}
    plan = build_plan(inputs(6, restrictions=[r]), library, meta)
    assert not [s for s in plan["sessions"] if "sprinting" in library[s["session_code"]]["activities"]]
    ws = PRE + timedelta(days=7)
    game = {"date": ws + timedelta(days=5), "sport": "ultimate", "session_type": "game", "planned_duration_min": 90,
            "planned_rpe": 8}
    plan = build_plan(inputs(2, fixed=[game]), library, meta)
    assert plan["return_to_play"]["game_before_full"]
    assert "before the build-up is complete" in render_plan(plan)
    assert not build_plan(inputs(5, fixed=[{**game, "date": PRE + timedelta(days=33)}]), library, meta)[
        "return_to_play"]["game_before_full"]


@pytest.mark.db
def test_stage_from_the_season_in_the_database(tx):
    from healthtracker.planning.library import day_state
    from healthtracker.planning.progression import return_stage_on
    from healthtracker.planning.season import save_season

    conn = tx
    save_season(conn, date(2027, 4, 1), date(2026, 11, 9))
    assert return_stage_on(conn, date(2027, 1, 20)) is None
    assert return_stage_on(conn, date(2027, 2, 10)) == 1
    assert return_stage_on(conn, date(2027, 2, 24)) == 2
    assert return_stage_on(conn, date(2027, 3, 12)) == 3
    assert return_stage_on(conn, date(2027, 4, 2)) is None  # in season
    st = day_state(conn, date(2027, 2, 10), "America/Chicago")
    assert st.phase == "preseason" and st.return_stage == 1 and st.as_dict()["return_stage"] == 1
