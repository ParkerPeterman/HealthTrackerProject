"""G4: unusual days explained from the record: quality, context, my own effects, other oddities."""

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.stats.anomaly import (
    DEFAULTS,
    credible_effects,
    explain_day,
    robust_z,
    summarize_day,
)

IDX = pd.date_range("2026-03-01", periods=60, freq="D")
DAY = IDX[50]


def frame(seed=1):
    rng = np.random.default_rng(seed)
    f = pd.DataFrame(index=IDX, data={
        "hrv": rng.normal(60, 3, 60), "resting_hr": rng.normal(52, 1.5, 60), "sleep_total_min": rng.normal(440, 20, 60),
        "alcohol_drinks": 0.0, "had_game": False, "watch_overnight": True, "sleep_plausible": True, "is_sick": False,
        "is_travel": False, "session_load": rng.normal(300, 40, 60), "caffeine_mg": rng.normal(200, 20, 60)})
    f["alcohol_drinks"] = f["alcohol_drinks"].astype(float)
    f.loc[f.index[::9], "alcohol_drinks"] = 1.0  # an occasional drink is ordinary
    return f


EFFECTS = [
    {"hypothesis_id": 41, "exposure": "alcohol_drinks", "outcome": "hrv", "lag_days": 1, "kind": "dose",
     "estimate": -3.3, "p_meaningful": 0.99},
    {"hypothesis_id": 5, "exposure": "alcohol_drinks >= 1", "outcome": "hrv", "lag_days": 1, "kind": "binary",
     "estimate": -4.2, "p_meaningful": 0.99},
    {"hypothesis_id": 6, "exposure": "alcohol_drinks >= 2", "outcome": "hrv", "lag_days": 1, "kind": "binary",
     "estimate": -4.0, "p_meaningful": 0.97},
    {"hypothesis_id": 11, "exposure": "had_game", "outcome": "hrv", "lag_days": 1, "kind": "binary",
     "estimate": -4.6, "p_meaningful": 0.96},
    {"hypothesis_id": 3, "exposure": "last_caffeine_hour >= 14", "outcome": "hrv", "lag_days": 1, "kind": "binary",
     "estimate": -0.1, "p_meaningful": 0.24},  # not credible: never used
]


def explain(f, metric="hrv", illness=False):
    z, med, _ = robust_z(f[metric].astype(float), 28, 14)
    eff = credible_effects(EFFECTS, metric, DEFAULTS["min_p"])
    dev = float(f.loc[DAY, metric] - med[DAY])
    drivers = explain_day(f, DAY, metric, dev, eff, illness, DEFAULTS)
    return drivers, summarize_day(metric, DAY.date(), float(f.loc[DAY, metric]), float(med[DAY]), float(z[DAY]), drivers)


def test_robust_z_leaves_the_day_out():
    s = pd.Series(np.r_[np.full(30, 50.0) + np.tile([-1, 1], 15), 60.0], index=pd.date_range("2026-01-01", periods=31))
    z, med, sd = robust_z(s, 28, 14)
    assert med.iloc[-1] == 50.0 and z.iloc[-1] > 5


def test_estimates_are_tried_dose_first_then_the_most_extreme_threshold():
    eff = credible_effects(EFFECTS, "hrv", 0.6)
    assert [e["hypothesis_id"] for e in eff["alcohol_drinks"]] == [41, 6, 5]
    assert "last_caffeine_hour" not in eff and set(eff) == {"alcohol_drinks", "had_game"}


def test_my_own_effects_explain_a_low_hrv_morning_without_double_counting():
    f = frame()
    f.loc[DAY - timedelta(days=1), "alcohol_drinks"] = 4.0
    f.loc[DAY, "hrv"] = 44.0
    drivers, (text, share) = explain(f)
    effects = [d for d in drivers if d["kind"] == "effect"]
    assert len(effects) == 1 and effects[0]["hypothesis_id"] == 41  # the dose estimate, once
    assert effects[0]["effect"] == pytest.approx(-13.2) and effects[0]["helps_explain"]
    assert "4 drinks the day before (your estimate: about 13.2 ms less)" in text
    assert text.startswith("HRV was unusually low on") and 0.6 < share <= 1.0


def test_quality_and_context_come_first_and_nothing_is_said_when_nothing_explains_it():
    f = frame()
    f.loc[DAY, "hrv"] = 44.0
    _, (text, share) = explain(f)
    assert text.endswith("Nothing in the record explains it.") and share == 0.0
    f.loc[DAY, "watch_overnight"] = False
    f.loc[DAY, "is_travel"] = True
    drivers, (text, _) = explain(f, illness=True)
    assert [d["kind"] for d in drivers][:3] == ["quality", "context", "context"]
    assert "the reading may be off: the watch wasn't worn overnight" in text
    assert "a travel day" in text and "possible-illness pattern" in text


def test_effects_against_the_deviation_and_other_oddities():
    f = frame()
    f.loc[DAY - timedelta(days=1), "had_game"] = True
    f.loc[DAY - timedelta(days=1), "session_load"] = 700.0
    f.loc[DAY, "hrv"] = 75.0  # unusually HIGH: a game lowers HRV, so it doesn't explain this
    drivers, (text, share) = explain(f)
    game = next(d for d in drivers if d["kind"] == "effect")
    assert game["exposure"] == "had_game" and not game["helps_explain"]
    assert "a game" not in text and share == 0.0
    assert "also unusual the day before: a big training load" in text


# ------------------------------------------------------------------ the database end
@pytest.mark.db
def test_anomalies_on_the_demo_feed_the_ranking(seeded, tx, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import fetch_all
    from healthtracker.stats.anomaly import anomaly_candidates, build_anomalies

    conn = tx
    end = seeded["end"]
    assert build_anomalies(conn, end, days=90) > 0
    rows = fetch_all(conn, "SELECT * FROM analytics.anomaly ORDER BY date")
    assert all(abs(float(r["z"])) >= 2 and r["summary"] for r in rows)
    assert all("medic" not in r["summary"].lower() for r in rows)
    cands = anomaly_candidates(conn, end)
    assert all(c["kind"] == "anomaly" and 0 <= c["p"] <= 1 for c in cands)
    recent = [r for r in rows if r["date"] >= end - timedelta(days=6)]
    assert len([c for c in cands if c["key"].startswith("anomaly:")]) == len(recent)
    from healthtracker.db import use_database

    use_database(db_url)
    data = TestClient(app).get("/api/anomalies", params={"day": str(end), "days": 90}).json()
    assert data["live"] is False and isinstance(data["anomalies"], list)
