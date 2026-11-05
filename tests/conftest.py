"""Shared fixtures. DB tests use TEST_DATABASE_URL and a freshly migrated database."""

from __future__ import annotations

import os
from datetime import date

import pytest

# The demo athlete lives at a synthetic home point (central Chicago, rounded to 41.9, -87.6) with
# synthetic weather in the cache, so sessions get weather the way they would at home (A7, D3).
# Set before anything reads the settings; a real .env home never reaches the tests.
os.environ["HOME_LAT"], os.environ["HOME_LON"] = "41.88", "-87.63"

from healthtracker.config import get_settings  # noqa: E402


def _test_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL") or get_settings().test_database_url


@pytest.fixture(scope="session")
def db_url() -> str:
    url = _test_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    from healthtracker.migrate import drop_database, ensure_database, migrate

    try:
        drop_database(url)
        ensure_database(url)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test database unavailable: {exc}")
    migrate(url)
    return url


@pytest.fixture(scope="session")
def seeded(db_url: str) -> dict:
    """A 90-day synthetic history, fully processed by the backfill job."""
    from healthtracker.db import connect, use_database
    from healthtracker.pipeline.jobs import backfill
    from healthtracker.sample.generator import generate

    use_database(db_url)
    with connect() as conn:
        info = generate(conn, days=90, end=date(2026, 6, 30), seed=7)
    report = backfill(info["start"], info["end"], llm_days=0)
    assert report.ok, report.summary()
    return info


@pytest.fixture()
def conn(db_url: str):
    from healthtracker.db import connect, use_database

    use_database(db_url)
    with connect() as c:
        yield c


@pytest.fixture()
def tx(seeded: dict, db_url: str):
    """A connection to the seeded database whose changes are always rolled back, pass or fail."""
    import psycopg
    from psycopg.rows import dict_row

    c = psycopg.connect(db_url, row_factory=dict_row)
    try:
        yield c
    finally:
        c.rollback()
        c.close()
