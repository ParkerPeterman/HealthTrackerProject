"""C1-C4: novelty, actionability, ranking, and the new insight types."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.stats import insight_types as it
from healthtracker.stats.ranking import MAX_SHOWN, actionability, novelty, rank


def test_novelty_and_actionability():
    assert novelty(-0.2, -0.2, 0.5) == 0.0 and novelty(-0.8, -0.2, 0.5) > 0.6  # "twice as much as typical" is novel
    assert actionability("last_caffeine_hour", False) == 1.0 and actionability("last_caffeine_hour", True) == 0.5
    assert actionability("is_travel", False) == 0.0 and actionability("med_missed", False) == 0.0


def cand(key, p=0.9, est=-5.0, exposure="alcohol_drinks", outcome="hrv", nov=0.5):
    return {"key": key, "kind": "effect", "p": p, "estimate": est, "swc": 2.0, "exposure": exposure,
            "outcome": outcome, "novelty": nov, "text": key}


def test_rank_shows_at_most_three_and_repeats_only_when_moved():
    cs = [cand(f"e{k}", p=p) for k, p in enumerate((0.70, 0.75, 0.82, 0.86, 0.90))] + [cand("weak", p=0.5), cand("travel", exposure="is_travel")]
    out = rank([dict(c) for c in cs], set(), {})
    shown = [c["key"] for c in out if c["shown"]]
    assert len(shown) == MAX_SHOWN and "weak" not in shown and "travel" not in shown
    again = rank([dict(c) for c in cs], set(), {k: {"estimate": -5.0, "p": 0.9} for k in shown})
    assert not {c["key"] for c in again if c["shown"]} & set(shown)  # unchanged: not repeated
    moved = rank([dict(cand("e4", p=0.95, est=-9.0))], set(), {"e4": {"estimate": -5.0, "p": 0.9}})
    assert moved[0]["shown"] and moved[0]["recency"] == 0.0


def daily(n=150, seed=4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-03-01", periods=n, freq="D")
    sleep = rng.normal(440, 45, n)
    load_z = rng.normal(0, 1, n)
    hrv = 60 + rng.normal(0, 4, n)
    short_h = np.clip(390 - sleep, 0, None) / 60  # planted: below 6h30 HRV drops 8 ms per hour short
    hrv[1:] -= 8 * short_h[:-1]
    hard_short = (load_z >= 1) & (sleep < 420)
    hrv[1:] -= 6 * hard_short[:-1]  # planted interaction
    caffeine = rng.uniform(8, 17, n)
    bedtime = 23 + 0.25 * (caffeine - 12) + rng.normal(0, 0.3, n)
    f = pd.DataFrame({"sleep_total_min": sleep, "hrv": hrv, "load_z_28d": load_z, "last_caffeine_hour": caffeine,
                      "bedtime_hour": np.r_[np.nan, bedtime[:-1]], "is_sick": False, "is_travel": False,
                      "injury_active": False, "sleep_plausible": True}, index=idx)
    return f


def test_threshold_interaction_and_timing_are_found():
    f = daily()
    t = it.threshold(f)
    assert t and 360 <= t["threshold"] <= 420 and t["estimate"] < -4
    i = it.interaction(f)
    assert i and i["estimate"] < 0 and i["p"] > 0.6
    tm = {x["exposure"]: x for x in it.timing(f)}
    assert tm["last_caffeine_hour"]["estimate"] == pytest.approx(15, abs=6)  # 0.25 h = 15 min per hour


def test_no_threshold_in_noise():
    f = daily()
    f["hrv"] = 60 + np.random.default_rng(1).normal(0, 4, len(f))
    assert it.threshold(f) is None


@pytest.mark.db
def test_weekly_ranking_on_the_demo(seeded, tx):
    from healthtracker.db import fetch_all
    from healthtracker.stats.ranking import run_ranking

    assert run_ranking(tx, seeded["end"]) > 0
    rows = fetch_all(tx, "SELECT * FROM analytics.weekly_insight WHERE week_start = %s",
                     (seeded["end"] - timedelta(days=seeded["end"].weekday()),))
    assert 0 < sum(r["shown"] for r in rows) <= 3
    assert all(r["record"]["actionability"] == float(r["actionability"]) for r in rows)
    assert not any(r["key"].startswith("med") for r in rows)
