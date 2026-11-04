"""D1: uncoupled acute/chronic load, weekly change, and ramp guards (ACWR no longer decides)."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.planning.ramp import ramp_cap
from healthtracker.profile import Profile
from healthtracker.rules import catalog
from healthtracker.rules.base import RuleContext

D = date(2026, 11, 12)
RUN = {"max_weekly_increase_pct": 10, "low_base_allowance": 60}
CH = {"sprint_increase_pct": 15, "sprint_allowance_m": 100, "plyo_increase_pct": 15, "plyo_allowance": 40}


def ctx(yesterday: dict) -> RuleContext:
    f = pd.DataFrame.from_dict({D - timedelta(days=1): yesterday}, orient="index")
    return RuleContext(date=D, features=f, params={}, profile=Profile())


def test_ramp_cap_allows_small_absolute_growth_from_a_low_base():
    assert ramp_cap(1000, 10, 60) == pytest.approx(1100)
    assert ramp_cap(100, 10, 60) == 160  # 10% of a low base is too little to build from
    assert ramp_cap(None, 10, 60) == 60


def test_run_ramp_fires_only_above_the_cap():
    r = catalog.train_run_ramp(ctx({"run_load_week": 1150.0, "run_load_week_prev": 1000.0}), RUN)
    assert r.fired and r.actions[0].target_value == 1100 and r.actions[0].metric == "run_load_week"
    assert not catalog.train_run_ramp(ctx({"run_load_week": 1090.0, "run_load_week_prev": 1000.0}), RUN).fired
    assert catalog.train_run_ramp(ctx({}), RUN).status == "no_data"


def test_channel_ramp_checks_sprints_and_jumps_separately():
    r = catalog.train_channel_ramp(ctx({"sprint_week": 400.0, "sprint_week_prev": 200.0,
                                        "plyo_week": 60.0, "plyo_week_prev": 50.0}), CH)
    assert r.fired and [a.metric for a in r.actions] == ["sprint_week"]
    assert r.actions[0].target_value == 300  # 200 + 100 m allowance beats +15%


def test_chronic_ewma_leaves_out_the_acute_week():
    from healthtracker.features.daily import weekly_change

    idx = pd.date_range("2026-01-01", periods=60, freq="D")
    load = pd.Series(100.0, index=idx)
    load.iloc[-7:] = 300.0  # a big week
    chronic = load.ewm(span=28, adjust=False).mean().shift(7)
    assert chronic.iloc[-1] == pytest.approx(100.0)  # the spike isn't in its own denominator
    assert weekly_change(pd.Series([2100.0]), pd.Series([700.0])).iloc[0] == 200.0
    assert np.isnan(weekly_change(pd.Series([100.0]), pd.Series([0.0])).iloc[0])


@pytest.mark.db
def test_weekly_columns_in_the_demo(seeded, conn):
    from healthtracker.db import fetch_one

    row = fetch_one(conn, "SELECT * FROM analytics.daily_features WHERE date = %s", (seeded["end"],))
    assert row["load_week"] > 0 and row["load_week_prev"] > 0 and row["chronic_ewma"] > 0
    assert row["run_load_week"] is not None and row["load_week_change_pct"] is not None
    retired = fetch_one(conn, "SELECT count(*) AS n FROM analytics.hypothesis WHERE exposure LIKE 'acwr%%' AND status <> 'retired'")
    assert retired["n"] == 0
