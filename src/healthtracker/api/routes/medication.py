"""H1: the daily medication check and medication settings. Sensitive (H5).

Today shows "<name> today: Taken / Not yet"; tapping Taken records the time, which can be
edited. If yesterday has no answer, it also asks "Yesterday: Taken / Missed / Don't
remember". An unknown day is never shown as missed. An in-app badge appears a set time
after the usual dose time (mine, or learned from logged times). There are no push
notifications: web push would go through Google's or Apple's servers.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from healthtracker.api.deps import get_conn, local_date, not_found, now_local
from healthtracker.db import execute, fetch_all, fetch_one, insert, upsert
from healthtracker.jsonutil import to_jsonable
from healthtracker.medication.features import usual_time
from healthtracker.medication.messages import med_params, render, week_count
from healthtracker.profile import load_profile

router = APIRouter(prefix="/api", tags=["medication"])


class MedicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    dose_mg: float | None = Field(None, gt=0, le=10000)
    start_date: date | None = None
    active: bool = True
    usual_time: time | None = None
    reminder_delay_min: int = Field(120, ge=15, le=720)


class DoseChangeIn(BaseModel):
    date: date
    new_dose_mg: float = Field(gt=0, le=10000)
    note: str | None = None


class LogIn(BaseModel):
    status: Literal["taken", "missed", "unknown"]
    taken_at: datetime | None = None


@router.get("/medications")
def list_medications(conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    meds = fetch_all(conn, "SELECT * FROM core.medication ORDER BY active DESC, id")
    for m in meds:
        m["changes"] = fetch_all(conn, "SELECT * FROM core.medication_change WHERE medication_id = %s ORDER BY date",
                                 (m["id"],))
    return to_jsonable(meds)


@router.post("/medications")
def create_medication(body: MedicationIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = body.model_dump()
    d["start_date"] = d["start_date"] or local_date(conn)
    return {"id": insert(conn, "core.medication", d)}


@router.put("/medications/{medication_id}")
def update_medication(medication_id: int, body: MedicationIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = body.model_dump(exclude={"dose_mg"})  # dose changes go through /dose-change so they are dated
    d["start_date"] = d["start_date"] or local_date(conn)
    n = execute(conn, f"UPDATE core.medication SET {', '.join(f'{k} = %s' for k in d)} WHERE id = %s",
                (*d.values(), medication_id))
    if not n:
        raise not_found("unknown medication")
    return {"ok": True}


@router.post("/medications/{medication_id}/dose-change")
def dose_change(medication_id: int, body: DoseChangeIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """A dated dose change: a known change point that restarts baseline maturity (H3)."""
    med = fetch_one(conn, "SELECT * FROM core.medication WHERE id = %s", (medication_id,))
    if not med:
        raise not_found("unknown medication")
    insert(conn, "core.medication_change", {"medication_id": medication_id, "date": body.date,
                                            "old_dose_mg": med["dose_mg"], "new_dose_mg": body.new_dose_mg,
                                            "note": body.note}, returning=None)
    execute(conn, "UPDATE core.medication SET dose_mg = %s WHERE id = %s", (body.new_dose_mg, medication_id))
    return {"ok": True}


@router.put("/medications/{medication_id}/log/{day}")
def put_log(medication_id: int, day: date, body: LogIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if not fetch_one(conn, "SELECT 1 FROM core.medication WHERE id = %s", (medication_id,)):
        raise not_found("unknown medication")
    taken_at = body.taken_at or (now_local(conn) if body.status == "taken" else None)
    upsert(conn, "core.medication_log", [{"medication_id": medication_id, "date": day, "status": body.status,
                                          "taken_at": taken_at if body.status == "taken" else None}],
           conflict=["medication_id", "date"], update=["status", "taken_at", "logged_at"])  # logged_at: now()
    return medication_today(day, conn)


@router.delete("/medications/{medication_id}/log/{day}")
def clear_log(medication_id: int, day: date, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """'Not yet': no answer for the day (unknown, never missed)."""
    execute(conn, "DELETE FROM core.medication_log WHERE medication_id = %s AND date = %s", (medication_id, day))
    return medication_today(day, conn)


@router.get("/medication/today")
def medication_today(day: date | None = None, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    """The Today card: today's tick, yesterday's question, the badge, the weekly count, and messages."""
    profile = load_profile(conn)
    d = day or local_date(conn)
    now = now_local(conn)
    p = med_params(conn)
    out = []
    for med in fetch_all(conn, "SELECT * FROM core.medication WHERE active AND start_date <= %s ORDER BY id", (d,)):
        logs = {r["date"]: r for r in fetch_all(conn, """SELECT * FROM core.medication_log WHERE medication_id = %s
                                                         AND date BETWEEN %s AND %s""",
                                                (med["id"], d - timedelta(days=1), d))}
        today, yesterday = logs.get(d), logs.get(d - timedelta(days=1))
        usual = usual_time(conn, med, d, profile.timezone, int(p["learn_time_min_days"]))
        due = None
        if usual is not None:
            due = datetime.combine(d, time(0), profile.tz) + timedelta(hours=usual, minutes=med["reminder_delay_min"])
        k, u = week_count(conn, med["id"], d)
        messages = fetch_all(conn, """SELECT template, text FROM analytics.medication_message
                                      WHERE date = %s AND medication_id = %s AND visible ORDER BY template""",
                             (d, med["id"]))
        out.append({
            "id": med["id"], "name": med["name"], "dose_mg": med["dose_mg"],
            "today": {"status": today["status"], "taken_at": today["taken_at"]} if today else None,
            "yesterday": {"status": yesterday["status"]} if yesterday else None,
            "ask_yesterday": yesterday is None and med["start_date"] < d,
            "usual_hour": usual,
            "badge": bool(today is None and due is not None and d == local_date(conn) and now >= due),
            "week": render("weekly_count", name=med["name"], k=k, u=u),
            "messages": messages,
        })
    return to_jsonable({"date": d, "medications": out})
