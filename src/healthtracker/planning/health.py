"""General health inside the plan (F6): the WHO weekly minutes and strength days (E4), sleep
regularity (E1), and the protein target, as part of the week rather than separate reminders.

* Aerobic minutes from the planned sessions, counted the way E4 counts logged ones. Runs give
  their hard minutes (the library's `hard_min`) as vigorous and the rest as moderate; games
  and my own sessions at RPE 7 or more count as vigorous, otherwise moderate. Sprint, jump,
  and drill sessions are mostly rest between reps and don't count. Equivalent minutes are
  moderate + 2 x vigorous; WHO 2020 asks for 150 or more.
* Strength days: days with a strength session (the add-on prehab exercises don't count).
  WHO asks for 2 or more.
* When the main days fall short of 150 equivalent minutes, easy runs lengthen toward it, first
  on the main days, then the optional ones, inside the ramp caps and each day's minutes. Not in
  a deload week or after recovery flags: there the shortfall is reported, on purpose.
* Sleep: once sleep regularity is live (E1) and 28 days give an SRI, a note when it is below
  `sri_low`, and when weekend nights run more than `weekend_gap_min` later.
* Protein: the active protein target, in grams a day at my latest weight.
"""

from __future__ import annotations

from datetime import date

import psycopg

from healthtracker.components import component_status, is_live
from healthtracker.db import fetch_one
from healthtracker.features.long_term import WHO_MIN, WHO_STRENGTH
from healthtracker.planning.library import mid_minutes

DEFAULTS = {"sri_low": 70, "weekend_gap_min": 60}


def session_minutes(entry: dict, minutes: int, addons: list[dict]) -> tuple[int, int]:
    """(moderate, vigorous) minutes of a planned library session and its add-ons."""
    mod = vig = 0
    for e, m in ((entry, minutes), *((a, mid_minutes(a)) for a in addons)):
        if e["sport"] != "run":
            continue
        hard = min(m, int(e.get("hard_min") or 0)) if e["hard"] else 0
        vig += hard
        mod += m - hard
    return mod, vig


def fixed_minutes(s: dict) -> tuple[int, int]:
    """(moderate, vigorous) minutes of a game or my own planned session."""
    if s["session_type"] in ("strength", "rest") or s.get("sport") in ("strength", "mobility", "prehab"):
        return 0, 0
    m = int(s.get("planned_duration_min") or 0)
    return (0, m) if (s.get("planned_rpe") or 0) >= 7 or s["session_type"] == "game" else (m, 0)


def tally(placed: list, fixed: list[dict], optional: bool) -> dict:
    """WHO counts for the week: main days only, or with the optional sessions too."""
    keep = [p for p in placed if optional or not p.slot.optional]
    mod = vig = 0
    days: set = set()
    for p in keep:
        m, v = session_minutes(p.entry, p.minutes, p.addons)
        mod, vig = mod + m, vig + v
        if p.entry["category"] == "strength":
            days.add(p.day)
    for s in fixed:
        m, v = fixed_minutes(s)
        mod, vig = mod + m, vig + v
        if s["session_type"] == "strength" or s.get("sport") == "strength":
            days.add(s["date"].weekday())
    eq = mod + 2 * vig
    return {"moderate_min": mod, "vigorous_min": vig, "equivalent_min": eq, "strength_days": len(days),
            "meets_aerobic": eq >= WHO_MIN, "meets_strength": len(days) >= WHO_STRENGTH}


def extend_easy_runs(week, room: dict[str, float]) -> list[str]:
    """Lengthen easy runs toward 150 equivalent minutes, inside the caps and each day's minutes."""
    f = week.inp.factor
    out = []
    runs = [p for p in week.placed if p.run and not p.entry["hard"] and p.entry["category"] == "endurance"]
    for optional in (False, True):
        for p in sorted((p for p in runs if p.slot.optional == optional), key=lambda p: (p.day, p.code)):
            before = p.minutes
            while tally(week.placed, week.inp.fixed, optional)["equivalent_min"] < WHO_MIN \
                    and p.minutes + 5 <= p.entry["duration_min_hi"]:
                p.minutes += 5
                if sum(x.run_load(f) for x in week.placed) > room["run"] or \
                        sum(x.load(f) for x in week.placed) > room["total"] or \
                        week.minutes(p.day) > week.budget(p.day, p.slot):
                    p.minutes -= 5
                    break
            if p.minutes > before:
                p.notes.append(f"lengthened to {p.minutes} min toward the WHO 150 weekly minutes")
                out.append(p.code)
    return out


