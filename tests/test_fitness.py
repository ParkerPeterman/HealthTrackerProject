"""D3: the aerobic fitness trend. Recovers planted effects and finds nothing where nothing was planted."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.features.fitness import DEFAULTS, adjust, smooth

END = date(2026, 10, 31)


def runs(n=30, temp_effect=0.0, improve_per_week=0.0, seed=3, with_temp=True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = sorted(rng.choice(np.arange(120), size=n, replace=False))
    d = [END - timedelta(days=int(119 - k)) for k in days]
    temp = rng.uniform(-5, 28, n)
    t_weeks = np.array([(x - d[0]).days / 7 for x in d])
    pace = 330 - improve_per_week * t_weeks + temp_effect * (temp - 10) + rng.normal(0, 4, n)
    return pd.DataFrame({"date": d, "pace": pace, "temp_c": temp if with_temp else np.nan,
                         "dew_point_c": np.nan, "climb_m_km": np.nan, "duration_min": rng.uniform(30, 70, n)})


def test_adjustment_recovers_a_temperature_effect():
    r = runs(temp_effect=0.8)
    adjusted, cov = adjust(r, DEFAULTS)
    assert "temp" in cov["used"] and cov["per_unit"]["temp"] == pytest.approx(0.8, abs=0.25)
    assert abs(np.corrcoef(adjusted, r["temp_c"])[0, 1]) < abs(np.corrcoef(r["pace"], r["temp_c"])[0, 1])
    assert np.std(adjusted) < np.std(r["pace"])


def test_no_weather_no_adjustment():
    r = runs(with_temp=False)
    adjusted, cov = adjust(r, DEFAULTS)
    assert "temp" not in cov["used"]
    few = runs(n=4)
    assert adjust(few, DEFAULTS)[1]["used"] == []


def test_the_trend_finds_a_planted_improvement():
    r = runs(improve_per_week=1.5)
    out, _ = smooth(pd.Series(r["pace"].to_numpy(), index=pd.to_datetime(r["date"])), END, trend=True)
    last = out.iloc[-1]
    assert last["slope_hi80"] < 0 and last["slope_week"] == pytest.approx(-1.5, abs=0.8)
    assert last["level_lo80"] < last["level"] < last["level_hi80"]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_flat_noise_shows_no_clear_change(seed):
    r = runs(seed=seed)
    out, _ = smooth(pd.Series(r["pace"].to_numpy(), index=pd.to_datetime(r["date"])), END, trend=True)
    last = out.iloc[-1]
    assert not np.isnan(last["slope_lo80"])
    assert last["slope_lo80"] <= 0 <= last["slope_hi80"] or abs(last["slope_week"]) < 0.3


@pytest.mark.db
def test_demo_fitness_trend_and_api(seeded, db_url):
    from fastapi.testclient import TestClient

    from healthtracker.api.main import app

    data = TestClient(app).get("/api/fitness", params={"day": str(seeded["end"])}).json()
    pace = data["aerobic_pace"]
    assert not pace["learning"] and pace["latest"]["n_obs"] >= 10
    assert pace["latest"]["slope_hi80"] < 0, "the demo athlete gets fitter: pace at ref HR falls"
    assert pace["latest"]["level_lo80"] < pace["latest"]["level"] < pace["latest"]["level_hi80"]
    assert pace["series"] and data["decoupling"]["threshold_pct"] == 5
    assert data["vo2max"]["latest"]["level"] > 0
