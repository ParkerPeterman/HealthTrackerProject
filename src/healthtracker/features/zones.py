"""Versioned heart-rate zones (D3) and the checks a new max HR must pass.

The newest row of core.athlete_zones applies to all history: when the field test
confirms (or corrects) max HR, every past session's time in zones and load is
recomputed with it. There is no age-formula fallback.

Wrist sensors can lock onto running cadence and report step rate instead of pulse,
most often in hard intervals, and sprint cadence can sit near 220. So an observed
max HR counts only if it (1) rose smoothly, without sensor jumps, (2) was held for
at least `sustain_s` seconds, and (3) does not match the cadence channel within
`cadence_margin_bpm` (or half of it, for per-leg cadence).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import fetch_all, fetch_df, fetch_one, insert

FIVE_ZONE_PCT = (50, 60, 70, 80, 90, 100)  # % max HR: zone k spans FIVE_ZONE_PCT[k-1]..[k]
DEFAULTS = {"z1_upper_pct_max": 80.0, "z2_upper_pct_max": 88.0, "sustain_s": 30.0, "max_jump_bpm": 10.0,
            "cadence_margin_bpm": 3.0}


def zone_params(conn: psycopg.Connection) -> dict[str, float]:
    rows = fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_ZONES'")
    return {**DEFAULTS, **{r["name"]: float(r["value"]) for r in rows}}


@dataclass(frozen=True)
class Zones:
    version: int
    max_hr: int
    source: str
    confirmed: bool
    valid_from: date
    z1_upper_pct: float = DEFAULTS["z1_upper_pct_max"]
    z2_upper_pct: float = DEFAULTS["z2_upper_pct_max"]

    def bpm(self, pct: float) -> int:
        return int(round(self.max_hr * pct / 100))

    def hrr_bpm(self, pct: float, resting_hr: float) -> int:
        """Karvonen: resting + pct of heart-rate reserve."""
        return int(round(resting_hr + (self.max_hr - resting_hr) * pct / 100))

    def five_zone(self, hr: np.ndarray | pd.Series) -> np.ndarray:
        """0 below zone 1 (under 50% max), then zones 1-5."""
        pct = np.asarray(hr, dtype=float) / self.max_hr * 100
        return np.clip(np.digitize(pct, FIVE_ZONE_PCT[:-1]), 0, 5)

    def three_zone(self, hr: np.ndarray | pd.Series) -> np.ndarray:
        """1 easy, 2 moderate (between the thresholds), 3 hard."""
        pct = np.asarray(hr, dtype=float) / self.max_hr * 100
        return np.where(pct < self.z1_upper_pct, 1, np.where(pct < self.z2_upper_pct, 2, 3))

    def table(self, resting_hr: float | None) -> dict:
        five = [{"zone": k, "pct_max": [FIVE_ZONE_PCT[k - 1], FIVE_ZONE_PCT[k]],
                 "bpm": [self.bpm(FIVE_ZONE_PCT[k - 1]), self.bpm(FIVE_ZONE_PCT[k])],
                 "hrr_bpm": ([self.hrr_bpm(FIVE_ZONE_PCT[k - 1], resting_hr), self.hrr_bpm(FIVE_ZONE_PCT[k], resting_hr)]
                             if resting_hr else None)} for k in range(1, 6)]
        three = [{"zone": 1, "label": "easy", "bpm": [None, self.bpm(self.z1_upper_pct) - 1]},
                 {"zone": 2, "label": "moderate", "bpm": [self.bpm(self.z1_upper_pct), self.bpm(self.z2_upper_pct) - 1]},
                 {"zone": 3, "label": "hard", "bpm": [self.bpm(self.z2_upper_pct), None]}]
        return {"five": five, "three": three, "resting_hr": resting_hr}


def _row_to_zones(row: dict, params: dict) -> Zones:
    return Zones(row["version"], int(row["max_hr"]), row["source"], bool(row["confirmed"]), row["valid_from"],
                 params["z1_upper_pct_max"], params["z2_upper_pct_max"])


def current_zones(conn: psycopg.Connection) -> Zones | None:
    row = fetch_one(conn, "SELECT * FROM core.athlete_zones ORDER BY version DESC LIMIT 1")
    return _row_to_zones(row, zone_params(conn)) if row else None


def zone_history(conn: psycopg.Connection) -> list[dict]:
    return fetch_all(conn, "SELECT * FROM core.athlete_zones ORDER BY version DESC")


def add_zones(conn: psycopg.Connection, max_hr: int, source: str, confirmed: bool, valid_from: date,
              test_id: int | None = None, workout_id: int | None = None, checks: dict | None = None,
              note: str | None = None) -> int:
    return insert(conn, "core.athlete_zones", {
        "max_hr": int(round(max_hr)), "source": source, "confirmed": confirmed, "valid_from": valid_from,
        "test_id": test_id, "workout_id": workout_id, "checks": checks, "note": note}, returning="version")


# ------------------------------------------------------------------ max HR checks
@dataclass
class MaxHrCheck:
    value: int | None                 # highest HR held for `sustain_s`
    peak: int | None                  # highest single sample
    smooth: bool = False
    sustained: bool = False
    not_cadence: bool = True
    reasons: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.value is not None and self.smooth and self.sustained and self.not_cadence

    def as_dict(self) -> dict:
        return {"value": self.value, "peak": self.peak, "smooth": self.smooth, "sustained": self.sustained,
                "not_cadence": self.not_cadence, "accepted": self.accepted, "reasons": self.reasons}


def check_max_hr(samples: pd.DataFrame, sustain_s: float = 30, max_jump_bpm: float = 10,
                 cadence_margin_bpm: float = 3) -> MaxHrCheck:
    """Run the three checks on one session's samples (columns ts, hr, optional cadence_spm)."""
    s = samples.dropna(subset=["hr"]).sort_values("ts").reset_index(drop=True)
    if len(s) < 10:
        return MaxHrCheck(None, None, reasons=["too few heart-rate samples"])
    t = (pd.to_datetime(s["ts"]) - pd.to_datetime(s["ts"]).iloc[0]).dt.total_seconds().to_numpy()
    hr = s["hr"].to_numpy(dtype=float)
    peak = int(hr.max())
    # Highest value held for the whole window: for each sample, the minimum over the window that
    # ends there, kept only when the samples actually span the window (no long gaps).
    best, best_end = None, None
    lo = 0
    for i in range(len(t)):
        while t[i] - t[lo] > sustain_s:
            lo += 1
        start = max(lo - 1, 0)
        if t[i] - t[start] < sustain_s * 0.95 or i - start < 3:
            continue
        window_min = hr[start:i + 1].min()
        gaps = np.diff(t[start:i + 1]).max() if i > start else sustain_s
        if gaps <= 10 and (best is None or window_min > best):
            best, best_end = window_min, i
    out = MaxHrCheck(int(best) if best is not None else None, peak)
    if best is None:
        out.reasons.append(f"no stretch of {int(sustain_s)} s with continuous heart-rate data")
        return out
    out.sustained = True
    end_t = t[best_end]
    lead = (t >= end_t - sustain_s - 120) & (t <= end_t)
    dt, dhr = np.diff(t[lead]), np.diff(hr[lead])
    jumps = np.abs(dhr[dt <= 3]) if len(dt) else np.array([])
    out.smooth = not (len(jumps) and jumps.max() > max_jump_bpm)
    if not out.smooth:
        out.reasons.append(f"heart rate jumped {int(jumps.max())} bpm within seconds on the way up (sensor artifact)")
    if "cadence_spm" in s and s["cadence_spm"].notna().any():
        win = (t >= end_t - sustain_s) & (t <= end_t)
        cad = s.loc[win, "cadence_spm"].to_numpy(dtype=float)
        h = hr[win]
        ok = ~np.isnan(cad)
        if ok.any():
            match = (np.abs(h[ok] - cad[ok]) <= cadence_margin_bpm) | (np.abs(h[ok] - cad[ok] / 2) <= cadence_margin_bpm)
            if match.mean() >= 0.5:
                out.not_cadence = False
                out.reasons.append("heart rate matched running cadence (wrist sensor cadence lock)")
    return out


