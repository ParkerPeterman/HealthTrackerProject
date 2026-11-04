"""A1: coverage report for the first month of watch data."""

from datetime import date, timedelta

import pytest

from healthtracker.db import execute
from healthtracker.ingest.coverage import _has, daily_coverage, field_presence, ranges, report


def test_ranges_collapse_consecutive_days():
    d = date(2026, 10, 1)
    days = [d, d + timedelta(days=1), d + timedelta(days=2), d + timedelta(days=6)]
    assert ranges(days) == [(d, d + timedelta(days=2)), (d + timedelta(days=6), d + timedelta(days=6))]
    assert ranges([]) == []


def test_has_follows_nested_paths_and_lists():
    assert _has({"a": {"b": 1}}, "a.b")
    assert not _has({"a": {"b": None}}, "a.b")
    assert _has([{"generic": {"vo2MaxPreciseValue": 51.2}}], "generic.vo2MaxPreciseValue")
    assert not _has({}, "x")


@pytest.mark.db
def test_coverage_finds_a_missing_night(seeded, conn):
    end = seeded["end"]
    cov = daily_coverage(conn, end)
    assert cov.days == 90 and cov.complete
    gap = end - timedelta(days=10)
    with conn.transaction():
        saved = conn.execute("SELECT * FROM core.sleep WHERE date = %s", (gap,)).fetchone()
        execute(conn, "DELETE FROM core.sleep WHERE date = %s", (gap,))
        cov = daily_coverage(conn, end)
        assert cov.missing["sleep"] == [gap] and cov.missing["hrv"] == [gap]
        assert not cov.complete
        text = report(cov, field_presence(conn))
        assert f"sleep: missing 1 day(s): {gap}" in text and "activity (" in text
        cols = ", ".join(saved)
        conn.execute(f"INSERT INTO core.sleep ({cols}) VALUES ({', '.join(['%s'] * len(saved))})",
                     list(saved.values()))
    presence = field_presence(conn)
    assert presence["sleep"]["payloads"] == 90
    assert presence["sleep"]["fields"]["avgOvernightHrv"] == 90
