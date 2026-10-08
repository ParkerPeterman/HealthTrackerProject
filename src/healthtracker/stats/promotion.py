"""Weekly: propose promoting shadow rules that earn it, and retiring ones that hurt.

Evidence comes from graded actions: outcome_delta on days a rule's advice was
followed vs not followed (analytics.v_rule_performance, plus a Welch t-test).
Safety rules are never retired automatically.
"""

from __future__ import annotations

import json
from datetime import date

import psycopg
from scipy import stats as sps

from healthtracker.db import execute, fetch_all, fetch_one, insert

MIN_GRADED = 20
MIN_PER_ARM = 8

# Rules that also need a supported personal finding before going live.
PERSONAL_EVIDENCE = {"SLEEP_CAFFEINE_CUTOFF": ("last_caffeine_hour", "sleep_score")}


def _arms(conn: psycopg.Connection, rule_id: str) -> tuple[list[float], list[float]]:
    rows = fetch_all(conn, """SELECT followed, outcome_delta FROM analytics.recommendation_action
                              WHERE rule_id = %s AND followed IS NOT NULL AND outcome_delta IS NOT NULL""",
                     (rule_id,))
    yes = [float(r["outcome_delta"]) for r in rows if r["followed"]]
    no = [float(r["outcome_delta"]) for r in rows if not r["followed"]]
    return yes, no


def _has_supported_insight(conn: psycopg.Connection, exposure_prefix: str, outcome: str) -> bool:
    row = fetch_one(conn, """SELECT 1 FROM analytics.insight i JOIN analytics.hypothesis h ON h.id = i.hypothesis_id
                             WHERE h.exposure LIKE %s AND h.outcome = %s AND i.status = 'supported'
                               AND i.computed_at > now() - interval '30 days' LIMIT 1""",
                    (exposure_prefix + "%", outcome))
    return bool(row)


def run_promotion(conn: psycopg.Connection, as_of: date) -> int:
    n = 0
    for r in fetch_all(conn, "SELECT * FROM analytics.rule WHERE status IN ('shadow','active')"):
        rule_id = r["rule_id"]
        if rule_id == "EXPERIMENT_PROTOCOL":
            continue
        yes, no = _arms(conn, rule_id)
        if len(yes) + len(no) < MIN_GRADED or min(len(yes), len(no)) < MIN_PER_ARM:
            continue
        diff = sum(yes) / len(yes) - sum(no) / len(no)
        p = float(sps.ttest_ind(yes, no, equal_var=False).pvalue)
        evidence = {"n_followed": len(yes), "n_not_followed": len(no), "mean_diff": round(diff, 3),
                    "p_value": round(p, 4)}
        new_status, reason = None, None
        if r["status"] == "shadow" and diff > 0 and p < 0.10:
            if rule_id in PERSONAL_EVIDENCE and not _has_supported_insight(conn, *PERSONAL_EVIDENCE[rule_id]):
                continue
            new_status = "active"
            reason = (f"Outcomes were {diff:+.2f} better on the {len(yes)} days its advice was followed "
                      f"than the {len(no)} days it wasn't (p={p:.3f}).")
        elif r["status"] == "active" and r["evidence_level"] != "safety" and diff < 0 and p < 0.05:
            new_status = "retired"
            reason = (f"Following it was associated with worse outcomes ({diff:+.2f}, p={p:.3f}, "
                      f"{len(yes)} vs {len(no)} days).")
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
    execute(conn, "UPDATE analytics.rule SET status = %s, reviewed_at = %s WHERE rule_id = %s",
            (status, date.today(), rule_id))
    insert(conn, "analytics.rule_status_change", {
        "rule_id": rule_id, "old_status": row["status"], "new_status": status, "reason": "manual",
        "proposed_by": "manual", "approved": True}, returning=None)