def workout_samples(conn: psycopg.Connection, workout_id: int) -> pd.DataFrame:
    return fetch_df(conn, "SELECT ts, hr, cadence_spm FROM core.hr_sample WHERE workout_id = %s ORDER BY ts",
                    (workout_id,))


def zones_from_test(conn: psycopg.Connection, test: dict) -> dict:
    """A logged max HR test (A8) -> a new zones version.

    A chest strap confirms it outright. On a wrist, the test's run (linked, or the day's run
    with samples) must pass the checks; without data to check, the value is used but stays
    provisional.
    """
    params = zone_params(conn)
    value = int(round(float(test["value"])))
    if test.get("chest_strap"):
        v = add_zones(conn, value, "field_test", True, test["date"], test_id=test["id"],
                      note="max HR test with a chest strap")
        return {"applied": True, "confirmed": True, "version": v, "reasons": []}
    wid = test.get("workout_id")
    if wid is None:
        row = fetch_one(conn, """SELECT w.id FROM core.v_workout w JOIN core.profile p ON p.id = 1
                                 WHERE (w.start_ts AT TIME ZONE p.timezone)::date = %s
                                   AND EXISTS (SELECT 1 FROM core.hr_sample s WHERE s.workout_id = w.id)
                                 ORDER BY w.max_hr DESC NULLS LAST LIMIT 1""", (test["date"],))
        wid = row and row["id"]
    if wid is None:
        v = add_zones(conn, value, "field_test", False, test["date"], test_id=test["id"],
                      note="wrist max HR test without samples to check: provisional")
        return {"applied": True, "confirmed": False, "version": v,
                "reasons": ["no per-second heart rate to check; use a chest strap or let the watch record the run"]}
    check = check_max_hr(workout_samples(conn, wid), params["sustain_s"], params["max_jump_bpm"],
                         params["cadence_margin_bpm"])
    if check.accepted and abs(check.value - value) <= 5:
        v = add_zones(conn, value, "field_test", True, test["date"], test_id=test["id"], workout_id=wid,
                      checks=check.as_dict(), note="wrist max HR test that passed the checks")
        return {"applied": True, "confirmed": True, "version": v, "reasons": []}
    reasons = check.reasons or [f"the run held {check.value} bpm, not {value}"]
    return {"applied": False, "confirmed": False, "version": None, "reasons": reasons, "checks": check.as_dict()}


