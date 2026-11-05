"""F1 (in season): the week before a tournament tapers."""

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.db

MONDAY = date(2027, 4, 12)
ROOMY = {"run": 900.0, "sprint": 2000.0, "plyo": 500.0, "total": 3000.0}


@pytest.fixture(scope="module")
def lib(db_url):
    from healthtracker.db import connect, use_database
    from healthtracker.planning.library import exercise_meta, load_library

    use_database(db_url)
    with connect() as c:
        return load_library(c), exercise_meta(c)


def game(day: int, minutes: int = 60) -> dict:
    return {"date": MONDAY + timedelta(days=day), "sport": "ultimate", "session_type": "game",
            "planned_duration_min": minutes, "planned_rpe": 8}


def inputs(fixed, **kw):
    from healthtracker.planning.library import DayState
    from healthtracker.planning.weekly import PlanInputs

    states = {MONDAY + timedelta(days=i): DayState(MONDAY + timedelta(days=i), phase="in_season") for i in range(7)}
    return PlanInputs(week_start=MONDAY, phase="in_season", states=states, fixed=fixed, last_week=ROOMY,
                      chronic_week=800.0, **kw)


def test_what_counts_as_a_tournament():
    assert inputs([game(5), game(5)]).tournaments == [MONDAY + timedelta(days=5)]
    assert inputs([game(5), game(6)]).tournaments == [MONDAY + timedelta(days=5), MONDAY + timedelta(days=6)]
    assert inputs([game(6)], next_game=True).tournaments == [MONDAY + timedelta(days=6)]
    assert not inputs([game(1), game(5)]).taper  # two league games apart: not a tournament
    assert not inputs([game(5)]).taper


def test_taper_week_before_a_saturday_tournament(lib):
    from healthtracker.planning.weekly import build_plan, render_plan

    library, meta = lib
    sat = MONDAY + timedelta(days=5)
    plan = build_plan(inputs([game(5), game(5), game(5)]), library, meta)
    normal = build_plan(inputs([game(5)]), library, meta)
    assert plan["taper"] == {"tournaments": [str(sat)]} and normal["taper"] is None
    assert plan["target_load"] == round(800 * 0.7) and "taper week" in plan["reason"]
    for s in plan["sessions"]:
        if library[s["session_code"]]["hard"]:
            assert (sat - s["date"]).days > 3, s
        if not s["key"]:
            assert any("taper for the tournament" in c for c in s["reason"]["changes"]) or \
                s["planned_duration_min"] <= library[s["session_code"]]["duration_min_lo"]
    assert plan["planned"]["total"] < normal["planned"]["total"]
    assert "a taper week" in render_plan(plan)
    assert plan["health"]["extended"] == []


def test_a_friday_tournament_moves_the_quality_session_earlier(lib):
    from healthtracker.planning.weekly import build_plan

    library, meta = lib
    fri = MONDAY + timedelta(days=4)
    plan = build_plan(inputs([game(4), game(4)]), library, meta)
    hard = [s for s in plan["sessions"] if library[s["session_code"]]["hard"]]
    assert all((fri - s["date"]).days > 3 for s in hard), hard
    quality = [s for s in plan["sessions"] if s["reason"]["slot"] == "quality"]
    if quality:  # Tuesday is too close now: it moves to Monday
        assert quality[0]["date"] == MONDAY and "taper" in quality[0]["reason"]["day"]
    else:
        assert any(x["slot"] == "quality" for x in plan["dropped"])
