"""Thin database helpers on psycopg 3. SQL stays visible; no ORM."""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pandas as pd
import psycopg
import psycopg.types.numeric
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from healthtracker.config import get_settings

# NUMERIC -> float everywhere: health metrics don't need Decimal, and pandas/JSON
# handle floats natively.
psycopg.adapters.register_loader("numeric", psycopg.types.numeric.FloatLoader)

_override_url: str | None = None


def use_database(url: str | None) -> None:
    """Point every later connect() at another database (demo, tests)."""
    global _override_url
    _override_url = url


def current_url() -> str:
    return _override_url or os.environ.get("HT_DATABASE_URL") or get_settings().database_url


@contextmanager
def connect(url: str | None = None, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(url or current_url(), row_factory=dict_row, autocommit=autocommit)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_df(conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d.name for d in cur.description] if cur.description else []
    return pd.DataFrame(rows, columns=cols)


def execute(conn: psycopg.Connection, sql: str, params: Sequence | dict | None = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def _adapt(value: Any) -> Any:
    # dicts (and lists of dicts) are JSONB; lists of scalars are Postgres arrays (TEXT[] etc.)
    if isinstance(value, dict) or (isinstance(value, list) and any(isinstance(v, dict | list) for v in value)):
        return Jsonb(value)
    if isinstance(value, list):
        return value
    if value is not None and not isinstance(value, str | bytes) and pd.isna(value):
        return None
    if hasattr(value, "item") and not isinstance(value, str | bytes):  # numpy scalar
        return value.item()
    return value


def upsert(
    conn: psycopg.Connection,
    table: str,
    rows: Iterable[dict],
    conflict: Sequence[str],
    update: Sequence[str] | None = None,
    returning: str | None = None,
) -> list[Any] | int:
    """INSERT ... ON CONFLICT (conflict) DO UPDATE for each row.

    `update` defaults to every non-key column. Returns the RETURNING values when
    asked, otherwise the number of rows written.
    """
    rows = list(rows)
    if not rows:
        return [] if returning else 0
    cols = list(rows[0].keys())
    update = [c for c in (update if update is not None else cols) if c not in conflict]
    placeholders = ", ".join(["%s"] * len(cols))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
    sql += f"ON CONFLICT ({', '.join(conflict)}) "
    if update:
        sql += "DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in update)
    else:
        sql += "DO NOTHING"
    if returning:
        sql += f" RETURNING {returning}"
    out: list[Any] = []
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(sql, [_adapt(row[c]) for c in cols])
            if returning:
                got = cur.fetchone()
                if got is not None:
                    out.append(got[returning] if returning in got else got)
    return out if returning else len(rows)


def insert(conn: psycopg.Connection, table: str, row: dict, returning: str | None = "id") -> Any:
    cols = list(row.keys())
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    if returning:
        sql += f" RETURNING {returning}"
    with conn.cursor() as cur:
        cur.execute(sql, [_adapt(row[c]) for c in cols])
        return cur.fetchone()[returning] if returning else None
