"""Section 13: a weekly log of where the old and new logic disagree, for the owner.

Each week, every pair that runs side by side is compared over the seven days, and the days
(or hypotheses, or rules) where they part ways are kept with a few examples:

  hrv_status     old: 30-day z of last night's HRV at or below -1  | new: the A2 band, 7-day ln HRV below it
  illness        old: RECOV_ILLNESS_FLAG fired                      | new: E5's possible-illness pattern fired
  daily_adjust   F4 (shadow) would have changed a day with a hard or moderate session planned
  readiness      old: a recovery flag or low HRV z                  | new: G1 score under 35 (or 50+ despite a flag)
  effects        old: the OLS+HAC test calls a hypothesis supported | new: B1's P(|effect| > SWC) at least 0.8
  promotion      old: followed vs not, raw                          | new: G5's within-state comparison (sign or
                                                                       conclusion differs)

Nothing here changes what is shown or decided: it is the evidence for turning a shadow
component on (or not). Medication never appears in it.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import psycopg

from healthtracker.db import execute, fetch_all, upsert
from healthtracker.jsonutil import to_jsonable

log = logging.getLogger(__name__)
MAX_EXAMPLES = 5
LOW_READY, OK_READY = 35, 50


def _week(as_of: date) -> tuple[date, date]:
    start = as_of - timedelta(days=as_of.weekday())
    return start, start + timedelta(days=6)


def _fired(conn, rule: str, start: date, end: date) -> set[date]:
    return {r["date"] for r in fetch_all(conn, """SELECT date FROM analytics.daily_flag WHERE rule_id = %s AND fired
                                                   AND date BETWEEN %s AND %s""", (rule, start, end))}


def hrv_status(conn, start: date, end: date) -> dict:
    rows = fetch_all(conn, """SELECT f.date, f.hrv_z_30d AS z, s.status FROM analytics.daily_features f
                              JOIN analytics.metric_status s ON s.date = f.date AND s.metric = 'ln_hrv'
                              WHERE f.date BETWEEN %s AND %s AND f.hrv_z_30d IS NOT NULL AND s.status IS NOT NULL
                              ORDER BY f.date""", (start, end))
    diff = [{"date": r["date"], "old": "low" if r["z"] <= -1 else "normal", "new": r["status"]}
            for r in rows if (r["z"] <= -1) != (r["status"] == "below")]
    return {"n_compared": len(rows), "disagree": diff}


def illness(conn, start: date, end: date) -> dict:
    old, new = _fired(conn, "RECOV_ILLNESS_FLAG", start, end), _fired(conn, "RECOV_POSSIBLE_ILLNESS", start, end)
    days = sorted(old ^ new)
    return {"n_compared": 7, "disagree": [{"date": d, "old": d in old, "new": d in new} for d in days]}


def daily_adjust(conn, start: date, end: date) -> dict:
    planned = {r["date"]: r for r in fetch_all(conn, """SELECT DISTINCT ON (date) date, session_type, notes
                                                         FROM core.planned_session
                                                         WHERE date BETWEEN %s AND %s AND session_type IN ('hard', 'moderate')
                                                         ORDER BY date, id""", (start, end))}
    flags = {r["date"]: r for r in fetch_all(conn, """SELECT date, details FROM analytics.daily_flag
                                                       WHERE rule_id = 'TRAIN_DAILY_ADJUST' AND fired
                                                       AND date BETWEEN %s AND %s""", (start, end))}
    diff = [{"date": d, "planned": planned[d]["notes"] or planned[d]["session_type"],
             "would": ((flags[d]["details"] or {}).get("decision") or (flags[d]["details"] or {}).get("action"))}
            for d in sorted(set(planned) & set(flags))]
    return {"n_compared": len(planned), "disagree": diff}


def readiness(conn, start: date, end: date) -> dict:
    scores = {r["date"]: r["score"] for r in fetch_all(conn, """SELECT date, score FROM analytics.readiness_daily
                                                                WHERE date BETWEEN %s AND %s""", (start, end))}
    low_z = {r["date"] for r in fetch_all(conn, """SELECT date FROM analytics.daily_features WHERE hrv_z_30d <= -1
                                                    AND date BETWEEN %s AND %s""", (start, end))}
    flagged = low_z | _fired(conn, "RECOV_HIGH_LOAD_LOW_HRV", start, end) | _fired(conn, "RECOV_ILLNESS_FLAG", start, end)
    diff = [{"date": d, "score": s, "old_flag": d in flagged} for d, s in sorted(scores.items())
            if (s < LOW_READY and d not in flagged) or (s >= OK_READY and d in flagged)]
    return {"n_compared": len(scores), "disagree": diff}


def readiness_v2(conn, start: date, end: date) -> dict:
    """G1 v2 vs v1: days where one calls the day low and the other doesn't."""
    rows = [r for r in fetch_all(conn, """SELECT date, score_v1, v2 FROM analytics.readiness_daily
                                          WHERE date BETWEEN %s AND %s AND v2 IS NOT NULL ORDER BY date""",
                                 (start, end)) if r["score_v1"] is not None]
    diff = [{"date": r["date"], "v1": r["score_v1"], "v2": r["v2"]["score"], "adopted": r["v2"]["adopted"]}
            for r in rows if (r["score_v1"] < LOW_READY) != (r["v2"]["score"] < LOW_READY)]
    return {"n_compared": len(rows), "disagree": diff}


