"""Weekly plan generator: next week's sessions around fixed games, by rules.

The plan targets a weekly load from my own chronic load, nudged up only when
recovery looks good and capped by the load-ramp parameter. Games and manual
sessions are fixed; generated sessions fill the rest:
  * the day before a game is easy (never lower-body strength)
  * the day after a game is rest or easy
  * strength sessions are at least two days apart, split lower/upper
  * at most one quality run a week, and none in a two-game week
  * active injury restrictions remove or cap the matching sessions
Regenerating replaces only generated sessions that are still 'planned'.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg

from healthtracker.db import execute, fetch_all, fetch_one, insert, upsert
from healthtracker.jsonutil import to_jsonable
from healthtracker.profile import load_profile
from healthtracker.rules.context import load_injuries, load_params

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
LOWER = ["quads", "hamstrings", "glutes", "calves"]
UPPER = ["chest", "upper_back", "lats", "shoulders", "biceps", "triceps"]
COLD_START_WEEKLY_LOAD = 1500.0


@dataclass
class Day:
    date: date
    fixed: list[dict] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)
    sessions: list[dict] = field(default_factory=list)

    @property
    def free(self) -> bool:
        return not self.fixed and not self.sessions and "rest" not in self.tags


def _session(d: date, sport: str, stype: str, minutes: int, rpe: int, focus=None, note="") -> dict:
    return {"date": d, "sport": sport, "session_type": stype, "planned_duration_min": minutes,
            "planned_rpe": rpe, "focus_muscles": focus, "status": "planned", "origin": "generated",
            "notes": note or None}


def _load(s: dict) -> float:
    return float((s.get("planned_rpe") or 0) * (s.get("planned_duration_min") or 0))


def plan_week(conn: psycopg.Connection, week_start: date) -> dict:
    profile = load_profile(conn)
    prefs = profile.prefs
    params = load_params(conn)
    ramp = params.get("TRAIN_LOAD_RAMP", {}).get("max_weekly_increase_pct", 10)
    days = [Day(week_start + timedelta(days=i)) for i in range(7)]
    by_date = {d.date: d for d in days}

    fixed = fetch_all(conn, """SELECT * FROM core.planned_session WHERE date BETWEEN %s AND %s
                               AND NOT (origin = 'generated' AND status = 'planned')""",
                      (week_start, week_start + timedelta(days=6)))
    for s in fixed:
        if s["session_type"] == "game":
            s.setdefault("planned_rpe", None)
            s["planned_rpe"] = s["planned_rpe"] or 8
            s["planned_duration_min"] = s["planned_duration_min"] or 90
        by_date[s["date"]].fixed.append(s)

    # Games shape the week. Also look one day past the week for a Monday game.
    game_dates = {s["date"] for s in fixed if s["session_type"] == "game"}
    nxt = fetch_one(conn, """SELECT 1 FROM core.planned_session WHERE date = %s AND session_type = 'game'
                             AND status <> 'skipped'""", (week_start + timedelta(days=7),))
    last_prev = fetch_one(conn, """SELECT 1 FROM core.planned_session WHERE date = %s AND session_type='game'""",
                          (week_start - timedelta(days=1),))
    for d in days:
        if d.date in game_dates:
            d.tags.add("game")
        if d.date + timedelta(days=1) in game_dates or (d.date == days[-1].date and nxt):
            d.tags.add("pre_game")
        if d.date - timedelta(days=1) in game_dates or (d.date == week_start and last_prev):
            d.tags.add("post_game")

    # Recovery state and target load
    f = fetch_one(conn, "SELECT * FROM analytics.daily_features WHERE date < %s ORDER BY date DESC LIMIT 1",
                  (week_start,)) or {}
    chronic = f.get("chronic_load_28d") or COLD_START_WEEKLY_LOAD
    acwr = f.get("acwr")
    flags = fetch_all(conn, """SELECT rule_id FROM analytics.daily_flag WHERE fired
                               AND date >= %s AND rule_id IN ('RECOV_ILLNESS_FLAG','RECOV_HIGH_LOAD_LOW_HRV',
                               'INJ_RETURN_TO_PLAY') AND (details->>'eval_status') = 'evaluated'
                               AND coalesce(severity, 0) >= 2""",
                      (week_start - timedelta(days=4),))
    caution = sorted({r["rule_id"] for r in flags})
    if caution:
        factor, why = 0.85, f"recent recovery flags ({', '.join(caution)})"
    elif acwr is not None and acwr > 1.3:
        factor, why = 0.9, f"acute:chronic {acwr:.2f} is high"
    elif acwr is not None and acwr < 0.8:
        factor, why = 1 + ramp / 100, f"acute:chronic {acwr:.2f} is low, building back"
    else:
        factor, why = 1.05, "recovery looks normal: small progression"
    target = round(min(chronic * factor, chronic * (1 + ramp / 100)))

    # Injury limits
    run_cap, no_lower, no_run = 10, False, False
    for inj in load_injuries(conn, week_start):
        for r in inj["restrictions"]:
            if r["end_date"] is not None and r["end_date"] < week_start:
                continue
            if r["target_type"] == "activity" and r["activity"] in ("running", "sprinting"):
                if r["max_rpe"] == 0 and r["activity"] == "running":
                    no_run = True
                elif r["max_rpe"] is not None:
                    run_cap = min(run_cap, r["max_rpe"])
            if r["target_type"] == "muscle_group" and r["muscle_group"] in LOWER and r["max_rpe"] == 0:
                no_lower = True
            if r["target_type"] == "movement_pattern" and r["movement_pattern"] in ("squat", "hinge", "lunge") \
                    and r["max_rpe"] == 0:
                no_lower = True

    # Rest days: post-game first, then Monday
    rest_needed = int(prefs["min_rest_days"])
    for d in [d for d in days if "post_game" in d.tags and not d.fixed] + [days[0]]:
        if rest_needed <= 0:
            break
        if d.free:
            d.tags.add("rest")
            d.sessions.append(_session(d.date, "rest", "rest", 0, 1, note="recovery day"))
            rest_needed -= 1

    # Strength: spaced >= 2 days, never the day before a game
    lifts = int(prefs["lifts_per_week"])
    lifts -= sum(1 for s in fixed if s["session_type"] == "strength")
    focus_cycle = [None] if lifts == 1 else (["upper", "upper"] if no_lower else ["lower", "upper"])
    last_lift = None
    for d in days:
        if lifts <= 0:
            break
        if not d.free or "pre_game" in d.tags or "game" in d.tags:
            continue
        if last_lift and (d.date - last_lift).days < 2:
            continue
        kind = focus_cycle[(int(prefs["lifts_per_week"]) - lifts) % len(focus_cycle)]
        if kind == "lower" and "post_game" in d.tags:
            kind = "upper"
        focus = LOWER if kind == "lower" else UPPER if kind == "upper" else (UPPER if no_lower else LOWER + UPPER)
        d.sessions.append(_session(d.date, "strength", "strength", 60, 6, focus,
                                   f"{kind or 'full'}-body strength"))
        last_lift = d.date
        lifts -= 1

    # Runs: long run on the preferred day, one quality run if the week allows
    runs = 0 if no_run else int(prefs["runs_per_week"]) - sum(1 for s in fixed if s["sport"] == "run")
    long_idx = DAY_NAMES.index(prefs.get("long_run_day", "sun"))
    order = sorted(range(7), key=lambda i: (abs(i - long_idx), i))
    long_day = next((days[i] for i in order if days[i].free and not days[i].tags & {"pre_game", "post_game"}), None)
    planned_runs = []
    if runs > 0 and long_day:
        planned_runs.append((long_day, "moderate", 60, min(4, run_cap), "long run"))
        long_day.tags.add("long")
        runs -= 1
    quality_ok = len(game_dates) < 2 and not caution and run_cap >= 7
    for d in days:
        if runs <= 0:
            break
        if d.fixed or "rest" in d.tags or "long" in d.tags or "game" in d.tags:
            continue
        has_lift = any(s["sport"] == "strength" for s in d.sessions)
        if has_lift and "post_game" not in d.tags and len([x for x in days if x.free]) > runs:
            continue
        if quality_ok and not d.tags & {"pre_game", "post_game"} and not has_lift:
            planned_runs.append((d, "hard", 45, 7, "quality run: intervals or tempo"))
            quality_ok = False
        else:
            planned_runs.append((d, "easy", 35, min(3, run_cap), "easy aerobic run"))
        runs -= 1

    # Scale run minutes so the week lands near the target load
    fixed_load = sum(_load(s) for d in days for s in d.fixed)
    lift_load = sum(_load(s) for d in days for s in d.sessions if s["sport"] == "strength")
    run_base = sum(m * r for _, _, m, r, _ in planned_runs)
    scale = 1.0
    if run_base > 0:
        scale = max(0.7, min(1.3, (target - fixed_load - lift_load) / run_base))
    for d, stype, minutes, rpe, note in planned_runs:
        mins = int(round(minutes * scale / 5) * 5)
        d.sessions.append(_session(d.date, "run", stype, max(20, mins), rpe, note=note))

    sessions = [s for d in days for s in d.sessions]
    planned_load = round(fixed_load + sum(_load(s) for s in sessions))
    return {
        "week_start": week_start, "target_load": target, "planned_load": planned_load,
        "chronic_weekly_load": round(chronic), "acwr": acwr, "reason": why,
        "games": sorted(str(g) for g in game_dates),
        "limits": {"run_rpe_cap": run_cap if run_cap < 10 else None, "no_lower_strength": no_lower,
                   "no_running": no_run},
        "sessions": sessions, "fixed": [{k: s[k] for k in ("date", "sport", "session_type")} for s in fixed],
    }


def render_plan(plan: dict) -> str:
    lines = [f"**Plan for the week of {plan['week_start']:%b %d}**", "",
             f"Target load {plan['target_load']} (your 4-week weekly average is {plan['chronic_weekly_load']}; "
             f"{plan['reason']}). Planned: {plan['planned_load']}.", ""]
    by_day: dict[date, list[str]] = {}
    for s in plan["fixed"]:
        by_day.setdefault(s["date"], []).append(f"{s['session_type']} {s['sport']}".replace("game ultimate", "ultimate game"))
    for s in plan["sessions"]:
        if s["session_type"] == "rest":
            by_day.setdefault(s["date"], []).append("rest")
        else:
            by_day.setdefault(s["date"], []).append(
                f"{s['notes'] or s['sport']} ({s['planned_duration_min']} min, RPE {s['planned_rpe']})")
    for d in sorted(by_day):
        lines.append(f"- {d:%a}: " + "; ".join(by_day[d]))
    return "\n".join(lines)


def write_week_plan(conn: psycopg.Connection, week_start: date) -> int:
    profile = load_profile(conn)
    if not profile.prefs.get("generate_plan", True):
        return 0
    plan = plan_week(conn, week_start)
    execute(conn, """DELETE FROM core.planned_session WHERE origin = 'generated' AND status = 'planned'
                     AND date BETWEEN %s AND %s""", (week_start, week_start + timedelta(days=6)))
    text = render_plan(plan)
    rec_id = upsert(conn, "analytics.recommendation", [{
        "date": week_start, "kind": "weekly", "input_snapshot": to_jsonable({"plan_inputs": {
            k: plan[k] for k in ("chronic_weekly_load", "acwr", "games", "limits")}}),
        "decisions": to_jsonable(plan), "rendered_text": text, "llm_model": None, "shown_to_user": True,
    }], conflict=["date", "kind"], update=["input_snapshot", "decisions", "rendered_text", "shown_to_user"],
        returning="id")[0]
    for s in plan["sessions"]:
        insert(conn, "core.planned_session", {**s, "recommendation_id": rec_id}, returning=None)
    return len(plan["sessions"])
