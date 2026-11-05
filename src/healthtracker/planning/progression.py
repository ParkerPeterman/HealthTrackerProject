"""Progression (F5): when next week may grow, when it deloads, and what to lift.

* Load. Next week's load target is my chronic load times the progression factor only when
  last week's key sessions were done (its graded week plan) and recovery held: no recovery
  flags during the week, and HRV not below or resting HR not above its band at the end of
  it. Otherwise the target holds at my chronic load. Recent recovery flags (the caution
  factor) and deloads come first. Running volume always stays inside the ramp guard
  (planning/ramp.py).
* Deloads. Every `deload_every`-th week of a training phase (base, VO2 build, pre-season;
  default every 4th) is a deload week: the deload factor, durations at the low end of their
  range, optional sessions dropped while the week is over its target, the VO2 level held, and
  one set fewer on the lifts. The holiday deload phase has its own template.
* Return to ultimate (F7). Pre-season builds the movements behind the common hamstring and
  ankle injuries back up by week: weeks 1-2 accelerations only (no max-velocity sprinting,
  cutting, or game-like work), weeks 3-4 add max velocity and cutting, week 5 on everything.
  Each library session names the first stage it belongs to (`return_stage`), and the week's
  sprint metres are scaled 0.5 / 0.75 / 1.0 before the ramp caps. Injury restrictions apply on
  top, as everywhere.
* Strength. Each lift's suggested weight follows the reps in reserve on my last session's
  top sets: RIR 3 or more +5%, RIR 2 +2.5%, RIR 1 hold, RIR 0 -5%. Weights round to what
  plates allow (2.5 kg, or 5 lb in imperial; 1 kg or 2.5 lb under 20 kg), and when the
  rounding cancels a step up the suggestion is one more rep at the same weight. A set logged
  without RIR counts as RIR 2, the app's default, as in the hard-set count.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import psycopg

from healthtracker.db import fetch_all, fetch_one
from healthtracker.planning.season import phase_on, phases
from healthtracker.rules.context import load_params
from healthtracker.units import KG_PER_LB

DELOAD_PHASES = ("base", "vo2_build", "preseason")
RIR_STEP = {0: -0.05, 1: 0.0, 2: 0.025}   # 3 or more: +5%
RIR_DEFAULT = 2


# ------------------------------------------------------------------ weeks within a phase
def phase_week(rows: list[dict], week_start: date) -> int | None:
    """Which week of its phase the Monday `week_start` falls in (1 = the week the phase
    starts), from core.season_phase rows. None outside any defined phase."""
    for r in rows:
        if r["start_date"] <= week_start and (r["end_date"] is None or week_start <= r["end_date"]):
            first = r["start_date"] - timedelta(days=r["start_date"].weekday())
            return (week_start - first).days // 7 + 1
    return None


def scheduled_deload(phase: str, week_no: int | None, every: float) -> bool:
    """Every Nth week of a training phase is a deload. `every` below 2 turns it off."""
    n = int(every or 0)
    return phase in DELOAD_PHASES and week_no is not None and n >= 2 and week_no % n == 0


def deload_on(conn: psycopg.Connection, d: date, params: dict | None = None) -> bool:
    """Is the week holding `d` a deload week (the holiday deload or a scheduled one)?"""
    rows = phases(conn)
    monday = d - timedelta(days=d.weekday())
    phase = phase_on(rows, monday)
    every = (params if params is not None else load_params(conn)).get("ANALYSIS_PLAN", {}).get("deload_every", 4)
    return phase == "deload" or scheduled_deload(phase, phase_week(rows, monday), every)


# ------------------------------------------------------------------ return to ultimate (F7)
RETURN_DEFAULTS = {"return_maxv_week": 3, "return_full_week": 5}
RETURN_SCALE = {1: 0.5, 2: 0.75, 3: 1.0}
RETURN_WORDS = {1: "accelerations only", 2: "max-velocity sprinting and cutting added",
                3: "everything, game-like work included"}
RETURN_ADDS = {2: "max-velocity sprinting and cutting", 3: "game-like work"}   # what each stage brings in


def return_weeks(params: dict | None) -> tuple[int, int]:
    p = {**RETURN_DEFAULTS, **((params or {}).get("ANALYSIS_PLAN", {}))}
    maxv = int(p["return_maxv_week"])
    return maxv, max(maxv, int(p["return_full_week"]))


def return_stage(phase: str, week_no: int | None, params: dict | None = None) -> int | None:
    """1, 2, or 3 in pre-season (by week of the phase); None in every other phase."""
    if phase != "preseason" or week_no is None:
        return None
    maxv, full = return_weeks(params)
    return 1 if week_no < maxv else 2 if week_no < full else 3


def return_stage_on(conn: psycopg.Connection, d: date, params: dict | None = None) -> int | None:
    rows = phases(conn)
    monday = d - timedelta(days=d.weekday())
    if phase_on(rows, d) != "preseason":
        return None
    return return_stage("preseason", phase_week(rows, monday), params if params is not None else load_params(conn))


# ------------------------------------------------------------------ may next week grow?
def progression_check(conn: psycopg.Connection, week_start: date, key_pct: float = 100) -> tuple[bool, str]:
    """Progress only after a graded week whose key sessions were done and whose recovery held."""
    prev = fetch_one(conn, """SELECT * FROM analytics.week_plan WHERE week_start = %s AND superseded_at IS NULL""",
                     (week_start - timedelta(days=7),))
    return judge_last_week(prev, key_pct)


def judge_last_week(prev: dict | None, key_pct: float = 100) -> tuple[bool, str]:
    if not prev:
        return False, "no plan last week to check, so the load holds"
    if prev.get("graded_at") is None:
        return False, "last week's plan isn't graded yet, so the load holds"
    done, planned = prev.get("key_done") or 0, prev.get("key_planned") or 0
    if planned == 0:
        return False, "last week had no key sessions to check, so the load holds"
    if done < math.ceil(planned * key_pct / 100 - 1e-9):
        return False, f"{done} of {planned} key sessions done last week, so the load holds"
    r = prev.get("response") or {}
    if r.get("recovery_flags"):
        return False, "recovery flags last week, so the load holds"
    if r.get("hrv_status") == "below":
        return False, "HRV ended last week below its band, so the load holds"
    if r.get("resting_hr_status") == "above":
        return False, "resting HR ended last week above its band, so the load holds"
    return True, f"last week's key sessions done ({done} of {planned}) and recovery held: small progression"


# ------------------------------------------------------------------ strength
def plate(units: str, kg: float) -> float:
    """The smallest weight step, in kg, for this load and these units."""
    if units == "imperial":
        return (5.0 if kg >= 20 else 2.5) * KG_PER_LB
    return 2.5 if kg >= 20 else 1.0


def round_to(kg: float, step: float) -> float:
    return round(round(kg / step) * step, 2)


def strength_step(top_kg: float, reps: int, rir: int | None, units: str, deload: bool = False) -> dict:
    """Next session's suggestion for one lift from its last top set."""
    used = RIR_DEFAULT if rir is None else int(rir)
    pct = 0.05 if used >= 3 else RIR_STEP[used]
    out = {"last_kg": round(top_kg, 2), "last_reps": reps, "last_rir": rir, "rir_assumed": rir is None,
           "step_pct": round(pct * 100, 1), "next_kg": round(top_kg, 2), "next_reps": reps, "fewer_sets": 0}
    if deload:
        out.update(step_pct=0.0, fewer_sets=1, why="deload week: same weight, one set fewer")
        return out
    rir_words = f"RIR {used}{' (not logged, counted as 2)' if rir is None else ''} last time"
    if top_kg <= 0:  # bodyweight: reps move instead of weight
        if pct > 0:
            out.update(next_reps=reps + 1, why=f"{rir_words}: one more rep")
        elif pct < 0:
            out.update(next_reps=max(1, reps - 1), why=f"{rir_words}: one rep fewer")
        else:
            out["why"] = f"{rir_words}: same reps"
        return out
    step = plate(units, top_kg)
    nxt = round_to(top_kg * (1 + pct), step)
    if pct > 0 and nxt <= top_kg:
        out.update(next_reps=reps + 1, why=f"{rir_words}: one more rep at the same weight (the next plate is too big a jump)")
        return out
    if pct < 0 and nxt >= top_kg:
        nxt = round_to(top_kg - step, step)
    out["next_kg"] = max(nxt, 0.0)
    out["why"] = f"{rir_words}: " + ("same weight" if pct == 0 else f"{'+' if pct > 0 else ''}{pct * 100:g}%")
    return out


