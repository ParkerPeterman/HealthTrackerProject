"""Today screen, recommendations and ratings, approvals, rules, experiments, pipeline."""

from __future__ import annotations

import threading
from datetime import date, timedelta
from datetime import date as Date
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from healthtracker.api.deps import bad_request, get_conn, local_date, not_found
from healthtracker.api.routes.goals import goals_summary
from healthtracker.db import execute, fetch_all, fetch_one
from healthtracker.goals.targets import decide_target, pending_target_proposals
from healthtracker.jsonutil import to_jsonable
from healthtracker.stats.experiments import create_experiment, protocol_for
from healthtracker.stats.promotion import decide_status, set_status
from healthtracker.stats.tuning import decide_parameter, pending_parameter_proposals, set_parameter

router = APIRouter(prefix="/api", tags=["coach"])


def _recommendation(conn: psycopg.Connection, rec: dict | None) -> dict | None:
    if not rec:
        return None
    rec["actions"] = fetch_all(conn, """SELECT a.*, r.domain FROM analytics.recommendation_action a
                                        JOIN analytics.rule r USING (rule_id)
                                        WHERE a.recommendation_id = %s AND a.visible ORDER BY a.id""", (rec["id"],))
    rec["flags"] = [f for f in (rec.get("decisions") or {}).get("fired", []) if f.get("visible")]
    rec.pop("input_snapshot", None)
    return rec


