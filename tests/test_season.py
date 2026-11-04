"""F1: season phases default from the first game."""

from datetime import date

import pytest

from healthtracker.planning.season import default_first_game, default_phases, monday_on_or_after, phase_on


def test_defaults_match_the_brief():
    p = default_phases(date(2027, 4, 1), date(2026, 11, 9))
    assert p["base"] == (date(2026, 11, 9), date(2026, 12, 20))
    assert p["deload"] == (date(2026, 12, 21), date(2027, 1, 3))
    assert p["vo2_build"] == (date(2027, 1, 4), date(2027, 2, 7))
    assert p["preseason"] == (date(2027, 2, 8), date(2027, 3, 31))
    assert p["in_season"] == (date(2027, 4, 1), None) and "transition" not in p
    with_end = default_phases(date(2027, 4, 1), date(2026, 11, 9), date(2027, 7, 15))
    assert with_end["transition"] == (date(2027, 7, 16), date(2027, 7, 29))


def test_first_game_and_monday_defaults():
    assert default_first_game(date(2026, 11, 4)) == date(2027, 4, 1)
    assert default_first_game(date(2027, 3, 1)) == date(2027, 4, 1)
    assert monday_on_or_after(date(2026, 11, 4)) == date(2026, 11, 9)
    assert monday_on_or_after(date(2026, 11, 9)) == date(2026, 11, 9)


def test_phase_lookup():
    rows = [{"phase": ph, "start_date": a, "end_date": b}
            for ph, (a, b) in default_phases(date(2027, 4, 1), date(2026, 11, 9)).items()]
    assert phase_on(rows, date(2026, 11, 20)) == "base"
    assert phase_on(rows, date(2027, 1, 10)) == "vo2_build"
    assert phase_on(rows, date(2027, 6, 1)) == "in_season"
    assert phase_on(rows, date(2026, 10, 1)) == "base"  # before anything is defined


@pytest.mark.db
def test_season_api(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app

    client = TestClient(app)
    out = client.put("/api/season", json={"first_game": "2027-04-10", "base_start": "2026-11-09"}).json()
    assert {p["phase"] for p in out["phases"]} >= {"base", "deload", "vo2_build", "preseason", "in_season"}
    pre = next(p for p in out["phases"] if p["phase"] == "preseason")
    assert pre["start_date"] == "2027-02-15" and pre["end_date"] == "2027-04-09"
    assert client.put("/api/season", json={"first_game": "2026-01-01", "base_start": "2026-11-09"}).status_code == 400
    bad = client.put("/api/season", json={"first_game": "2027-04-10", "overrides": {"nope": ["2027-01-01", None]}})
    assert bad.status_code == 400
