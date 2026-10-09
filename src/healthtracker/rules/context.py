"""Build a RuleContext for one day from the database."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import pandas as pd
import psycopg

from healthtracker.db import fetch_all, fetch_df
from healthtracker.features.daily import estimate_hr_max, impute_rpe
from healthtracker.features.lifting import HARD_SET_SQL
from healthtracker.profile import load_profile
from healthtracker.rules.base import RuleContext


def load_params(conn: psycopg.Connection) -> dict[str, dict[str, float]]:
    params: dict[str, dict[str, float]] = defaultdict(dict)
    for r in fetch_all(conn, "SELECT rule_id, name, value FROM analytics.rule_parameter"):
        params[r["rule_id"]][r["name"]] = float(r["value"])
    return dict(params)


def load_features(conn: psycopg.Connection, end: date, days: int = 120) -> pd.DataFrame:
    df = fetch_df(conn, "SELECT * FROM analytics.daily_features WHERE date BETWEEN %s AND %s ORDER BY date",
                  (end - timedelta(days=days), end))
    if df.empty:
        return df
    return df.set_index("date")


def history_days(features: pd.DataFrame, before: date) -> int:
    """Days of usable history before `before`: any overnight, food, or training data."""
    if features.empty:
        return 0
    f = features[features.index < before]
    has = f["hrv"].notna() | f["sleep_total_min"].notna() | f["food_log_complete"].fillna(False).astype(bool)
    return int(has.sum())


def load_injuries(conn: psycopg.Connection, d: date) -> list[dict]:
    injuries = fetch_all(
        conn,
        """SELECT * FROM core.injury
           WHERE onset_date <= %s AND (status <> 'resolved' OR resolved_date > %s)""",
        (d, d),
    )
    for inj in injuries:
        if inj["status"] == "resolved":  # resolved later: as of `d` it was still healing
            inj["status"] = "recovering"
        inj["checkins"] = fetch_all(
            conn, "SELECT * FROM core.injury_checkin WHERE injury_id = %s AND date <= %s ORDER BY date",
            (inj["id"], d))
        inj["restrictions"] = fetch_all(
            conn,
            """SELECT r.*, e.name AS exercise_name FROM core.injury_restriction r
               LEFT JOIN core.exercise e ON e.id = r.exercise_id WHERE r.injury_id = %s""",
            (inj["id"],))
    return injuries


def nutrients_daily(conn: psycopg.Connection, start: date, end: date) -> pd.DataFrame:
    """Per-day nutrient totals on complete food days: columns plus the micros JSON."""
    df = fetch_df(
        conn,
        """WITH e AS (
             SELECT n.date, n.servings, c.fiber_g, c.sodium_mg, c.sugar_g, c.micros
             FROM core.food_entry n JOIN core.food_catalog c ON c.id = n.catalog_id
             JOIN core.food_day_status s ON s.date = n.date AND s.complete
             WHERE n.date BETWEEN %(s)s AND %(e)s)
           SELECT date, 'fiber_g' AS nutrient, sum(servings * coalesce(fiber_g, 0)) AS amount FROM e GROUP BY date
           UNION ALL
           SELECT date, 'sodium_mg', sum(servings * coalesce(sodium_mg, 0)) FROM e GROUP BY date
           UNION ALL
           SELECT date, 'sugar_g', sum(servings * coalesce(sugar_g, 0)) FROM e GROUP BY date
           UNION ALL
           SELECT e.date, m.key, sum(e.servings * (m.value)::numeric)
           FROM e, jsonb_each_text(coalesce(e.micros, '{}'::jsonb)) m
           WHERE m.value ~ '^[0-9.]+$' GROUP BY e.date, m.key""",
        {"s": start, "e": end},
    )
    return df


def nutrient_coverage(conn: psycopg.Connection, start: date, end: date) -> dict[str, float]:
    """Share of logged calories (complete days) from foods that record each nutrient.

    Foods without a value count as zero intake, so a low share means a "gap" may just be
    missing label data (common for Open Food Facts items).
    """
    rows = fetch_all(
        conn,
        """WITH e AS (
             SELECT n.servings * coalesce(c.kcal, 0) AS kcal, c.fiber_g, c.sodium_mg, c.sugar_g, c.micros
             FROM core.food_entry n JOIN core.food_catalog c ON c.id = n.catalog_id
             JOIN core.food_day_status s ON s.date = n.date AND s.complete
             WHERE n.date BETWEEN %s AND %s),
           t AS (SELECT nullif(sum(kcal), 0) AS total FROM e)
           SELECT 'fiber_g' AS nutrient, sum(kcal) FILTER (WHERE fiber_g IS NOT NULL) / (SELECT total FROM t) AS share FROM e
           UNION ALL SELECT 'sodium_mg', sum(kcal) FILTER (WHERE sodium_mg IS NOT NULL) / (SELECT total FROM t) FROM e
           UNION ALL SELECT 'sugar_g', sum(kcal) FILTER (WHERE sugar_g IS NOT NULL) / (SELECT total FROM t) FROM e
           UNION ALL SELECT k.key, sum(e.kcal) / (SELECT total FROM t)
           FROM e, jsonb_object_keys(coalesce(e.micros, '{}'::jsonb)) AS k(key) GROUP BY k.key""",
        (start, end))
    return {r["nutrient"]: float(r["share"] or 0) for r in rows}


def build_context(conn: psycopg.Connection, d: date) -> RuleContext:
    profile = load_profile(conn)
    tz = profile.timezone
    features = load_features(conn, d)
    params = load_params(conn)

    planned = fetch_all(
        conn, "SELECT * FROM core.planned_session WHERE date BETWEEN %s AND %s ORDER BY date",
        (d - timedelta(days=1), d + timedelta(days=7)))

    workouts = fetch_df(
        conn,
        """SELECT id, source, sport, is_game, rpe, duration_s, distance_m, avg_hr, start_ts,
                  (start_ts AT TIME ZONE %(tz)s)::date AS date
           FROM core.v_workout WHERE start_ts >= %(since)s::date - 1""",
        {"tz": tz, "since": d - timedelta(days=35)})
    if not workouts.empty:
        hr_max = estimate_hr_max(conn, profile, d)
        workouts["rpe_eff"] = [r if pd.notna(r) else impute_rpe(h, hr_max)
                               for r, h in zip(workouts["rpe"], workouts["avg_hr"], strict=False)]
        workouts["is_game"] = workouts["is_game"].astype(bool)

    hard_sets = fetch_df(conn, f"SELECT * FROM ({HARD_SET_SQL}) h WHERE h.date BETWEEN %(s)s AND %(e)s",
                         {"tz": tz, "s": d - timedelta(days=35), "e": d})
    if not hard_sets.empty:
        hard_sets["start_ts"] = pd.to_datetime(hard_sets["start_ts"], utc=True)
        hard_sets["set_credit"] = hard_sets["set_credit"].astype(float)
    lifted = fetch_all(
        conn,
        """SELECT 1 FROM core.lift_set s JOIN core.v_workout w ON w.id = s.workout_id
           WHERE w.start_ts >= %s::date - 14 AND w.start_ts < %s::date LIMIT 1""",
        (d, d))

    targets = fetch_all(
        conn, "SELECT * FROM core.nutrient_target WHERE active AND valid_from <= %s", (d,))

    lift_progress = fetch_df(
        conn,
        """SELECT p.date, p.exercise_id, e.name, p.best_e1rm_kg FROM analytics.lift_progress p
           JOIN core.exercise e ON e.id = p.exercise_id WHERE p.date BETWEEN %s AND %s""",
        (d - timedelta(days=7 * 12), d))

    experiments = fetch_all(
        conn,
        """SELECT * FROM analytics.experiment WHERE status = 'running'
           AND start_date <= %s AND (end_date IS NULL OR end_date >= %s)""", (d, d))

    last_fired = {r["rule_id"]: r["d"] for r in fetch_all(
        conn, """SELECT rule_id, max(date) AS d FROM analytics.daily_flag
                 WHERE fired AND date < %s AND date >= %s GROUP BY rule_id""", (d, d - timedelta(days=14)))}

    wake = fetch_all(
        conn,
        """SELECT extract(epoch FROM (sleep_end AT TIME ZONE %(tz)s) - date::timestamp) / 3600.0 AS h
           FROM core.sleep WHERE date BETWEEN %(s)s AND %(e)s AND sleep_end IS NOT NULL""",
        {"tz": tz, "s": d - timedelta(days=14), "e": d})
    wake_hours = sorted(float(w["h"]) for w in wake if w["h"] is not None)
    typical_wake = wake_hours[len(wake_hours) // 2] if wake_hours else None

    return RuleContext(
        date=d,
        features=features,
        params=params,
        profile=profile,
        planned=planned,
        workouts=workouts,
        hard_sets=hard_sets,
        lifted_recently=bool(lifted),
        injuries=load_injuries(conn, d),
        nutrient_targets=targets,
        nutrients_daily=nutrients_daily(conn, d - timedelta(days=14), d),
        nutrient_coverage=nutrient_coverage(conn, d - timedelta(days=7), d - timedelta(days=1)),
        lift_progress=lift_progress,
        experiments=experiments,
        typical_wake_hour=typical_wake,
        last_fired=last_fired,
        history_days=history_days(features, d),
    )
