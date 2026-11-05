"""B7: randomized N-of-1 designs in blocks, and choosing what to test next.

Blocks follow the week: Monday-Thursday and Friday-Sunday, so each block is 3-4 days and the
weekend (usually travel) is its own block, with travel as a covariate. Weekday and weekend
blocks are randomized separately, so both conditions get the same days and the same number
of travel weekends. Both conditions are actions on their days ("no
caffeine at or after 12:00" vs "no caffeine at or after 15:00"), graded from the food log,
so nothing extra is logged.

Analysis (intention-to-treat, by assigned condition): outcome = a + b*A + trend +
carryover + travel, AR(1) errors, conjugate Normal-Inverse-Gamma (B1). "Carryover" marks the
first day of a block whose condition differs from the block before.

The next experiment is the one expected to teach the most about priority outcomes (sleep
and endurance): the expected shrinkage of the posterior SD over its outcomes, with the
brief's order breaking ties (caffeine cutoff, then dinner timing, then run time of day).
Designs that conflict with an active injury restriction are skipped; medication is never one.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import fetch_all, fetch_one, insert
from healthtracker.medication import is_sensitive
from healthtracker.stats.bayes import DEFAULT_PRIOR, VAGUE, fit, priors, summarize, swc_params
from healthtracker.stats.records import confidence_word

DESIGNS: dict[str, dict] = {
    "caffeine_cutoff": {
        "rank": 1, "name": "Caffeine cutoff: noon vs 3 pm", "weeks": 4, "activities": [],
        "conditions": {
            "A": {"label": "noon cutoff", "metric": "last_caffeine_hour", "comparator": "<", "target_value": 12,
                  "action_text": "no caffeine at or after 12:00"},
            "B": {"label": "3 pm cutoff", "metric": "last_caffeine_hour", "comparator": "<", "target_value": 15,
                  "action_text": "no caffeine at or after 15:00"}},
        "outcomes": [("sleep_total_min", 1), ("sleep_midpoint_min", 1), ("hrv", 1)]},
    "dinner_timing": {
        "rank": 2, "name": "Dinner timing: 3+ hours before bed vs under 2", "weeks": 4, "activities": [],
        "conditions": {
            "A": {"label": "early dinner", "metric": "last_meal_hour", "comparator": "<", "target_value": "bed-3",
                  "action_text": "finish dinner by {t}"},
            "B": {"label": "late dinner", "metric": "last_meal_hour", "comparator": ">=", "target_value": "bed-2",
                  "action_text": "have dinner at or after {t}"}},
        "outcomes": [("sleep_total_min", 1), ("sleep_midpoint_min", 1), ("hrv", 1)]},
    "run_time": {
        "rank": 3, "name": "Run time of day: morning vs evening", "weeks": 4, "activities": ["running"],
        "mon_thu_only": True,
        "conditions": {
            "A": {"label": "morning run", "metric": "train_end_hour", "comparator": "<", "target_value": 12,
                  "action_text": "do today's run before noon"},
            "B": {"label": "evening run", "metric": "train_end_hour", "comparator": ">=", "target_value": 17,
                  "action_text": "do today's run after 17:00"}},
        "outcomes": [("sleep_total_min", 1), ("hrv", 1)]},
}
PRIORITY = {"sleep_total_min": 1.0, "sleep_midpoint_min": 1.0, "hrv": 1.0, "resting_hr": 0.8}


def blocks(start: date, weeks: int, mon_thu_only: bool = False) -> list[list[date]]:
    monday = start - timedelta(days=start.weekday())
    out = []
    for w in range(weeks):
        m = monday + timedelta(days=7 * w)
        out.append([m + timedelta(days=k) for k in range(4)])
        if not mon_thu_only:
            out.append([m + timedelta(days=k) for k in range(4, 7)])
    return [[d for d in b if d >= start] for b in out if any(d >= start for d in b)]


def assign_blocks(start: date, weeks: int, seed: int | None = None, mon_thu_only: bool = False) -> dict[str, str]:
    """A/B over blocks, randomized separately for weekday and weekend blocks, so days and travel
    weekends are balanced between conditions (a block with too few days left is dropped)."""
    bs = [b for b in blocks(start, weeks, mon_thu_only) if len(b) >= 3]
    rng = np.random.default_rng(seed)
    out: dict[str, str] = {}
    for stratum in ([b for b in bs if b[0].weekday() < 4], [b for b in bs if b[0].weekday() >= 4]):
        stratum = stratum[: len(stratum) - len(stratum) % 2]
        conds = rng.permutation(["A", "B"] * (len(stratum) // 2))
        out.update({d.isoformat(): str(c) for b, c in zip(stratum, conds, strict=True) for d in b})
    return dict(sorted(out.items()))


def resolve_targets(design: dict, typical_bed: float | None) -> dict:
    """Bedtime-relative targets (dinner timing) become clock hours from my usual bedtime."""
    from healthtracker.units import fmt_hour

    conds = {}
    for k, c in design["conditions"].items():
        c = dict(c)
        if isinstance(c["target_value"], str):
            bed = typical_bed or 23.0
            c["target_value"] = round(bed - float(c["target_value"].split("-")[1]), 2)
            c["action_text"] = c["action_text"].format(t=fmt_hour(c["target_value"]))
        conds[k] = c
    return conds


def conflicts(design: dict, restrictions: list[dict]) -> list[str]:
    return [r["activity"] for r in restrictions if r["target_type"] == "activity" and r["activity"] in design["activities"]]


def expected_gain(design: dict, prior_sd: float = 0.5, rho: float = 0.3) -> float:
    """Expected shrinkage of the posterior SD (in nats), summed over priority outcomes."""
    n = 7 * design["weeks"] * (4 / 7 if design.get("mon_thu_only") else 1.0)
    n_eff = n * (1 - rho) / (1 + rho)
    post_var = 1 / (1 / prior_sd**2 + n_eff / 4)  # balanced two-arm difference, unit noise
    gain = 0.5 * np.log(prior_sd**2 / post_var)
    return round(float(sum(PRIORITY.get(o, 0.5) * gain for o, _ in design["outcomes"])), 3)


def next_experiment(conn: psycopg.Connection, d: date) -> dict | None:
    from healthtracker.planning.library import active_restrictions
    from healthtracker.rules.context import load_injuries

    done = {r["design_code"] for r in fetch_all(conn, """SELECT design_code FROM analytics.experiment
                                                         WHERE design_code IS NOT NULL AND status <> 'abandoned'""")}
    rs = active_restrictions(load_injuries(conn, d), d)
    options = []
    for code, des in DESIGNS.items():
        if code in done or any(is_sensitive(c["metric"]) for c in des["conditions"].values()):
            continue
        blocked = conflicts(des, rs)
        options.append({"code": code, "name": des["name"], "weeks": des["weeks"], "gain": expected_gain(des),
                        "rank": des["rank"], "blocked": blocked})
    ok = [o for o in options if not o["blocked"]]
    return min(ok, key=lambda o: (-o["gain"], o["rank"])) if ok else None


def create_block_experiment(conn: psycopg.Connection, code: str, start: date, seed: int | None = None) -> int:
    des = DESIGNS[code]
    if any(is_sensitive(c["metric"]) for c in des["conditions"].values()):
        raise ValueError("medication is never an experiment")
    bed = fetch_one(conn, """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY bedtime_hour) AS b
                             FROM analytics.daily_features WHERE bedtime_hour IS NOT NULL AND date >= %s""",
                    (start - timedelta(days=28),))
    conds = resolve_targets(des, bed["b"] if bed else None)
    assignment = assign_blocks(start, des["weeks"], seed, des.get("mon_thu_only", False))
    days = sorted(assignment)
    first = des["outcomes"][0]
    protocol = {"design": code, "conditions": conds, "outcomes": [list(o) for o in des["outcomes"]],
                "outcome": first[0], "outcome_lag": first[1], "block": "Mon-Thu" + ("" if des.get("mon_thu_only")
                                                                                     else " and Fri-Sun")}
    return insert(conn, "analytics.experiment", {
        "hypothesis_id": None, "design_code": code,
        "design": f"randomized blocks of 3-4 days ({protocol['block']}), {len(days)} days: {des['name']}",
        "start_date": date.fromisoformat(days[0]), "end_date": date.fromisoformat(days[-1]),
        "assignment": assignment, "protocol": protocol,
        "status": "running" if date.fromisoformat(days[0]) <= date.today() else "planned"})


# ------------------------------------------------------------------ analysis
def block_frame(features: pd.DataFrame, assignment: dict[str, str], outcome: str, lag: int) -> pd.DataFrame:
    days = sorted(pd.Timestamp(d) for d in assignment)
    rows, prev_cond, prev_day = [], None, None
    for d in days:
        cond = assignment[d.date().isoformat()]
        new_block = prev_day is None or (d - prev_day).days > 1 or d.weekday() in (0, 4)
        carry = float(new_block and prev_cond is not None and cond != prev_cond)
        od = d + pd.Timedelta(days=lag)
        y = features[outcome].get(od) if outcome in features else None
        travel = features["is_travel"].get(d) if "is_travel" in features else None
        rows.append({"date": d, "Y": np.nan if y is None or pd.isna(y) else float(y), "A": float(cond == "A"),
                     "ctl_trend": float((d - days[0]).days), "ctl_carry": carry,
                     "ctl_travel": float(bool(travel)) if travel is not None and not pd.isna(travel) else 0.0})
        prev_cond, prev_day = cond, d
    return pd.DataFrame(rows).set_index("date").dropna(subset=["Y"])


def analyze_blocks(features: pd.DataFrame, exp: dict, pri: dict, swc: dict) -> dict:
    proto = exp["protocol"] or {}
    code = proto.get("design")
    out = {}
    for outcome, lag in proto.get("outcomes", []):
        df = block_frame(features, exp["assignment"], outcome, lag)
        if df["A"].sum() < 4 or (1 - df["A"]).sum() < 4:
            out[outcome] = {"n_days": len(df), "note": "not enough outcome data"}
            continue
        y_sd = float(df["Y"].std()) or 1.0
        y = ((df["Y"] - df["Y"].mean()) / y_sd).to_numpy()
        ctl = df[[c for c in df.columns if c.startswith("ctl_")]]
        ctl = ctl.loc[:, ctl.std() > 0]
        X = np.column_stack([df["A"].to_numpy(), ((ctl - ctl.mean()) / ctl.std()).to_numpy(), np.ones(len(df))])
        idx = pd.DatetimeIndex(df.index)
        consecutive = np.r_[False, (idx[1:] - idx[:-1]).days == 1]
        mean, sd, src = pri.get((f"experiment:{code}", outcome, lag), DEFAULT_PRIOR)
        m0, v0 = np.zeros(X.shape[1]), np.full(X.shape[1], VAGUE)
        m0[0], v0[0] = mean, sd**2
        post = fit(X, y, consecutive, m0, v0)
        if post is None:
            out[outcome] = {"n_days": len(df), "note": "not enough consecutive days"}
            continue
        s = summarize(post, 0, y_sd, swc.get(outcome, swc.get("default", 0.2)))
        out[outcome] = {"n_days": len(df), "rho": round(post["rho"], 3), "prior_source": src, **s,
                        "confidence": confidence_word(s["p_meaningful"])}
    return out


def summary_text(exp: dict, result: dict, adherence: dict | None) -> str:
    conds = (exp["protocol"] or {}).get("conditions", {})
    a, b = conds.get("A", {}).get("label", "A"), conds.get("B", {}).get("label", "B")
    parts = []
    for outcome, r in result.items():
        if "estimate" not in r:
            parts.append(f"{outcome}: {r['note']}")
            continue
        parts.append(f"{outcome} {r['estimate']:+g} (80% interval {r['lo80']:g} to {r['hi80']:g}; {r['confidence']})")
    adh = f"; adherence {adherence['rate'] * 100:.0f}% of {adherence['n']} days" if adherence and adherence["n"] else ""
    return f"{a.capitalize()} vs {b}: " + "; ".join(parts) + adh + "."


def finish_block_experiment(conn: psycopg.Connection, exp: dict) -> tuple[str, dict]:
    from healthtracker.stats.insights import load_features_frame

    result = analyze_blocks(load_features_frame(conn), exp, priors(conn), swc_params(conn))
    adherence = fetch_one(conn, """SELECT avg(a.followed::int) AS rate, count(*) AS n
                                   FROM analytics.recommendation_action a
                                   WHERE a.rule_id = 'EXPERIMENT_PROTOCOL' AND a.followed IS NOT NULL
                                     AND a.evaluate_on BETWEEN %s AND %s""", (exp["start_date"], exp["end_date"]))
    return summary_text(exp, result, adherence), result
