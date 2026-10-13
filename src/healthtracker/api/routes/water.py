"""Water intake: quick-add drinks and the day's total against a suggested target."""

from __future__ import annotations

from datetime import date, datetime

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from healthtracker.api.deps import get_conn, not_found, now_local
from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.jsonutil import to_jsonable
from healthtracker.profile import load_profile

router = APIRouter(prefix="/api", tags=["water"])

# A common general guide (about 35 ml per kg of body weight a day, food water aside);
# shown as a suggestion, not a rule.
ML_PER_KG = 35
FALLBACK_ML = 2500


class WaterIn(BaseModel):
    ml: float = Field(gt=0, le=5000)
    ts: datetime | None = None


@router.get("/water/{day}")
def water_day(day: date, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    tz = load_profile(conn).timezone
    entries = fetch_all(conn, """SELECT id, ts, ml FROM core.water_entry
                                 WHERE (ts AT TIME ZONE %s)::date = %s ORDER BY ts""", (tz, day))
    w = fetch_one(conn, """SELECT weight_kg FROM core.body_measurement
                           WHERE (ts AT TIME ZONE %s)::date <= %s ORDER BY ts DESC LIMIT 1""", (tz, day))
    target = round(float(w["weight_kg"]) * ML_PER_KG / 100) * 100 if w else FALLBACK_ML
    return to_jsonable({
        "date": day,
        "total_ml": sum(float(e["ml"]) for e in entries),
        "target_ml": target,
        "target_basis": "weight" if w else "default",
        "entries": entries,
    })


@router.post("/water")
def add_water(body: WaterIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    ts = body.ts or now_local(conn)
    wid = insert(conn, "core.water_entry", {"ts": ts, "ml": round(body.ml, 1)})
    return to_jsonable({"id": wid, "ts": ts, "ml": body.ml})


@router.delete("/water/{entry_id}")
def delete_water(entry_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if not execute(conn, "DELETE FROM core.water_entry WHERE id = %s", (entry_id,)):
        raise not_found("water entry not found")
    return {"ok": True}
