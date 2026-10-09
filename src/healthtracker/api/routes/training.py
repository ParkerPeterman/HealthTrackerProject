"""Workouts, games, lift sets, exercises, and planned sessions."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import date as Date
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from healthtracker.api.deps import bad_request, get_conn, local_date, not_found, now_local
from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.jsonutil import to_jsonable
from healthtracker.profile import load_profile

router = APIRouter(prefix="/api", tags=["training"])

PATTERNS = Literal["squat", "hinge", "lunge", "horizontal_push", "vertical_push", "horizontal_pull",
                   "vertical_pull", "carry", "core", "plyometric", "other"]


# ------------------------------------------------------------------ workouts
class WorkoutIn(BaseModel):
    sport: str = Field(min_length=1)
    is_game: bool = False
    start_ts: datetime | None = None
    duration_min: float = Field(gt=0, le=600)
    rpe: int | None = Field(None, ge=1, le=10)
    distance_m: float | None = None
    notes: str | None = None


class WorkoutPatch(BaseModel):
    sport: str | None = None
    is_game: bool | None = None
    start_ts: datetime | None = None
    duration_min: float | None = Field(None, gt=0, le=600)
    rpe: int | None = Field(None, ge=1, le=10)
    distance_m: float | None = None
    notes: str | None = None


@router.get("/workouts")
def list_workouts(start: date | None = None, end: date | None = None,
                  conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    tz = load_profile(conn).timezone
    end = end or local_date(conn)
    start = start or end - timedelta(days=14)
    rows = fetch_all(
        conn,
        """SELECT w.*, (w.start_ts AT TIME ZONE %(tz)s)::date AS date,
                  (SELECT count(*) FROM core.lift_set s WHERE s.workout_id = w.id AND NOT s.is_warmup) AS n_sets,
                  (SELECT array_agg(m.id) FROM core.workout m WHERE m.superseded_by = w.id) AS merged_from
           FROM core.v_workout w
           WHERE (w.start_ts AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s
           ORDER BY w.start_ts DESC""", {"tz": tz, "s": start, "e": end})
    return to_jsonable(rows)


def _resolve(conn: psycopg.Connection, workout_id: int) -> dict:
    """Follow superseded_by so edits land on the row that counts."""
    row = fetch_one(conn, "SELECT * FROM core.workout WHERE id = %s", (workout_id,))
    if not row:
        raise not_found("unknown workout")
    while row["superseded_by"]:
        row = fetch_one(conn, "SELECT * FROM core.workout WHERE id = %s", (row["superseded_by"],))
    return row


@router.get("/workouts/{workout_id}")
def get_workout(workout_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    w = _resolve(conn, workout_id)
    w["sets"] = fetch_all(conn, """SELECT s.*, e.name AS exercise FROM core.lift_set s
                                   JOIN core.exercise e ON e.id = s.exercise_id
                                   WHERE s.workout_id = %s ORDER BY s.id""", (w["id"],))
    w["splits"] = fetch_all(conn, "SELECT * FROM core.run_split WHERE workout_id = %s ORDER BY split_no", (w["id"],))
    return to_jsonable(w)


@router.post("/workouts")
def create_workout(body: WorkoutIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    start = body.start_ts or (now_local(conn) - timedelta(minutes=body.duration_min))
    wid = insert(conn, "core.workout", {
        "source": "manual", "sport": body.sport.lower(), "is_game": body.is_game, "start_ts": start,
        "duration_s": int(body.duration_min * 60), "rpe": body.rpe,
        "rpe_source": "manual" if body.rpe else None, "distance_m": body.distance_m, "notes": body.notes})
    if body.is_game:
        d = local_date(conn, start)
        execute(conn, """UPDATE core.planned_session SET status = 'done', linked_workout_id = %s
                         WHERE date = %s AND session_type = 'game' AND status = 'planned'
                           AND linked_workout_id IS NULL""", (wid, d))
    return get_workout(wid, conn)


@router.put("/workouts/{workout_id}")
def update_workout(workout_id: int, body: WorkoutPatch, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    w = _resolve(conn, workout_id)
    d = body.model_dump(exclude_none=True)
    if w["source"] == "garmin":  # device facts stay; my annotations can change
        d = {k: v for k, v in d.items() if k in ("sport", "is_game", "rpe", "notes")}
        if "rpe" in d:
            d["rpe_source"] = "manual"
    else:
        if "duration_min" in d:
            d["duration_s"] = int(d.pop("duration_min") * 60)
        if "rpe" in d:
            d["rpe_source"] = "manual"
    if d:
        execute(conn, f"UPDATE core.workout SET {', '.join(f'{k} = %s' for k in d)} WHERE id = %s",
                (*d.values(), w["id"]))
    return get_workout(w["id"], conn)


@router.delete("/workouts/{workout_id}")
def delete_workout(workout_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    w = fetch_one(conn, "SELECT source FROM core.workout WHERE id = %s", (workout_id,))
    if not w:
        raise not_found()
    if w["source"] != "manual":
        raise bad_request("Garmin workouts can't be deleted here; they come back on the next sync")
    execute(conn, "UPDATE core.planned_session SET linked_workout_id = NULL WHERE linked_workout_id = %s",
            (workout_id,))
    execute(conn, "DELETE FROM core.workout WHERE id = %s", (workout_id,))
    return {"ok": True}


# ------------------------------------------------------------------ exercises
class MuscleIn(BaseModel):
    muscle_group: str
    role: Literal["primary", "secondary"] = "primary"
    set_credit: float = Field(1.0, ge=0, le=1)


class ExerciseIn(BaseModel):
    name: str = Field(min_length=1)
    movement_pattern: PATTERNS
    is_main_lift: bool = False
    notes: str | None = None
    muscles: list[MuscleIn] = Field(default_factory=list)


@router.get("/muscle-groups")
def muscle_groups(conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM core.muscle_group ORDER BY body_region, code")


@router.get("/exercises")
def list_exercises(conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    rows = fetch_all(conn, """SELECT e.*, coalesce(json_agg(json_build_object('muscle_group', m.muscle_group,
                                     'role', m.role, 'set_credit', m.set_credit)) FILTER (WHERE m.exercise_id IS NOT NULL),
                                     '[]') AS muscles,
                                     (SELECT max(w.start_ts) FROM core.lift_set s JOIN core.workout w ON w.id = s.workout_id
                                      WHERE s.exercise_id = e.id) AS last_used
                              FROM core.exercise e LEFT JOIN core.exercise_muscle m ON m.exercise_id = e.id
                              GROUP BY e.id ORDER BY e.name""")
    return to_jsonable(rows)


def _save_muscles(conn: psycopg.Connection, eid: int, muscles: list[MuscleIn]) -> None:
    execute(conn, "DELETE FROM core.exercise_muscle WHERE exercise_id = %s", (eid,))
    for m in muscles:
        insert(conn, "core.exercise_muscle", {"exercise_id": eid, **m.model_dump()}, returning=None)


@router.post("/exercises")
def create_exercise(body: ExerciseIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if fetch_one(conn, "SELECT 1 FROM core.exercise WHERE lower(name) = lower(%s)", (body.name,)):
        raise bad_request("an exercise with that name exists")
    eid = insert(conn, "core.exercise", body.model_dump(exclude={"muscles"}))
    _save_muscles(conn, eid, body.muscles)
    return {"id": eid}


@router.put("/exercises/{exercise_id}")
def update_exercise(exercise_id: int, body: ExerciseIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = body.model_dump(exclude={"muscles"})
    execute(conn, f"UPDATE core.exercise SET {', '.join(f'{k} = %s' for k in d)} WHERE id = %s",
            (*d.values(), exercise_id))
    _save_muscles(conn, exercise_id, body.muscles)
    return {"id": exercise_id}


@router.get("/exercises/{exercise_id}/last")
def last_session(exercise_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Sets from the most recent session with this exercise, for 'repeat last'."""
    w = fetch_one(conn, """SELECT s.workout_id, max(w.start_ts) AS ts FROM core.lift_set s
                           JOIN core.workout w ON w.id = s.workout_id
                           WHERE s.exercise_id = %s GROUP BY s.workout_id ORDER BY ts DESC LIMIT 1""",
                  (exercise_id,))
    if not w:
        return {"sets": []}
    sets = fetch_all(conn, """SELECT reps, weight_kg, rir, is_warmup FROM core.lift_set
                              WHERE workout_id = %s AND exercise_id = %s ORDER BY set_no""",
                     (w["workout_id"], exercise_id))
    best = fetch_one(conn, "SELECT max(best_e1rm_kg) AS e1rm FROM analytics.lift_progress WHERE exercise_id = %s",
                     (exercise_id,))
    return to_jsonable({"date": w["ts"], "sets": sets, "best_e1rm_kg": best["e1rm"] if best else None})


# ------------------------------------------------------------------ sets
class SetIn(BaseModel):
    exercise_id: int
    reps: int = Field(gt=0, le=100)
    weight_kg: float = Field(ge=0, le=500)
    rir: int | None = Field(2, ge=0, le=10)
    is_warmup: bool = False


@router.post("/workouts/{workout_id}/sets")
def add_set(workout_id: int, body: SetIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    w = _resolve(conn, workout_id)
    nxt = fetch_one(conn, """SELECT coalesce(max(set_no), 0) + 1 AS n FROM core.lift_set
                             WHERE workout_id = %s AND exercise_id = %s""", (w["id"], body.exercise_id))["n"]
    sid = insert(conn, "core.lift_set", {"workout_id": w["id"], "set_no": nxt, **body.model_dump()})
    return {"id": sid, "workout_id": w["id"], "set_no": nxt}


@router.put("/sets/{set_id}")
def update_set(set_id: int, body: SetIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = body.model_dump()
    n = execute(conn, f"UPDATE core.lift_set SET {', '.join(f'{k} = %s' for k in d)} WHERE id = %s",
                (*d.values(), set_id))
    if not n:
        raise not_found()
    return {"ok": True}


@router.delete("/sets/{set_id}")
def delete_set(set_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "DELETE FROM core.lift_set WHERE id = %s", (set_id,))
    return {"ok": True}


# ------------------------------------------------------------------ planned sessions
SessionType = Literal["game", "hard", "moderate", "easy", "strength", "rest"]


class PlannedIn(BaseModel):
    date: Date
    sport: str = Field(min_length=1)
    session_type: SessionType
    planned_duration_min: int | None = Field(None, gt=0, le=600)
    planned_rpe: int | None = Field(None, ge=1, le=10)
    focus_muscles: list[str] | None = None
    status: Literal["planned", "done", "skipped", "modified"] = "planned"
    notes: str | None = None


@router.get("/planned")
def list_planned(start: date | None = None, end: date | None = None,
                 conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    start = start or local_date(conn) - timedelta(days=1)
    end = end or start + timedelta(days=14)
    return to_jsonable(fetch_all(conn, """SELECT * FROM core.planned_session WHERE date BETWEEN %s AND %s
                                          ORDER BY date, session_type = 'game' DESC, id""", (start, end)))


@router.post("/planned")
def create_planned(body: PlannedIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    pid = insert(conn, "core.planned_session", {**body.model_dump(), "origin": "manual"})
    return {"id": pid}


@router.put("/planned/{planned_id}")
def update_planned(planned_id: int, body: PlannedIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = body.model_dump()
    # Editing a generated session makes it mine: it won't be regenerated.
    n = execute(conn, f"""UPDATE core.planned_session SET {', '.join(f'{k} = %s' for k in d)},
                          origin = 'manual' WHERE id = %s""", (*d.values(), planned_id))
    if not n:
        raise not_found()
    return {"ok": True}


@router.delete("/planned/{planned_id}")
def delete_planned(planned_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "DELETE FROM core.planned_session WHERE id = %s", (planned_id,))
    return {"ok": True}
