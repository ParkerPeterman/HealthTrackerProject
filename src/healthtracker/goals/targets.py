"""Goals -> nutrient targets, and the weekly goal check.

A body-weight goal sets the calorie and protein targets. Setting them from the
goal screen is my explicit choice; after that, the weekly job re-estimates what I
burn from my own logs and proposes a new target when it drifts. Nothing changes
until I approve, and every change lands in analytics.target_proposal.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import psycopg

from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.goals.energy import (
    BAND_KCAL,
    CaloriePlan,
    Expenditure,
    from_data,
    from_formula,
    plan_for,
    projected_date,
)
from healthtracker.goals.metrics import current_value
from healthtracker.goals.progress import goal_progress, planned_rate
from healthtracker.profile import Profile, load_profile
from healthtracker.stats.insights import load_features_frame
from healthtracker.units import Display

KCAL_CHANGE_TO_PROPOSE = 100      # re-propose when the plan moves at least this much
PROTEIN_CHANGE_TO_PROPOSE = 0.1   # g/kg


def active_weight_goal(conn: psycopg.Connection) -> dict | None:
    return fetch_one(conn, "SELECT * FROM core.goal WHERE active AND goal_type = 'body_weight' ORDER BY id DESC LIMIT 1")


def weekly_training_minutes(conn: psycopg.Connection, as_of: date, days: int = 28) -> float:
    row = fetch_one(conn, """SELECT coalesce(sum(duration_s), 0) / 60.0 AS m FROM core.v_workout
                             WHERE start_ts::date > %s AND start_ts::date <= %s""",
                    (as_of - timedelta(days=days), as_of))
    return float(row["m"]) * 7 / days


def expenditure(conn: psycopg.Connection, features: pd.DataFrame, profile: Profile, weight_kg: float | None,
                as_of: date) -> Expenditure | None:
    """My own data when there's enough of it, otherwise the formula."""
    return from_data(features, as_of) or from_formula(profile, weight_kg, as_of, weekly_training_minutes(conn, as_of))


def plan_for_goal(conn: psycopg.Connection, rate_kg_per_week: float, as_of: date,
                  features: pd.DataFrame | None = None, target_kg: float | None = None) -> dict:
    """Everything the goal screen shows before saving: expenditure, calorie plan, projection."""
    features = load_features_frame(conn, as_of) if features is None else features
    profile = load_profile(conn)
    weight = current_value(conn, features, "weight_kg", as_of)
    exp = expenditure(conn, features, profile, weight, as_of)
    out: dict = {"current_weight_kg": weight, "expenditure": exp.as_dict() if exp else None, "plan": None,
                 "projected_date": None}
    if weight is None:
        out["missing"] = "Log a morning weight first (Today tab)."
        return out
    if exp is None:
        out["missing"] = "Add your height, birth date, and sex in Settings, or log two weeks of complete food days."
        return out
    plan = plan_for(exp, rate_kg_per_week, weight, profile.sex)
    out["plan"] = plan.as_dict()
    if target_kg is not None:
        when = projected_date(as_of, weight, target_kg, plan.expected_rate)
        out["projected_date"] = when.isoformat() if when else None
    return out


def current_targets(conn: psycopg.Connection) -> dict[str, dict | None]:
    rows = fetch_all(conn, "SELECT * FROM core.nutrient_target WHERE active AND nutrient IN ('kcal', 'protein_g')")
    return {n: next((r for r in rows if r["nutrient"] == n), None) for n in ("kcal", "protein_g")}


def _set_target(conn: psycopg.Connection, nutrient: str, basis: str, lo: float | None, hi: float | None,
                source: str, as_of: date) -> None:
    execute(conn, "UPDATE core.nutrient_target SET active = false WHERE nutrient = %s AND active", (nutrient,))
    insert(conn, "core.nutrient_target", {"nutrient": nutrient, "basis": basis, "evaluation": "daily", "min_value": lo,
                                          "max_value": hi, "source_ref": source, "valid_from": as_of}, returning=None)


def _changes(plan: CaloriePlan, current: dict[str, dict | None]) -> list[dict]:
    """The target rows this plan would write, with what they replace."""
    kcal, protein = current["kcal"], current["protein_g"]
    return [
        {"nutrient": "kcal", "basis": "per_day", "new_min": plan.kcal_min, "new_max": plan.kcal_max,
         "old_min": kcal and kcal["min_value"], "old_max": kcal and kcal["max_value"]},
        {"nutrient": "protein_g", "basis": "per_kg_bodyweight", "new_min": plan.protein_g_per_kg, "new_max": None,
         "old_min": protein and protein["min_value"], "old_max": protein and protein["max_value"],
         "old_basis": protein and protein["basis"]},
    ]


def _differs(change: dict) -> bool:
    if change["old_min"] is None and change["old_max"] is None:
        return True
    if change["nutrient"] == "kcal":
        old_mid = ((change["old_min"] or change["old_max"]) + (change["old_max"] or change["old_min"])) / 2
        return abs(old_mid - (change["new_min"] + BAND_KCAL)) >= KCAL_CHANGE_TO_PROPOSE
    return change.get("old_basis") != "per_kg_bodyweight" or abs((change["old_min"] or 0) - change["new_min"]) >= PROTEIN_CHANGE_TO_PROPOSE


