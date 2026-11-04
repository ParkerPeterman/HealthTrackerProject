"""Analysis components: new logic that runs in shadow mode until I turn it on.

Every new method computes and logs its results from day one, but nothing reaches the
coaching message (or the app's coaching views) until its switch is active:

    healthtracker rules set COMP_HRV_BANDS active

Switches are rows in analytics.rule with domain 'analysis' and a COMP_ prefix, so they
share the rules' status, approval, and history machinery. Their min_history_days is the
data gate from the upgrade brief; a switch that is active but short of data stays quiet.
"""

from __future__ import annotations

import psycopg

from healthtracker.db import fetch_all


def component_status(conn: psycopg.Connection) -> dict[str, dict]:
    rows = fetch_all(conn, """SELECT rule_id, status, min_history_days, description FROM analytics.rule
                              WHERE rule_id LIKE 'COMP\\_%%' ORDER BY rule_id""")
    return {r["rule_id"]: r for r in rows}


def is_live(components: dict[str, dict], code: str, history_days: int | None = None) -> bool:
    c = components.get(code)
    if not c or c["status"] != "active":
        return False
    return history_days is None or history_days >= (c["min_history_days"] or 0)
