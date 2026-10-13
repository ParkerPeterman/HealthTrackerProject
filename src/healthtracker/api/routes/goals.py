"""Goals: weight goals that set nutrition targets, plus tracked performance, health, and habit goals."""

from __future__ import annotations

from datetime import date
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from healthtracker.api.deps import bad_request, get_conn, local_date, not_found
from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.goals.metrics import METRICS, best_e1rm_kg, current_value
from healthtracker.goals.progress import goal_progress
from healthtracker.goals.targets import apply_goal_targets, plan_for_goal
from healthtracker.jsonutil import to_jsonable
from healthtracker.stats.insights import load_features_frame

router = APIRouter(prefix="/api", tags=["goals"])

STATUS_ORDER = {"behind": 0, "on_track": 1, "ahead": 1, "in_progress": 2, "no_data": 3, "not_tracked": 4, "achieved": 5}


class GoalIn(BaseModel):
    goal_type: Literal["body_weight", "performance", "health", "habit"]
    description: str = Field(min_length=1, max_length=120)
    metric: str | None = None
    direction: Literal["decrease", "increase", "maintain"] | None = None
    target_value: float | None = None
    unit: str | None = None
    target_date: date | None = None
    rate_per_week: float | None = Field(None, ge=-2, le=2)   # kg/week
    days_per_week: int | None = Field(None, ge=1, le=7)
    exercise_id: int | None = None
    start_value: float | None = None
    active: bool = True
    apply_targets: bool = False   # weight goals: also set calorie and protein targets now

    @model_validator(mode="after")
    def check(self) -> GoalIn:
        if self.goal_type == "body_weight":
            self.metric = "weight_kg"
            if self.direction != "maintain" and self.target_value is None:
                raise ValueError("a weight goal needs a target weight")
            if self.direction == "maintain":
                self.rate_per_week = 0.0
            elif self.rate_per_week and (self.rate_per_week > 0) != (self.direction == "increase"):
                raise ValueError("the pace has to point the same way as the goal")
        if self.metric is not None:
            spec = METRICS.get(self.metric)
            if spec is None or self.goal_type not in spec.goal_types:
                raise ValueError(f"{self.metric} isn't a {self.goal_type.replace('_', ' ')} metric")
            self.unit = self.unit or spec.unit
            if self.metric == "e1rm_kg" and self.exercise_id is None:
                raise ValueError("pick the lift for an estimated-1RM goal")
            if spec.kind == "daily" and self.metric not in ("food_log_complete", "training_day") and self.target_value is None:
                raise ValueError("set the daily target for this habit")
        return self


def _with_progress(conn: psycopg.Connection, goals: list[dict], as_of: date) -> list[dict]:
    features = load_features_frame(conn, as_of)
    for g in goals:
        g["progress"] = goal_progress(conn, g, features, as_of)
    return goals


def goals_summary(conn: psycopg.Connection, as_of: date) -> list[dict]:
    """Active goals for the Today screen, most in need of attention first."""
    goals = _with_progress(conn, fetch_all(conn, "SELECT * FROM core.goal WHERE active ORDER BY id"), as_of)
    goals.sort(key=lambda g: STATUS_ORDER.get(g["progress"]["status"], 9))
    return [{"id": g["id"], "goal_type": g["goal_type"], "description": g["description"], "metric": g["metric"],
             "status": g["progress"]["status"], "pct": g["progress"].get("pct")} for g in goals]


@router.get("/goals")
def list_goals(include_inactive: bool = True, day: date | None = None,
               conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    rows = fetch_all(conn, "SELECT * FROM core.goal WHERE active OR %s ORDER BY active DESC, id", (include_inactive,))
    return to_jsonable(_with_progress(conn, rows, day or local_date(conn)))


@router.get("/goals/metrics")
def goal_metrics(day: date | None = None, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """What goals can track, with today's values, so the goal form can prefill sensible targets."""
    today = day or local_date(conn)
    features = load_features_frame(conn, today)
    metrics = []
    for m in METRICS.values():
        cur = current_value(conn, features, m.key, today) if m.kind == "level" and m.key != "e1rm_kg" else None
        metrics.append({"key": m.key, "label": m.label, "unit": m.unit, "better": m.better, "kind": m.kind,
                        "goal_types": list(m.goal_types), "current": cur})
    lifts = fetch_all(conn, "SELECT id, name FROM core.exercise WHERE is_main_lift ORDER BY name")
    for lift in lifts:
        lift["best_e1rm_kg"] = best_e1rm_kg(conn, lift["id"], today)
    return to_jsonable({"metrics": metrics, "lifts": lifts})


@router.get("/goals/plan")
def goal_plan(rate_kg_per_week: float = 0.0, target_kg: float | None = None, day: date | None = None,
              conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """Preview for the weight-goal form: what I burn, what to eat at this pace, and when I'd get there."""
    if not -2 <= rate_kg_per_week <= 2:
        raise bad_request("rate must be between -2 and 2 kg/week")
    return to_jsonable(plan_for_goal(conn, rate_kg_per_week, day or local_date(conn), target_kg=target_kg))


def _save(conn: psycopg.Connection, body: GoalIn, goal_id: int | None, day: date | None) -> dict:
    today = day or local_date(conn)
    data = body.model_dump(exclude={"apply_targets"})
    if body.active and body.goal_type == "body_weight":
        # One weight goal at a time: a new one replaces the old.
        execute(conn, """UPDATE core.goal SET active = false
                         WHERE active AND goal_type = 'body_weight' AND id IS DISTINCT FROM %s""", (goal_id,))
    if body.metric and data["start_value"] is None and METRICS[body.metric].kind == "level":
        data["start_value"] = current_value(conn, load_features_frame(conn, today), body.metric, today, body.exercise_id)
    if body.goal_type == "body_weight" and body.direction == "maintain" and data["target_value"] is None:
        data["target_value"] = data["start_value"]
    if goal_id is None:
        goal_id = insert(conn, "core.goal", {**data, "start_date": today})
    else:
        if not execute(conn, f"UPDATE core.goal SET {', '.join(f'{k} = %s' for k in data)} WHERE id = %s",
                       (*data.values(), goal_id)):
            raise not_found()
    goal = fetch_one(conn, "SELECT * FROM core.goal WHERE id = %s", (goal_id,))
    applied = apply_goal_targets(conn, goal, today) if body.apply_targets and body.goal_type == "body_weight" else None
    goal = _with_progress(conn, [goal], today)[0]
    return to_jsonable({**goal, "applied_targets": applied})


@router.post("/goals")
def create_goal(body: GoalIn, day: date | None = None, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return _save(conn, body, None, day)


@router.put("/goals/{goal_id}")
def update_goal(goal_id: int, body: GoalIn, day: date | None = None,
                conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return _save(conn, body, goal_id, day)


@router.delete("/goals/{goal_id}")
def delete_goal(goal_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "DELETE FROM core.goal WHERE id = %s", (goal_id,))
    return {"ok": True}
