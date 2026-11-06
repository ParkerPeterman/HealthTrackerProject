"""Analysis components: new logic that runs in shadow mode until I turn it on.

Every new method computes and logs its results from day one, but nothing reaches the
coaching message (or the app's coaching views) until its switch is active:

    healthtracker rules set COMP_HRV_BANDS active

Switches are rows in analytics.rule with domain 'analysis' and a COMP_ prefix, so they
share the rules' status, approval, and history machinery. Their min_history_days is the
data gate from the upgrade brief; a switch that is active but short of data stays quiet.
"""

from __future__ import annotations

from datetime import date, timedelta

import psycopg

from healthtracker.db import fetch_all, fetch_one


def component_status(conn: psycopg.Connection) -> dict[str, dict]:
    rows = fetch_all(conn, """SELECT rule_id, status, min_history_days, description FROM analytics.rule
                              WHERE rule_id LIKE 'COMP\\_%%' ORDER BY rule_id""")
    return {r["rule_id"]: r for r in rows}


def is_live(components: dict[str, dict], code: str, history_days: int | None = None) -> bool:
    c = components.get(code)
    if not c or c["status"] != "active":
        return False
    return history_days is None or history_days >= (c["min_history_days"] or 0)


# ------------------------------------------------------------------ when to switch each one on
# The brief's phasing table (section 14), against my own season (F1): a phase start, or days
# after base training starts ("about Dec 1" is three weeks in with the default Nov 9 start).
# 'evidence' parts have no date: they switch on when their own test says so.
SCHEDULE: dict[str, tuple[str, str | int | None, str]] = {
    "COMP_SLEEP_REGULARITY": ("phase", "base", "E1, live from the start"),
    "COMP_MED_DISPLAY": ("phase", "base", "H4 adherence display, live from the start"),
    "COMP_MED_SAFETY": ("phase", "base", "H4 safety messages, live from the start"),
    "COMP_HRV_BANDS": ("base_plus", 22, "A2 bands, about Dec 1"),
    "COMP_SLEEP_DRIVERS": ("base_plus", 22, "E3, about Dec 1"),
    "COMP_MED_READINESS": ("base_plus", 22, "H4 readiness, about Dec 1 after two weeks in shadow"),
    "COMP_INSIGHT_RANKING": ("phase", "vo2_build", "C1-C4 at the VO2 build"),
    "COMP_READINESS_V1": ("phase", "vo2_build", "G1 v1 at the VO2 build"),
    "COMP_ANOMALIES": ("phase", "vo2_build", "G4 with the ranked insights"),
    "TRAIN_DAILY_ADJUST": ("phase", "vo2_build", "F4 at the VO2 build"),
    "COMP_SESSION_RESPONSE": ("phase", "preseason", "D5 at pre-season"),
    "COMP_CHANGE_POINTS": ("phase", "preseason", "G3 at pre-season"),
    "COMP_MED_ANALYSIS": ("phase", "preseason", "H3 at pre-season, if enough data"),
    "COMP_FITNESS_FATIGUE": ("phase", "in_season", "D2 in the season"),
    "COMP_FORECASTS": ("phase", "in_season", "G2 in the season (each forecast still has to beat naive)"),
    "COMP_READINESS_V2": ("evidence", "readiness_v2", "G1 v2 once it predicts sessions better than v1"),
    "COMP_BAYES_EFFECTS": ("evidence", "effects", "B1 once the OLS-vs-B1 disagreements make sense"),
}
# The shadow-log comparison that is the evidence for each switch.
EVIDENCE = {"COMP_HRV_BANDS": "hrv_status", "COMP_READINESS_V1": "readiness", "COMP_READINESS_V2": "readiness_v2",
            "COMP_BAYES_EFFECTS": "effects", "TRAIN_DAILY_ADJUST": "daily_adjust"}


def planned_on(kind: str, arg: str | int | None, phase_starts: dict[str, date], base_start: date | None) -> date | None:
    if kind == "phase":
        return phase_starts.get(str(arg)) or (base_start if arg == "base" else None)
    if kind == "base_plus" and base_start is not None:
        return base_start + timedelta(days=int(arg or 0))
    return None


def schedule(conn: psycopg.Connection, today: date) -> list[dict]:
    """Every scheduled switch: status, planned date, data gate, the shadow log's latest line, and
    whether it is due (planned date reached, data gate met, still in shadow). Due means "look at
    the evidence, then switch it on by hand"; nothing here switches anything."""
    from healthtracker.planning.season import (
        active_season,
        default_first_game,
        default_phases,
        monday_on_or_after,
        phases,
    )

    season = active_season(conn)
    if season:
        starts = {r["phase"]: r["start_date"] for r in phases(conn)}
        base_start = season["base_start"]
    else:  # no season set yet: the default calendar (F1), not saved
        base_start = monday_on_or_after(today)
        starts = {k: v[0] for k, v in default_phases(default_first_game(today), base_start).items()}
    rows = {r["rule_id"]: r for r in fetch_all(conn, """SELECT rule_id, status, min_history_days, description
                                                       FROM analytics.rule WHERE rule_id = ANY(%s)""",
                                                (list(SCHEDULE),))}
    hist = fetch_one(conn, """SELECT count(*) AS n FROM analytics.daily_features WHERE date < %s
                              AND (hrv IS NOT NULL OR sleep_total_min IS NOT NULL OR food_log_complete)""", (today,))
    history = int((hist or {}).get("n") or 0)
    log = {r["component"]: r["summary"] for r in fetch_all(conn, """
        SELECT DISTINCT ON (component) component, summary FROM analytics.shadow_log
        ORDER BY component, week_start DESC""")}
    v2 = fetch_one(conn, "SELECT adopted, reason FROM analytics.readiness_model ORDER BY computed_on DESC LIMIT 1")
    out = []
    for code, (kind, arg, note) in SCHEDULE.items():
        r = rows.get(code)
        if not r:
            continue
        on = planned_on(kind, arg, starts, base_start)
        gate = history >= (r["min_history_days"] or 0)
        evidence = log.get(EVIDENCE.get(code, ""))
        if code == "COMP_READINESS_V2":
            evidence = f"latest model: {v2['reason']}" if v2 else "no model yet"
            ready = bool(v2 and v2["adopted"])
        else:
            ready = on is not None and on <= today
        out.append({"code": code, "status": r["status"], "planned_on": on, "note": note, "history_days": history,
                    "needs_days": r["min_history_days"] or 0, "gate_met": gate, "evidence": evidence,
                    "due": r["status"] == "shadow" and gate and ready})
    return sorted(out, key=lambda x: (x["planned_on"] or date.max, x["code"]))
