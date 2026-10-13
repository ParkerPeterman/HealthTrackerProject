"""Water API: add, total, suggested target, delete."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def test_water_api_totals_by_local_day(db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import use_database

    use_database(db_url)
    client = TestClient(app)
    day = "2031-03-04"
    noon = datetime(2031, 3, 4, 18, tzinfo=UTC)  # midday in any US time zone
    a = client.post("/api/water", json={"ml": 250, "ts": noon.isoformat()}).json()
    client.post("/api/water", json={"ml": 500, "ts": (noon + timedelta(hours=1)).isoformat()})
    client.post("/api/water", json={"ml": 300, "ts": (noon + timedelta(days=1)).isoformat()})  # next day

    out = client.get(f"/api/water/{day}").json()
    assert out["total_ml"] == 750
    assert [e["ml"] for e in out["entries"]] == [250, 500]
    assert out["target_ml"] > 0

    assert client.delete(f"/api/water/{a['id']}").json() == {"ok": True}
    assert client.get(f"/api/water/{day}").json()["total_ml"] == 500
    assert client.delete(f"/api/water/{a['id']}").status_code == 404
    assert client.post("/api/water", json={"ml": 0}).status_code == 422