def last_top_sets(conn: psycopg.Connection, names: list[str], before: date, tz: str) -> dict[str, dict]:
    """For each exercise, the last workout before `before` that had working sets of it: the top
    weight, the most reps at that weight, and the lowest RIR at that weight."""
    if not names:
        return {}
    rows = fetch_all(conn, """
        WITH s AS (
            SELECT e.name, w.id AS wid, w.start_ts, (w.start_ts AT TIME ZONE %(tz)s)::date AS d,
                   s.weight_kg::float AS kg, s.reps, s.rir
            FROM core.lift_set s
            JOIN core.v_workout w ON w.id = s.workout_id
            JOIN core.exercise e ON e.id = s.exercise_id
            WHERE e.name = ANY(%(names)s) AND NOT s.is_warmup
              AND (w.start_ts AT TIME ZONE %(tz)s)::date < %(before)s
        ), last AS (
            SELECT DISTINCT ON (name) name, wid FROM s ORDER BY name, start_ts DESC, wid DESC
        ), top AS (
            SELECT s.name, s.d, max(s.kg) OVER (PARTITION BY s.name) AS top_kg, s.kg, s.reps, s.rir
            FROM s JOIN last USING (name, wid)
        )
        SELECT name, d, top_kg, max(reps) AS reps,
               min(coalesce(rir, %(dflt)s)) AS rir, bool_and(rir IS NULL) AS rir_missing
        FROM top WHERE kg = top_kg GROUP BY name, d, top_kg""",
                     {"tz": tz, "names": names, "before": before, "dflt": RIR_DEFAULT})
    return {r["name"]: {"date": r["d"], "top_kg": float(r["top_kg"]), "reps": int(r["reps"]),
                        "rir": None if r["rir_missing"] else int(r["rir"])} for r in rows}


def strength_hints(conn: psycopg.Connection, names: list[str], d: date, tz: str, units: str,
                   deload: bool = False) -> dict[str, dict]:
    """Suggested weight and reps for each named lift on day `d`, from its last session."""
    out = {}
    for name, last in last_top_sets(conn, sorted(set(names)), d, tz).items():
        out[name] = {"exercise": name, "last_date": last["date"],
                     **strength_step(last["top_kg"], last["reps"], last["rir"], units, deload)}
    return out
