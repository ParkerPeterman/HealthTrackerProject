from datetime import UTC, date, datetime

import pytest

from healthtracker.transform.garmin import (
    map_sport,
    normalize_feel,
    normalize_rpe,
    parse_activity,
    parse_daily_summary,
    parse_sleep,
    parse_splits,
    parse_vo2max,
    wear_minutes,
)
from healthtracker.units import Display, fmt_hour, kg_to_lb, lb_to_kg, pace_s_per_km_to_s_per_mi


def test_unit_round_trips():
    assert lb_to_kg(kg_to_lb(80)) == pytest.approx(80)
    assert kg_to_lb(100) == pytest.approx(220.46, abs=0.01)
    assert pace_s_per_km_to_s_per_mi(300) == pytest.approx(482.8, abs=0.1)


def test_display_formats():
    imp, met = Display("imperial"), Display("metric")
    assert imp.weight(80) == 176.4 and met.weight(80) == 80
    assert imp.pace(300) == "8:03/mi" and met.pace(300) == "5:00/km"
    assert fmt_hour(14) == "14:00" and fmt_hour(24.5) == "00:30" and fmt_hour(None) is None


SLEEP = {
    "dailySleepDTO": {
        "calendarDate": "2026-10-07", "sleepTimeSeconds": 27000,
        "sleepStartTimestampGMT": 1759806000000, "sleepEndTimestampGMT": 1759834800000,
        "deepSleepSeconds": 5400, "remSleepSeconds": 6000, "lightSleepSeconds": 14400, "awakeSleepSeconds": 1200,
        "averageSpO2Value": 95.2, "averageRespirationValue": 14.1,
        "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}},
    },
    "avgOvernightHrv": 55.5, "hrvStatus": "BALANCED", "restingHeartRate": 49,
}


def test_parse_sleep():
    row = parse_sleep(SLEEP, date(2026, 10, 7))
    assert row["date"] == date(2026, 10, 7)
    assert row["total_min"] == 450 and row["deep_min"] == 90 and row["sleep_score"] == 82
    assert row["overnight_hrv"] == 55.5 and row["resting_hr"] == 49
    assert row["sleep_start"] == datetime.fromtimestamp(1759806000, tz=UTC)


def test_parse_sleep_falls_back_to_hrv_endpoint_and_handles_empty():
    p = {**SLEEP, "avgOvernightHrv": None, "hrvStatus": None}
    row = parse_sleep(p, date(2026, 10, 7), {"hrvSummary": {"lastNightAvg": 61, "status": "LOW"}})
    assert row["overnight_hrv"] == 61 and row["hrv_status"] == "LOW"
    assert parse_sleep({"dailySleepDTO": {}}, date(2026, 10, 7)) is None
    assert parse_sleep({}, date(2026, 10, 7)) is None


def test_wear_minutes_counts_present_samples():
    start = 1_759_000_000_000
    vals = [[start + i * 120_000, 60 if i % 10 else None] for i in range(720)]  # every 10th missing
    assert wear_minutes({"heartRateValues": vals}) == 1296  # 648 samples x 2 min
    assert wear_minutes({"heartRateValues": []}) == 0
    assert wear_minutes(None) is None


def test_daily_summary_sentinels_become_null():
    row = parse_daily_summary({"totalSteps": 9000, "averageStressLevel": -1, "bodyBatteryLowestValue": -2,
                               "restingHeartRate": 50}, date(2026, 10, 7), None,
                              [{"generic": {"vo2MaxPreciseValue": 51.34}}])
    assert row["steps"] == 9000 and row["avg_stress"] is None and row["body_battery_low"] is None
    assert row["vo2max"] == 51.3


@pytest.mark.parametrize(("raw", "expected"), [(70, 7), (100, 10), (10, 1), (7, 7), (0, None), (None, None), (35, 4)])
def test_normalize_rpe(raw, expected):
    assert normalize_rpe(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [(0, 1), (25, 2), (50, 3), (75, 4), (100, 5), (None, None)])
def test_normalize_feel(raw, expected):
    assert normalize_feel(raw) == expected


def test_parse_activity_and_splits():
    item = {"activityId": 123, "activityName": "Maize Running", "activityType": {"typeKey": "running"},
            "startTimeGMT": "2026-10-07 11:15:00", "duration": 2400.4, "distance": 8046.7, "averageHR": 150,
            "maxHR": 171, "averageRunningCadenceInStepsPerMinute": 170, "calories": 560}
    row = parse_activity(item, {"summaryDTO": {"directWorkoutRpe": 50, "directWorkoutFeel": 75}})
    assert row["sport"] == "run" and row["source_id"] == "123" and row["duration_s"] == 2400
    assert row["rpe"] == 5 and row["rpe_source"] == "garmin" and row["feel"] == 4
    assert row["start_ts"] == datetime(2026, 10, 7, 11, 15, tzinfo=UTC)
    no_rpe = parse_activity(item, None)
    assert no_rpe["rpe"] is None and no_rpe["rpe_source"] is None
    splits = parse_splits({"lapDTOs": [{"lapIndex": 1, "distance": 1609.3, "duration": 480, "averageHR": 148},
                                       {"lapIndex": 2, "distance": 0, "duration": 3}]})
    assert len(splits) == 1 and splits[0]["duration_s"] == 480


def test_map_sport():
    assert map_sport("strength_training") == "strength"
    assert map_sport("other", "Ultimate pickup") == "ultimate"
    assert map_sport("kayaking") == "kayaking"
    assert map_sport(None) == "other"


def test_vo2max_shapes():
    assert parse_vo2max({"generic": {"vo2MaxValue": 50}}) == 50.0
    assert parse_vo2max([{"generic": None}]) is None
