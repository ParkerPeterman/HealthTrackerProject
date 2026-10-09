"""Food logging: catalog search, barcode lookup (Open Food Facts), entries, saved meals."""

from __future__ import annotations

import logging
from datetime import date, datetime
from datetime import date as Date
from typing import Literal

import httpx
import psycopg
from fastapi import APIRouter, Depends
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from healthtracker.api.deps import bad_request, get_conn, local_date, not_found, now_local
from healthtracker.db import execute, fetch_all, fetch_one, insert, upsert
from healthtracker.jsonutil import to_jsonable

router = APIRouter(prefix="/api", tags=["food"])
log = logging.getLogger("healthtracker.api.food")

Meal = Literal["breakfast", "lunch", "dinner", "snack", "pre_workout", "post_workout"]
NUTRIENT_COLS = ["kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "sugar_g", "sodium_mg",
                 "caffeine_mg", "alcohol_g"]


class FoodIn(BaseModel):
    name: str = Field(min_length=1)
    brand: str | None = None
    barcode: str | None = None
    serving_desc: str | None = None
    serving_g: float | None = None
    kcal: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    sugar_g: float | None = None
    sodium_mg: float | None = None
    caffeine_mg: float | None = None
    alcohol_g: float | None = None
    micros: dict[str, float] | None = None


