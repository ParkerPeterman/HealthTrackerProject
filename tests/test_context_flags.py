"""A4: data-quality and context flags, one-tap tags, per-hypothesis exclusions."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.features.daily import dst_weeks
from healthtracker.stats.hypothesis import Hypothesis, build_frame, excluded_days
from healthtracker.transform.garmin import parse_sleep


def test_dst_week_is_the_week_of_the_change():
    idx = pd.date_range("2026-10-24", "2026-11-10", freq="D")
    flagged = idx[dst_weeks(idx, "America/Chicago")]
    assert list(flagged.strftime("%m-%d")) == ["10-26", "10-27", "10-28", "10-29", "10-30", "10-31", "11-01"]
    spring = pd.date_range("2027-03-01", "2027-03-20", freq="D")
    assert list(spring[dst_weeks(spring, "America/Chicago")].strftime("%m-%d"))[0] == "03-08"


def test_sleep_offset_from_local_timestamps():
    gmt = 1759806000000
    p = {"dailySleepDTO": {"calendarDate": "2026-10-07", "sleepTimeSeconds": 27000, "sleepStartTimestampGMT": gmt,
                           "sleepEndTimestampGMT": gmt + 28_800_000, "sleepStartTimestampLocal": gmt - 300 * 60_000}}
    assert parse_sleep(p, date(2026, 10, 7))["tz_offset_min"] == -300
    p["dailySleepDTO"].pop("sleepStartTimestampLocal")
    assert parse_sleep(p, date(2026, 10, 7))["tz_offset_min"] is None


def test_excluded_days_follow_the_hypothesis():
    idx = pd.date_range("2026-01-01", periods=6, freq="D")
    f = pd.DataFrame({"is_sick": [1, 0, 0, 0, 0, 0], "is_travel": [0, 1, 0, 0, 0, 0],
                      "injury_active": [0, 0, 1, 0, 0, 0], "sleep_plausible": [True, True, True, False, None, True],
                      "tz_change": [0, 0, 0, 0, 0, 1]}, index=idx).astype({"is_sick": bool, "is_travel": bool,
                                                                           "injury_active": bool, "tz_change": bool})
    default = excluded_days(f, ("sick", "travel", "injury", "implausible"))
    assert default.tolist() == [True, True, True, True, False, False]  # unknown plausibility isn't excluded
    assert excluded_days(f, ("sick",)).tolist() == [True] + [False] * 5
    assert excluded_days(f, ("tz_change",)).tolist()[-1]
    assert not excluded_days(f, ("injury",), exposure_col="injury_active").any()  # never exclude the exposure
    legacy = pd.DataFrame({"exclude_from_baseline": [True, False]}, index=idx[:2])
    assert excluded_days(legacy, ("sick",)).tolist() == [True, False]


def test_build_frame_uses_declared_exclusions():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    f = pd.DataFrame({"alcohol_drinks": rng.integers(0, 3, 40).astype(float), "hrv": rng.normal(60, 5, 40),
                      "sleep_total_min": 450.0, "session_load": 300.0, "is_sick": False, "is_travel": False,
                      "injury_active": False, "tag_alcohol": False}, index=idx)
    f.iloc[5:10, f.columns.get_loc("is_travel")] = True
    keep_travel = build_frame(f, Hypothesis(1, "alcohol_drinks >= 1", "hrv", 1, excludes=("sick",)))[0]
    drop_travel = build_frame(f, Hypothesis(1, "alcohol_drinks >= 1", "hrv", 1))[0]
    assert len(keep_travel) - len(drop_travel) == 6  # 5 travel days + the day before the first (lagged outcome)


@pytest.mark.db
def test_flags_and_tags_in_features(seeded, conn, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import fetch_all
    from healthtracker.features.daily import build_daily_features

    f = {r["date"]: r for r in fetch_all(conn, "SELECT * FROM analytics.daily_features ORDER BY date")}
    away = [d for d, r in f.items() if r["tz_change"]]
    assert away and all(f[d - timedelta(days=1)]["is_travel"] for d in away)  # the night after a travel day
    assert all(r["watch_overnight"] and r["sleep_plausible"] for r in f.values())
    assert any(r["tag_alcohol"] for r in f.values()) and not any(r["dst_week"] for r in f.values())

    client = TestClient(app)
    day = seeded["end"]
    out = client.put(f"/api/tags/{day}", json={"tags": ["groin_tweak", "dizzy"]}).json()
    assert out["tags"] == ["dizzy", "groin_tweak"]
    assert client.put(f"/api/tags/{day}", json={"tags": ["groin_tweak"]}).json()["tags"] == ["groin_tweak"]
    assert client.put(f"/api/tags/{day}", json={"tags": ["oops"]}).status_code == 422
    build_daily_features(conn, day, day)
    row = fetch_all(conn, "SELECT tag_groin, tag_dizzy FROM analytics.daily_features WHERE date = %s", (day,))[0]
    assert row["tag_groin"] and not row["tag_dizzy"]
    client.put(f"/api/tags/{day}", json={"tags": []})
    build_daily_features(conn, day, day)
