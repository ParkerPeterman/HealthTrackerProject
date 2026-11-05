"""H4: medication messages, only ever from these approved templates (approved 2026-11-04).

Values in the braces are computed by code. The messages are written to
analytics.medication_message and shown under the coaching message, after the LLM output
guard: the LLM never sees them or any medication data. Nothing here gives dosing advice.

Templates 6-8 belong to the H3 analysis and are filled by the weekly job once it exists.
"""

from __future__ import annotations

from datetime import date, timedelta
from string import Formatter

import psycopg

from healthtracker.components import component_status, is_live
from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.profile import load_profile

TEMPLATES = {
    # 1. the weekly count (unknown days never count as missed)
    "weekly_count": "{name}: taken {k} of the last 7 days ({u} not recorded)",
    # 2. two or more recorded misses in a row
    "missed_in_a_row": "You've missed {n} days in a row. Follow your prescriber's instructions for missed doses.",
    # 3. readiness (shadow for two weeks)
    "readiness": "Missed doses and {symptom} today, so today counts as a lower-readiness day: no max sprints, "
                 "heavy lifts, or intervals.",
    # 4. dizziness logged during training
    "dizzy_training": "You logged dizziness during training. If it happens again, stop the session, and tell your "
                      "prescriber.",
    # 5. lowest mood rating several days running
    "low_mood": "Your mood has been at the lowest rating for {n} days. Consider checking in with your prescriber or "
                "someone you trust. If you're in crisis, call or text 988 (Suicide & Crisis Lifeline).",
    # 6-8. findings from the H3 analysis
    "dose_time": "On days you take {name} later, {outcome} is {effect} ({confidence}). It may be worth asking your "
                 "prescriber whether timing matters.",
    "missed_effect": "After a missed dose, {outcome} was {effect} ({confidence}).",
    "missed_effect_early": "Too early to tell whether missed doses affect {outcome}.",
    "travel_confounded": "Missed doses mostly fell on travel days, so their effect can't be separated from travel.",
}
SWITCH = {"weekly_count": "COMP_MED_DISPLAY", "missed_in_a_row": "COMP_MED_DISPLAY",
          "readiness": "COMP_MED_READINESS", "dizzy_training": "COMP_MED_SAFETY", "low_mood": "COMP_MED_SAFETY",
          "dose_time": "COMP_MED_ANALYSIS", "missed_effect": "COMP_MED_ANALYSIS",
          "missed_effect_early": "COMP_MED_ANALYSIS", "travel_confounded": "COMP_MED_ANALYSIS"}
SYMPTOMS = ("dizziness", "low mood", "low energy")
DEFAULTS = {"missed_in_a_row": 2, "low_mood_days": 3, "low_energy_max": 2, "low_mood_max": 2,
            "weekly_count_weekday": 0, "learn_time_min_days": 5}


def fields(template: str) -> set[str]:
    return {f for _, f, _, _ in Formatter().parse(TEMPLATES[template]) if f}


def render(template: str, **params) -> str:
    """The only way a medication message is made: an approved template and exactly its fields."""
    if set(params) != fields(template):
        raise ValueError(f"{template} needs {sorted(fields(template))}, got {sorted(params)}")
    if template == "readiness" and params["symptom"] not in SYMPTOMS:
        raise ValueError("unknown symptom")
    return TEMPLATES[template].format(**params)


def med_params(conn: psycopg.Connection) -> dict[str, float]:
    rows = fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_MEDICATION'")
    return {**DEFAULTS, **{r["name"]: float(r["value"]) for r in rows}}


def week_count(conn: psycopg.Connection, medication_id: int, end: date) -> tuple[int, int]:
    """Taken days and unrecorded days in the 7 days ending `end`."""
    rows = fetch_all(conn, "SELECT status FROM core.medication_log WHERE medication_id = %s AND date BETWEEN %s AND %s",
                     (medication_id, end - timedelta(days=6), end))
    taken = sum(r["status"] == "taken" for r in rows)
    unknown = 7 - sum(r["status"] in ("taken", "missed") for r in rows)
    return taken, unknown


def messages_for(conn: psycopg.Connection, med: dict, d: date, p: dict, tz: str) -> list[tuple[str, dict]]:
    """Which templates apply to `d` (the morning of), with their values."""
    out: list[tuple[str, dict]] = []
    yesterday = d - timedelta(days=1)
    streak = fetch_one(conn, """SELECT consecutive_missed AS n FROM analytics.medication_daily
                                WHERE medication_id = %s AND date = %s""", (med["id"], yesterday))
    streak = int(streak["n"]) if streak else 0
    if d.weekday() == int(p["weekly_count_weekday"]):
        k, u = week_count(conn, med["id"], yesterday)
        out.append(("weekly_count", {"name": med["name"], "k": k, "u": u}))
    if streak >= p["missed_in_a_row"]:
        out.append(("missed_in_a_row", {"n": streak}))
    journal = fetch_one(conn, """SELECT mood, energy FROM core.journal WHERE date IN (%s, %s)
                                 ORDER BY date DESC LIMIT 1""", (d, yesterday)) or {}
    tags = {r["type_code"] for r in fetch_all(conn, """SELECT DISTINCT type_code FROM core.behavior_event
                                                      WHERE (ts AT TIME ZONE %s)::date IN (%s, %s)
                                                        AND type_code IN ('dizzy', 'dizzy_training')""",
                                              (tz, d, yesterday))}
    if streak >= p["missed_in_a_row"]:
        symptoms = [s for s, hit in (("dizziness", bool(tags)),
                                     ("low mood", (journal.get("mood") or 9) <= p["low_mood_max"]),
                                     ("low energy", (journal.get("energy") or 9) <= p["low_energy_max"])) if hit]
        if symptoms:
            out.append(("readiness", {"symptom": symptoms[0]}))
    if "dizzy_training" in tags:
        out.append(("dizzy_training", {}))
    need = int(p["low_mood_days"])
    moods = fetch_all(conn, "SELECT date, mood FROM core.journal WHERE date BETWEEN %s AND %s ORDER BY date DESC",
                      (d - timedelta(days=need + 30), d))
    run = 0
    expect = moods[0]["date"] if moods and moods[0]["date"] >= yesterday else None  # the run must reach yesterday
    for r in moods:
        if r["date"] != expect or r["mood"] != 1:
            break
        run, expect = run + 1, r["date"] - timedelta(days=1)
    if run >= need:
        out.append(("low_mood", {"n": run}))
    return out


def build_messages(conn: psycopg.Connection, d: date) -> int:
    """Write the day's medication messages. Hidden while their switch is in shadow."""
    tz = load_profile(conn).timezone
    p, comps = med_params(conn), component_status(conn)
    execute(conn, "DELETE FROM analytics.medication_message WHERE date = %s AND template = ANY(%s)",
            (d, [t for t in TEMPLATES if SWITCH[t] != "COMP_MED_ANALYSIS"]))
    n = 0
    meds = fetch_all(conn, "SELECT * FROM core.medication WHERE active AND start_date <= %s ORDER BY id", (d,))
    for med in meds:
        for template, params in messages_for(conn, med, d, p, tz):
            insert(conn, "analytics.medication_message", {
                "date": d, "medication_id": med["id"], "template": template, "params": params,
                "text": render(template, **params), "visible": is_live(comps, SWITCH[template])}, returning=None)
            n += 1
    return n