@router.get("/foods")
def search_foods(q: str = "", limit: int = 30, conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    rows = fetch_all(
        conn,
        """SELECT c.*, count(e.id) AS uses, max(e.logged_ts) AS last_used
           FROM core.food_catalog c LEFT JOIN core.food_entry e ON e.catalog_id = c.id
           WHERE %(q)s = '' OR c.name ILIKE %(like)s OR c.brand ILIKE %(like)s OR c.barcode = %(q)s
           GROUP BY c.id ORDER BY count(e.id) DESC, c.name LIMIT %(limit)s""",
        {"q": q.strip(), "like": f"%{q.strip()}%", "limit": limit})
    return to_jsonable(rows)


@router.get("/foods/recent")
def recent_foods(limit: int = 20, conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    rows = fetch_all(
        conn,
        """SELECT c.*, max(e.logged_ts) AS last_used,
                  (array_agg(e.servings ORDER BY e.logged_ts DESC))[1] AS last_servings,
                  (array_agg(e.meal ORDER BY e.logged_ts DESC))[1] AS last_meal
           FROM core.food_entry e JOIN core.food_catalog c ON c.id = e.catalog_id
           WHERE e.logged_ts > now() - interval '45 days'
           GROUP BY c.id ORDER BY max(e.logged_ts) DESC LIMIT %s""", (limit,))
    return to_jsonable(rows)


def off_to_food(barcode: str, product: dict) -> dict:
    """Open Food Facts product -> catalog row, values per serving (or per 100 g)."""
    n = product.get("nutriments") or {}
    qty = product.get("serving_quantity")
    try:
        qty = float(qty) if qty not in (None, "") else None
    except (TypeError, ValueError):
        qty = None

    def per_serving(key: str, scale: float = 1.0) -> float | None:
        v = n.get(f"{key}_serving")
        if v is None and n.get(f"{key}_100g") is not None:
            v = float(n[f"{key}_100g"]) * ((qty or 100) / 100)
        return None if v is None else round(float(v) * scale, 1)

    micros = {}
    for key, out in (("iron", "iron_mg"), ("magnesium", "magnesium_mg"), ("calcium", "calcium_mg"),
                     ("potassium", "potassium_mg"), ("zinc", "zinc_mg")):
        v = per_serving(key, 1000.0)  # OFF stores minerals in grams
        if v is not None:
            micros[out] = v
    vd = per_serving("vitamin-d", 1_000_000.0)  # grams -> mcg
    if vd is not None:
        micros["vitamin_d_mcg"] = vd
    kcal = per_serving("energy-kcal")
    if kcal is None and per_serving("energy") is not None:
        kcal = round(per_serving("energy") / 4.184, 1)
    return {
        "name": product.get("product_name") or product.get("generic_name") or f"Barcode {barcode}",
        "brand": (product.get("brands") or "").split(",")[0].strip() or None,
        "barcode": barcode,
        "serving_desc": product.get("serving_size") or ("100 g" if not qty else f"{qty:g} g"),
        "serving_g": qty or 100.0,
        "kcal": kcal,
        "protein_g": per_serving("proteins"),
        "carbs_g": per_serving("carbohydrates"),
        "fat_g": per_serving("fat"),
        "fiber_g": per_serving("fiber"),
        "sugar_g": per_serving("sugars"),
        "sodium_mg": per_serving("sodium", 1000.0),
        "caffeine_mg": per_serving("caffeine", 1000.0),
        "micros": micros or None,
        "origin": "open_food_facts",
        "verified": False,
    }


@router.get("/foods/barcode/{barcode}")
def lookup_barcode(barcode: str, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if not barcode.isdigit() or not 6 <= len(barcode) <= 14:
        raise bad_request("barcode should be 6-14 digits")
    hit = fetch_one(conn, "SELECT * FROM core.food_catalog WHERE barcode = %s", (barcode,))
    if hit:
        return to_jsonable({**hit, "found_in": "catalog"})
    try:
        r = httpx.get(f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json",
                      params={"fields": "product_name,generic_name,brands,serving_size,serving_quantity,nutriments"},
                      headers={"User-Agent": "HealthTracker/0.1 (personal, self-hosted)"}, timeout=10)
        data = r.json() if r.status_code == 200 else {}
    except httpx.HTTPError as exc:
        log.warning("Open Food Facts lookup failed: %s", exc)
        raise bad_request("Open Food Facts is unreachable; enter the food manually") from exc
    if data.get("status") != 1 or not data.get("product"):
        raise not_found("barcode not in Open Food Facts; add it manually")
    food = off_to_food(barcode, data["product"])
    fid = insert(conn, "core.food_catalog", food)
    return to_jsonable({**fetch_one(conn, "SELECT * FROM core.food_catalog WHERE id = %s", (fid,)),
                        "found_in": "open_food_facts"})


@router.post("/foods")
def create_food(body: FoodIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    fid = insert(conn, "core.food_catalog", {**body.model_dump(), "origin": "manual", "verified": True})
    return to_jsonable(fetch_one(conn, "SELECT * FROM core.food_catalog WHERE id = %s", (fid,)))


@router.put("/foods/{food_id}")
def update_food(food_id: int, body: FoodIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = {**body.model_dump(), "verified": True}
    if "micros" in d and d["micros"] is not None:
        d["micros"] = Jsonb(d["micros"])
    n = execute(conn, f"UPDATE core.food_catalog SET {', '.join(f'{k} = %s' for k in d)} WHERE id = %s",
                (*d.values(), food_id))
    if not n:
        raise not_found("unknown food")
    return to_jsonable(fetch_one(conn, "SELECT * FROM core.food_catalog WHERE id = %s", (food_id,)))


# ------------------------------------------------------------------ entries
class EntryIn(BaseModel):
    catalog_id: int
    servings: float = Field(1, gt=0, le=50)
    meal: Meal | None = None
    logged_ts: datetime | None = None
    date: Date | None = None


class EntryUpdate(BaseModel):
    servings: float | None = Field(None, gt=0, le=50)
    meal: Meal | None = None
    logged_ts: datetime | None = None


def _entry_values(conn: psycopg.Connection, body: EntryIn) -> dict:
    ts = body.logged_ts or now_local(conn)
    return {"catalog_id": body.catalog_id, "servings": body.servings, "meal": body.meal,
            "logged_ts": ts, "date": body.date or local_date(conn, ts)}


@router.post("/food-entries")
def add_entry(body: EntryIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if not fetch_one(conn, "SELECT 1 FROM core.food_catalog WHERE id = %s", (body.catalog_id,)):
        raise not_found("unknown food")
    eid = insert(conn, "core.food_entry", _entry_values(conn, body))
    return {"id": eid}


@router.put("/food-entries/{entry_id}")
def update_entry(entry_id: int, body: EntryUpdate, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = body.model_dump(exclude_none=True)
    if not d:
        return {"ok": True}
    if "logged_ts" in d:
        d["date"] = local_date(conn, d["logged_ts"])
    n = execute(conn, f"UPDATE core.food_entry SET {', '.join(f'{k} = %s' for k in d)} WHERE id = %s",
                (*d.values(), entry_id))
    if not n:
        raise not_found()
    return {"ok": True}


@router.delete("/food-entries/{entry_id}")
def delete_entry(entry_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "DELETE FROM core.food_entry WHERE id = %s", (entry_id,))
    return {"ok": True}


@router.get("/food-day/{day}")
def food_day(day: date, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    entries = fetch_all(conn, """SELECT n.*, e.catalog_id, c.brand, c.serving_desc FROM core.v_food_nutrients n
                                 JOIN core.food_entry e ON e.id = n.id
                                 JOIN core.food_catalog c ON c.id = e.catalog_id
                                 WHERE n.date = %s ORDER BY n.logged_ts""", (day,))
    totals = {c: round(sum((e[c] or 0) for e in entries), 1) for c in NUTRIENT_COLS}
    status = fetch_one(conn, "SELECT complete FROM core.food_day_status WHERE date = %s", (day,))
    weight = fetch_one(conn, """SELECT weight_kg FROM core.body_measurement WHERE ts < (%s::date + 1)
                                ORDER BY ts DESC LIMIT 1""", (day,))
    targets = fetch_all(conn, "SELECT * FROM core.nutrient_target WHERE active ORDER BY nutrient")
    resolved = []
    for t in targets:
        lo, hi = t["min_value"], t["max_value"]
        if t["basis"] == "per_kg_bodyweight":
            if not weight:
                continue
            lo = None if lo is None else round(lo * weight["weight_kg"])
            hi = None if hi is None else round(hi * weight["weight_kg"])
        resolved.append({"nutrient": t["nutrient"], "min": lo, "max": hi, "evaluation": t["evaluation"]})
    return to_jsonable({"date": day, "complete": bool(status and status["complete"]), "entries": entries,
                        "totals": totals, "targets": resolved})


class CompleteIn(BaseModel):
    complete: bool


@router.put("/food-day/{day}/complete")
def set_complete(day: date, body: CompleteIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    upsert(conn, "core.food_day_status", [{"date": day, "complete": body.complete}], conflict=["date"])
    return {"date": day.isoformat(), "complete": body.complete}


# ------------------------------------------------------------------ saved meals
class SavedMealIn(BaseModel):
    name: str = Field(min_length=1)
    items: list[dict] = Field(default_factory=list)  # [{catalog_id, servings}]
    from_date: date | None = None
    from_meal: Meal | None = None


@router.get("/saved-meals")
def saved_meals(conn: psycopg.Connection = Depends(get_conn)) -> list[dict]:
    meals = fetch_all(conn, "SELECT * FROM core.saved_meal ORDER BY name")
    for m in meals:
        m["items"] = fetch_all(conn, """SELECT i.catalog_id, i.servings, c.name, i.servings * c.kcal AS kcal,
                                               i.servings * c.protein_g AS protein_g
                                        FROM core.saved_meal_item i JOIN core.food_catalog c ON c.id = i.catalog_id
                                        WHERE i.saved_meal_id = %s""", (m["id"],))
        m["kcal"] = round(sum(i["kcal"] or 0 for i in m["items"]))
        m["protein_g"] = round(sum(i["protein_g"] or 0 for i in m["items"]))
    return to_jsonable(meals)


@router.post("/saved-meals")
def create_saved_meal(body: SavedMealIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    items = body.items
    if body.from_date:
        items = fetch_all(conn, """SELECT catalog_id, sum(servings) AS servings FROM core.food_entry
                                   WHERE date = %s AND (%s::text IS NULL OR meal = %s) GROUP BY catalog_id""",
                          (body.from_date, body.from_meal, body.from_meal))
    if not items:
        raise bad_request("a saved meal needs at least one food")
    if fetch_one(conn, "SELECT 1 FROM core.saved_meal WHERE name = %s", (body.name,)):
        raise bad_request("a saved meal with that name exists")
    mid = insert(conn, "core.saved_meal", {"name": body.name})
    for i in items:
        insert(conn, "core.saved_meal_item", {"saved_meal_id": mid, "catalog_id": i["catalog_id"],
                                              "servings": i.get("servings", 1)}, returning=None)
    return {"id": mid}


@router.delete("/saved-meals/{meal_id}")
def delete_saved_meal(meal_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    execute(conn, "DELETE FROM core.saved_meal WHERE id = %s", (meal_id,))
    return {"ok": True}


class LogMealIn(BaseModel):
    meal: Meal | None = None
    logged_ts: datetime | None = None
    date: Date | None = None


@router.post("/saved-meals/{meal_id}/log")
def log_saved_meal(meal_id: int, body: LogMealIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    items = fetch_all(conn, "SELECT * FROM core.saved_meal_item WHERE saved_meal_id = %s", (meal_id,))
    if not items:
        raise not_found("unknown saved meal")
    ids = []
    for i in items:
        e = EntryIn(catalog_id=i["catalog_id"], servings=float(i["servings"]), meal=body.meal,
                    logged_ts=body.logged_ts, date=body.date)
        ids.append(insert(conn, "core.food_entry", _entry_values(conn, e)))
    return {"ids": ids}
