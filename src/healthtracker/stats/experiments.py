"""Self-experiments: randomized treatment days, then a permutation test.

Days are assigned in pairs (one treatment, one control, order random), so the
two conditions stay balanced across weekdays and time. On treatment days the
daily job adds the protocol as a visible, graded action (EXPERIMENT_PROTOCOL).
Analysis is intention-to-treat: outcome by assigned condition.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.stats.hypothesis import parse_exposure
from healthtracker.stats.insights import load_features_frame
from healthtracker.units import fmt_hour

# Protocol presets: the treatment removes the exposure being tested.
PRESETS = {
    "last_caffeine_hour": lambda v: {"metric": "last_caffeine_hour", "comparator": "<", "target_value": v,
                                     "action_text": f"no caffeine at or after {fmt_hour(v)}"},
    "alcohol_drinks": lambda v: {"metric": "alcohol_drinks", "comparator": "<", "target_value": v,
                                 "action_text": "no alcohol today" if v <= 1 else f"fewer than {v:g} drinks today"},
    "last_meal_hour": lambda v: {"metric": "last_meal_hour", "comparator": "<", "target_value": v,
                                 "action_text": f"finish your last meal before {fmt_hour(v)}"},
    "caffeine_mg": lambda v: {"metric": "caffeine_mg", "comparator": "<", "target_value": v,
                              "action_text": f"keep caffeine under {v:g} mg today"},
}


def protocol_for(exposure: str, outcome: str, lag: int) -> dict:
    col, op, val = parse_exposure(exposure)
    if col not in PRESETS or op not in (">=", ">"):
        raise ValueError(f"no experiment preset for exposure {exposure!r}")
    return {**PRESETS[col](val), "outcome": outcome, "outcome_lag": lag}


def assign_days(start: date, days: int, seed: int | None = None) -> dict[str, str]:
    rng = np.random.default_rng(seed)
    out = {}
    for i in range(0, days - days % 2, 2):
        first = "treatment" if rng.random() < 0.5 else "control"
        second = "control" if first == "treatment" else "treatment"
        out[(start + timedelta(days=i)).isoformat()] = first
        out[(start + timedelta(days=i + 1)).isoformat()] = second
    return out


def create_experiment(conn: psycopg.Connection, hypothesis_id: int, start: date, days: int = 14,
                      seed: int | None = None, protocol: dict | None = None) -> int:
    if days < 14:
        raise ValueError("experiments run at least two weeks")
    h = fetch_one(conn, "SELECT * FROM analytics.hypothesis WHERE id = %s", (hypothesis_id,))
    if not h:
        raise ValueError("unknown hypothesis")
    protocol = protocol or protocol_for(h["exposure"], h["outcome"], h["lag_days"])
    assignment = assign_days(start, days, seed)
    end = start + timedelta(days=len(assignment) - 1)
    return insert(conn, "analytics.experiment", {
        "hypothesis_id": hypothesis_id,
        "design": f"randomized paired days, {len(assignment)} days, outcome {h['outcome']} lag {h['lag_days']}",
        "start_date": start, "end_date": end, "assignment": assignment, "protocol": protocol,
        "status": "running" if start <= date.today() else "planned",
    })


def permutation_test(y: np.ndarray, treat: np.ndarray, reps: int = 10000, seed: int = 1) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    obs = y[treat].mean() - y[~treat].mean()
    count = 0
    for _ in range(reps):
        perm = rng.permutation(treat)
        if abs(y[perm].mean() - y[~perm].mean()) >= abs(obs) - 1e-12:
            count += 1
    return float(obs), (count + 1) / (reps + 1)


def analyze(conn: psycopg.Connection, exp: dict) -> str:
    proto = exp["protocol"] or {}
    features = load_features_frame(conn)
    lag = int(proto.get("outcome_lag", 1))
    outcome = proto.get("outcome")
    rows = []
    for d, cond in (exp["assignment"] or {}).items():
        od = pd.Timestamp(d) + pd.Timedelta(days=lag)
        y = features[outcome].get(od) if outcome in features.columns else None
        if y is not None and not pd.isna(y):
            rows.append((cond == "treatment", float(y)))
    if len([r for r in rows if r[0]]) < 4 or len([r for r in rows if not r[0]]) < 4:
        return f"Not enough outcome data ({len(rows)} days with {outcome})."
    treat = np.array([r[0] for r in rows])
    y = np.array([r[1] for r in rows])
    diff, p = permutation_test(y, treat)
    adherence = fetch_one(conn, """SELECT avg(a.followed::int) AS rate, count(*) AS n
                                   FROM analytics.recommendation_action a
                                   WHERE a.rule_id = 'EXPERIMENT_PROTOCOL' AND a.followed IS NOT NULL
                                     AND a.evaluate_on BETWEEN %s AND %s""",
                           (exp["start_date"], exp["end_date"]))
    adh = (f"; adherence {adherence['rate'] * 100:.0f}% of {adherence['n']} treatment days"
           if adherence and adherence["n"] else "")
    return (f"Treatment ({proto.get('action_text')}) vs control: {outcome} {diff:+.1f} "
            f"(permutation p={p:.3f}; {treat.sum()} vs {(~treat).sum()} days{adh}).")


def update_experiments(conn: psycopg.Connection, as_of: date) -> int:
    n = execute(conn, """UPDATE analytics.experiment SET status = 'running'
                         WHERE status = 'planned' AND start_date <= %s""", (as_of,))
    for exp in fetch_all(conn, """SELECT * FROM analytics.experiment
                                  WHERE status = 'running' AND end_date < %s""", (as_of,)):
        summary = analyze(conn, exp)
        execute(conn, "UPDATE analytics.experiment SET status = 'done', result_summary = %s WHERE id = %s",
                (summary, exp["id"]))
        n += 1
    return n
