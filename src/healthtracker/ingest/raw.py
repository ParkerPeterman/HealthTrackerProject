"""raw.ingest: untouched payloads, deduplicated by content hash."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from healthtracker.db import fetch_all


def payload_hash(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def store_raw(
    conn: psycopg.Connection,
    source: str,
    endpoint: str,
    for_date: date | None,
    payload: Any,
) -> bool:
    """Insert a payload unless an identical one is already stored. Returns True if new."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO raw.ingest (source, endpoint, for_date, payload, payload_hash)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (source, endpoint, for_date, payload_hash) DO NOTHING""",
            (source, endpoint, for_date, Jsonb(payload), payload_hash(payload)),
        )
        return cur.rowcount == 1


def latest_payloads(
    conn: psycopg.Connection,
    source: str,
    endpoint: str,
    start: date | None = None,
    end: date | None = None,
    key: str | None = None,
) -> list[dict]:
    """Most recent payload per for_date (or per for_date + payload->>key).

    Older payloads stay in raw for audit; parsing always uses the newest one.
    """
    if key is not None and not key.isidentifier():
        raise ValueError(f"bad payload key {key!r}")
    partition = "for_date" + (f", payload->>'{key}'" if key else "")
    rows = fetch_all(
        conn,
        f"""SELECT DISTINCT ON ({partition}) id, for_date, fetched_at, payload
            FROM raw.ingest
            WHERE source = %s AND endpoint = %s
              AND (%s::date IS NULL OR for_date >= %s::date)
              AND (%s::date IS NULL OR for_date <= %s::date)
            ORDER BY {partition}, fetched_at DESC, id DESC""",
        (source, endpoint, start, start, end, end),
    )
    return rows
