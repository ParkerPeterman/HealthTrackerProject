"""Session library (F2): what each session is, when it fits a day, and what it asks for.

Sessions live in knowledge.session_library. `check()` lists why a session doesn't fit a day
(empty means it fits):
  * its phases (depth jumps only in pre-season, for example)
  * its prerequisites: no hard work while the 7-day HRV is below its band (once the A2 bands
    are live), nothing that loads a hamstring or groin tagged in the last few days, no
    throwing while the shoulders are flagged
  * active injury restrictions, matched on the session's activities and effort, and on its
    exercises' names, movement patterns, and primary muscles
  * in pre-season, the return-to-ultimate stage (F7): max-velocity sprinting and cutting wait
    for stage 2, game-like work for stage 3

`exercises_for()` applies the swaps: overhead pressing becomes landmine pressing while the
shoulders are flagged, and an exercise a restriction rules out becomes its listed
alternative or is left out. The Copenhagen level steps up after enough sessions at the
current level and down after a groin tweak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg

from healthtracker.components import component_status, is_live
from healthtracker.db import fetch_all, fetch_one
from healthtracker.features.zones import Zones
from healthtracker.planning.prehab import prehab_params, shoulder_state
from healthtracker.planning.progression import RETURN_ADDS, return_stage_on, return_weeks
from healthtracker.planning.season import phase_for

TAG_WORDS = {"hamstring_tight": "tight hamstring", "groin_tweak": "groin tweak"}
PHASE_WORDS = {"base": "base", "deload": "deload", "vo2_build": "VO2 build", "preseason": "pre-season",
               "in_season": "in-season", "transition": "transition"}


@dataclass
class DayState:
    """What a day looks like to the library."""

    date: date
    phase: str = "base"
    hrv_status: str | None = None                            # A2 band position; None until the bands are live
    restrictions: list[dict] = field(default_factory=list)   # injury_restriction rows active on `date`
    tag_days: dict[str, date] = field(default_factory=dict)  # tag code -> last day tagged, on or before `date`
    shoulder_flagged: bool = False
    return_stage: int | None = None                          # F7: 1-3 in pre-season, None otherwise
    return_weeks: tuple[int, int] = (3, 5)                   # pre-season weeks that start stages 2 and 3

    def as_dict(self) -> dict:
        return {"date": self.date, "phase": self.phase, "hrv_status": self.hrv_status,
                "restrictions": [_restriction_words(r) for r in self.restrictions],
                "tag_days": self.tag_days, "shoulder_flagged": self.shoulder_flagged,
                "return_stage": self.return_stage}


def load_library(conn: psycopg.Connection, include_inactive: bool = False) -> dict[str, dict]:
    rows = fetch_all(conn, """SELECT * FROM knowledge.session_library WHERE active OR %s
                              ORDER BY day_order, category, coalesce(level, 0), code""", (include_inactive,))
    return {r["code"]: r for r in rows}


def exercise_meta(conn: psycopg.Connection) -> dict[str, dict]:
    rows = fetch_all(conn, """SELECT e.name, e.movement_pattern,
                                     coalesce(array_agg(m.muscle_group) FILTER (WHERE m.role = 'primary'), '{}') AS primary
                              FROM core.exercise e LEFT JOIN core.exercise_muscle m ON m.exercise_id = e.id
                              GROUP BY e.id""")
    return {r["name"]: {"pattern": r["movement_pattern"], "primary": set(r["primary"])} for r in rows}


def active_restrictions(injuries: list[dict], d: date) -> list[dict]:
    return [r for inj in injuries for r in inj["restrictions"]
            if r["start_date"] <= d and (r["end_date"] is None or r["end_date"] >= d)]


def _limits(r: dict, rpe: int) -> bool:
    """Does restriction `r` rule out work at this effort? No max (or 0) means avoid it entirely."""
    return not r.get("max_rpe") or rpe > r["max_rpe"]


def _restriction_words(r: dict) -> str:
    target = r.get("activity") or r.get("muscle_group") or r.get("movement_pattern") or r.get("exercise_name") or "exercise"
    limit = "avoid" if not r.get("max_rpe") else f"max RPE {r['max_rpe']}"
    return f"{str(target).replace('_', ' ')}: {limit}"


def blocked_exercise(name: str, meta: dict[str, dict], restrictions: list[dict], rpe: int) -> dict | None:
    m = meta.get(name, {"pattern": None, "primary": set()})
    for r in restrictions:
        hit = ((r["target_type"] == "exercise" and r.get("exercise_name") == name)
               or (r["target_type"] == "movement_pattern" and r.get("movement_pattern") == m["pattern"])
               or (r["target_type"] == "muscle_group" and r.get("muscle_group") in m["primary"]))
        if hit and _limits(r, rpe):
            return r
    return None


def exercises_for(entry: dict, state: DayState, meta: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """The session's exercises for this day, with swaps applied, and a note for each change."""
    out, notes = [], []
    for ex in entry.get("exercises") or []:
        name = ex["exercise"]
        swap = (ex.get("swap") or {}).get("shoulder")
        if swap and state.shoulder_flagged:
            notes.append(f"{name} becomes {swap} while the shoulders are sore")
            name = swap
        if blocked_exercise(name, meta, state.restrictions, entry["rpe"]):
            alt = ex.get("alt")
            if alt and not blocked_exercise(alt, meta, state.restrictions, entry["rpe"]):
                notes.append(f"{name} becomes {alt} (injury restriction)")
                name = alt
            else:
                notes.append(f"{name} left out (injury restriction)")
                continue
        out.append({**{k: v for k, v in ex.items() if k not in ("swap", "alt")}, "exercise": name})
    return out, notes


