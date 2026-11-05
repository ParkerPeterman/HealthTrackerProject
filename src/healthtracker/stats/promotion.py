"""Weekly: propose promoting shadow rules that earn it, and retiring ones that hurt.

Evidence comes from graded actions: outcome_delta on days a rule's advice was
followed vs not followed, compared only within the same decision-time state (G5,
recommend/fair.py), because advice gets skipped on bad days. The p-value is from the
matched difference and its standard error; the raw difference is kept in the evidence.
Safety rules are never retired automatically.
"""

from __future__ import annotations

import json
from datetime import date

import psycopg
from scipy import stats as sps

from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.quality import activation_problem
from healthtracker.recommend.fair import fair_compare, graded_rows

MIN_GRADED = 20
MIN_PER_ARM = 8

# Rules that also need a supported personal finding before going live.
PERSONAL_EVIDENCE = {"SLEEP_CAFFEINE_CUTOFF": ("last_caffeine_hour", "sleep_score")}


def _has_supported_insight(conn: psycopg.Connection, exposure_prefix: str, outcome: str) -> bool:
    row = fetch_one(conn, """SELECT 1 FROM analytics.insight i JOIN analytics.hypothesis h ON h.id = i.hypothesis_id
                             WHERE h.exposure LIKE %s AND h.outcome = %s AND i.status = 'supported'
                               AND i.computed_at > now() - interval '30 days' LIMIT 1""",
                    (exposure_prefix + "%", outcome))
    return bool(row)


def run_promotion(conn: psycopg.Connection, as_of: date) -> int:
    n = 0
    graded = graded_rows(conn)
    for r in fetch_all(conn, "SELECT * FROM analytics.rule WHERE status IN ('shadow','active')"):
        rule_id = r["rule_id"]
        if rule_id == "EXPERIMENT_PROTOCOL":
            continue
        rows = graded.get(rule_id, [])
        g = fair_compare(rows)
        yes, no = g["n_matched_followed"], g["n_matched_not"]
        if len(rows) < MIN_GRADED or min(yes, no) < MIN_PER_ARM or g["matched_diff"] is None or not g["se"]:
            continue
        diff = float(g["matched_diff"])
        p = float(2 * sps.norm.sf(abs(diff) / float(g["se"])))
        evidence = {"n_followed": yes, "n_not_followed": no, "mean_diff": round(diff, 3), "p_value": round(p, 4),
                    "raw_diff": g["raw_diff"], "matched_on": "HRV band, short night, travel or game day",
                    "strata_used": g["strata_used"]}
        new_status, reason = None, None
        if r["status"] == "shadow" and diff > 0 and p < 0.10:
            if rule_id in PERSONAL_EVIDENCE and not _has_supported_insight(conn, *PERSONAL_EVIDENCE[rule_id]):
                continue
            new_status = "active"
            reason = (f"On comparable days, outcomes were {diff:+.2f} better on the {yes} days its advice was "
                      f"followed than the {no} days it wasn't (p={p:.3f}).")
        elif r["status"] == "active" and r["evidence_level"] != "safety" and diff < 0 and p < 0.05:
            new_status = "retired"
            reason = (f"On comparable days, following it was associated with worse outcomes ({diff:+.2f}, "
                      f"p={p:.3f}, {yes} vs {no} days).")
        if not new_status:
            continue
        pending = fetch_one(conn, """SELECT id FROM analytics.rule_status_change
                                     WHERE rule_id = %s AND approved IS NULL""", (rule_id,))
        if pending:
            execute(conn, """UPDATE analytics.rule_status_change SET new_status = %s, reason = %s,
                             evidence = %s::jsonb, changed_at = now() WHERE id = %s""",
                    (new_status, reason, json.dumps(evidence), pending["id"]))
        else:
            insert(conn, "analytics.rule_status_change", {
                "rule_id": rule_id, "old_status": r["status"], "new_status": new_status, "reason": reason,
                "evidence": evidence, "proposed_by": "tuning_job"}, returning=None)
        n += 1
    return n


def decide_status(conn: psycopg.Connection, change_id: int, approve: bool) -> dict:
    row = fetch_one(conn, "SELECT * FROM analytics.rule_status_change WHERE id = %s", (change_id,))
    if not row or row["approved"] is not None:
        raise ValueError("no pending status change with that id")
    if approve and row["new_status"] == "active" and (problem := activation_problem(conn, row["rule_id"])):
        raise ValueError(problem)
    if approve:
        execute(conn, "UPDATE analytics.rule SET status = %s, reviewed_at = %s WHERE rule_id = %s",
                (row["new_status"], date.today(), row["rule_id"]))
    execute(conn, "UPDATE analytics.rule_status_change SET approved = %s, changed_at = now() WHERE id = %s",
            (approve, change_id))
    return row


def set_status(conn: psycopg.Connection, rule_id: str, status: str) -> None:
    row = fetch_one(conn, "SELECT status FROM analytics.rule WHERE rule_id = %s", (rule_id,))
    if not row:
        raise ValueError("unknown rule")
    if status == "active" and (problem := activation_problem(conn, rule_id)):
        raise ValueError(problem)
    execute(conn, "UPDATE analytics.rule SET status = %s, reviewed_at = %s WHERE rule_id = %s",
            (status, date.today(), rule_id))
    insert(conn, "analytics.rule_status_change", {
        "rule_id": rule_id, "old_status": row["status"], "new_status": status, "reason": "manual",
        "proposed_by": "manual", "approved": True}, returning=None)
