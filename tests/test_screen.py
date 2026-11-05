"""B6: the exploratory screen finds a planted dose effect and rarely flags noise."""

import numpy as np
import pandas as pd
import pytest

from healthtracker.stats import screen as scr
from healthtracker.stats.hypothesis import build_frame


def synthetic(n=120, seed=6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    f = pd.DataFrame(index=idx)
    for col in ("caffeine_mg", "kcal", "protein_g", "training_min", "load_internal", "last_meal_hour"):
        f[col] = rng.normal(100, 20, n)
    f["alcohol_drinks"] = rng.poisson(0.7, n).astype(float)
    hrv = 60 + rng.normal(0, 6, n)
    hrv[1:] -= 4.0 * f["alcohol_drinks"].to_numpy()[:-1]  # planted: drinks -> next morning's HRV
    f["hrv"] = hrv
    for col in ("energy", "mood", "soreness"):
        f[col] = rng.integers(1, 6, n).astype(float)
    f["sleep_total_min"] = rng.normal(450, 30, n)
    f["session_load"] = rng.normal(300, 50, n)
    for col in ("is_sick", "is_travel", "injury_active"):
        f[col] = False
    f["sleep_plausible"] = True
    return f


def test_screen_finds_the_planted_effect_and_little_else(monkeypatch):
    f = synthetic()
    results = []
    p = {"prior_sd": 0.1, "flag_p": 0.5}
    for col, dose, outcome, lag in scr.combos(f, 3, {}):
        h = scr.Hypothesis(0, col, outcome, lag, kind="dose" if dose else "binary")
        df = build_frame(f, h)[0]
        e = scr.estimate(df, h, (0.0, p["prior_sd"], "x"), 0.2)
        if e.p_meaningful is not None:
            results.append((col, outcome, lag, e.p_meaningful))
    flagged = {(c, o, k) for c, o, k, pm in results if pm >= p["flag_p"]}
    assert ("alcohol_drinks", "hrv", 1) in flagged
    noise = [r for r in results if r[:3] != ("alcohol_drinks", "hrv", 1)]
    assert np.mean([pm >= p["flag_p"] for *_, pm in noise]) < 0.05


def test_screen_never_scans_medication_or_poor_measures():
    f = synthetic()
    f["med_missed"] = 0.0
    f["body_battery_low"] = 50.0
    cols = {c for c, *_ in scr.combos(f, 3, {"body_battery_low": "poor"})}
    assert "med_missed" not in cols and "body_battery_low" not in cols
    assert all(lag >= 1 for c, _, o, lag in scr.combos(f, 3, {}) if o == "hrv" and c != "bedtime_hour")


@pytest.mark.db
def test_screen_on_the_demo(seeded, tx):
    from healthtracker.db import fetch_all

    assert scr.run_screen(tx, seeded["end"]) > 50
    flagged = fetch_all(tx, "SELECT * FROM analytics.screen_result WHERE flagged AND computed_on = %s", (seeded["end"],))
    assert any(r["exposure"] == "alcohol_drinks" and r["outcome"] == "hrv" for r in flagged)
