"""Apply db/migrations/*.sql in order, once each."""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql as psql

from healthtracker.config import get_settings
from healthtracker.db import connect, fetch_all


def ensure_database(url: str) -> bool:
    """Create the database named in `url` if it does not exist. Returns True if created."""
    info = psycopg.conninfo.conninfo_to_dict(url)
    dbname = info["dbname"]
    info["dbname"] = "postgres"
    admin = psycopg.conninfo.make_conninfo(**info)
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if exists:
            return False
        conn.execute(psql.SQL("CREATE DATABASE {}").format(psql.Identifier(dbname)))
        return True


def drop_database(url: str) -> None:
    info = psycopg.conninfo.conninfo_to_dict(url)
    dbname = info["dbname"]
    info["dbname"] = "postgres"
    with psycopg.connect(psycopg.conninfo.make_conninfo(**info), autocommit=True) as conn:
        conn.execute(psql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(psql.Identifier(dbname)))


def migrate(url: str | None = None, migrations_dir: Path | None = None) -> list[str]:
    """Apply pending migrations. Returns the file names applied."""
    migrations_dir = migrations_dir or get_settings().migrations_dir
    files = sorted(p for p in Path(migrations_dir).glob("*.sql"))
    applied_now: list[str] = []
    with connect(url) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS public.schema_migrations (
                   name TEXT PRIMARY KEY,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
        done = {r["name"] for r in fetch_all(conn, "SELECT name FROM public.schema_migrations")}
        conn.commit()
        for f in files:
            if f.name in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text())
                conn.execute("INSERT INTO public.schema_migrations (name) VALUES (%s)", (f.name,))
            applied_now.append(f.name)
    return applied_now