def effects(conn, start: date, end: date) -> dict:
    from healthtracker.medication import is_sensitive
    from healthtracker.stats.hypothesis import parse_exposure

    rows = fetch_all(conn, """
        SELECT h.id, h.exposure, h.outcome, h.lag_days,
               (SELECT i.status FROM analytics.insight i WHERE i.hypothesis_id = h.id AND i.computed_at::date <= %(e)s
                ORDER BY i.computed_at DESC LIMIT 1) AS ols,
               (SELECT e.p_meaningful FROM analytics.effect_estimate e WHERE e.hypothesis_id = h.id
                AND e.computed_on <= %(e)s ORDER BY e.computed_on DESC LIMIT 1) AS p
        FROM analytics.hypothesis h WHERE h.kind = 'binary' AND h.status <> 'retired'""", {"e": end})
    rows = [r for r in rows if r["ols"] is not None and r["p"] is not None
            and not is_sensitive(parse_exposure(r["exposure"])[0])]
    diff = [{"hypothesis_id": r["id"], "what": f"{r['exposure']} -> {r['outcome']} (lag {r['lag_days']})",
             "ols": r["ols"], "p_meaningful": float(r["p"])}
            for r in rows if (r["ols"] == "supported") != (float(r["p"]) >= 0.8)]
    return {"n_compared": len(rows), "disagree": diff}


def promotion(conn, start: date, end: date) -> dict:
    from healthtracker.recommend.fair import latest_grades

    rows = [g for g in latest_grades(conn, end).values() if g["raw_diff"] is not None and g["matched_diff"] is not None]
    diff = []
    for g in rows:
        raw, matched = float(g["raw_diff"]), float(g["matched_diff"])
        conclusive = g["lo80"] is not None and (float(g["lo80"]) > 0 or float(g["hi80"]) < 0)
        if (raw > 0) != (matched > 0) or (abs(raw) > 2 * abs(matched) and not conclusive):
            diff.append({"rule_id": g["rule_id"], "raw_diff": raw, "matched_diff": matched,
                         "interval80": [g["lo80"], g["hi80"]]})
    return {"n_compared": len(rows), "disagree": diff}


COMPARISONS = {"hrv_status": hrv_status, "illness": illness, "daily_adjust": daily_adjust, "readiness": readiness,
               "readiness_v2": readiness_v2, "effects": effects, "promotion": promotion}
WORDS = {"hrv_status": "days the HRV band and the old z-score disagreed",
         "illness": "days only one of the illness checks fired",
         "daily_adjust": "planned hard days the daily adjustment would have changed",
         "readiness": "days the readiness score and the old flags disagreed",
         "readiness_v2": "days the learned readiness (v2) and v1 disagreed on a low day",
         "effects": "hypotheses where the Bayesian estimate and the old test disagree",
         "promotion": "rules whose raw and fair grades point different ways"}


def build_shadow_log(conn: psycopg.Connection, as_of: date) -> int:
    start, end = _week(as_of)
    rows = []
    for name, fn in COMPARISONS.items():
        try:
            with conn.transaction():  # a savepoint: one comparison failing shouldn't hide the others
                res = fn(conn, start, end)
        except Exception:  # noqa: BLE001
            log.exception("shadow comparison %s failed", name)
            continue
        n = len(res["disagree"])
        rows.append({"week_start": start, "component": name, "n_compared": res["n_compared"], "n_disagree": n,
                     "examples": to_jsonable(res["disagree"][:MAX_EXAMPLES]),
                     "summary": f"{n} of {res['n_compared']} {WORDS[name]}"})
    execute(conn, "DELETE FROM analytics.shadow_log WHERE week_start = %s", (start,))
    return upsert(conn, "analytics.shadow_log", rows, conflict=["week_start", "component"]) if rows else 0


def render_log(rows: list[dict]) -> str:
    if not rows:
        return "No shadow comparisons yet."
    lines = [f"**Shadow comparisons, week of {rows[0]['week_start']:%b %d}**", ""]
    lines += [f"- {r['summary']}" for r in rows]
    return "\n".join(lines)
