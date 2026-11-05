"""Weekly: test every non-retired hypothesis and append insight rows."""

from __future__ import annotations

from datetime import date

import pandas as pd
import psycopg

from healthtracker.components import component_status, is_live
from healthtracker.db import execute, fetch_all, fetch_df, insert
from healthtracker.quality import insight_problem, tiers
from healthtracker.stats.hypothesis import DEFAULT_EXCLUDES, Hypothesis, finalize, run_test
from healthtracker.stats.records import build_record
from healthtracker.stats.records import render as render_record


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
    # Dose hypotheses (B3) have no yes/no split: only the Bayesian estimates (B1) cover them.
    rows = fetch_all(conn, """SELECT * FROM analytics.hypothesis WHERE status <> 'retired' AND kind = 'binary'
                              ORDER BY id""")
    hyps = {r["id"]: Hypothesis(r["id"], r["exposure"], r["outcome"], r["lag_days"], r["min_n_per_group"],
                                tuple(r.get("excludes") or DEFAULT_EXCLUDES))
            for r in rows}
    results = [run_test(features, h) for h in hyps.values()]
    finalize(results, hyps)
    tier = tiers(conn)
    bayes = {}
    if is_live(component_status(conn), "COMP_BAYES_EFFECTS"):  # B1 replaces the direction probability
        bayes = {e["hypothesis_id"]: e for e in fetch_all(
            conn, "SELECT * FROM analytics.effect_estimate WHERE computed_on = %s", (as_of,))}
    for r in results:
        h = hyps[r.hypothesis_id]
        problem = insight_problem(h.exposure, h.outcome, tier)
        if r.status == "supported" and problem:  # E2: no insight rests on poor-quality measures alone
            r.status, r.summary = "weak", f"{r.summary} - {problem}"
        rec = build_record(r, h, problem, bayes.get(r.hypothesis_id))  # C5
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
            "claim_type": rec["claim_type"], "effect_unit": rec["unit"], "probability": rec["probability"],
            "probability_kind": rec["probability_kind"], "confidence": rec["confidence"],
            "what_would_change": rec["what_would_change"], "shown": rec["shown"],
            "record": {**rec, "text": render_record(rec)},
        }, returning=None)
        if r.p_value is not None:
            execute(conn, "UPDATE analytics.hypothesis SET status = 'active' WHERE id = %s AND status = 'queued'",
                    (r.hypothesis_id,))
    return len(results)


def _round(v, nd):
    return None if v is None else round(float(v), nd)
