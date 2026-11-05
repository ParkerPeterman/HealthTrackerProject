"""G5: fair grading of advice.

Comparing days I followed advice with days I didn't is biased: advice gets skipped on bad
days (short night, low HRV, travel, a game), and those days have worse outcomes anyway. So:

  * Each action logs a coarse state at decision time (recommendation_action.state): HRV band
    (7-day z below -0.5 or not), a short night (under 7 h), and a busy day (travel or a game).
    Actions from before the column existed take it from the recommendation's input snapshot.
  * Followed and not-followed days are compared only within the same state: coarsened exact
    matching, 8 strata. States with only one side drop out. The difference is averaged over
    the followed days' states (the effect of following, on days I followed).
  * The result has 80% and 95% intervals (pooled within-stratum variance) and P(following
    helps), with the raw, unmatched difference alongside so the bias is visible.

Rule promotion (stats/promotion.py) uses the matched comparison, and the weekly ranking gets
'follow_through' candidates from it.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import psycopg
from scipy import stats

from healthtracker.db import fetch_all, upsert

SHORT_SLEEP_MIN = 420
LOW_HRV_Z = -0.5
MIN_MATCHED = 5   # followed and not-followed days each, after matching
Z80, Z95 = 1.2815515655446004, 1.959963984540054


def decision_state(today: dict | None) -> dict:
    """The coarse state of the decision day: what makes advice easier or harder to follow."""
    t = today or {}

    def num(k):
        v = t.get(k)
        try:
            return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)
        except (TypeError, ValueError):
            return None

    hrv, sleep = num("hrv_z_30d"), num("sleep_total_min")
    return {"hrv_low": None if hrv is None else hrv <= LOW_HRV_Z,
            "short_sleep": None if sleep is None else sleep < SHORT_SLEEP_MIN,
            "busy": bool(t.get("is_travel")) or bool(t.get("had_game"))}


def stratum(state: dict) -> tuple:
    return (state.get("hrv_low"), state.get("short_sleep"), bool(state.get("busy")))


def fair_compare(rows: list[dict]) -> dict:
    """rows: {followed, outcome_delta, state}. Matched (within-state) and raw differences."""
    yes = [float(r["outcome_delta"]) for r in rows if r["followed"]]
    no = [float(r["outcome_delta"]) for r in rows if not r["followed"]]
    out = {"n_followed": len(yes), "n_not": len(no),
           "raw_diff": round(float(np.mean(yes) - np.mean(no)), 3) if yes and no else None}
    groups: dict[tuple, dict[bool, list[float]]] = {}
    for r in rows:
        groups.setdefault(stratum(r["state"] or {}), {True: [], False: []})[bool(r["followed"])].append(
            float(r["outcome_delta"]))
    used = {k: g for k, g in groups.items() if g[True] and g[False]}
    n1 = sum(len(g[True]) for g in used.values())
    n0 = sum(len(g[False]) for g in used.values())
    out.update(n_matched_followed=n1, n_matched_not=n0, strata_used=len(used), strata=len(groups))
    if n1 < MIN_MATCHED or n0 < MIN_MATCHED:
        return {**out, "matched_diff": None, "se": None, "lo80": None, "hi80": None, "lo95": None, "hi95": None,
                "p_benefit": None}
    resid = [x - np.mean(g[arm]) for g in used.values() for arm in (True, False) for x in g[arm]]
    dof = len(resid) - 2 * len(used)
    var = float(np.sum(np.square(resid)) / dof) if dof > 0 else float(np.var(yes + no, ddof=1))
    diff = se2 = 0.0
    for g in used.values():
        w = len(g[True]) / n1
        diff += w * (np.mean(g[True]) - np.mean(g[False]))
        se2 += w ** 2 * var * (1 / len(g[True]) + 1 / len(g[False]))
    se = math.sqrt(se2) if se2 > 0 else None
    if se is None:
        return {**out, "matched_diff": round(float(diff), 3), "se": None, "lo80": None, "hi80": None, "lo95": None,
                "hi95": None, "p_benefit": None}
    return {**out, "matched_diff": round(float(diff), 3), "se": round(se, 4),
            "lo80": round(diff - Z80 * se, 3), "hi80": round(diff + Z80 * se, 3),
            "lo95": round(diff - Z95 * se, 3), "hi95": round(diff + Z95 * se, 3),
            "p_benefit": round(float(stats.norm.cdf(diff / se)), 3)}


# ------------------------------------------------------------------ from the database
def graded_rows(conn: psycopg.Connection, rule_id: str | None = None) -> dict[str, list[dict]]:
    rows = fetch_all(conn, """SELECT a.rule_id, a.followed, a.outcome_delta, a.state, a.metric, a.outcome_metric,
                                     r.input_snapshot->'features_today' AS today
                              FROM analytics.recommendation_action a
                              JOIN analytics.recommendation r ON r.id = a.recommendation_id
                              WHERE a.followed IS NOT NULL AND a.outcome_delta IS NOT NULL
                                AND (%(rule)s::text IS NULL OR a.rule_id = %(rule)s)
                              ORDER BY a.rule_id, a.id""", {"rule": rule_id})
    out: dict[str, list[dict]] = {}
    for r in rows:
        r["state"] = r["state"] or decision_state(r.pop("today"))
        out.setdefault(r["rule_id"], []).append(r)
    return out


def grade_advice(conn: psycopg.Connection, as_of: date) -> int:
    rows = []
    for rule_id, items in graded_rows(conn).items():
        res = fair_compare(items)
        rows.append({"computed_on": as_of, "rule_id": rule_id, "metric": items[0]["metric"],
                     "outcome_metric": items[0]["outcome_metric"], **res})
    return upsert(conn, "analytics.advice_grade", rows, conflict=["computed_on", "rule_id"]) if rows else 0


def latest_grades(conn: psycopg.Connection, as_of: date) -> dict[str, dict]:
    return {r["rule_id"]: r for r in fetch_all(conn, """SELECT DISTINCT ON (rule_id) * FROM analytics.advice_grade
                                                        WHERE computed_on <= %s ORDER BY rule_id, computed_on DESC""",
                                               (as_of,))}


def follow_through_candidates(conn: psycopg.Connection, as_of: date) -> list[dict]:
    """Ranking candidates (C3): what following a rule's advice did, on comparable days."""
    from healthtracker.recommend.metrics import split_outcome

    rules = {r["rule_id"]: r for r in fetch_all(conn, "SELECT rule_id, description FROM analytics.rule")}
    out = []
    for rid, g in latest_grades(conn, as_of).items():
        if g["matched_diff"] is None or g["p_benefit"] is None:
            continue
        p = float(g["p_benefit"])
        outcome = (split_outcome(g["outcome_metric"]) or (g["outcome_metric"] or "", 0))[0]
        better = float(g["matched_diff"]) >= 0
        desc = (rules.get(rid) or {}).get("description") or rid
        out.append({"kind": "follow_through", "key": f"follow:{rid}", "exposure": g["metric"], "outcome": outcome,
                    "p": round(max(p, 1 - p), 3), "estimate": float(g["matched_diff"]), "swc": None, "novelty": 0.5,
                    "text": (f"On comparable days, following \"{desc}\" went with {'better' if better else 'worse'} "
                             f"{outcome.replace('_', ' ')} (by {abs(float(g['matched_diff'])):.1f}; 80% interval "
                             f"{float(g['lo80']):.1f} to {float(g['hi80']):.1f}; {g['n_matched_followed']} followed vs "
                             f"{g['n_matched_not']} not).")})
    return out
