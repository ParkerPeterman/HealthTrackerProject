"""Grade past actions: was it followed, and how did the outcome compare to baseline?

Runs every morning over a trailing window so late data (a food log completed
a day late) still gets graded. Re-running is safe: values are recomputed.
"""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

from healthtracker.db import execute, fetch_all
from healthtracker.recommend.metrics import (
    OUTCOME_DIRECTION,
    MetricResolver,
    compare,
    split_outcome,
)


def grade_actions(conn: psycopg.Connection, as_of: date, lookback_days: int = 10,
                  resolver: MetricResolver | None = None) -> int:
    actions = fetch_all(
        conn,
        """SELECT * FROM analytics.recommendation_action
           WHERE evaluate_on BETWEEN %s AND %s ORDER BY id""",
        (as_of - timedelta(days=lookback_days), as_of),
    )
    if not actions:
        return 0
    resolver = resolver or MetricResolver(conn)
    n = 0
    for a in actions:
        if not resolver.available(a["metric"], a["evaluate_on"], as_of):
            continue
        actual = resolver.value(a["metric"], a["evaluate_on"])
        followed = compare(actual, a["comparator"], a["target_value"], a["target_value_high"])

        delta = None
        out = split_outcome(a["outcome_metric"])
        if out:
            metric, lag = out
            od = a["evaluate_on"] + timedelta(days=lag)
            if resolver.available(metric, od, as_of):
                val, base = resolver.value(metric, od), resolver.baseline(metric, od)
                if val is not None and base is not None:
                    delta = round(OUTCOME_DIRECTION.get(metric, 1) * (val - base), 2)

        execute(
            conn,
            """UPDATE analytics.recommendation_action
               SET actual_value = %s, followed = %s, outcome_delta = %s, evaluated_at = now()
               WHERE id = %s""",
            (None if actual is None else round(actual, 2), followed, delta, a["id"]),
        )
        n += 1
    return n
