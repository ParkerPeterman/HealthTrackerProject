"""Write the day's recommendation, its structured decisions, and its actions.

Shadow rules produce actions too (visible = false). They are graded the same
way, so a rule proves itself before it is ever shown.
"""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

from healthtracker.db import execute, fetch_all, insert, upsert
from healthtracker.jsonutil import to_jsonable
from healthtracker.rules.base import RuleContext
from healthtracker.rules.engine import Evaluated
from healthtracker.units import Display, fmt_hour

SEVERITY = {1: "info", 2: "caution", 3: "important"}


def _r(v, nd=0):
    if v is None:
        return None
    return int(round(v)) if nd == 0 else round(float(v), nd)


def _hm(minutes) -> str | None:
    if minutes is None:
        return None
    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}"


def build_status(ctx: RuleContext) -> dict:
    """Today's numbers in display units, as the LLM and dashboard see them."""
    d = Display(ctx.profile.display_units)
    t, y = ctx.today, ctx.yesterday
    status = {
        "recovery": {
            "hrv_ms": _r(t.get("hrv")),
            "hrv_vs_baseline_sd": _r(t.get("hrv_z_30d"), 1),
            "hrv_30d_avg_ms": _r(t.get("hrv_30d_avg")),
            "resting_hr_bpm": _r(t.get("resting_hr")),
            "resting_hr_vs_baseline_sd": _r(t.get("rhr_z_30d"), 1),
            "sleep_last_night": _hm(t.get("sleep_total_min")),
            "sleep_score": _r(t.get("sleep_score")),
        },
        "training": {
            "load_yesterday": _r(y.get("session_load")),
            "load_7d": _r(y.get("acute_load_7d")),
            "weekly_avg_load_28d": _r(y.get("chronic_load_28d")),
            "acute_chronic_ratio": _r(y.get("acwr"), 2),
            "days_since_game": _r(y.get("days_since_game")),
            "next_game_in_days": _r(t.get("next_game_in_days")),
            f"run_{d.distance_unit}_7d": d.distance_km(y.get("run_km_7d")),
            f"pace_at_ref_hr{d.pace_unit}": d.pace(y.get("pace_at_ref_hr_s_km")),
            "vo2max": _r(y.get("vo2max"), 1),
        },
        "nutrition_yesterday": None,
        "body": {
            f"weight_{d.weight_unit}": d.weight(t.get("weight_kg") or y.get("weight_kg")),
            f"weight_trend_{d.weight_unit}": d.weight(t.get("weight_kg_smoothed") or y.get("weight_kg_smoothed")),
        },
        "journal_yesterday": {k: y.get(k) for k in ("mood", "energy", "soreness") if y.get(k) is not None} or None,
    }
    if y.get("food_log_complete"):
        status["nutrition_yesterday"] = {
            "kcal": _r(y.get("kcal")), "protein_g": _r(y.get("protein_g")),
            "protein_g_per_kg": _r(y.get("protein_g_per_kg"), 1), "carbs_g": _r(y.get("carbs_g")),
            "fat_g": _r(y.get("fat_g")), "fiber_g": _r(y.get("fiber_g")),
            "caffeine_mg": _r(y.get("caffeine_mg")),
            "last_caffeine": fmt_hour(y.get("last_caffeine_hour")),
            "alcohol_drinks": _r(y.get("alcohol_drinks"), 1),
        }
    return _drop_empty(status)


def _drop_empty(obj):
    if isinstance(obj, dict):
        out = {k: _drop_empty(v) for k, v in obj.items()}
        return {k: v for k, v in out.items() if v not in (None, {}, [])}
    return obj


def last_7_days(ctx: RuleContext) -> list[dict]:
    d = Display(ctx.profile.display_units)
    rows = []
    for i in range(7, 0, -1):
        day = ctx.date - timedelta(days=i)
        r = ctx.row(day)
        if not r:
            continue
        rows.append(_drop_empty({
            "day": f"{day:%a %m-%d}", "hrv": _r(r.get("hrv")), "rhr": _r(r.get("resting_hr")),
            "sleep": _hm(r.get("sleep_total_min")), "load": _r(r.get("session_load")),
            "game": True if r.get("had_game") else None,
            f"weight_{d.weight_unit}": d.weight(r.get("weight_kg")),
            "kcal": _r(r.get("kcal")), "protein_g": _r(r.get("protein_g")),
        }))
    return rows


def upcoming(ctx: RuleContext) -> list[dict]:
    out = []
    for s in sorted(ctx.planned, key=lambda s: s["date"]):
        if s["date"] < ctx.date or s["status"] == "skipped":
            continue
        out.append(_drop_empty({
            "day": "today" if s["date"] == ctx.date else f"{s['date']:%a %m-%d}",
            "session": f"{s['session_type']} {s['sport']}".strip(),
            "minutes": s.get("planned_duration_min"), "rpe": s.get("planned_rpe"),
            "focus": s.get("focus_muscles") or None,
        }))
    return out[:7]