@router.get("/today")
def today(day: date | None = None, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = day or local_date(conn)
    rec = fetch_one(conn, "SELECT * FROM analytics.recommendation WHERE date = %s AND kind = 'daily'", (d,))
    status = (rec or {}).get("input_snapshot", {}).get("status") if rec else None
    week = fetch_one(conn, """SELECT id, date, rendered_text FROM analytics.recommendation
                              WHERE kind = 'weekly' AND date <= %s ORDER BY date DESC LIMIT 1""", (d + timedelta(days=1),))
    food = fetch_one(conn, """SELECT round(sum(kcal)) AS kcal, round(sum(protein_g)) AS protein_g, count(*) AS n
                              FROM core.v_food_nutrients WHERE date = %s""", (d,))
    food_status = fetch_one(conn, "SELECT complete FROM core.food_day_status WHERE date = %s", (d,))
    yesterday_status = fetch_one(conn, "SELECT complete FROM core.food_day_status WHERE date = %s",
                                 (d - timedelta(days=1),))
    tz_row = fetch_one(conn, "SELECT timezone FROM core.profile WHERE id = 1")
    weight = fetch_one(conn, """SELECT weight_kg, ts FROM core.body_measurement
                                WHERE (ts AT TIME ZONE %s)::date = %s ORDER BY ts LIMIT 1""", (tz_row["timezone"], d))
    planned = fetch_all(conn, """SELECT * FROM core.planned_session WHERE date = %s AND status <> 'skipped'
                                 ORDER BY session_type = 'game' DESC""", (d,))
    injuries = fetch_all(conn, """SELECT i.id, i.body_part, i.side, i.status,
                                         EXISTS (SELECT 1 FROM core.injury_checkin c
                                                 WHERE c.injury_id = i.id AND c.date = %s) AS checked_in
                                  FROM core.injury i WHERE i.status <> 'resolved'""", (d,))
    pending = fetch_one(conn, """SELECT (SELECT count(*) FROM analytics.rule_parameter_history WHERE approved IS NULL)
                                      + (SELECT count(*) FROM analytics.rule_status_change WHERE approved IS NULL)
                                      + (SELECT count(*) FROM analytics.target_proposal WHERE approved IS NULL) AS n""")
    last_run = fetch_one(conn, """
        SELECT r.job, min(r.started_at) AS started_at, max(r.finished_at) AS finished_at,
               bool_and(coalesce(r.status, 'ok') = 'ok') AS ok,
               string_agg(r.step, ', ') FILTER (WHERE r.status = 'failed') AS failed_steps
        FROM analytics.pipeline_run r,
             (SELECT job, started_at FROM analytics.pipeline_run ORDER BY id DESC LIMIT 1) l
        WHERE r.job = l.job AND r.started_at > l.started_at - interval '30 minutes'
        GROUP BY r.job""")
    return to_jsonable({
        "date": d,
        "recommendation": _recommendation(conn, rec),
        "status": status,
        "week_plan": week,
        "journal": fetch_one(conn, "SELECT * FROM core.journal WHERE date = %s", (d,)),
        "weight": weight,
        "food": {**(food or {}), "complete": bool(food_status and food_status["complete"]),
                 "yesterday_complete": bool(yesterday_status and yesterday_status["complete"])},
        "planned": planned,
        "injuries": injuries,
        "pending_proposals": pending["n"] if pending else 0,
        "goals": goals_summary(conn, d),
        "last_run": last_run,
    })


@router.get("/recommendations")
def recommendations(limit: int = 30, kind: Literal["daily", "weekly"] = "daily",
                    conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    recs = fetch_all(conn, """SELECT * FROM analytics.recommendation WHERE kind = %s
                              ORDER BY date DESC LIMIT %s""", (kind, limit))
    return to_jsonable([_recommendation(conn, r) for r in recs])


class RatingIn(BaseModel):
    rating: int = Field(ge=1, le=5)


@router.put("/recommendations/{rec_id}/rating")
def rate(rec_id: int, body: RatingIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if not execute(conn, "UPDATE analytics.recommendation SET rating = %s WHERE id = %s", (body.rating, rec_id)):
        raise not_found()
    return {"ok": True}


class NoteIn(BaseModel):
    user_note: str | None = None


@router.put("/actions/{action_id}/note")
def action_note(action_id: int, body: NoteIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "UPDATE analytics.recommendation_action SET user_note = %s WHERE id = %s",
            (body.user_note, action_id))
    return {"ok": True}


# ------------------------------------------------------------------ approvals and rules
@router.get("/proposals")
def proposals(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return to_jsonable({
        "parameters": pending_parameter_proposals(conn),
        "targets": pending_target_proposals(conn),
        "statuses": fetch_all(conn, """SELECT c.*, r.description, r.evidence_level FROM analytics.rule_status_change c
                                       JOIN analytics.rule r USING (rule_id) WHERE c.approved IS NULL
                                       ORDER BY c.changed_at DESC"""),
        "history": fetch_all(conn, """SELECT 'parameter' AS kind, rule_id, name, old_value::text AS old,
                                             new_value::text AS new, approved, proposed_by, reason, changed_at
                                      FROM analytics.rule_parameter_history WHERE approved IS NOT NULL
                                      UNION ALL
                                      SELECT 'status', rule_id, NULL, old_status, new_status, approved, proposed_by,
                                             reason, changed_at
                                      FROM analytics.rule_status_change WHERE approved IS NOT NULL
                                      ORDER BY changed_at DESC LIMIT 30"""),
    })


class DecisionIn(BaseModel):
    approve: bool


@router.post("/proposals/parameter/{proposal_id}")
def decide_param(proposal_id: int, body: DecisionIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    try:
        decide_parameter(conn, proposal_id, body.approve)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    return {"ok": True}


@router.post("/proposals/target/{proposal_id}")
def decide_nutrient_target(proposal_id: int, body: DecisionIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    try:
        decide_target(conn, proposal_id, body.approve, local_date(conn))
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    return {"ok": True}


@router.post("/proposals/status/{proposal_id}")
def decide_rule_status(proposal_id: int, body: DecisionIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    try:
        decide_status(conn, proposal_id, body.approve)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    return {"ok": True}


@router.get("/rules")
def rules(conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    rows = fetch_all(conn, """SELECT r.*, p.n_actions, p.followed_rate, p.avg_outcome_when_followed,
                                     p.avg_outcome_when_not
                              FROM analytics.rule r LEFT JOIN analytics.v_rule_performance p USING (rule_id)
                              ORDER BY r.domain, r.rule_id""")
    params = fetch_all(conn, "SELECT * FROM analytics.rule_parameter ORDER BY rule_id, name")
    for r in rows:
        r["parameters"] = [p for p in params if p["rule_id"] == r["rule_id"]]
    return to_jsonable(rows)


class StatusIn(BaseModel):
    status: Literal["shadow", "active", "retired"]


@router.put("/rules/{rule_id}/status")
def put_rule_status(rule_id: str, body: StatusIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    try:
        set_status(conn, rule_id, body.status)
    except ValueError as exc:
        raise not_found(str(exc)) from exc
    return {"ok": True}


class ParamIn(BaseModel):
    value: float


@router.put("/rules/{rule_id}/parameters/{name}")
def put_param(rule_id: str, name: str, body: ParamIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    p = fetch_one(conn, "SELECT * FROM analytics.rule_parameter WHERE rule_id = %s AND name = %s", (rule_id, name))
    if not p:
        raise not_found("unknown parameter")
    if (p["min_allowed"] is not None and body.value < p["min_allowed"]) or \
            (p["max_allowed"] is not None and body.value > p["max_allowed"]):
        raise bad_request(f"value must be within [{p['min_allowed']}, {p['max_allowed']}]")
    set_parameter(conn, rule_id, name, body.value, "manual change in settings")
    return {"ok": True}


# ------------------------------------------------------------------ insights and experiments
@router.get("/hypotheses")
def hypotheses(conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    rows = fetch_all(conn, """SELECT h.*, i.status AS insight_status, i.summary, i.effect_size, i.ci_low, i.ci_high,
                                     i.p_adjusted, i.n_exposed, i.n_unexposed, i.replicated, i.computed_at
                              FROM analytics.hypothesis h
                              LEFT JOIN LATERAL (SELECT * FROM analytics.insight x WHERE x.hypothesis_id = h.id
                                                 ORDER BY computed_at DESC LIMIT 1) i ON true
                              ORDER BY i.status = 'supported' DESC NULLS LAST, i.p_adjusted NULLS LAST, h.id""")
    for r in rows:
        try:
            protocol_for(r["exposure"], r["outcome"], r["lag_days"])
            r["can_experiment"] = True
        except ValueError:
            r["can_experiment"] = False
    return to_jsonable(rows)


@router.get("/experiments")
def experiments(conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    return to_jsonable(fetch_all(conn, """SELECT e.*, h.exposure, h.outcome FROM analytics.experiment e
                                          LEFT JOIN analytics.hypothesis h ON h.id = e.hypothesis_id
                                          ORDER BY e.start_date DESC"""))


class ExperimentIn(BaseModel):
    hypothesis_id: int
    start_date: date | None = None
    days: int = Field(14, ge=14, le=56)


@router.post("/experiments")
def start_experiment(body: ExperimentIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    running = fetch_one(conn, "SELECT 1 FROM analytics.experiment WHERE status IN ('planned','running')")
    if running:
        raise bad_request("finish or abandon the current experiment first (one at a time keeps results clean)")
    try:
        eid = create_experiment(conn, body.hypothesis_id, body.start_date or local_date(conn) + timedelta(days=1),
                                body.days)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    return {"id": eid}


@router.post("/experiments/{experiment_id}/abandon")
def abandon_experiment(experiment_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "UPDATE analytics.experiment SET status = 'abandoned' WHERE id = %s", (experiment_id,))
    return {"ok": True}


# ------------------------------------------------------------------ pipeline
_running = threading.Lock()


class RunIn(BaseModel):
    job: Literal["nightly", "weekly"] = "nightly"
    date: Date | None = None
    skip_ingest: bool = False


@router.post("/pipeline/run")
def run_pipeline(body: RunIn) -> dict:
    """Kick off a job in the background (e.g. after the watch syncs). One at a time."""
    from healthtracker.db import current_url, use_database
    from healthtracker.pipeline.jobs import nightly, weekly

    if not _running.acquire(blocking=False):
        raise bad_request("a job is already running")
    url = current_url()

    def work() -> None:
        try:
            use_database(url)
            if body.job == "nightly":
                nightly(body.date, skip_ingest=body.skip_ingest)
            else:
                weekly(body.date)
        finally:
            _running.release()

    threading.Thread(target=work, daemon=True).start()
    return {"started": body.job}


@router.get("/pipeline/runs")
def pipeline_runs(limit: int = 30, conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    return to_jsonable(fetch_all(conn, "SELECT * FROM analytics.pipeline_run ORDER BY id DESC LIMIT %s", (limit,)))


@router.get("/pipeline/status")
def pipeline_status() -> dict:
    return {"running": _running.locked()}