def check(entry: dict, state: DayState, meta: dict[str, dict]) -> list[str]:
    """Why this session doesn't fit the day. An empty list means it fits."""
    reasons = []
    if entry.get("phases") and state.phase not in entry["phases"]:
        reasons.append("only in " + " and ".join(PHASE_WORDS.get(p, p) for p in entry["phases"]))
    pre = entry.get("prerequisites") or {}
    if pre.get("hrv_band") == "not_below" and state.hrv_status == "below":
        reasons.append("7-day HRV is below its normal band")
    for tag, days in (pre.get("no_tags_days") or {}).items():
        last = state.tag_days.get(tag)
        if last is not None and (state.date - last).days < days:
            ago = (state.date - last).days
            when = "today" if ago == 0 else "yesterday" if ago == 1 else f"{ago} days ago"
            reasons.append(f"{TAG_WORDS.get(tag, tag)} {when}")
    if pre.get("shoulder_ok") and state.shoulder_flagged:
        reasons.append("shoulders are sore")
    stage = entry.get("return_stage") or 1
    if state.return_stage is not None and stage > state.return_stage:
        week = state.return_weeks[stage - 2]
        reasons.append(f"return to ultimate: {RETURN_ADDS[stage]} from week {week} of pre-season")
    for r in state.restrictions:
        if r["target_type"] != "activity":
            continue
        involved = r["activity"] in entry["activities"] or (r["activity"] == "ultimate" and entry["category"] == "ultimate")
        if involved and _limits(r, entry["rpe"]):
            reasons.append(f"injury restriction ({_restriction_words(r)})")
    if entry.get("exercises"):
        kept, _ = exercises_for(entry, state, meta)
        if not kept:
            reasons.append("every exercise in it is restricted")
    return reasons


def mid_minutes(e: dict) -> int:
    """Middle of a session's duration range, on a 5-minute step inside the range."""
    lo, hi = e["duration_min_lo"], e["duration_min_hi"]
    return int(min(hi, max(lo, 5 * round((lo + hi) / 10))))


def expected_load(entry: dict, factor: float, minutes: int | None = None) -> dict:
    """Load on each A6 channel: internal load is minutes x RPE on the TRIMP scale (my calibration factor)."""
    m = minutes or round((entry["duration_min_lo"] + entry["duration_min_hi"]) / 2)
    return {"minutes": m, "internal": round(m * entry["rpe"] * factor, 1), "plyo_contacts": entry["plyo_contacts"],
            "sprint_m": entry["sprint_m"], "shoulder_min": entry["shoulder_min"]}


