"""E1: sleep regularity. The Sleep Regularity Index, midpoint spread, and the weekend gap.

SRI (Phillips et al. 2017): over pairs of moments exactly 24 hours apart, how often I'm in
the same state (asleep or awake), scaled from -100 to 100: 100 means identical days, 0 no
better than chance; real values mostly sit between 0 and 100. In the UK Biobank (Windred
et al. 2024, over 60,000 people) it predicted all-cause mortality more strongly than sleep
duration did.

Built from Garmin's main sleep window each night plus logged naps, on 5-minute epochs of the
local clock where I slept, so daylight-saving changes and travel keep clock times aligned. A
pair of days counts only when both have a recorded night. Sleep stages are not used.

Also: the spread (SD) of the sleep midpoint, and how much later the midpoint falls on
weekend nights than on weekday nights (Friday to Sunday are usually travel days). Each over
7 and 28 days.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import fetch_df

EPOCH_MIN = 5
EPOCHS = 24 * 60 // EPOCH_MIN
MIN_PAIRS = {7: 5, 28: 14}
MIN_NIGHTS = {7: 5, 28: 14}


def day_states(nights: pd.DataFrame, naps: pd.DataFrame) -> dict[date, np.ndarray]:
    """Asleep (1) / awake (0) per 5-minute epoch, for each wake date with a recorded night.

    Day D runs from local noon on D-1 to local noon on D. `nights`: date, start, end (local
    clock, naive). `naps`: start (local, naive), minutes.
    """
    out: dict[date, np.ndarray] = {}
    for r in nights.itertuples():
        if pd.isna(r.start) or pd.isna(r.end):
            continue
        d = r.date
        s = out.setdefault(d, np.zeros(EPOCHS, dtype=np.int8))
        _mark(s, d, pd.Timestamp(r.start).to_pydatetime(), pd.Timestamp(r.end).to_pydatetime())
    for r in naps.itertuples():
        start = pd.Timestamp(r.start).to_pydatetime()
        d = (start + timedelta(hours=12)).date()  # the day window the nap falls in
        if d in out:
            _mark(out[d], d, start, start + timedelta(minutes=float(r.minutes)))
    return out


def _mark(states: np.ndarray, d: date, start: datetime, end: datetime) -> None:
    window = datetime.combine(d - timedelta(days=1), datetime.min.time()) + timedelta(hours=12)
    a = int(max(0, (start - window).total_seconds() // (EPOCH_MIN * 60)))
    b = int(min(EPOCHS, -(-(end - window).total_seconds() // (EPOCH_MIN * 60))))
    if b > a:
        states[a:b] = 1


def sri(states: dict[date, np.ndarray], end: date, days: int) -> tuple[float | None, int]:
    """SRI over the pairs (D-1, D) for D in the last `days` days ending `end`."""
    same, total, pairs = 0, 0, 0
    for k in range(days):
        d = end - timedelta(days=k)
        a, b = states.get(d - timedelta(days=1)), states.get(d)
        if a is None or b is None:
            continue
        same += int((a == b).sum())
        total += EPOCHS
        pairs += 1
    if pairs < MIN_PAIRS.get(days, 1) or not total:
        return None, pairs
    return round(200 * same / total - 100, 1), pairs


def weekend_gap(mid: pd.Series, end: pd.Timestamp, days: int = 28) -> float | None:
    """Mean midpoint on weekend nights (waking Saturday or Sunday) minus weekday nights, minutes."""
    w = mid[(mid.index > end - pd.Timedelta(days=days)) & (mid.index <= end)].dropna()
    wkend = w[w.index.weekday >= 5]
    wkday = w[w.index.weekday < 5]
    if len(wkend) < 2 or len(wkday) < 5:
        return None
    return round(float(wkend.mean() - wkday.mean()), 1)


def regularity(conn: psycopg.Connection, tz: str, index: pd.DatetimeIndex, midpoint: pd.Series) -> pd.DataFrame:
    nights = fetch_df(conn, """SELECT date,
                                      coalesce((sleep_start AT TIME ZONE 'UTC') + make_interval(mins => tz_offset_min),
                                               sleep_start AT TIME ZONE %(tz)s) AS start,
                                      coalesce((sleep_end AT TIME ZONE 'UTC') + make_interval(mins => tz_offset_min),
                                               sleep_end AT TIME ZONE %(tz)s) AS "end"
                               FROM core.sleep WHERE sleep_start IS NOT NULL AND sleep_end IS NOT NULL""", {"tz": tz})
    naps = fetch_df(conn, """SELECT ts AT TIME ZONE %(tz)s AS start, value AS minutes FROM core.behavior_event
                             WHERE type_code = 'nap' AND value > 0""", {"tz": tz})
    if naps.empty:
        naps = pd.DataFrame(columns=["start", "minutes"])
    states = day_states(nights, naps)
    mid = midpoint.astype(float)
    out = pd.DataFrame(index=index)
    rows = {"sri_7d": [], "sri_28d": [], "sri_pairs_28d": [], "weekend_gap_min_28d": []}
    for ts in index:
        d = ts.date()
        v7, _ = sri(states, d, 7)
        v28, n28 = sri(states, d, 28)
        rows["sri_7d"].append(v7)
        rows["sri_28d"].append(v28)
        rows["sri_pairs_28d"].append(n28)
        rows["weekend_gap_min_28d"].append(weekend_gap(mid, ts))
    for k, v in rows.items():
        out[k] = pd.Series(v, index=index, dtype=float)
    for days in (7, 28):
        out[f"midpoint_sd_{days}d"] = mid.rolling(days, min_periods=MIN_NIGHTS[days]).std().round(1)
    return out
