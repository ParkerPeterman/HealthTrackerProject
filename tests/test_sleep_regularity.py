"""E1: the Sleep Regularity Index, midpoint spread, and weekend gap."""

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from healthtracker.features.sleep import day_states, sri, weekend_gap

END = date(2026, 11, 29)


def nights(bed_hours: list[float], length_h: float = 8.0) -> pd.DataFrame:
    rows = []
    for k, h in enumerate(bed_hours):
        d = END - timedelta(days=len(bed_hours) - 1 - k)
        start = datetime.combine(d - timedelta(days=1), datetime.min.time()) + timedelta(hours=h)
        rows.append({"date": d, "start": start, "end": start + timedelta(hours=length_h)})
    return pd.DataFrame(rows)


NO_NAPS = pd.DataFrame(columns=["start", "minutes"])


def test_identical_days_score_100():
    s = day_states(nights([23.0] * 30), NO_NAPS)
    assert sri(s, END, 28) == (100.0, 28) and sri(s, END, 7)[0] == 100.0


def test_irregular_and_weekend_shifted_schedules_score_lower():
    rng = np.random.default_rng(4)
    shifted = [23.0 + (2.5 if (END - timedelta(days=29 - k)).weekday() >= 5 else 0) for k in range(30)]
    chaotic = list(rng.uniform(20, 28, 30))
    v_shift = sri(day_states(nights(shifted), NO_NAPS), END, 28)[0]
    v_chaos = sri(day_states(nights(chaotic), NO_NAPS), END, 28)[0]
    assert 40 < v_shift < 100 and v_chaos < v_shift


def test_naps_count_and_missing_nights_drop_pairs():
    base = nights([23.0] * 10)
    nap = pd.DataFrame([{"start": datetime.combine(END - timedelta(days=1), datetime.min.time()) + timedelta(hours=14),
                         "minutes": 60}])
    with_nap = day_states(base, nap)
    assert with_nap[END].sum() == day_states(base, NO_NAPS)[END].sum() + 12  # 60 min = 12 epochs
    gappy = base.drop(index=[3, 6])
    assert sri(day_states(gappy, NO_NAPS), END, 7)[1] < 7
    assert sri(day_states(nights([23.0] * 4), NO_NAPS), END, 7) == (None, 3)  # too few pairs


def test_weekend_gap():
    idx = pd.date_range(END - timedelta(days=27), END, freq="D")
    mid = pd.Series([240.0 + (60 if d.weekday() >= 5 else 0) for d in idx], index=idx)
    assert weekend_gap(mid, pd.Timestamp(END)) == 60.0


@pytest.mark.db
def test_regularity_in_the_demo(seeded, conn):
    from healthtracker.db import fetch_all

    f = fetch_all(conn, "SELECT * FROM analytics.daily_features ORDER BY date")
    last = f[-1]
    assert last["sri_28d"] is not None and 40 < last["sri_28d"] <= 100
    assert last["midpoint_sd_28d"] is not None and last["midpoint_sd_7d"] is not None
    assert all(r["sri_28d"] is None for r in f[:14])  # 14-night gate
