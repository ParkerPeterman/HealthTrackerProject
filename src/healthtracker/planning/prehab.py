"""Injury-prevention state by body area (F2): sore shoulders, groin tweaks, tight hamstrings.

Shoulders are *flagged* while there is an open shoulder injury, or while a sore-shoulder tag
or a shoulder check-in at or above the pain threshold falls in the last week. Two low
check-ins after the last sore one clear the flag early. While the shoulders are flagged by a
tag or check-in (not an injury, which has its own check-in), Today asks for a 0-10 shoulder
check-in, overhead pressing becomes landmine pressing, and throwing waits.

Groin tweaks and sore-shoulder days are counted per area for the physio prompt.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import psycopg

from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.profile import Profile

DEFAULTS = {"shoulder_flag_days": 7, "shoulder_pain_threshold": 3, "shoulder_clear_checkins": 2,
            "copenhagen_sessions_per_level": 6, "copenhagen_tweak_days": 14}
# Words an injury's body_part may use for each area.
AREA_PARTS = {"shoulder": ("shoulder", "rotator"), "groin": ("groin", "adductor"), "hamstring": ("hamstring",)}
# Daily-feature column counting each area's events for the physio prompt.
AREA_FEATURE = {"groin": "tag_groin", "shoulder": "tag_shoulder"}


def prehab_params(conn: psycopg.Connection) -> dict[str, float]:
    rows = fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_PREHAB'")
    return {**DEFAULTS, **{r["name"]: float(r["value"]) for r in rows}}


def _part_patterns(area: str) -> list[str]:
    return [f"%{p}%" for p in AREA_PARTS[area]]


def open_injury(conn: psycopg.Connection, area: str, d: date) -> dict | None:
    """An injury in this area that was open on `d`."""
    return fetch_one(conn, """SELECT * FROM core.injury WHERE body_part ILIKE ANY(%s) AND onset_date <= %s
                              AND (status <> 'resolved' OR resolved_date > %s) ORDER BY onset_date DESC LIMIT 1""",
                     (_part_patterns(area), d, d))


def seen_professional(conn: psycopg.Connection, area: str) -> bool:
    row = fetch_one(conn, "SELECT bool_or(seen_professional) AS seen FROM core.injury WHERE body_part ILIKE ANY(%s)",
                    (_part_patterns(area),))
    return bool(row and row["seen"])


def shoulder_state(conn: psycopg.Connection, d: date, tz: str, params: dict | None = None) -> dict:
    p = params or prehab_params(conn)
    inj = open_injury(conn, "shoulder", d)
    events = fetch_all(conn, """SELECT (ts AT TIME ZONE %(tz)s)::date AS date, type_code, value, ts
                                FROM core.behavior_event WHERE type_code IN ('shoulder_sore', 'shoulder_pain')
                                  AND (ts AT TIME ZONE %(tz)s)::date BETWEEN %(s)s AND %(e)s ORDER BY ts""",
                       {"tz": tz, "s": d - timedelta(days=int(p["shoulder_flag_days"]) - 1), "e": d})
    thr = p["shoulder_pain_threshold"]
    pain_today = next((int(e["value"]) for e in reversed(events)
                       if e["type_code"] == "shoulder_pain" and e["date"] == d and e["value"] is not None), None)
    if inj:
        return {"flagged": True, "source": "injury", "injury_id": inj["id"], "pain_today": pain_today,
                "checkin_needed": False}
    sore = [e for e in events if e["type_code"] == "shoulder_sore"
            or (e["type_code"] == "shoulder_pain" and e["value"] is not None and float(e["value"]) >= thr)]
    if not sore:
        return {"flagged": False, "source": None, "pain_today": pain_today, "checkin_needed": False}
    last = sore[-1]["ts"]
    low_after = [e for e in events if e["type_code"] == "shoulder_pain" and e["ts"] > last
                 and e["value"] is not None and float(e["value"]) < thr]
    flagged = len(low_after) < int(p["shoulder_clear_checkins"])
    return {"flagged": flagged, "source": "tag" if sore[-1]["type_code"] == "shoulder_sore" else "checkin",
            "since": sore[0]["date"], "pain_today": pain_today, "checkin_needed": flagged and pain_today is None}


def save_shoulder_checkin(conn: psycopg.Connection, d: date, pain: int, profile: Profile) -> None:
    """One check-in per day, stored as a behavior event at local noon (re-saving replaces it)."""
    execute(conn, """DELETE FROM core.behavior_event WHERE type_code = 'shoulder_pain'
                     AND (ts AT TIME ZONE %s)::date = %s""", (profile.timezone, d))
    insert(conn, "core.behavior_event", {"ts": datetime.combine(d, time(12), profile.tz), "type_code": "shoulder_pain",
                                         "value": pain, "note": "check-in"}, returning=None)


def last_physio_prompts(conn: psycopg.Connection, d: date) -> dict[str, dict]:
    """The most recent physio prompt per area before `d`: {area: {"date", "count"}}."""
    rows = fetch_all(conn, """SELECT date, details FROM analytics.daily_flag
                              WHERE rule_id = 'INJ_PHYSIO_PROMPT' AND fired AND date < %s ORDER BY date""", (d,))
    out: dict[str, dict] = {}
    for r in rows:
        for a in (r["details"] or {}).get("prompted", []):
            out[a["area"]] = {"date": r["date"], "count": a["count"]}
    return out


def body_state(conn: psycopg.Connection, d: date, tz: str) -> dict[str, dict]:
    """Per-area state for the rules: shoulder flag, professional visits, last physio prompt."""
    prompts = last_physio_prompts(conn, d)
    overhead = fetch_all(conn, """SELECT code FROM knowledge.session_library
                                  WHERE jsonb_path_exists(exercises, '$[*].swap.shoulder')""")
    return {
        "shoulder": {**shoulder_state(conn, d, tz), "seen_professional": seen_professional(conn, "shoulder"),
                     "last_prompt": prompts.get("shoulder"), "overhead_codes": sorted(r["code"] for r in overhead)},
        "groin": {"open_injury": open_injury(conn, "groin", d) is not None,
                  "seen_professional": seen_professional(conn, "groin"), "last_prompt": prompts.get("groin")},
        "hamstring": {"open_injury": open_injury(conn, "hamstring", d) is not None},
    }
