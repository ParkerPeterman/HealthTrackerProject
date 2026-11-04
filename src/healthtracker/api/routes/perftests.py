"""Performance tests (A8): max HR, 5 km time trial, sprints, and jumps."""

from __future__ import annotations

from datetime import date
from datetime import date as Date
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from healthtracker.api.deps import get_conn, local_date, not_found
from healthtracker.db import execute, fetch_all, fetch_one, insert
from healthtracker.features.zones import zones_from_test
from healthtracker.jsonutil import to_jsonable
from healthtracker.perftests import TESTS, WHEN, protocols, summary

router = APIRouter(prefix="/api", tags=["tests"])

TestKey = Literal["max_hr", "time_trial_5k", "sprint_10m", "sprint_30m", "cmj", "broad_jump"]


class PerfTestIn(BaseModel):
    test: TestKey
    date: Date | None = None
    value: float | None = None
    trials: list[float] | None = Field(None, max_length=5)
    chest_strap: bool = False
    method: str | None = None
    conditions: str | None = None
    workout_id: int | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def check(self) -> PerfTestIn:
        spec = TESTS[self.test]
        trials = [t for t in (self.trials or []) if t is not None]
        if trials:
            self.trials = trials
            self.value = spec.best(trials) if spec.better else max(trials)
        if self.value is None:
            raise ValueError("enter a result")
        for v in trials or [self.value]:
            if not spec.lo <= v <= spec.hi:
                raise ValueError(f"{spec.label} should be between {spec.lo:g} and {spec.hi:g} {spec.unit}")
        return self


@router.get("/tests")
def list_tests(day: date | None = None, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    today = day or local_date(conn)
    rows = fetch_all(conn, "SELECT * FROM core.performance_test ORDER BY date DESC, id DESC LIMIT 200")
    return to_jsonable({"tests": rows, "summary": summary(conn, today), "protocols": protocols(), "when": WHEN})


def _row(body: PerfTestIn, conn: psycopg.Connection) -> dict:
    data = body.model_dump()
    data["date"] = data["date"] or local_date(conn)
    data["unit"] = TESTS[body.test].unit
    return data


@router.post("/tests")
def create_test(body: PerfTestIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    tid = insert(conn, "core.performance_test", _row(body, conn))
    row = fetch_one(conn, "SELECT * FROM core.performance_test WHERE id = %s", (tid,))
    if body.test == "max_hr":  # a max HR test sets the zones (D3), confirmed if strap or checks allow
        row["zones"] = zones_from_test(conn, row)
    return to_jsonable(row)


@router.put("/tests/{test_id}")
def update_test(test_id: int, body: PerfTestIn, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    d = _row(body, conn)
    if not execute(conn, f"UPDATE core.performance_test SET {', '.join(f'{k} = %s' for k in d)} WHERE id = %s",
                   (*d.values(), test_id)):
        raise not_found()
    return to_jsonable(fetch_one(conn, "SELECT * FROM core.performance_test WHERE id = %s", (test_id,)))


@router.delete("/tests/{test_id}")
def delete_test(test_id: int, conn: psycopg.Connection = Depends(get_conn)) -> dict:
    if not execute(conn, "DELETE FROM core.performance_test WHERE id = %s", (test_id,)):
        raise not_found()
    return {"ok": True}
