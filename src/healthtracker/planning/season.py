"""Season phases (F1).

Defaults, given the first game (April 1 unless I set it) and when base training starts:

  base        start of base training -> the holiday deload
  deload      2 weeks: reduced volume, flexible days
  vo2_build   5 weeks: two VO2 sessions a week
  preseason   about 7.5 weeks before the first game: repeated sprints, change of
              direction, plyometric progression, return to ultimate
  in_season   from the first game
  transition  1-2 weeks after the last game

With a first game on 2027-04-01 and base starting 2026-11-09 this gives exactly the
brief's dates (deload Dec 21 - Jan 3, VO2 build Jan 4 - Feb 7, pre-season Feb 8).
Outside any defined phase, the day counts as base.
"""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

from healthtracker.db import execute, fetch_all, fetch_one, insert

PHASES = ("base", "deload", "vo2_build", "preseason", "in_season", "transition")
FOCUS = {
    "base": "One VO2 session a week (3 x 3 building to 4 x 4), one speed/jump session, two strength sessions, "
            "easy volume building gradually",
    "deload": "Reduced volume, flexible days",
    "vo2_build": "Two VO2 sessions a week, one speed/jump session, strength shifting toward power",
    "preseason": "Repeated sprints, change of direction, game-like intervals, plyometric progression, one VO2 "
                 "session a week, building back to full ultimate",
    "in_season": "Games anchor the week; 1-2 maintenance strength sessions; recovery management; taper before tournaments",
    "transition": "Unstructured and low load",
}
PRESEASON_DAYS, VO2_DAYS, DELOAD_DAYS, TRANSITION_DAYS = 52, 35, 14, 14


def monday_on_or_after(d: date) -> date:
    return d + timedelta(days=(7 - d.weekday()) % 7)


def monday_on_or_before(d: date) -> date:
    return d - timedelta(days=d.weekday())


def default_first_game(today: date) -> date:
    april = date(today.year, 4, 1)
    return april if today <= april else date(today.year + 1, 4, 1)


def default_phases(first_game: date, base_start: date, season_end: date | None = None) -> dict[str, tuple]:
    pre = monday_on_or_before(first_game - timedelta(days=PRESEASON_DAYS))
    vo2 = pre - timedelta(days=VO2_DAYS)
    deload = vo2 - timedelta(days=DELOAD_DAYS)
    out: dict[str, tuple] = {}
    if base_start < deload:
        out["base"] = (base_start, deload - timedelta(days=1))
    out["deload"] = (deload, vo2 - timedelta(days=1))
    out["vo2_build"] = (vo2, pre - timedelta(days=1))
    out["preseason"] = (pre, first_game - timedelta(days=1))
    out["in_season"] = (first_game, season_end)
    if season_end:
        out["transition"] = (season_end + timedelta(days=1), season_end + timedelta(days=TRANSITION_DAYS))
    return out


def active_season(conn: psycopg.Connection) -> dict | None:
    return fetch_one(conn, "SELECT * FROM core.season WHERE active ORDER BY id DESC LIMIT 1")


def save_season(conn: psycopg.Connection, first_game: date, base_start: date, season_end: date | None = None,
                overrides: dict[str, tuple] | None = None) -> int:
    """Replace the active season: defaults from the first game, then any phases I set by hand."""
    execute(conn, "UPDATE core.season SET active = false WHERE active")
    sid = insert(conn, "core.season", {"first_game": first_game, "base_start": base_start, "season_end": season_end})
    phases = default_phases(first_game, base_start, season_end)
    for name, (start, end) in (overrides or {}).items():
        phases[name] = (start, end)
    for name, (start, end) in phases.items():
        insert(conn, "core.season_phase", {"season_id": sid, "phase": name, "start_date": start, "end_date": end,
                                           "source": "manual" if overrides and name in overrides else "default"},
               returning=None)
    return sid


def ensure_season(conn: psycopg.Connection, today: date) -> dict:
    s = active_season(conn)
    if s is None:
        save_season(conn, default_first_game(today), monday_on_or_after(today))
        s = active_season(conn)
    return s


def phases(conn: psycopg.Connection) -> list[dict]:
    s = active_season(conn)
    if not s:
        return []
    return fetch_all(conn, "SELECT * FROM core.season_phase WHERE season_id = %s ORDER BY start_date", (s["id"],))


def phase_on(rows: list[dict], d: date) -> str:
    for r in rows:
        if r["start_date"] <= d and (r["end_date"] is None or d <= r["end_date"]):
            return r["phase"]
    return "base"


def phase_for(conn: psycopg.Connection, d: date) -> str:
    return phase_on(phases(conn), d)
