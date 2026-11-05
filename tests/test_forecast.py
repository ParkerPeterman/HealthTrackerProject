"""G2: local-linear-trend forecasts, rolling-origin backtests against naive, goal arrival dates."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.stats.forecast import backtest, fit_llt, forecast_series, goal_eta, project

END = date(2026, 6, 30)


def daily(values, end=END, every=1):
    idx = pd.date_range(end=pd.Timestamp(end), periods=len(values), freq="D")
    return pd.Series(values, index=idx).iloc[::every]


def test_intervals_widen_with_the_horizon():
    rng = np.random.default_rng(3)
    s = daily(85 - 0.04 * np.arange(90) + rng.normal(0, 0.4, 90))
    p = project(fit_llt(s, pd.Timestamp(END)), 60)
    assert p["sd"][59] > p["sd"][6] > 0
    assert p["mean"][59] < p["mean"][0], "the downward trend carries on"


def test_a_steady_trend_beats_naive_and_intervals_cover():
    rng = np.random.default_rng(11)
    s = daily(85 - 0.05 * np.arange(120) + rng.normal(0, 0.35, 120))
    bt = backtest(s, test=14, min_obs=21, window=7)
    assert bt["backtest_n"] >= 8 and bt["beats_naive"], bt
    assert 0.5 <= bt["coverage80"] <= 1.0


def test_too_little_data_gives_no_forecast():
    s = daily([20.0, 19.8, 19.9])
    assert forecast_series("sprint_10m", s, END) is None
    assert backtest(s, test=28, min_obs=5, window=0)["beats_naive"] is False


def test_forecast_row_has_a_headline_and_a_year_of_path():
    rng = np.random.default_rng(5)
    s = daily(50 + 0.02 * np.arange(100) + rng.normal(0, 0.2, 100), every=3)
    r = forecast_series("vo2max", s, END)
    assert r["headline"]["date"] == str(END + timedelta(days=56))
    assert r["headline"]["lo80"] < r["headline"]["mean"] < r["headline"]["hi80"]
    assert len(r["path"]["mean"]) == 365 and r["path"]["start"] == str(END + timedelta(days=1))


def test_goal_eta_is_a_date_range():
    start = date(2026, 7, 1)
    days = np.arange(365)
    path = {"start": str(start), "mean": list(80 - 0.01 * days), "lo80": list(80 - 0.01 * days - 0.004 * days),
            "hi80": list(80 - 0.01 * days + 0.004 * days)}
    eta = goal_eta({"path": path}, 79.0, "lower")
    likely, earliest = date.fromisoformat(eta["likely"]), date.fromisoformat(eta["earliest"])
    latest = date.fromisoformat(eta["latest"])
    assert earliest < likely < latest
    assert likely == start + timedelta(days=100)
    far = goal_eta({"path": path}, 76.0, "lower")
    assert far["likely"] is None and far["latest"] is None and far["earliest"] is not None
    up = goal_eta({"path": {**path, "mean": [-v for v in path["mean"]], "lo80": [-v for v in path["hi80"]],
                            "hi80": [-v for v in path["lo80"]]}}, -79.0, "higher")
    assert up["likely"] == eta["likely"]
    assert goal_eta({"path": None}, 1.0, "lower") == {"likely": None, "earliest": None, "latest": None}


# ------------------------------------------------------------------ the database end
@pytest.mark.db
def test_forecasts_on_the_demo_and_shown_only_when_live(seeded, tx, db_url):
    from healthtracker.db import execute, fetch_all
    from healthtracker.goals.progress import goal_progress
    from healthtracker.stats.forecast import build_forecasts
    from healthtracker.stats.insights import load_features_frame

    conn = tx
    end = seeded["end"]
    assert build_forecasts(conn, end) >= 3
    rows = {(r["metric"], r["model"]): r for r in fetch_all(conn, "SELECT * FROM analytics.forecast WHERE computed_on = %s",
                                                             (end,))}
    assert {("weight_kg", "local_linear_trend"), ("aerobic_pace", "local_linear_trend"),
            ("aerobic_pace", "fitness_fatigue")} <= set(rows)
    w = rows[("weight_kg", "local_linear_trend")]
    assert w["backtest_n"] >= 3 and w["mae_naive"] is not None and w["path"]["start"] == str(end + timedelta(days=1))
    assert ("sprint_10m", "local_linear_trend") not in rows, "three tests are too few to forecast"

    goal = {"id": 0, "goal_type": "body_weight", "metric": "weight_kg", "direction": "decrease",
            "start_value": float(w["last_value"]) + 1, "target_value": float(w["last_value"]) - 2,
            "start_date": end - timedelta(days=30), "target_date": None, "rate_per_week": -0.25}
    features = load_features_frame(conn, end)
    assert "eta" not in goal_progress(conn, goal, features, end), "shadow: no arrival dates yet"
    execute(conn, "UPDATE analytics.rule SET status = 'active' WHERE rule_id = 'COMP_FORECASTS'")
    p = goal_progress(conn, goal, features, end)
    if w["beats_naive"]:
        assert set(p["eta"]) >= {"likely", "earliest", "latest"} and p["eta"]["backtest_n"] == w["backtest_n"]
    else:
        assert "eta" not in p


@pytest.mark.db
def test_forecasts_api(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app
    from healthtracker.db import use_database

    use_database(db_url)
    data = TestClient(app).get("/api/forecasts", params={"day": str(seeded["end"])}).json()
    assert data["live"] is False and data["forecasts"]
    assert all(f["shown"] is False for f in data["forecasts"])
    assert all("path" not in f for f in data["forecasts"])
