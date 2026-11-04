"""D3: versioned heart-rate zones and the max HR acceptance checks."""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.features.zones import Zones, check_max_hr
from healthtracker.transform.garmin import record_row

T0 = datetime(2026, 11, 10, 17, 0)


def samples(hr: list[float], cadence: list[float] | None = None, step_s: int = 1) -> pd.DataFrame:
    ts = [T0 + timedelta(seconds=i * step_s) for i in range(len(hr))]
    df = pd.DataFrame({"ts": ts, "hr": hr})
    if cadence is not None:
        df["cadence_spm"] = cadence
    return df


def ramp_then_hold(top: float, hold_s: int, start: float = 150, ramp_s: int = 180) -> list[float]:
    rise = list(np.linspace(start, top, ramp_s))
    return rise + [top] * hold_s + list(np.linspace(top, 140, 60))


def test_smooth_sustained_peak_is_accepted():
    c = check_max_hr(samples(ramp_then_hold(196, 40), cadence=[172] * 280))
    assert c.accepted and c.value >= 195 and c.peak == 196


def test_brief_spike_does_not_count_as_max():
    hr = ramp_then_hold(182, 60) + [182] * 20
    hr[200:204] = [219, 220, 220, 219]  # four seconds at 220
    c = check_max_hr(samples(hr))
    assert c.peak == 220 and c.value <= 183  # only what was held for 30 s counts
    assert not c.smooth  # the spike also jumped


def test_jump_is_rejected_as_artifact():
    hr = [158] * 120 + [188] * 60
    c = check_max_hr(samples(hr))
    assert not c.accepted and not c.smooth and "jumped" in c.reasons[0]


def test_cadence_lock_is_rejected():
    hr = ramp_then_hold(176, 60)
    cadence = [176.0] * len(hr)
    c = check_max_hr(samples(hr, cadence=cadence))
    assert not c.accepted and not c.not_cadence and "cadence" in c.reasons[0]
    off = check_max_hr(samples(hr, cadence=[150.0] * len(hr)))  # cadence well away from HR: fine
    assert off.accepted


def test_too_little_data():
    assert not check_max_hr(samples([180] * 5)).accepted


def test_zone_maths():
    z = Zones(1, 200, "field_test", True, date(2026, 11, 1))
    assert z.bpm(90) == 180 and z.hrr_bpm(50, 50) == 125
    assert list(z.five_zone([90, 110, 150, 170, 185, 199])) == [0, 1, 3, 4, 5, 5]
    assert list(z.three_zone([150, 165, 180])) == [1, 2, 3]
    table = z.table(50)
    assert table["five"][0]["bpm"] == [100, 120] and table["five"][4]["hrr_bpm"] == [185, 200]


def test_fit_record_cadence_is_doubled_for_running():
    row = record_row(7, ["2026-11-10T17:00:00+00:00", 150, 3.1, 85.5, 210.4], per_leg_cadence=True)
    assert row[4] == 171 and row[5] == 210.4
    old = record_row(7, ["2026-11-10T17:00:00+00:00", 150, 3.1], per_leg_cadence=True)
    assert old[4] is None and old[5] is None


@pytest.mark.db
def test_zones_api_and_field_test(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import connect, execute

    client = TestClient(app)
    z = client.get("/api/zones").json()
    cur = z["current"]
    assert cur["max_hr"] == 194 and cur["confirmed"] and cur["source"] == "field_test"
    assert cur["vo2_target_bpm"] == [175, 184]
    assert [h["source"] for h in z["history"]] == ["field_test", "self_report"]

    try:
        out = client.post("/api/zones", json={"max_hr": 220}).json()
        assert out["current"]["max_hr"] == 220 and not out["current"]["confirmed"]
        wrist = client.post("/api/tests", json={"test": "max_hr", "value": 205, "date": "2026-07-01"}).json()
        assert wrist["zones"]["applied"] and not wrist["zones"]["confirmed"]  # no samples to check
        strap = client.post("/api/tests", json={"test": "max_hr", "value": 198, "chest_strap": True,
                                                "date": "2026-07-02"}).json()
        assert strap["zones"]["confirmed"]
        assert client.get("/api/zones").json()["current"]["max_hr"] == 198
    finally:
        with connect() as conn:
            execute(conn, "DELETE FROM core.athlete_zones WHERE version > 2")
            execute(conn, "DELETE FROM core.performance_test WHERE date > %s", (seeded["end"],))
    assert client.get("/api/zones").json()["current"]["max_hr"] == 194
