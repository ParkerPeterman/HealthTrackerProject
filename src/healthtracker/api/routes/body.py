"""Weight, journal, injuries (check-ins, restrictions), and behavior events."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from healthtracker.api.deps import get_conn, local_date, not_found, now_local
from healthtracker.db import execute, fetch_all, fetch_one, insert, upsert
from healthtracker.jsonutil import to_jsonable
from healthtracker.profile import load_profile

router = APIRouter(prefix="/api", tags=["body"])


# ------------------------------------------------------------------ weight
class WeightIn(BaseModel):
    weight_kg: float = Field(gt=20, lt=400)
    ts: datetime | None = None


@router.get("/weights")
def weights(days: int = 120, conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    tz = load_profile(conn).timezone
    rows = fetch_all(conn, """SELECT m.ts, (m.ts AT TIME ZONE %s)::date AS date, m.weight_kg, m.source,
                                     f.weight_kg_smoothed
                              FROM core.body_measurement m
                              LEFT JOIN analytics.daily_features f ON f.date = (m.ts AT TIME ZONE %s)::date
                              WHERE m.ts > now() - make_interval(days => %s) ORDER BY m.ts""", (tz, tz, days))
    return to_jsonable(rows)


@router.post("/weights")
def add_weight(body: WeightIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    ts = body.ts or now_local(conn)
    upsert(conn, "core.body_measurement", [{"ts": ts, "weight_kg": round(body.weight_kg, 2), "source": "manual"}],
           conflict=["ts"])
    return to_jsonable({"ts": ts, "weight_kg": body.weight_kg})


@router.delete("/weights/{ts}")
def delete_weight(ts: datetime, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "DELETE FROM core.body_measurement WHERE ts = %s", (ts,))
    return {"ok": True}


# ------------------------------------------------------------------ journal
Score = Annotated[int | None, Field(ge=1, le=5)]


class JournalIn(BaseModel):
    mood: Score = None
    energy: Score = None
    soreness: Score = None
    stress: Score = None
    motivation: Score = None
    note: str | None = None


@router.get("/journal")
def journal_range(start: date | None = None, end: date | None = None,
                  conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    end = end or local_date(conn)
    start = start or end - timedelta(days=30)
    return to_jsonable(fetch_all(conn, "SELECT * FROM core.journal WHERE date BETWEEN %s AND %s ORDER BY date DESC",
                                 (start, end)))


@router.get("/journal/{day}")
def journal_day(day: date, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return to_jsonable(fetch_one(conn, "SELECT * FROM core.journal WHERE date = %s", (day,)) or {"date": day})


@router.put("/journal/{day}")
def put_journal(day: date, body: JournalIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    upsert(conn, "core.journal", [{"date": day, **body.model_dump()}], conflict=["date"])
    return journal_day(day, conn)


# ------------------------------------------------------------------ injuries
class InjuryIn(BaseModel):
    body_part: str = Field(min_length=1)
    side: Literal["left", "right", "both", "n/a"] | None = None
    injury_type: Literal["strain", "sprain", "tendon", "contusion", "soreness", "other"]
    onset_date: date
    mechanism: str | None = None
    status: Literal["active", "recovering", "resolved"] = "active"
    resolved_date: date | None = None
    seen_professional: bool = False
    notes: str | None = None


class CheckinIn(BaseModel):
    pain_rest: int | None = Field(None, ge=0, le=10)
    pain_activity: int | None = Field(None, ge=0, le=10)
    function_score: int | None = Field(None, ge=1, le=5)
    swelling: bool | None = None
    note: str | None = None


class RestrictionIn(BaseModel):
    target_type: Literal["exercise", "muscle_group", "movement_pattern", "activity"]
    exercise_id: int | None = None
    muscle_group: str | None = None
    movement_pattern: str | None = None
    activity: str | None = None
    max_rpe: int | None = Field(None, ge=0, le=10)
    start_date: date | None = None
    end_date: date | None = None
    origin: Literal["manual", "professional", "rule"] = "manual"

    @model_validator(mode="after")
    def one_target(self) -> RestrictionIn:
        if getattr(self, self.target_type if self.target_type != "exercise" else "exercise_id") is None:
            raise ValueError(f"{self.target_type} restriction needs its target set")
        return self


@router.get("/injuries")
def list_injuries(include_resolved: bool = False, conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    injuries = fetch_all(conn, """SELECT * FROM core.injury WHERE %s OR status <> 'resolved'
                                  ORDER BY status = 'resolved', onset_date DESC""", (include_resolved,))
    today = local_date(conn)
    for i in injuries:
        i["checkins"] = fetch_all(conn, """SELECT * FROM core.injury_checkin WHERE injury_id = %s
                                           ORDER BY date DESC LIMIT 60""", (i["id"],))
        i["restrictions"] = fetch_all(conn, """SELECT r.*, e.name AS exercise_name FROM core.injury_restriction r
                                               LEFT JOIN core.exercise e ON e.id = r.exercise_id
                                               WHERE r.injury_id = %s ORDER BY r.start_date""", (i["id"],))
        i["checked_in_today"] = any(c["date"] == today for c in i["checkins"])
    return to_jsonable(injuries)


@router.post("/injuries")
def create_injury(body: InjuryIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    return {"id": insert(conn, "core.injury", body.model_dump())}


@router.put("/injuries/{injury_id}")
def update_injury(injury_id: int, body: InjuryIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = body.model_dump()
    if d["status"] == "resolved" and not d["resolved_date"]:
        d["resolved_date"] = local_date(conn)
    n = execute(conn, f"UPDATE core.injury SET {', '.join(f'{k} = %s' for k in d)} WHERE id = %s",
                (*d.values(), injury_id))
    if not n:
        raise not_found()
    return {"ok": True}


@router.put("/injuries/{injury_id}/checkins/{day}")
def put_checkin(injury_id: int, day: date, body: CheckinIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if not fetch_one(conn, "SELECT 1 FROM core.injury WHERE id = %s", (injury_id,)):
        raise not_found("unknown injury")
    upsert(conn, "core.injury_checkin", [{"date": day, "injury_id": injury_id, **body.model_dump()}],
           conflict=["date", "injury_id"])
    return {"ok": True}


@router.post("/injuries/{injury_id}/restrictions")
def add_restriction(injury_id: int, body: RestrictionIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = body.model_dump()
    d["start_date"] = d["start_date"] or local_date(conn)
    return {"id": insert(conn, "core.injury_restriction", {"injury_id": injury_id, **d})}


@router.put("/restrictions/{restriction_id}")
def update_restriction(restriction_id: int, body: RestrictionIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = body.model_dump()
    d["start_date"] = d["start_date"] or local_date(conn)
    execute(conn, f"UPDATE core.injury_restriction SET {', '.join(f'{k} = %s' for k in d)} WHERE id = %s",
            (*d.values(), restriction_id))
    return {"ok": True}


@router.delete("/restrictions/{restriction_id}")
def delete_restriction(restriction_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "DELETE FROM core.injury_restriction WHERE id = %s", (restriction_id,))
    return {"ok": True}


# ------------------------------------------------------------------ events
class EventIn(BaseModel):
    type_code: str
    ts: datetime | None = None
    value: float | None = None
    note: str | None = None


@router.get("/event-types")
def event_types(conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM core.event_type ORDER BY code")


@router.get("/events")
def list_events(days: int = 30, conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    return to_jsonable(fetch_all(conn, """SELECT * FROM core.behavior_event WHERE ts > now() - make_interval(days => %s)
                                          ORDER BY ts DESC""", (days,)))


@router.post("/events")
def add_event(body: EventIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if not fetch_one(conn, "SELECT 1 FROM core.event_type WHERE code = %s", (body.type_code,)):
        raise not_found("unknown event type")
    return {"id": insert(conn, "core.behavior_event", {**body.model_dump(), "ts": body.ts or now_local(conn)})}


@router.delete("/events/{event_id}")
def delete_event(event_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "DELETE FROM core.behavior_event WHERE id = %s", (event_id,))
    return {"ok": True}
