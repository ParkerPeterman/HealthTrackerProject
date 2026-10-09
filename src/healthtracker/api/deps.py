"""Shared API helpers: per-request DB access and small conveniences."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime

import psycopg
from fastapi import HTTPException

from healthtracker.db import connect
from healthtracker.profile import load_profile


def get_conn() -> Iterator[psycopg.Connection]:
    with connect() as conn:
        yield conn


def not_found(what: str = "not found") -> HTTPException:
    return HTTPException(status_code=404, detail=what)


def bad_request(what: str) -> HTTPException:
    return HTTPException(status_code=400, detail=what)


def local_date(conn: psycopg.Connection, ts: datetime | None = None) -> date:
    tz = load_profile(conn).tz
    return (ts or datetime.now(tz)).astimezone(tz).date()


def now_local(conn: psycopg.Connection) -> datetime:
    return datetime.now(load_profile(conn).tz)