def hr_target(entry: dict, zones: Zones | None) -> dict | None:
    """Heart-rate target in bpm from my zones. Provisional until a max HR test confirms max HR."""
    if entry.get("target_pct_lo") is None or zones is None:
        return None
    return {"bpm": [zones.bpm(entry["target_pct_lo"]), zones.bpm(entry["target_pct_hi"])],
            "pct_max": [entry["target_pct_lo"], entry["target_pct_hi"]], "max_hr": zones.max_hr,
            "provisional": not zones.confirmed}


def copenhagen_level(conn: psycopg.Connection, d: date, tz: str, params: dict | None = None) -> tuple[int, str]:
    """Which Copenhagen level to program on `d`: up after enough sessions, down after a groin tweak."""
    p = params or prehab_params(conn)
    per_level, tweak_days = int(p["copenhagen_sessions_per_level"]), int(p["copenhagen_tweak_days"])
    rows = fetch_all(conn, """SELECT DISTINCT (w.start_ts AT TIME ZONE %(tz)s)::date AS date, e.name
                              FROM core.lift_set s JOIN core.v_workout w ON w.id = s.workout_id
                              JOIN core.exercise e ON e.id = s.exercise_id
                              WHERE e.name LIKE 'Copenhagen adduction L_' AND NOT s.is_warmup
                                AND (w.start_ts AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s
                              ORDER BY 1""", {"tz": tz, "s": d - timedelta(days=56), "e": d - timedelta(days=1)})
    sessions = [(r["date"], int(r["name"][-1])) for r in rows]
    last_date, current = sessions[-1] if sessions else (None, 1)
    tweak = fetch_one(conn, """SELECT max((ts AT TIME ZONE %s)::date) AS d FROM core.behavior_event
                               WHERE type_code = 'groin_tweak' AND (ts AT TIME ZONE %s)::date BETWEEN %s AND %s""",
                      (tz, tz, d - timedelta(days=tweak_days - 1), d))["d"]
    if tweak and (last_date is None or tweak >= last_date):
        if current > 1:
            return current - 1, f"stepped down a level after the groin tweak on {tweak:%b %d}"
        return 1, f"level 1 after the groin tweak on {tweak:%b %d}"
    if tweak:
        return current, f"holding the level: groin tweak on {tweak:%b %d}"
    n = len({s for s, lv in sessions if lv == current})
    if current < 3 and n >= per_level:
        return current + 1, f"{n} sessions at level {current} without a groin tweak"
    if not sessions:
        return 1, "starting at level 1"
    return current, f"{n} of {per_level} sessions at level {current}" if current < 3 else "top level"


def day_state(conn: psycopg.Connection, d: date, tz: str, injuries: list[dict] | None = None) -> DayState:
    from healthtracker.rules.context import load_injuries, load_params

    hrv = None
    if is_live(component_status(conn), "COMP_HRV_BANDS"):
        row = fetch_one(conn, "SELECT status FROM analytics.metric_status WHERE metric = 'ln_hrv' AND date = %s", (d,))
        hrv = row["status"] if row else None
    tags = fetch_all(conn, """SELECT type_code, max((ts AT TIME ZONE %s)::date) AS d FROM core.behavior_event
                              WHERE type_code IN ('hamstring_tight', 'groin_tweak')
                                AND (ts AT TIME ZONE %s)::date BETWEEN %s AND %s GROUP BY type_code""",
                     (tz, tz, d - timedelta(days=14), d))
    injuries = load_injuries(conn, d) if injuries is None else injuries
    params = load_params(conn)
    return DayState(date=d, phase=phase_for(conn, d), hrv_status=hrv, restrictions=active_restrictions(injuries, d),
                    tag_days={r["type_code"]: r["d"] for r in tags},
                    shoulder_flagged=shoulder_state(conn, d, tz)["flagged"],
                    return_stage=return_stage_on(conn, d, params), return_weeks=return_weeks(params))