def raise_from_observed(conn: psycopg.Connection, tz: str) -> int:
    """Raise max HR when a recorded session holds a higher HR that passes every check."""
    z = current_zones(conn)
    params = zone_params(conn)
    floor = z.max_hr if z else 0
    candidates = fetch_all(conn, """SELECT w.id, (w.start_ts AT TIME ZONE %s)::date AS date FROM core.v_workout w
                                    WHERE w.max_hr > %s AND EXISTS (SELECT 1 FROM core.hr_sample s WHERE s.workout_id = w.id)
                                      AND NOT EXISTS (SELECT 1 FROM core.athlete_zones a WHERE a.workout_id = w.id)
                                    ORDER BY w.max_hr DESC LIMIT 5""", (tz, floor))
    for c in candidates:
        check = check_max_hr(workout_samples(conn, c["id"]), params["sustain_s"], params["max_jump_bpm"],
                             params["cadence_margin_bpm"])
        if check.accepted and check.value > floor:
            add_zones(conn, check.value, "observed", False, c["date"], workout_id=c["id"], checks=check.as_dict(),
                      note="held this heart rate in a recorded session; passed the max HR checks")
            return 1
    return 0


def hr_max(conn: psycopg.Connection) -> float | None:
    """Max HR for HR-based estimates: the current zones, else the highest recorded session max."""
    z = current_zones(conn)
    if z:
        return float(z.max_hr)
    row = fetch_one(conn, """SELECT max(max_hr) AS m FROM core.v_workout WHERE start_ts > now() - interval '365 days'""")
    return float(row["m"]) if row and row["m"] else None