def plan_health(week, room: dict[str, float], extend: bool, skip_why: str | None = None) -> dict:
    """The plan's health block. Lengthens easy runs first when `extend` and the main days fall short."""
    extended = extend_easy_runs(week, room) if extend else []
    main, all_ = tally(week.placed, week.inp.fixed, False), tally(week.placed, week.inp.fixed, True)
    notes = []
    if not main["meets_aerobic"]:
        if all_["meets_aerobic"]:
            notes.append("the optional sessions bring the week to the WHO 150 minutes")
        elif skip_why:
            notes.append(f"below the WHO 150 minutes on purpose: {skip_why}")
        else:
            notes.append("below the WHO 150 minutes: the ramp caps or the day's minutes leave no more room")
    if not all_["meets_strength"]:
        notes.append(f"{all_['strength_days']} strength day{'s' if all_['strength_days'] != 1 else ''} "
                     f"planned; WHO suggests at least {WHO_STRENGTH}")
    return {"main": main, "with_optional": all_, "who_minutes": WHO_MIN, "who_strength_days": WHO_STRENGTH,
            "extended": extended, "notes": notes, "sleep": sleep_note(week.inp), "protein": week.inp.protein}


def sleep_note(inp) -> dict | None:
    s = inp.sleep or {}
    if s.get("sri") is None:
        return None
    p = {**DEFAULTS, **inp.params.get("ANALYSIS_PLAN", {})}
    low = s["sri"] < p["sri_low"]
    gap = s.get("weekend_gap_min")
    late = gap is not None and gap > p["weekend_gap_min"]
    note = None
    if low or late:
        bits = [f"sleep regularity is low (SRI {s['sri']:.0f} over 28 days)"] if low else []
        if late:
            bits.append(f"weekend nights run about {gap:.0f} min later than weekdays")
        note = "; ".join(bits) + ". The same wake time every day, travel days included, is the main lever."
    return {"sri": s["sri"], "low": low, "weekend_gap_min": gap, "note": note}


# ------------------------------------------------------------------ from the database
def gather_sleep(conn: psycopg.Connection, features: dict) -> dict:
    if not is_live(component_status(conn), "COMP_SLEEP_REGULARITY"):
        return {}
    sri = features.get("sri_28d")
    gap = features.get("weekend_gap_min_28d")
    return {"sri": float(sri) if sri is not None else None, "weekend_gap_min": float(gap) if gap is not None else None}


def gather_protein(conn: psycopg.Connection, as_of: date) -> dict | None:
    """The active protein target in grams a day (per-kg targets at my latest weight)."""
    t = fetch_one(conn, """SELECT * FROM core.nutrient_target WHERE active AND nutrient = 'protein_g'
                           ORDER BY (basis = 'per_kg_bodyweight') DESC, valid_from DESC, id DESC LIMIT 1""")
    if not t:
        return None
    lo, hi = t["min_value"], t["max_value"]
    out = {"basis": t["basis"], "per_kg": None}
    if t["basis"] == "per_kg_bodyweight":
        w = fetch_one(conn, """SELECT weight_kg FROM core.body_measurement WHERE weight_kg IS NOT NULL
                               AND ts < (%s::date + 1) ORDER BY ts DESC LIMIT 1""", (as_of,))
        if not w:
            return None
        kg = float(w["weight_kg"])
        out["per_kg"] = [None if lo is None else float(lo), None if hi is None else float(hi)]
        lo, hi = (None if lo is None else float(lo) * kg), (None if hi is None else float(hi) * kg)
    out["g_lo"] = None if lo is None else int(5 * round(float(lo) / 5))
    out["g_hi"] = None if hi is None else int(5 * round(float(hi) / 5))
    return out


def render_health(h: dict | None) -> list[str]:
    if not h:
        return []
    m, a = h["main"], h["with_optional"]
    aer = f"{m['equivalent_min']} aerobic minutes on the main days"
    if a["equivalent_min"] != m["equivalent_min"]:
        aer += f" ({a['equivalent_min']} with the optional sessions)"
    lines = [f"Health: {aer}, vigorous counted double (WHO: {h['who_minutes']}+); "
             f"{a['strength_days']} strength days (WHO: {h['who_strength_days']}+)."]
    lines += [f"- {n[0].upper()}{n[1:]}." for n in h["notes"]]
    if h.get("sleep") and h["sleep"].get("note"):
        n = h["sleep"]["note"]
        lines.append(f"Sleep: {n[0].upper()}{n[1:]}")
    p = h.get("protein")
    if p and (p.get("g_lo") or p.get("g_hi")):
        g = f"{p['g_lo']}-{p['g_hi']} g" if p.get("g_lo") and p.get("g_hi") else \
            (f"at least {p['g_lo']} g" if p.get("g_lo") else f"up to {p['g_hi']} g")
        kg = p.get("per_kg")
        per = ""
        if kg:
            per = f" ({kg[0]:g}-{kg[1]:g} g/kg)" if kg[0] and kg[1] else f" ({(kg[0] or kg[1]):g} g/kg)"
        lines.append(f"Protein: {g} a day{per}.")
    return lines
