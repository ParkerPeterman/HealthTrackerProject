"""Lifting features: weekly hard sets per muscle group and best estimated 1RM per main lift.

A hard set is a working set with RIR <= 3. Sets logged without RIR count as
hard (the app defaults RIR to 2, so a blank usually means "a normal working set").
"""

from __future__ import annotations

from datetime import date

import psycopg

from healthtracker.db import execute

HARD_SET_SQL = """
    SELECT s.id AS set_id, s.workout_id, s.exercise_id, w.start_ts,
           (w.start_ts AT TIME ZONE %(tz)s)::date AS date,
           em.muscle_group, em.role, em.set_credit, mg.body_region
    FROM core.lift_set s
    JOIN core.v_workout w ON w.id = s.workout_id
    JOIN core.exercise_muscle em ON em.exercise_id = s.exercise_id
    JOIN core.muscle_group mg ON mg.code = em.muscle_group
    WHERE NOT s.is_warmup AND coalesce(s.rir, 2) <= 3
"""


def build_weekly_muscle_volume(conn: psycopg.Connection, tz: str, since: date | None = None) -> int:
    params = {"tz": tz, "since": since}
    execute(conn, """DELETE FROM analytics.weekly_muscle_volume
                     WHERE %(since)s::date IS NULL
                        OR week_start >= date_trunc('week', %(since)s::date)::date""", params)
    return execute(
        conn,
        f"""INSERT INTO analytics.weekly_muscle_volume (week_start, muscle_group, hard_sets)
            SELECT date_trunc('week', h.date)::date, h.muscle_group, sum(h.set_credit)
            FROM ({HARD_SET_SQL}) h
            WHERE %(since)s::date IS NULL OR h.date >= date_trunc('week', %(since)s::date)::date
            GROUP BY 1, 2""",
        params,
    )


def build_lift_progress(conn: psycopg.Connection, tz: str, since: date | None = None) -> int:
    params = {"tz": tz, "since": since}
    execute(conn, """DELETE FROM analytics.lift_progress
                     WHERE %(since)s::date IS NULL OR date >= %(since)s::date""", params)
    return execute(
        conn,
        """INSERT INTO analytics.lift_progress (date, exercise_id, best_e1rm_kg, top_set_kg, top_set_reps)
           SELECT DISTINCT ON (d, s.exercise_id)
                  (w.start_ts AT TIME ZONE %(tz)s)::date AS d, s.exercise_id,
                  round(s.weight_kg * (1 + s.reps / 30.0), 1), s.weight_kg, s.reps
           FROM core.lift_set s
           JOIN core.v_workout w ON w.id = s.workout_id
           JOIN core.exercise e ON e.id = s.exercise_id AND e.is_main_lift
           WHERE NOT s.is_warmup AND s.reps <= 10 AND s.weight_kg > 0
             AND (%(since)s::date IS NULL OR (w.start_ts AT TIME ZONE %(tz)s)::date >= %(since)s::date)
           ORDER BY d, s.exercise_id, s.weight_kg * (1 + s.reps / 30.0) DESC""",
        params,
    )
