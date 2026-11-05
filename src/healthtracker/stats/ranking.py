"""C1-C3: which insights are worth my attention this week.

  C1 novelty        how far my estimate sits from the population prior: 1 - exp(-|m - m0| / s0)
                    ("alcohol lowers your HRV" scores low; "twice as much as typical" scores high).
                    Insight types with no prior (progress, regularity) get a neutral 0.5.
  C2 actionability  can I change the exposure (caffeine timing yes; weather, travel, games no;
                    medication never: 0), halved when I'm already acting on it (an active rule
                    or an experiment on it).
  C3 rank           P(meaningful) x novelty x actionability x priority weight (endurance and sleep
                    high) + a recency boost for new or materially moved estimates. At most three a
                    week; an insight shown before comes back only when its estimate moved by at
                    least its SWC or its confidence word changed. Every component is stored.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import psycopg

from healthtracker.db import fetch_all, upsert
from healthtracker.medication import is_sensitive
from healthtracker.stats.hypothesis import UNITS, Hypothesis, describe, parse_exposure
from healthtracker.stats.records import confidence_word

CHANGEABLE = {
    "last_caffeine_hour": 1.0, "caffeine_mg": 1.0, "caffeine_hours_before_bed": 1.0, "alcohol_drinks": 1.0,
    "last_meal_hour": 1.0, "bedtime_hour": 1.0, "sleep_total_min": 0.8, "kcal": 0.8, "protein_g": 0.8,
    "protein_g_per_kg": 0.8, "carbs_g": 0.8, "fiber_g": 0.8, "train_end_hour": 0.8, "water_late_ml": 1.0,
    "training_min": 0.6, "load_internal": 0.6, "session_load": 0.6, "load_week_change_pct": 0.6, "training": 0.6,
    "sleep_timing": 0.8, "sleep timing": 0.8, "hard_day x short_sleep": 0.6, "sleep_midpoint_min": 0.8,
    "is_travel": 0.0, "had_game": 0.0, "tag_stress": 0.3, "acwr": 0.6,
}
PRIORITY = {"hrv": 1.0, "resting_hr": 0.9, "aerobic_pace": 1.0, "vo2max": 1.0, "sleep_total_min": 1.0,
            "sleep_score": 0.9, "sleep_midpoint_min": 0.9, "sleep_quality": 0.9, "sri_28d": 0.9,
            "bedtime_hour": 0.8, "energy": 0.7, "mood": 0.7, "soreness": 0.6, "sprint_10m": 0.9,
            "sprint_30m": 0.9, "cmj": 0.9, "broad_jump": 0.9, "kcal": 0.4, "avg_stress": 0.3}
MAX_SHOWN = 3
RECENCY = 0.1


def novelty(post_mean_sd: float | None, prior_mean: float | None, prior_sd: float | None) -> float:
    if post_mean_sd is None or prior_mean is None or not prior_sd:
        return 0.5
    return round(1 - math.exp(-abs(post_mean_sd - prior_mean) / prior_sd), 3)


def actionability(exposure: str, acted_on: bool) -> float:
    col = exposure.split(" ")[0] if " >=" in exposure or " <" in exposure else exposure
    if is_sensitive(col):
        return 0.0  # medication is never something to change (H3)
    a = CHANGEABLE.get(col, 0.5)
    return round(a * (0.5 if acted_on else 1.0), 3)


def effect_candidates(conn: psycopg.Connection, d: date) -> list[dict]:
    """B1 estimates as candidates (the latest run on or before d), worded from the hypothesis."""
    rows = fetch_all(conn, """SELECT DISTINCT ON (e.hypothesis_id) e.*, h.exposure, h.outcome, h.lag_days, h.kind AS hkind
                              FROM analytics.effect_estimate e JOIN analytics.hypothesis h ON h.id = e.hypothesis_id
                              WHERE e.computed_on <= %s ORDER BY e.hypothesis_id, e.computed_on DESC""", (d,))
    out = []
    for r in rows:
        if r["p_meaningful"] is None or r["estimate"] is None:
            continue
        h = Hypothesis(r["hypothesis_id"], r["exposure"], r["outcome"], r["lag_days"], kind=r["hkind"])
        exp, outc = describe(h)
        unit = UNITS.get(r["outcome"], "points")
        word = confidence_word(r["p_meaningful"])
        lead = f"each one more {exp}" if h.kind == "dose" else f"after {exp}"
        out.append({"kind": "effect", "key": f"effect:{r['hypothesis_id']}", "exposure": parse_exposure(r["exposure"])[0],
                    "outcome": r["outcome"], "p": float(r["p_meaningful"]), "estimate": float(r["estimate"]),
                    "swc": float(r["swc"]) if r["swc"] is not None else None,
                    "novelty": novelty(r["estimate_sd"], r["prior_mean"], r["prior_sd"]),
                    "text": f"{word.capitalize()}: {lead}, {outc} is {abs(r['estimate']):.1f} {unit} "
                            f"{'higher' if r['estimate'] > 0 else 'lower'} (80% interval {r['lo80']:.1f} to "
                            f"{r['hi80']:.1f}; {r['n_days']} days)."})
    return out


def acted_on(conn: psycopg.Connection) -> set[str]:
    """Exposures I'm already acting on: an active rule's inputs, or a current or finished experiment."""
    cols = set()
    for r in fetch_all(conn, "SELECT inputs FROM analytics.rule WHERE status = 'active' AND inputs IS NOT NULL"):
        cols |= set(r["inputs"])
    for r in fetch_all(conn, """SELECT h.exposure, e.protocol FROM analytics.experiment e
                                LEFT JOIN analytics.hypothesis h ON h.id = e.hypothesis_id
                                WHERE e.status IN ('planned', 'running', 'done')"""):
        if r["exposure"]:
            cols.add(parse_exposure(r["exposure"])[0])
        for c in ((r["protocol"] or {}).get("conditions") or {}).values():
            cols.add(c.get("metric"))
    return cols


def rank(cands: list[dict], acting: set[str], last_shown: dict[str, dict]) -> list[dict]:
    for c in cands:
        c.setdefault("novelty", 0.5)
        c["actionability"] = actionability(c["exposure"], c["exposure"] in acting)
        c["priority"] = PRIORITY.get(c["outcome"], 0.5)
        prev = last_shown.get(c["key"])
        moved = prev is None or (c.get("swc") and abs(c["estimate"] - prev["estimate"]) >= c["swc"]) or \
            confidence_word(c["p"]) != confidence_word(prev["p"])
        c["moved"] = bool(moved)
        c["recency"] = RECENCY if prev is None else 0.0
        c["score"] = round(c["p"] * c["novelty"] * c["actionability"] * c["priority"] + c["recency"], 4)
    eligible = [c for c in cands if c["moved"] and c["p"] >= 0.6 and c["actionability"] > 0]
    top = {c["key"] for c in sorted(eligible, key=lambda c: -c["score"])[:MAX_SHOWN]}
    for c in cands:
        c["shown"] = c["key"] in top
    return sorted(cands, key=lambda c: (-c["shown"], -c["score"]))


def run_ranking(conn: psycopg.Connection, as_of: date, features=None) -> int:
    from healthtracker.recommend.fair import follow_through_candidates
    from healthtracker.stats.anomaly import anomaly_candidates
    from healthtracker.stats.insight_types import candidates
    from healthtracker.stats.insights import load_features_frame

    f = load_features_frame(conn, as_of) if features is None else features
    cands = effect_candidates(conn, as_of) + (candidates(conn, f, as_of) if not f.empty else []) + \
        anomaly_candidates(conn, as_of) + follow_through_candidates(conn, as_of)  # G4/G3, G5
    week = as_of - timedelta(days=as_of.weekday())
    prev = {r["key"]: r for r in fetch_all(conn, """SELECT DISTINCT ON (key) key, estimate, p FROM analytics.weekly_insight
                                                    WHERE shown AND week_start < %s
                                                    ORDER BY key, week_start DESC""", (week,))}
    ranked = rank(cands, acted_on(conn), prev)
    rows = [{"week_start": week, "key": c["key"], "kind": c["kind"], "text": c["text"], "p": c["p"],
             "estimate": c["estimate"], "novelty": c["novelty"], "actionability": c["actionability"],
             "priority": c["priority"], "recency": c["recency"], "moved": c["moved"], "score": c["score"],
             "shown": c["shown"], "record": {k: v for k, v in c.items() if k not in ("text",)}} for c in ranked]
    return upsert(conn, "analytics.weekly_insight", rows, conflict=["week_start", "key"]) if rows else 0
