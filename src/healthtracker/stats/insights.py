"""Weekly: test every non-retired hypothesis and append insight rows."""

from __future__ import annotations

from datetime import date

import pandas as pd
import psycopg

from healthtracker.db import execute, fetch_all, fetch_df, insert
from healthtracker.stats.hypothesis import Hypothesis, finalize, run_test


def load_features_frame(conn: psycopg.Connection, end: date | None = None) -> pd.DataFrame:
    df = fetch_df(conn, "SELECT * FROM analytics.daily_features WHERE %s::date IS NULL OR date <= %s ORDER BY date",
                  (end, end))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def run_insights(conn: psycopg.Connection, as_of: date) -> int:
    features = load_features_frame(conn, as_of)
    if features.empty:
        return 0
    rows = fetch_all(conn, "SELECT * FROM analytics.hypothesis WHERE status <> 'retired' ORDER BY id")
    hyps = {r["id"]: Hypothesis(r["id"], r["exposure"], r["outcome"], r["lag_days"], r["min_n_per_group"])
            for r in rows}
    results = [run_test(features, h) for h in hyps.values()]
    finalize(results, hyps)
    for r in results:
        insert(conn, "analytics.insight", {
            "hypothesis_id": r.hypothesis_id,
            "window_start": r.window[0] if r.window else None,
            "window_end": r.window[1] if r.window else None,
            "n_exposed": r.n_exposed, "n_unexposed": r.n_unexposed,
            "effect_size": _round(r.effect, 3), "ci_low": _round(r.ci_low, 3), "ci_high": _round(r.ci_high, 3),
            "p_value": _round(r.p_value, 6), "p_adjusted": _round(r.p_adjusted, 6),
            "method": "ols_hac+block_bootstrap" if r.p_value is not None else None,
            "controlled_for": r.controlled_for, "replicated": r.replicated,
            "status": r.status, "summary": r.summary,
        }, returning=None)
        if r.p_value is not None:
            execute(conn, "UPDATE analytics.hypothesis SET status = 'active' WHERE id = %s AND status = 'queued'",
                    (r.hypothesis_id,))
    return len(results)


def _round(v, nd):
    return None if v is None else round(float(v), nd)