def supported_insights(conn: psycopg.Connection) -> list[dict]:
    rows = fetch_all(
        conn,
        """SELECT DISTINCT ON (i.hypothesis_id) h.exposure, h.outcome, h.lag_days, i.effect_size,
                  i.n_exposed, i.n_unexposed, i.summary, i.status
           FROM analytics.insight i JOIN analytics.hypothesis h ON h.id = i.hypothesis_id
           ORDER BY i.hypothesis_id, i.computed_at DESC""")
    return [{"finding": r["summary"], "n_days": r["n_exposed"] + r["n_unexposed"]}
            for r in rows if r["status"] == "supported"][:5]


def dominated_actions(fired: list[Evaluated]) -> set[int]:
    """Visible actions made redundant by a stricter one on the same metric and day.

    E.g. 'nothing above RPE 6' and 'RPE 4 or less' both fire: only RPE 4 is shown.
    The redundant action is still stored and graded; it just isn't displayed.
    """
    best: dict[tuple, tuple[float, int]] = {}
    for e in fired:
        if not e.visible:
            continue
        for a in e.result.actions:
            if a.comparator not in ("<", "<=", ">", ">=") or a.target_value is None:
                continue
            key = (a.metric, a.comparator in ("<", "<="), a.evaluate_on)
            strict = a.target_value if key[1] else -a.target_value
            if key not in best or strict < best[key][0]:
                best[key] = (strict, id(a))
    keep = {v[1] for v in best.values()}
    return {id(a) for e in fired if e.visible for a in e.result.actions
            if a.comparator in ("<", "<=", ">", ">=") and a.target_value is not None and id(a) not in keep}


def write_recommendation(conn: psycopg.Connection, ctx: RuleContext, results: list[Evaluated]) -> int:
    d = ctx.date
    fired = [e for e in results if e.result.fired]
    decisions = {
        "date": d.isoformat(),
        "history_days": ctx.history_days,
        "fired": [{
            "rule_id": e.rule_id, "domain": e.domain, "rule_status": e.status, "visible": e.visible,
            "severity": e.result.severity, "description": e.description,
            "evidence_level": e.evidence_level, "details": e.result.details,
        } for e in fired],
        "dormant": [e.rule_id for e in results if e.result.status == "dormant"],
        "no_data": [e.rule_id for e in results if e.result.status == "no_data"],
        "errors": [{"rule_id": e.rule_id, **e.result.details} for e in results if e.result.status == "error"],
    }
    snapshot = {
        "status": build_status(ctx),
        "last_7_days": last_7_days(ctx),
        "upcoming": upcoming(ctx),
        "injuries": [
            _drop_empty({"body_part": f"{(i.get('side') or '') + ' ' if i.get('side') not in (None, 'n/a') else ''}{i['body_part']}",
                         "status": i["status"],
                         "latest_pain_activity": (i["checkins"][-1]["pain_activity"] if i["checkins"] else None)})
            for i in ctx.injuries],
        "insights": supported_insights(conn),
        "features_today": ctx.today,
        "features_yesterday": ctx.yesterday,
    }
    rec_id = upsert(
        conn, "analytics.recommendation",
        [{"date": d, "kind": "daily", "input_snapshot": to_jsonable(snapshot),
          "decisions": to_jsonable(decisions), "shown_to_user": True}],
        conflict=["date", "kind"], update=["input_snapshot", "decisions", "shown_to_user"],
        returning="id")[0]
    execute(conn, "DELETE FROM analytics.recommendation_action WHERE recommendation_id = %s", (rec_id,))
    hidden = dominated_actions(fired)
    n = 0
    for e in fired:
        for a in e.result.actions:
            insert(conn, "analytics.recommendation_action", {
                "recommendation_id": rec_id, "rule_id": e.rule_id,
                "visible": e.visible and id(a) not in hidden,
                "action_text": a.action_text, "metric": a.metric, "comparator": a.comparator,
                "target_value": a.target_value, "target_value_high": a.target_value_high,
                "evaluate_on": a.evaluate_on, "outcome_metric": a.outcome_metric,
            })
            n += 1
    return 1 + n  # the recommendation row plus its actions


def recommendation_id(conn: psycopg.Connection, d: date, kind: str = "daily") -> int | None:
    rows = fetch_all(conn, "SELECT id FROM analytics.recommendation WHERE date = %s AND kind = %s", (d, kind))
    return rows[0]["id"] if rows else None
