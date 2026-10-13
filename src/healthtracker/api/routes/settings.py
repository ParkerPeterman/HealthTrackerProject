"""Profile and nutrient targets. Goals live in routes/goals.py."""

from __future__ import annotations

from datetime import date
from typing import Literal
from zoneinfo import available_timezones

import psycopg
from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from healthtracker.api.deps import bad_request, get_conn, local_date, not_found
from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.jsonutil import to_jsonable
from healthtracker.profile import DEFAULT_PREFS, load_profile

router = APIRouter(prefix="/api", tags=["settings"])


class ProfileIn(BaseModel):
    birth_date: date | None = None
    sex: Literal["male", "female", "other"] | None = None
    height_cm: float | None = Field(None, gt=50, lt=260)
    timezone: str = "America/Chicago"
    ref_hr_bpm: int | None = Field(None, ge=80, le=210)
    display_units: Literal["imperial", "metric"] = "imperial"
    training_prefs: dict | None = None
    notes: str | None = None


@router.get("/profile")
def get_profile(conn: psycopg.Connection = Depends(get_conn)) -> dict:
    row = fetch_one(conn, "SELECT * FROM core.profile WHERE id = 1")
    row["training_prefs"] = {**DEFAULT_PREFS, **(row.get("training_prefs") or {})}
    return to_jsonable(row)


@router.put("/profile")
def put_profile(body: ProfileIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if body.timezone not in available_timezones():
        raise bad_request(f"unknown time zone {body.timezone}")
    prefs = {**DEFAULT_PREFS, **(body.training_prefs or {})}
    execute(conn, """UPDATE core.profile SET birth_date=%s, sex=%s, height_cm=%s, timezone=%s, ref_hr_bpm=%s,
                     display_units=%s, training_prefs=%s, notes=%s WHERE id = 1""",
            (body.birth_date, body.sex, body.height_cm, body.timezone, body.ref_hr_bpm, body.display_units,
             Jsonb(prefs), body.notes))
    return get_profile(conn)


# ------------------------------------------------------------------ nutrient targets
class TargetIn(BaseModel):
    nutrient: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    basis: Literal["per_day", "per_kg_bodyweight"] = "per_day"
    evaluation: Literal["daily", "weekly_avg"] = "daily"
    min_value: float | None = None
    max_value: float | None = None
    source_ref: str | None = None
    active: bool = True


@router.get("/targets")
def list_targets(conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    return to_jsonable(fetch_all(conn, "SELECT * FROM core.nutrient_target ORDER BY active DESC, nutrient"))


@router.post("/targets")
def create_target(body: TargetIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "UPDATE core.nutrient_target SET active = false WHERE nutrient = %s AND basis = %s AND active",
            (body.nutrient, body.basis))
    tid = insert(conn, "core.nutrient_target", {**body.model_dump(), "valid_from": local_date(conn)})
    return to_jsonable(fetch_one(conn, "SELECT * FROM core.nutrient_target WHERE id = %s", (tid,)))


@router.put("/targets/{target_id}")
def update_target(target_id: int, body: TargetIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = body.model_dump()
    n = execute(conn, f"UPDATE core.nutrient_target SET {', '.join(f'{k} = %s' for k in d)} WHERE id = %s",
                (*d.values(), target_id))
    if not n:
        raise not_found()
    return to_jsonable(fetch_one(conn, "SELECT * FROM core.nutrient_target WHERE id = %s", (target_id,)))


@router.delete("/targets/{target_id}")
def delete_target(target_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "DELETE FROM core.nutrient_target WHERE id = %s", (target_id,))
    return {"ok": True}


def dri_defaults(sex: str | None, age: int | None) -> list[dict]:
    """NIH ODS Dietary Reference Intakes for adults (RDA or AI), as weekly averages."""
    if sex not in ("male", "female") or age is None:
        raise bad_request("set sex and birth date in your profile first")
    m = sex == "male"
    ref = "NIH ODS Dietary Reference Intakes (RDA/AI), adults"
    rows = [
        ("fiber_g", (38 if age <= 50 else 30) if m else (25 if age <= 50 else 21)),
        ("iron_mg", 8 if m or age > 50 else 18),
        ("magnesium_mg", (400 if age <= 30 else 420) if m else (310 if age <= 30 else 320)),
        ("calcium_mg", 1000 if (age <= 70 if m else age <= 50) else 1200),
        ("potassium_mg", 3400 if m else 2600),
        ("zinc_mg", 11 if m else 8),
        ("vitamin_d_mcg", 15 if age <= 70 else 20),
    ]
    return [{"nutrient": n, "basis": "per_day", "evaluation": "weekly_avg", "min_value": v,
             "max_value": None, "source_ref": ref} for n, v in rows]


@router.post("/targets/defaults")
def load_defaults(conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    profile = load_profile(conn)
    today = local_date(conn)
    for t in dri_defaults(profile.sex, profile.age_on(today)):
        execute(conn, "UPDATE core.nutrient_target SET active = false WHERE nutrient = %s AND active",
                (t["nutrient"],))
        insert(conn, "core.nutrient_target", {**t, "valid_from": today}, returning=None)
    return list_targets(conn)
