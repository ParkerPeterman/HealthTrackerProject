"""The starter rule set. Each rule reads a RuleContext and returns a RuleResult.

Thresholds come from analytics.rule_parameter (`p`), never from code, so the
weekly tuning job can move them within bounds. Action texts are written in the
user's display units; metrics and targets are stored metric for grading.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from healthtracker.rules.base import Action, RuleContext, RuleResult, rule
from healthtracker.units import fmt_hour

LOWER_BODY = {"quads", "hamstrings", "glutes", "calves", "adductors"}

# Restricted activity -> sports that involve it.
ACTIVITY_SPORTS = {
    "running": {"run"},
    "sprinting": {"ultimate", "soccer", "basketball"},
    "jumping": {"ultimate", "basketball"},
    "cutting": {"ultimate", "soccer", "basketball"},
    "ultimate": {"ultimate"},
    "lifting": {"strength"},
    "cycling": {"bike"},
}


def label(code: str) -> str:
    return code.replace("_", " ")


def _training_day(ctx: RuleContext, d: date) -> bool:
    row = ctx.row(d)
    return bool(row.get("n_workouts") or 0) or bool(row.get("had_game"))


def _workouts_on(ctx: RuleContext, d: date) -> pd.DataFrame:
    if ctx.workouts.empty:
        return ctx.workouts
    return ctx.workouts[ctx.workouts["date"] == d]


# ------------------------------------------------------------------ nutrition
@rule("NUTR_PROTEIN_LOW", outcome="soreness@1")
def nutr_protein_low(ctx: RuleContext, p: dict) -> RuleResult:
    y_date = ctx.date - timedelta(days=1)
    y = ctx.yesterday
    if not _training_day(ctx, y_date):
        return RuleResult(False, details={"reason": "rest day yesterday"})
    if not y.get("food_log_complete") or y.get("protein_g_per_kg") is None:
        return RuleResult(False, status="no_data", details={"reason": "food log incomplete"})
    per_kg, target = float(y["protein_g_per_kg"]), float(p["min_g_per_kg"])
    details = {"protein_g": y.get("protein_g"), "protein_g_per_kg": per_kg, "min_g_per_kg": target}
    if per_kg >= target:
        return RuleResult(False, details=details)
    weight = ctx.today.get("weight_kg_smoothed") or y.get("weight_kg_smoothed") or y.get("weight_kg")
    grams = int(math.ceil(target * float(weight) / 5.0) * 5) if weight else None
    details["target_g_today"] = grams
    actions = []
    if grams:
        actions.append(Action(
            f"Eat at least {grams} g of protein today (yesterday: {y['protein_g']:.0f} g, "
            f"{per_kg:.1f} g/kg on a training day; target {target:.1f} g/kg)",
            "protein_g", ">=", grams, ctx.date))
    return RuleResult(True, 2, details, actions)


@rule("NUTR_CALORIE_DRIFT", outcome="energy@1")
def nutr_calorie_drift(ctx: RuleContext, p: dict) -> RuleResult:
    target = next((t for t in ctx.nutrient_targets if t["nutrient"] == "kcal" and t["basis"] == "per_day"), None)
    if not target or (target["min_value"] is None and target["max_value"] is None):
        return RuleResult(False, status="no_data", details={"reason": "no calorie goal set"})
    lo_t, hi_t = target["min_value"], target["max_value"]
    goal = (lo_t + hi_t) / 2 if lo_t is not None and hi_t is not None else (lo_t or hi_t)
    week = ctx.window(ctx.date - timedelta(days=7), ctx.date - timedelta(days=1))
    logged = week[week["food_log_complete"].fillna(False).astype(bool)]["kcal"].dropna() if not week.empty else []
    if len(logged) < p["min_logged_days"]:
        return RuleResult(False, status="no_data",
                          details={"logged_days": len(logged), "min_logged_days": p["min_logged_days"]})
    avg = float(np.mean(logged))
    pct = (avg - goal) / goal * 100
    details = {"avg_kcal_7d": round(avg), "goal_kcal": round(goal), "pct_from_goal": round(pct, 1),
               "logged_days": len(logged)}
    if abs(pct) <= p["max_pct_from_goal"]:
        return RuleResult(False, details=details)
    lo = round((lo_t if lo_t is not None else goal * 0.95) / 50) * 50
    hi = round((hi_t if hi_t is not None else goal * 1.05) / 50) * 50
    direction = "below" if pct < 0 else "above"
    action = Action(
        f"Aim for {lo}-{hi} kcal today; your 7-day average is {round(avg)} kcal, "
        f"{abs(round(pct))}% {direction} your goal of {round(goal)}",
        "kcal", "between", lo, ctx.date, target_value_high=hi)
    severity = 2 if abs(pct) > 2 * p["max_pct_from_goal"] else 1
    return RuleResult(True, severity, details, [action])


NUTRIENT_LABELS = {
    "fiber_g": ("fiber", "g"), "iron_mg": ("iron", "mg"), "magnesium_mg": ("magnesium", "mg"),
    "calcium_mg": ("calcium", "mg"), "potassium_mg": ("potassium", "mg"),
    "vitamin_d_mcg": ("vitamin D", "mcg"), "zinc_mg": ("zinc", "mg"), "sodium_mg": ("sodium", "mg"),
    "vitamin_c_mg": ("vitamin C", "mg"), "omega3_g": ("omega-3", "g"), "sugar_g": ("sugar", "g"),
}


@rule("NUTR_MICRO_GAP", outcome="energy@1")
def nutr_micro_gap(ctx: RuleContext, p: dict) -> RuleResult:
    weekly = [t for t in ctx.nutrient_targets
              if t["evaluation"] == "weekly_avg" and t["basis"] == "per_day" and t["nutrient"] != "kcal"]
    if not weekly:
        return RuleResult(False, status="no_data", details={"reason": "no weekly nutrient targets"})
    nd = ctx.nutrients_daily
    start, end = ctx.date - timedelta(days=7), ctx.date - timedelta(days=1)
    if nd.empty:
        return RuleResult(False, status="no_data", details={"reason": "no complete food days"})
    week = nd[(nd["date"] >= start) & (nd["date"] <= end)]
    days = week["date"].nunique()
    if days < p["min_logged_days"]:
        return RuleResult(False, status="no_data", details={"logged_days": int(days)})
    gaps = []
    for t in weekly:
        amounts = week[week["nutrient"] == t["nutrient"]].groupby("date")["amount"].sum()
        avg = float(amounts.sum() / days)  # days with none of it count as zero intake
        if t["min_value"] is not None and avg < t["min_value"]:
            gaps.append({"nutrient": t["nutrient"], "avg": round(avg, 1), "target": t["min_value"],
                         "kind": "low", "shortfall": 1 - avg / t["min_value"]})
        elif t["max_value"] is not None and avg > t["max_value"]:
            gaps.append({"nutrient": t["nutrient"], "avg": round(avg, 1), "target": t["max_value"],
                         "kind": "high", "shortfall": avg / t["max_value"] - 1})
    details = {"logged_days": int(days), "gaps": gaps}
    if not gaps:
        return RuleResult(False, details=details)
    actions = []
    for g in sorted(gaps, key=lambda g: -g["shortfall"])[:2]:
        name, unit = NUTRIENT_LABELS.get(g["nutrient"], (label(g["nutrient"]), ""))
        tgt = round(g["target"], 1) if g["target"] < 20 else round(g["target"])
        if g["kind"] == "low":
            actions.append(Action(
                f"Get at least {tgt} {unit} of {name} today (7-day average {g['avg']} {unit})",
                f"nutrient:{g['nutrient']}", ">=", tgt, ctx.date))
        else:
            actions.append(Action(
                f"Keep {name} at or under {tgt} {unit} today (7-day average {g['avg']} {unit})",
                f"nutrient:{g['nutrient']}", "<=", tgt, ctx.date))
    return RuleResult(True, 1, details, actions)


# ------------------------------------------------------------------ training
@rule("TRAIN_POST_GAME", outcome="hrv_z_30d@1")
def train_post_game(ctx: RuleContext, p: dict) -> RuleResult:
    thr, days = p["hard_rpe_threshold"], int(p.get("recovery_days", 1))
    w = ctx.workouts
    if w.empty:
        return RuleResult(False, details={"reason": "no workouts"})
    recent = w[(w["date"] >= ctx.date - timedelta(days=days)) & (w["date"] < ctx.date)]
    hard = recent[recent["is_game"] | (recent["rpe_eff"] >= thr)]
    if hard.empty:
        return RuleResult(False, details={"recovery_days": days})
    last = hard.sort_values("start_ts").iloc[-1]
    what = "a game" if last["is_game"] else f"a hard {last['sport']} session (RPE {int(last['rpe_eff'])})"
    when = "yesterday" if last["date"] == ctx.date - timedelta(days=1) else f"on {last['date']:%a}"
    details = {"last_hard_date": str(last["date"]), "was_game": bool(last["is_game"]),
               "rpe": float(last["rpe_eff"]), "hard_rpe_threshold": thr, "recovery_days": days}
    today_plan = ctx.planned_on(ctx.date)
    if any(s["session_type"] == "game" for s in today_plan):
        details["game_today"] = True
        return RuleResult(True, 1, details)
    cap = int(thr) - 1
    conflict = [s for s in today_plan if s["session_type"] in ("hard", "game")
                or (s.get("planned_rpe") or 0) >= thr]
    details["planned_hard_today"] = bool(conflict)
    action = Action(f"Keep today easy: nothing above RPE {cap}, since you had {what} {when}",
                    "max_session_rpe", "<=", cap, ctx.date)
    return RuleResult(True, 3 if conflict else 2, details, [action])


@rule("TRAIN_LOAD_RAMP", outcome="hrv_z_30d@1")
def train_load_ramp(ctx: RuleContext, p: dict) -> RuleResult:
    hist = ctx.window(ctx.date - timedelta(days=28), ctx.date - timedelta(days=1))
    if len(hist) < 28:
        return RuleResult(False, status="no_data", details={"days": len(hist)})
    loads = hist["session_load"].fillna(0).astype(float)
    this_week = float(loads.iloc[-7:].sum())
    prior_avg = float(loads.iloc[:21].sum() / 3)
    if prior_avg <= 0:
        return RuleResult(False, status="no_data", details={"reason": "no prior load"})
    pct = (this_week - prior_avg) / prior_avg * 100
    details = {"load_7d": round(this_week), "prior_weekly_avg": round(prior_avg), "pct_increase": round(pct, 1),
               "max_weekly_increase_pct": p["max_weekly_increase_pct"]}
    if pct <= p["max_weekly_increase_pct"]:
        return RuleResult(False, details=details)
    cap = round(prior_avg * (1 + p["max_weekly_increase_pct"] / 100))
    action = Action(
        f"Hold your load steady: keep the rolling 7-day load at or under {cap} "
        f"(now {round(this_week)}, up {round(pct)}% on your 3-week average of {round(prior_avg)})",
        "acute_load_7d", "<=", cap, ctx.date + timedelta(days=3))
    return RuleResult(True, 2, details, [action])


@rule("TRAIN_MUSCLE_VOLUME", outcome="soreness@1")
def train_muscle_volume(ctx: RuleContext, p: dict) -> RuleResult:
    if not ctx.lifted_recently:
        return RuleResult(False, details={"reason": "no lifting in the last 14 days"})
    hs = ctx.hard_sets
    week = hs[(hs["date"] >= ctx.date - timedelta(days=7)) & (hs["date"] < ctx.date)] if not hs.empty else hs
    lo, hi = p["min_hard_sets"], p["max_hard_sets"]
    # Low volume is flagged only for groups I normally train at volume (>= lo in one of the
    # four weeks before this one), so a drop is caught without nagging about minor groups.
    trained = set()
    if not hs.empty:
        for k in range(1, 5):
            wk = hs[(hs["date"] >= ctx.date - timedelta(days=7 * (k + 1))) & (hs["date"] < ctx.date - timedelta(days=7 * k))]
            trained |= {g for g, v in wk.groupby("muscle_group")["set_credit"].sum().items() if v >= lo}
    sets = week.groupby("muscle_group")["set_credit"].sum().to_dict() if not week.empty else {}
    low = {g: round(sets.get(g, 0.0), 1) for g in trained if sets.get(g, 0.0) < lo}
    high = {g: round(v, 1) for g, v in sets.items() if v > hi}
    details = {"hard_sets_7d": {k: round(v, 1) for k, v in sets.items()}, "low": low, "high": high,
               "min_hard_sets": lo, "max_hard_sets": hi}
    if not low and not high:
        return RuleResult(False, details=details)
    if ctx.fired_within("TRAIN_MUSCLE_VOLUME", 7):  # weekly check-in, not a daily nag
        return RuleResult(False, details={**details, "suppressed": "fired in the last 7 days"})
    actions = []
    for g, v in sorted(high.items(), key=lambda kv: -kv[1])[:2]:
        actions.append(Action(f"Hold {label(g)} to {int(hi)} hard sets this week (now {v:g})",
                              f"hard_sets_7d:{g}", "<=", hi, ctx.date + timedelta(days=3)))
    for g, v in sorted(low.items(), key=lambda kv: kv[1])[:2]:
        need = math.ceil(lo - v)
        actions.append(Action(
            f"Add {need} hard sets for {label(g)} in the next 3 days ({v:g} of {int(lo)} this week)",
            f"hard_sets_7d:{g}", ">=", lo, ctx.date + timedelta(days=3)))
    return RuleResult(True, 1, details, actions)


@rule("TRAIN_MUSCLE_RECOVERY", outcome="soreness@1")
def train_muscle_recovery(ctx: RuleContext, p: dict) -> RuleResult:
    plan = [s for s in ctx.planned_on(ctx.date) if s["session_type"] in ("strength", "hard")
            and s.get("focus_muscles")]
    if not plan:
        return RuleResult(False, details={"reason": "no strength session with focus muscles planned"})
    hs = ctx.hard_sets
    if hs.empty:
        return RuleResult(False, details={"reason": "no recent lifting"})
    ref = datetime.combine(ctx.date, datetime.min.time(), ctx.profile.tz) + timedelta(hours=17)
    window_start = ref - timedelta(hours=p["min_hours"])
    recent = hs[(hs["start_ts"] >= window_start) & (hs["start_ts"] < ref)]
    per_group = recent.groupby("muscle_group").agg(sets=("set_credit", "sum"), last=("start_ts", "max"))
    per_group = per_group[per_group["sets"] >= 2]
    focus = {m for s in plan for m in s["focus_muscles"]}
    clash = {g: r for g, r in per_group.iterrows() if g in focus}
    details = {"focus_muscles": sorted(focus), "min_hours": p["min_hours"],
               "recent": {g: round(float(r["sets"]), 1) for g, r in per_group.iterrows()}}
    if not clash:
        return RuleResult(False, details=details)
    actions = []
    for g, r in list(clash.items())[:3]:
        hours = int((ref - r["last"].to_pydatetime()).total_seconds() // 3600)
        actions.append(Action(
            f"Skip hard {label(g)} work today: trained {hours}h ago, under the {int(p['min_hours'])}h minimum",
            f"hard_sets_day:{g}", "<=", 0, ctx.date))
    details["clash"] = sorted(clash)
    return RuleResult(True, 2, details, actions)


@rule("TRAIN_PRE_GAME_LEGS", outcome="soreness@1")
def train_pre_game_legs(ctx: RuleContext, p: dict) -> RuleResult:
    tomorrow = ctx.date + timedelta(days=1)
    if not any(s["session_type"] == "game" for s in ctx.planned_on(tomorrow)):
        return RuleResult(False, details={"reason": "no game tomorrow"})
    heavy = []
    for s in ctx.planned_on(ctx.date):
        focus = set(s.get("focus_muscles") or [])
        if s["session_type"] == "strength" and (not focus or focus & LOWER_BODY):
            heavy.append(s)
        elif s["session_type"] == "hard" and s["sport"] in ("run", "strength"):
            heavy.append(s)
    details = {"game_tomorrow": True, "heavy_sessions_today": [s["sport"] for s in heavy]}
    if not heavy:
        return RuleResult(False, details=details)
    cap = p.get("max_lower_hard_sets", 0)
    text = ("No heavy leg work today: game tomorrow" if cap == 0
            else f"Keep leg work to {int(cap)} hard sets today: game tomorrow")
    return RuleResult(True, 2, details, [Action(text, "lower_hard_sets_day", "<=", cap, ctx.date)])


@rule("TRAIN_LIFT_PLATEAU")
def train_lift_plateau(ctx: RuleContext, p: dict) -> RuleResult:
    lp = ctx.lift_progress
    if lp.empty:
        return RuleResult(False, details={"reason": "no main-lift history"})
    weeks = int(p["weeks_flat"])
    recent_start = ctx.date - timedelta(days=7 * weeks)
    prior_start = recent_start - timedelta(days=7 * weeks)
    plateaus = []
    for ex_id, g in lp.groupby("exercise_id"):
        recent = g[(g["date"] >= recent_start) & (g["date"] < ctx.date)]
        prior = g[(g["date"] >= prior_start) & (g["date"] < recent_start)]
        if len(recent) < weeks or prior.empty:
            continue
        best_recent, best_prior = float(recent["best_e1rm_kg"].max()), float(prior["best_e1rm_kg"].max())
        gain = (best_recent - best_prior) / best_prior * 100
        if gain < p["min_improvement_pct"]:
            plateaus.append({"exercise_id": int(ex_id), "name": g["name"].iloc[0],
                             "best_recent_kg": best_recent, "best_prior_kg": best_prior,
                             "gain_pct": round(gain, 1), "sessions": len(recent)})
    details = {"weeks_flat": weeks, "plateaus": plateaus}
    if not plateaus:
        return RuleResult(False, details=details)
    if ctx.fired_within("TRAIN_LIFT_PLATEAU", 7):
        return RuleResult(False, details={**details, "suppressed": "fired in the last 7 days"})
    d = ctx.display
    actions = [Action(
        f"{pl['name']}: estimated 1RM flat for {weeks} weeks ({d.weight(pl['best_recent_kg'], 0):g} "
        f"{d.weight_unit}). Change the stimulus: new rep range or a deload week",
        f"e1rm_best:{pl['exercise_id']}", ">", round(pl["best_recent_kg"], 1),
        ctx.date + timedelta(days=21)) for pl in plateaus[:2]]
    return RuleResult(True, 1, details, actions)


# ------------------------------------------------------------------ recovery
@rule("RECOV_HIGH_LOAD_LOW_HRV", outcome="hrv_z_30d@1")
def recov_high_load_low_hrv(ctx: RuleContext, p: dict) -> RuleResult:
    y, t = ctx.yesterday, ctx.today
    load_z, hrv_z = y.get("load_z_28d"), t.get("hrv_z_30d")
    if load_z is None or hrv_z is None:
        return RuleResult(False, status="no_data", details={"load_z": load_z, "hrv_z": hrv_z})
    details = {"load_z_yesterday": load_z, "hrv_z_today": hrv_z, "hrv_today": t.get("hrv"),
               "hrv_30d_avg": t.get("hrv_30d_avg"), "session_load_yesterday": y.get("session_load"),
               "load_z_threshold": p["load_z_threshold"], "hrv_z_threshold": p["hrv_z_threshold"]}
    if not (load_z >= p["load_z_threshold"] and hrv_z <= p["hrv_z_threshold"]):
        return RuleResult(False, details=details)
    cap = int(p.get("easy_max_rpe", 4))
    action = Action(
        f"Make today a recovery day (RPE {cap} or less): HRV {t['hrv']:.0f} ms is {abs(hrv_z):.1f} SD "
        f"below your 30-day baseline after a high-load day",
        "max_session_rpe", "<=", cap, ctx.date)
    return RuleResult(True, 2, details, [action])


@rule("RECOV_ILLNESS_FLAG", outcome="hrv_z_30d@1")
def recov_illness_flag(ctx: RuleContext, p: dict) -> RuleResult:
    t = ctx.today
    rz, hz = t.get("rhr_z_30d"), t.get("hrv_z_30d")
    if rz is None or hz is None:
        return RuleResult(False, status="no_data", details={"rhr_z": rz, "hrv_z": hz})
    details = {"rhr_z": rz, "hrv_z": hz, "resting_hr": t.get("resting_hr"), "hrv": t.get("hrv"),
               "rhr_z_threshold": p["rhr_z_threshold"], "hrv_z_threshold": p["hrv_z_threshold"]}
    if not (rz >= p["rhr_z_threshold"] and hz <= p["hrv_z_threshold"]):
        return RuleResult(False, details=details)
    action = Action(
        f"Rest today (nothing above RPE 3): resting HR is up {rz:.1f} SD and HRV down {abs(hz):.1f} SD "
        f"together, a pattern seen with illness or overreaching. If you're sick, log it so baselines skip it",
        "max_session_rpe", "<=", 3, ctx.date)
    return RuleResult(True, 3, details, [action])


# ------------------------------------------------------------------ sleep
@rule("SLEEP_SHORT", outcome="energy@0")
def sleep_short(ctx: RuleContext, p: dict) -> RuleResult:
    n, minimum = int(p["consecutive_nights"]), p["min_minutes"]
    nights = [ctx.row(ctx.date - timedelta(days=i)).get("sleep_total_min") for i in range(n)]
    if any(v is None for v in nights):
        return RuleResult(False, status="no_data", details={"nights": nights})
    details = {"nights_min": nights, "min_minutes": minimum, "consecutive_nights": n}
    if not all(v < minimum for v in nights):
        return RuleResult(False, details=details)
    tomorrow = ctx.date + timedelta(days=1)
    hours, mins = divmod(int(minimum), 60)
    actions = [Action(f"Get at least {hours}h{mins:02d} of sleep tonight ({n} short nights in a row, "
                      f"last night {nights[0] // 60}h{nights[0] % 60:02d})",
                      "sleep_total_min", ">=", minimum, tomorrow)]
    wake = ctx.typical_wake_hour
    if wake:
        bedtime = wake - (minimum + 15) / 60
        bedtime = bedtime + 24 if bedtime < 12 else bedtime
        details["bedtime_target"] = fmt_hour(bedtime)
        actions.append(Action(f"Be in bed by {fmt_hour(bedtime)} tonight",
                              "bedtime_hour", "<=", round(bedtime, 2), tomorrow))
    return RuleResult(True, 2, details, actions)


@rule("SLEEP_CAFFEINE_CUTOFF", outcome="sleep_score@1")
def sleep_caffeine_cutoff(ctx: RuleContext, p: dict) -> RuleResult:
    y = ctx.yesterday
    if not y.get("food_log_complete"):
        return RuleResult(False, status="no_data", details={"reason": "food log incomplete"})
    last = y.get("last_caffeine_hour")
    cutoff = p["cutoff_hour"]
    details = {"last_caffeine_hour_yesterday": last, "cutoff_hour": cutoff}
    if last is None or last <= cutoff:
        return RuleResult(False, details=details)
    action = Action(f"No caffeine after {fmt_hour(cutoff)} today (yesterday's last was {fmt_hour(last)})",
                    "last_caffeine_hour", "<=", cutoff, ctx.date)
    return RuleResult(True, 1, details, [action])


# ------------------------------------------------------------------ injury
def _restriction_target(r: dict) -> str:
    if r["target_type"] == "exercise":
        return r.get("exercise_name") or f"exercise {r['exercise_id']}"
    return label(r.get(r["target_type"]) or "")


@rule("INJ_RESTRICTION", outcome="max_pain_activity@1")
def inj_restriction(ctx: RuleContext, p: dict) -> RuleResult:
    active = [(inj, r) for inj in ctx.injuries for r in inj["restrictions"]
              if r["start_date"] <= ctx.date and (r["end_date"] is None or r["end_date"] >= ctx.date)]
    if not active:
        return RuleResult(False, details={"reason": "no active restrictions"})
    plan = [s for s in ctx.planned_on(ctx.date) if s["session_type"] != "rest"]
    conflicts, actions = [], []
    for inj, r in active:
        where = f"{inj['side'] + ' ' if inj.get('side') not in (None, 'n/a') else ''}{inj['body_part']}"
        max_rpe = r["max_rpe"]
        tgt = _restriction_target(r)
        for s in plan:
            hit = False
            if r["target_type"] == "activity":
                hit = s["sport"] in ACTIVITY_SPORTS.get(r["activity"], {r["activity"]})
            elif r["target_type"] == "muscle_group":
                hit = r["muscle_group"] in (s.get("focus_muscles") or [])
            elif r["target_type"] in ("exercise", "movement_pattern"):
                hit = s["session_type"] == "strength"
            planned_rpe = s.get("planned_rpe")
            if hit and (not max_rpe or planned_rpe is None or planned_rpe > max_rpe):
                conflicts.append({"restriction_id": r["id"], "sport": s["sport"], "target": tgt})
        limit = ("avoid entirely" if max_rpe == 0 else
                 f"max RPE {max_rpe}" if max_rpe is not None else "limit it")
        if r["target_type"] == "activity" and max_rpe is not None:
            metric, cmp_, val = f"activity_rpe:{r['activity']}", "<=", max_rpe
        elif r["target_type"] == "muscle_group" and max_rpe == 0:
            metric, cmp_, val = f"hard_sets_day:{r['muscle_group']}", "<=", 0
        elif r["target_type"] == "exercise" and max_rpe == 0:
            metric, cmp_, val = f"exercise_sets_day:{r['exercise_id']}", "<=", 0
        elif r["target_type"] == "movement_pattern" and max_rpe == 0:
            metric, cmp_, val = f"pattern_sets_day:{r['movement_pattern']}", "<=", 0
        else:
            metric = None
        if metric and len(actions) < 3:
            actions.append(Action(f"{tgt.capitalize()}: {limit} ({where} restriction)",
                                  metric, cmp_, val, ctx.date))
    details = {"active_restrictions": len(active), "conflicts": conflicts}
    return RuleResult(True, 3 if conflicts else 2, details, actions)


@rule("INJ_RETURN_TO_PLAY", outcome="max_pain_activity@1")
def inj_return_to_play(ctx: RuleContext, p: dict) -> RuleResult:
    injuries = [i for i in ctx.injuries if i["status"] in ("active", "recovering")]
    if not injuries:
        return RuleResult(False, details={"reason": "no active injuries"})
    need, max_pain = int(p["sessions_required"]), p["max_pain_activity"]
    statuses, hold = [], []
    for inj in injuries:
        recent = [c for c in sorted(inj["checkins"], key=lambda c: c["date"], reverse=True)
                  if c["pain_activity"] is not None][:need]
        ok = len(recent) == need and all(c["pain_activity"] <= max_pain for c in recent)
        statuses.append({"injury_id": inj["id"], "body_part": inj["body_part"],
                         "recent_pain": [c["pain_activity"] for c in recent], "progress_allowed": ok})
        if not ok:
            hold.append(inj)
    details = {"injuries": statuses, "max_pain_activity": max_pain, "sessions_required": need}
    if not hold:
        return RuleResult(True, 1, details)  # progression allowed, nothing to grade
    current = ctx.yesterday.get("acute_load_7d")
    parts = ", ".join(i["body_part"] for i in hold)
    actions = []
    if current:
        actions.append(Action(
            f"Hold training load at its current level (7-day load {round(current)}) until {parts} pain "
            f"during activity stays at {int(max_pain)}/10 or lower for {need} sessions",
            "acute_load_7d", "<=", round(current), ctx.date + timedelta(days=2)))
    return RuleResult(True, 2, details, actions)


@rule("INJ_SEE_PROFESSIONAL")
def inj_see_professional(ctx: RuleContext, p: dict) -> RuleResult:
    flagged = []
    for inj in ctx.injuries:
        if inj["status"] == "resolved" or inj.get("seen_professional"):
            continue
        week = sorted([c for c in inj["checkins"] if c["date"] > ctx.date - timedelta(days=8)],
                      key=lambda c: c["date"])
        if not week:
            continue
        last = week[-1]
        reasons = []
        if last.get("swelling"):
            reasons.append("swelling")
        if last.get("function_score") == 1:
            reasons.append("can't bear weight / use it normally")
        pains = [max(c["pain_rest"] or 0, c["pain_activity"] or 0) for c in week]
        if len(pains) >= 3 and pains[-1] - pains[0] >= p.get("pain_increase", 2):
            reasons.append(f"pain up from {pains[0]} to {pains[-1]} this week")
        if reasons:
            flagged.append({"injury_id": inj["id"], "body_part": inj["body_part"], "reasons": reasons})
    if not flagged:
        return RuleResult(False, details={"checked": len(ctx.injuries)})
    actions = [Action(f"See a doctor or physical therapist about your {f['body_part']} "
                      f"({'; '.join(f['reasons'])})",
                      f"injury_seen_professional:{f['injury_id']}", "=", 1, ctx.date + timedelta(days=3))
               for f in flagged]
    return RuleResult(True, 3, {"flagged": flagged}, actions)


# ------------------------------------------------------------------ experiments
@rule("EXPERIMENT_PROTOCOL")
def experiment_protocol(ctx: RuleContext, p: dict) -> RuleResult:
    todays = []
    for e in ctx.experiments:
        cond = (e.get("assignment") or {}).get(ctx.date.isoformat())
        if cond:
            todays.append((e, cond))
    if not todays:
        return RuleResult(False, details={"reason": "no experiment day"})
    actions, details = [], {"experiments": []}
    for e, cond in todays:
        proto = e.get("protocol") or {}
        details["experiments"].append({"experiment_id": e["id"], "condition": cond})
        if cond == "treatment" and proto.get("metric"):
            outcome = f"{proto['outcome']}@{proto.get('outcome_lag', 1)}" if proto.get("outcome") else None
            actions.append(Action(f"Experiment day: {proto['action_text']}", proto["metric"],
                                  proto["comparator"], proto["target_value"], ctx.date,
                                  outcome_metric=outcome))
    return RuleResult(True, 1, details, actions)
