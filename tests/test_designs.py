"""B7: block-randomized N-of-1 designs, their protocol actions, analysis, and choosing the next one."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.rules import catalog
from healthtracker.rules.base import RuleContext
from healthtracker.stats.designs import (
    DESIGNS,
    analyze_blocks,
    assign_blocks,
    blocks,
    conflicts,
    expected_gain,
)

MON = date(2026, 12, 7)


def test_blocks_follow_the_week_and_balance():
    a = assign_blocks(MON, 4, seed=3)
    assert len(a) == 28 and list(a.values()).count("A") == 14
    for b in blocks(MON, 4):  # one condition per block; blocks are Mon-Thu and Fri-Sun
        assert len({a[d.isoformat()] for d in b}) == 1 and len(b) in (3, 4)
    assert assign_blocks(MON, 4, seed=3) == a and assign_blocks(MON, 4, seed=4) != a
    mt = assign_blocks(MON, 4, seed=1, mon_thu_only=True)
    assert all(date.fromisoformat(d).weekday() < 4 for d in mt)


def test_protocol_action_follows_the_condition():
    proto = {"conditions": DESIGNS["caffeine_cutoff"]["conditions"], "outcome": "sleep_total_min", "outcome_lag": 1}
    exp = {"id": 1, "assignment": {MON.isoformat(): "A", (MON + timedelta(days=4)).isoformat(): "B"}, "protocol": proto}
    for d, target in ((MON, 12), (MON + timedelta(days=4), 15)):
        r = catalog.experiment_protocol(RuleContext(date=d, features=pd.DataFrame(), params={}, experiments=[exp]), {})
        assert r.fired and r.actions[0].metric == "last_caffeine_hour" and r.actions[0].target_value == target


def test_analysis_recovers_a_planted_contrast_despite_a_trend():
    rng = np.random.default_rng(2)
    a = assign_blocks(MON, 6, seed=9)
    days = pd.date_range(MON, periods=50, freq="D")
    cond = pd.Series({pd.Timestamp(d): v for d, v in a.items()})
    sleep = pd.Series(430 + 0.6 * np.arange(50) + rng.normal(0, 18, 50), index=days)  # drifting upward
    sleep = sleep + 20 * (cond.reindex(days - pd.Timedelta(days=1)).eq("A").to_numpy())  # A: +20 min that night
    f = pd.DataFrame({"sleep_total_min": sleep, "is_travel": False}, index=days)
    exp = {"assignment": a, "protocol": {"design": "caffeine_cutoff", "outcomes": [["sleep_total_min", 1]]}}
    r = analyze_blocks(f, exp, {}, {"default": 0.2})["sleep_total_min"]
    assert r["estimate"] == pytest.approx(20, abs=10) and r["p_direction"] > 0.95
    assert r["confidence"] in ("possibly", "likely", "very likely")


def test_choosing_and_guarding_designs():
    assert expected_gain(DESIGNS["caffeine_cutoff"]) > expected_gain(DESIGNS["run_time"])
    running = [{"target_type": "activity", "activity": "running", "max_rpe": 0}]
    assert conflicts(DESIGNS["run_time"], running) == ["running"] and not conflicts(DESIGNS["caffeine_cutoff"], running)


@pytest.mark.db
def test_next_experiment_and_creation(seeded, tx):
    from healthtracker.db import fetch_one
    from healthtracker.stats.designs import create_block_experiment, next_experiment

    conn = tx
    assert next_experiment(conn, seeded["end"])["code"] == "caffeine_cutoff"
    eid = create_block_experiment(conn, "dinner_timing", MON, seed=1)
    e = fetch_one(conn, "SELECT * FROM analytics.experiment WHERE id = %s", (eid,))
    early = e["protocol"]["conditions"]["A"]
    assert e["design_code"] == "dinner_timing" and isinstance(early["target_value"], float)
    assert early["action_text"].startswith("finish dinner by ")
    assert next_experiment(conn, seeded["end"])["code"] == "caffeine_cutoff"  # dinner is taken
