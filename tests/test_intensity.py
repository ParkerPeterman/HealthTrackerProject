"""D4: weekly intensity distribution and the moderately-hard flag."""

from datetime import date, timedelta

import pandas as pd
import pytest

from healthtracker.features.intensity import pattern, rolling_shares, weekly_rows
from healthtracker.profile import Profile
from healthtracker.rules import catalog
from healthtracker.rules.base import RuleContext

P = {"easy_target_pct": 75, "easy_target_hi_pct": 80, "moderate_flag_pct": 25, "min_minutes": 150}


def test_patterns():
    assert pattern(240, 50, 20) == "pyramidal"
    assert pattern(240, 10, 40) == "polarized"
    assert pattern(100, 150, 20) == "threshold"
    assert pattern(140, 80, 20) == "moderate_heavy"
    assert pattern(20, 5, 0) == "too_little"


def test_weekly_rows_and_rolling_shares():
    days = pd.to_datetime(["2026-11-09", "2026-11-11", "2026-11-16"])
    s = pd.DataFrame({"date": days, "low_min": [40.0, 0.0, 30.0], "mid_min": [5.0, 0.0, 0.0],
                      "high_min": [0.0, 90.0, 0.0], "tid_method": ["hr", "session_rpe", "hr"]})
    rows = weekly_rows(s)
    assert [r["week_start"] for r in rows] == [date(2026, 11, 9), date(2026, 11, 16)]
    assert rows[0]["easy_pct"] == pytest.approx(29.6, abs=0.1) and rows[0]["hr_share"] == pytest.approx(0.33, abs=0.01)
    r = rolling_shares(s, pd.date_range("2026-11-09", "2026-11-16", freq="D"))
    assert r.loc["2026-11-16", "zone_min_14d"] == 165 and r.loc["2026-11-09", "easy_pct_14d"] == pytest.approx(88.9, abs=0.1)


def test_intensity_rule():
    d = date(2026, 11, 12)

    def ctx(row):
        return RuleContext(date=d, features=pd.DataFrame.from_dict({d - timedelta(days=1): row}, orient="index"),
                           params={}, profile=Profile())
    hot = catalog.train_intensity_mix(ctx({"zone_min_14d": 400.0, "easy_pct_14d": 55.0, "mid_pct_14d": 35.0}), P)
    assert hot.fired and hot.actions[0].metric == "easy_pct_14d" and hot.actions[0].target_value == 75
    assert not catalog.train_intensity_mix(ctx({"zone_min_14d": 400.0, "easy_pct_14d": 78.0, "mid_pct_14d": 15.0}), P).fired
    assert catalog.train_intensity_mix(ctx({"zone_min_14d": 90.0, "easy_pct_14d": 40.0, "mid_pct_14d": 50.0}), P).status == "no_data"


@pytest.mark.db
def test_weekly_table_in_the_demo(seeded, conn):
    from healthtracker.db import fetch_all

    weeks = fetch_all(conn, "SELECT * FROM analytics.intensity_weekly ORDER BY week_start")
    assert len(weeks) >= 12 and all(w["low_min"] + w["mid_min"] + w["high_min"] > 0 for w in weeks)
    assert {w["pattern"] for w in weeks} - {"too_little"}
