"""Section 13: the weekly old-vs-new shadow comparison log."""

from datetime import timedelta

import pytest

pytestmark = pytest.mark.db


def test_weekly_log_covers_every_pair(seeded, tx):
    from healthtracker.db import fetch_all
    from healthtracker.stats.shadow_log import COMPARISONS, build_shadow_log, render_log

    conn = tx
    end = seeded["end"]
    assert build_shadow_log(conn, end) == len(COMPARISONS)
    week = end - timedelta(days=end.weekday())
    rows = fetch_all(conn, "SELECT * FROM analytics.shadow_log WHERE week_start = %s ORDER BY component", (week,))
    assert {r["component"] for r in rows} == set(COMPARISONS)
    for r in rows:
        assert 0 <= r["n_disagree"] <= max(r["n_compared"], r["n_disagree"])
        assert len(r["examples"]) <= 5 and r["summary"].startswith(f"{r['n_disagree']} of {r['n_compared']}")
        assert "medic" not in (r["summary"] + str(r["examples"])).lower()
    text = render_log(rows)
    assert text.startswith("**Shadow comparisons, week of") and text.count("\n- ") == len(COMPARISONS)
    # Re-running the week replaces it.
    assert build_shadow_log(conn, end) == len(COMPARISONS)
    assert len(fetch_all(conn, "SELECT 1 FROM analytics.shadow_log WHERE week_start = %s", (week,))) == len(COMPARISONS)


def test_hrv_disagreements_are_real_disagreements(seeded, tx):
    from healthtracker.stats.shadow_log import hrv_status

    end = seeded["end"]
    res = hrv_status(tx, end - timedelta(days=60), end)
    assert res["n_compared"] > 0
    for d in res["disagree"]:
        assert (d["old"] == "low") != (d["new"] == "below")


def test_shadow_log_api(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import use_database

    use_database(db_url)
    rows = TestClient(app).get("/api/shadow-log", params={"weeks": 2}).json()
    assert isinstance(rows, list) and rows, "the backfill's weekly runs write the log"
    assert {"component", "summary", "n_disagree"} <= set(rows[0])