def explain(exp: Expenditure, plan: CaloriePlan, goal: dict, display: Display) -> str:
    w = display.weight_unit
    rate = display.weight(abs(plan.rate_kg_per_week), 2)
    if exp.source == "data":
        trend = display.weight(exp.trend_kg_per_week, 2)
        basis = (f"Over the last {exp.window_days} days you averaged {exp.intake_kcal:,.0f} kcal on {exp.logged_days} "
                 f"complete days while your trend weight moved {trend:+.2f} {w}/week, so you burn about "
                 f"{exp.kcal:,.0f} kcal a day.")
    else:
        basis = (f"From your height, age, and recent training (activity factor {exp.activity_factor}), you burn "
                 f"roughly {exp.kcal:,.0f} kcal a day. Two weeks of complete food logs will replace this estimate.")
    if plan.rate_kg_per_week < 0:
        aim = f"To lose {rate} {w}/week toward {display.weight(goal['target_value'])} {w}"
    elif plan.rate_kg_per_week > 0:
        aim = f"To gain {rate} {w}/week toward {display.weight(goal['target_value'])} {w}"
    else:
        aim = "To hold your weight"
    out = f"{basis} {aim}, eat {plan.kcal_min:,}-{plan.kcal_max:,} kcal with {plan.protein_g_per_kg:g} g/kg protein."
    if plan.capped:
        out += " (Pace capped at a safe rate.)"
    if plan.floor_applied:
        out += " (Held at the minimum calorie floor.)"
    return out


def apply_goal_targets(conn: psycopg.Connection, goal: dict, as_of: date) -> dict | None:
    """Set calorie and protein targets from a weight goal now (I asked for it on the goal screen)."""
    features = load_features_frame(conn, as_of)
    profile = load_profile(conn)
    weight = current_value(conn, features, "weight_kg", as_of)
    exp = expenditure(conn, features, profile, weight, as_of)
    rate = planned_rate(goal, weight, as_of)
    if exp is None or weight is None or rate is None:
        return None
    plan = plan_for(exp, rate, weight, profile.sex)
    reason = explain(exp, plan, goal, Display(profile.display_units))
    # Targets set from the goal supersede anything the weekly check proposed earlier.
    execute(conn, "DELETE FROM analytics.target_proposal WHERE approved IS NULL")
    for c in _changes(plan, current_targets(conn)):
        _set_target(conn, c["nutrient"], c["basis"], c["new_min"], c["new_max"], "weight goal", as_of)
        insert(conn, "analytics.target_proposal", {
            "goal_id": goal["id"], "nutrient": c["nutrient"], "basis": c["basis"], "old_min": c["old_min"],
            "old_max": c["old_max"], "new_min": c["new_min"], "new_max": c["new_max"], "reason": reason,
            "details": {"expenditure": exp.as_dict(), "plan": plan.as_dict()}, "proposed_by": "manual",
            "approved": True}, returning=None)
    return {"plan": plan.as_dict(), "expenditure": exp.as_dict(), "reason": reason}


def run_goal_check(conn: psycopg.Connection, as_of: date) -> int:
    """Weekly: mark achieved goals, and propose calorie/protein targets from my own energy data."""
    features = load_features_frame(conn, as_of)
    n = 0
    for g in fetch_all(conn, "SELECT * FROM core.goal WHERE active AND achieved_date IS NULL"):
        if goal_progress(conn, g, features, as_of)["status"] == "achieved":
            execute(conn, "UPDATE core.goal SET achieved_date = %s WHERE id = %s", (as_of, g["id"]))
            n += 1
    goal = active_weight_goal(conn)
    if not goal or goal.get("achieved_date"):
        return n
    exp = from_data(features, as_of)  # proposals only from my own data, never the formula
    weight = current_value(conn, features, "weight_kg", as_of)
    rate = planned_rate(goal, weight, as_of)
    if exp is None or weight is None or rate is None:
        return n
    profile = load_profile(conn)
    plan = plan_for(exp, rate, weight, profile.sex)
    reason = explain(exp, plan, goal, Display(profile.display_units))
    for c in _changes(plan, current_targets(conn)):
        pending = fetch_one(conn, "SELECT id FROM analytics.target_proposal WHERE nutrient = %s AND approved IS NULL",
                            (c["nutrient"],))
        if not _differs(c):
            if pending:  # the plan came back in line: withdraw the stale proposal
                execute(conn, "DELETE FROM analytics.target_proposal WHERE id = %s", (pending["id"],))
            continue
        row = {"goal_id": goal["id"], "nutrient": c["nutrient"], "basis": c["basis"], "old_min": c["old_min"],
               "old_max": c["old_max"], "new_min": c["new_min"], "new_max": c["new_max"], "reason": reason,
               "details": {"expenditure": exp.as_dict(), "plan": plan.as_dict()}, "proposed_by": "goal_job"}
        if pending:  # one pending proposal per nutrient: replace it with this week's numbers
            execute(conn, "DELETE FROM analytics.target_proposal WHERE id = %s", (pending["id"],))
        insert(conn, "analytics.target_proposal", {**row, "approved": None}, returning=None)
        n += 1
    return n


def pending_target_proposals(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, """SELECT p.*, g.description AS goal_description FROM analytics.target_proposal p
                              LEFT JOIN core.goal g ON g.id = p.goal_id
                              WHERE p.approved IS NULL ORDER BY p.changed_at DESC""")


def decide_target(conn: psycopg.Connection, proposal_id: int, approve: bool, as_of: date) -> dict:
    row = fetch_one(conn, "SELECT * FROM analytics.target_proposal WHERE id = %s", (proposal_id,))
    if not row or row["approved"] is not None:
        raise ValueError("no pending target proposal with that id")
    if approve:
        _set_target(conn, row["nutrient"], row["basis"], row["new_min"], row["new_max"], "weight goal (weekly check)", as_of)
    execute(conn, "UPDATE analytics.target_proposal SET approved = %s, changed_at = now() WHERE id = %s",
            (approve, proposal_id))
    return row
