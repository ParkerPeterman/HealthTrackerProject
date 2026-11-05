"""Session-level features (A5): one row per workout in analytics.session_features.

Each run is its own observation: its type (easy, long, tempo, intervals, VO2), time in
heart-rate zones, pace at the reference HR, grade-adjusted pace, and, for steady runs
of 40 minutes or more, aerobic decoupling (pace:HR in the first half vs the second,
warm-up skipped) and HR drift. Games and lifts get rows too; their time-in-zone comes
from the session's effort (Seiler's session-goal method) because the watch is off or
the HR is not endurance work.

Everything HR-based uses the current zones (D3); when zones change, every session is
recomputed. A library code from the plan (F2) names the session type outright.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from healthtracker.db import execute, fetch_all, fetch_df, upsert
from healthtracker.features.daily import impute_rpe, pace_at_ref_hr
from healthtracker.features.zones import Zones, current_zones
from healthtracker.ingest.weather import home_point, session_point, weather_at
from healthtracker.profile import load_profile

SPEED_JUMP_SPORTS = {"speed", "sprints", "plyometrics"}
# Session library code prefixes (F2) -> session type.
CODE_TYPES = (("vo2", "vo2"), ("tempo", "tempo"), ("threshold", "tempo"), ("long", "long"), ("easy", "easy"),
              ("strides", "easy"), ("hill", "intervals"), ("repeated_sprint", "intervals"), ("game_like", "intervals"),
              ("cod", "speed_jump"), ("accel", "speed_jump"), ("maxv", "speed_jump"), ("jump", "speed_jump"),
              ("depth", "speed_jump"), ("plyo", "speed_jump"), ("speed", "speed_jump"))
DEFAULTS = {"decoupling_min_minutes": 40, "warmup_skip_minutes": 10, "long_run_minutes": 75,
            "tempo_block_minutes": 15, "vo2_pct_max": 90}
# Minetti et al. 2002: energy cost of running (J/kg/m) as a polynomial in grade.
MINETTI = (155.4, -30.4, -43.3, 46.3, 19.5, 3.6)


def session_params(conn: psycopg.Connection) -> dict[str, float]:
    rows = fetch_all(conn, "SELECT name, value FROM analytics.rule_parameter WHERE rule_id = 'ANALYSIS_SESSIONS'")
    return {**DEFAULTS, **{r["name"]: float(r["value"]) for r in rows}}


def type_from_code(code: str | None) -> str | None:
    if not code:
        return None
    for prefix, t in CODE_TYPES:
        if code.startswith(prefix):
            return t
    return None


def cost_of_running(grade: np.ndarray | float) -> np.ndarray:
    g = np.clip(np.asarray(grade, dtype=float), -0.45, 0.45)
    return np.polyval(MINETTI, g)


def seconds_per_sample(ts: pd.Series) -> np.ndarray:
    """Each sample stands for the time until the next one (gaps capped at 10 s)."""
    t = pd.to_datetime(pd.Series(ts).reset_index(drop=True))
    t = (t - t.iloc[0]).dt.total_seconds().to_numpy()
    dt = np.diff(t, append=t[-1] + (np.median(np.diff(t)) if len(t) > 1 else 1.0))
    return np.clip(dt, 0, 10)


def minute_bins(samples: pd.DataFrame) -> pd.DataFrame:
    """Per-minute means (hr, speed, altitude) with minutes since the start and seconds covered."""
    s = samples.sort_values("ts").copy()
    s["dt"] = seconds_per_sample(s["ts"])
    t0 = pd.to_datetime(s["ts"]).iloc[0]
    s["minute"] = ((pd.to_datetime(s["ts"]) - t0).dt.total_seconds() // 60).astype(int)
    agg = {"hr": ("hr", "mean"), "dt": ("dt", "sum")}
    for col in ("speed_mps", "altitude_m"):
        if col in s and s[col].notna().any():
            agg[col] = (col, "mean")
    out = s.groupby("minute").agg(**agg).reset_index()
    return out


def time_in_zones(samples: pd.DataFrame, zones: Zones) -> dict[str, float]:
    dt = seconds_per_sample(samples["ts"])
    hr = samples["hr"].to_numpy(dtype=float)
    five = np.maximum(zones.five_zone(hr), 1)  # under 50% of max counts as zone 1
    three = zones.three_zone(hr)
    out = {f"z{k}_min": round(float(dt[five == k].sum()) / 60, 1) for k in range(1, 6)}
    for k, name in ((1, "low_min"), (2, "mid_min"), (3, "high_min")):
        out[name] = round(float(dt[three == k].sum()) / 60, 1)
    return out


def tid_from_effort(rpe: float | None, minutes: float) -> dict[str, float]:
    """Seiler's session-goal approach: the whole session goes to one zone by its effort."""
    zone = 1 if rpe is None or rpe <= 4 else 2 if rpe <= 6 else 3
    return {"low_min": minutes if zone == 1 else 0.0, "mid_min": minutes if zone == 2 else 0.0,
            "high_min": minutes if zone == 3 else 0.0}


def _bouts(flags: np.ndarray) -> list[int]:
    """Lengths of consecutive True runs."""
    out, n = [], 0
    for f in flags:
        if f:
            n += 1
        elif n:
            out.append(n)
            n = 0
    if n:
        out.append(n)
    return out


def classify_run(bins: pd.DataFrame | None, zones: Zones | None, minutes: float, avg_hr: float | None,
                 rpe: float | None, p: dict) -> tuple[str, str]:
    if bins is not None and zones is not None and len(bins) >= 5:
        hr = bins["hr"].to_numpy(dtype=float)
        pct = hr / zones.max_hr * 100
        hard = _bouts(pct >= zones.z2_upper_pct)
        very_hard_min = float((pct >= p["vo2_pct_max"]).sum())
        if len([b for b in hard if b >= 1]) >= 3 and very_hard_min >= 6:
            return "vo2", "hr"
        if len([b for b in hard if b >= 1]) >= 3:
            return "intervals", "hr"
        if max(_bouts(pct >= zones.z1_upper_pct), default=0) >= p["tempo_block_minutes"]:
            return "tempo", "hr"
        return ("long" if minutes >= p["long_run_minutes"] else "easy"), "hr"
    if zones is not None and avg_hr and avg_hr / zones.max_hr * 100 >= zones.z2_upper_pct:
        return "tempo", "summary"
    if rpe is not None and rpe >= 7:
        return "tempo", "summary"
    return ("long" if minutes >= p["long_run_minutes"] else "easy"), "summary"


def decoupling(bins: pd.DataFrame, minutes: float, p: dict) -> tuple[float | None, float | None]:
    """Aerobic decoupling (pace:HR, first half vs second half) and HR drift, warm-up skipped."""
    if minutes < p["decoupling_min_minutes"] or "speed_mps" not in bins:
        return None, None
    b = bins[(bins["minute"] >= p["warmup_skip_minutes"]) & (bins["speed_mps"] > 1.5) & (bins["hr"] > 0)]
    if len(b) < 20:
        return None, None
    half = (b["minute"].min() + b["minute"].max()) / 2
    first, second = b[b["minute"] <= half], b[b["minute"] > half]
    ef1 = first["speed_mps"].mean() / first["hr"].mean()
    ef2 = second["speed_mps"].mean() / second["hr"].mean()
    drift = (second["hr"].mean() - first["hr"].mean()) / first["hr"].mean() * 100
    return round(float((ef1 - ef2) / ef1 * 100), 2), round(float(drift), 2)


def grade_adjusted_pace(bins: pd.DataFrame) -> float | None:
    """Pace (s/km) the same effort would give on the flat, from per-minute grade."""
    if "speed_mps" not in bins or "altitude_m" not in bins:
        return None
    b = bins.dropna(subset=["speed_mps", "altitude_m"])
    b = b[b["speed_mps"] > 1.5]
    if len(b) < 5:
        return None
    dist = (b["speed_mps"] * b["dt"]).to_numpy()
    grade = np.diff(b["altitude_m"].to_numpy(), prepend=b["altitude_m"].iloc[0]) / np.maximum(dist, 1.0)
    flat = b["speed_mps"].to_numpy() * cost_of_running(grade) / cost_of_running(0.0)
    mean = float((flat * b["dt"]).sum() / b["dt"].sum())
    return round(1000.0 / mean, 1) if mean > 0 else None


# Banister's TRIMP weighting: men 0.64 e^(1.92x), women 0.86 e^(1.67x), x = fraction of HR reserve.
TRIMP_COEF = {"female": (0.86, 1.67)}
TRIMP_DEFAULT = (0.64, 1.92)
SRPE_ONLY = {"game", "lift", "speed_jump"}  # session-RPE even when HR exists (brief A6)


def banister_trimp(hr: np.ndarray, minutes: np.ndarray, resting_hr: float, max_hr: float, sex: str | None) -> float:
    a, b = TRIMP_COEF.get(sex or "", TRIMP_DEFAULT)
    x = np.clip((np.asarray(hr, dtype=float) - resting_hr) / max(max_hr - resting_hr, 1.0), 0, 1)
    return float(np.sum(np.asarray(minutes, dtype=float) * x * a * np.exp(b * x)))


def resting_hr_by_date(conn: psycopg.Connection) -> pd.Series:
    """30-day rolling median of resting HR, for heart-rate reserve on each date."""
    df = fetch_df(conn, """SELECT coalesce(s.date, w.date) AS date, coalesce(s.resting_hr, w.resting_hr) AS rhr
                           FROM core.sleep s FULL JOIN core.daily_wellness w ON w.date = s.date ORDER BY 1""")
    if df.empty:
        return pd.Series(dtype=float)
    s = df.dropna().set_index(pd.to_datetime(df.dropna()["date"]))["rhr"].astype(float)
    s = s[~s.index.duplicated()].asfreq("D")
    return s.rolling(30, min_periods=3).median().ffill()


def _plyo_from_sets(conn: psycopg.Connection) -> dict[int, int]:
    rows = fetch_all(conn, """SELECT s.workout_id, sum(s.reps) AS n FROM core.lift_set s
                              JOIN core.exercise e ON e.id = s.exercise_id
                              WHERE e.movement_pattern = 'plyometric' AND NOT s.is_warmup GROUP BY s.workout_id""")
    return {r["workout_id"]: int(r["n"]) for r in rows}


def session_rows(conn: psycopg.Connection, workout_ids: list[int]) -> list[dict]:
    if not workout_ids:
        return []
    profile = load_profile(conn)
    zones = current_zones(conn)
    p = session_params(conn)
    ref_hr = float(profile.ref_hr_bpm) if profile.ref_hr_bpm else (0.75 * zones.max_hr if zones else None)
    workouts = fetch_all(conn, """SELECT w.*, (w.start_ts AT TIME ZONE %s)::date AS date FROM core.v_workout w
                                  WHERE w.id = ANY(%s) ORDER BY w.id""", (profile.timezone, workout_ids))
    samples = fetch_df(conn, """SELECT workout_id, ts, hr, speed_mps, cadence_spm, altitude_m FROM core.hr_sample
                                WHERE workout_id = ANY(%s) ORDER BY workout_id, ts""", (workout_ids,))
    splits = fetch_df(conn, """SELECT workout_id, avg_hr AS hr, distance_m / duration_s AS speed FROM core.run_split
                               WHERE workout_id = ANY(%s) AND distance_m >= 400 AND split_no > 1""", (workout_ids,))
    plyo = _plyo_from_sets(conn)
    rest = resting_hr_by_date(conn)
    home = home_point()
    travel = {r["d"] for r in fetch_all(conn, """SELECT DISTINCT (ts AT TIME ZONE %s)::date AS d FROM core.behavior_event
                                                 WHERE type_code = 'travel'""", (profile.timezone,))}
    by_workout = dict(tuple(samples.groupby("workout_id"))) if not samples.empty else {}
    rows = []
    for w in workouts:
        minutes = round(w["duration_s"] / 60, 1)
        s = by_workout.get(w["id"])
        has_samples = s is not None and len(s) >= 10
        rpe = float(w["rpe"]) if w["rpe"] is not None else None
        resting = float(rest.asof(pd.Timestamp(w["date"]))) if not rest.empty and pd.Timestamp(w["date"]) >= rest.index[0] \
            else 60.0
        resting = 60.0 if np.isnan(resting) else resting
        row = {"workout_id": w["id"], "date": w["date"], "sport": w["sport"], "duration_min": minutes,
               "distance_m": w["distance_m"], "avg_hr": w["avg_hr"],
               "hr_source": "samples" if has_samples else "summary" if w["avg_hr"] else "none",
               "plyo_contacts": w["plyo_contacts"] if w["plyo_contacts"] is not None else plyo.get(w["id"]),
               "sprint_m": w["sprint_m"], "zones_version": zones.version if zones else None}
        for k in ("z1_min", "z2_min", "z3_min", "z4_min", "z5_min", "pace_at_ref_hr_s_km", "gap_s_km",
                  "decoupling_pct", "hr_drift_pct"):
            row[k] = None
        coded = type_from_code(w["session_code"])
        bins = minute_bins(s) if has_samples else None
        if w["is_game"]:
            row["session_type"], row["type_source"] = "game", "logged"
        elif coded:
            row["session_type"], row["type_source"] = coded, "logged"
        elif w["sport"] in SPEED_JUMP_SPORTS:
            row["session_type"], row["type_source"] = "speed_jump", "logged"
        elif w["sport"] == "strength":
            row["session_type"], row["type_source"] = "lift", "logged"
        elif w["sport"] == "run":
            row["session_type"], row["type_source"] = classify_run(bins, zones, minutes, w["avg_hr"], rpe, p)
        else:
            row["session_type"], row["type_source"] = "other", "logged"

        endurance = row["session_type"] not in ("game", "lift", "speed_jump")
        if has_samples and zones is not None and endurance:
            row.update(time_in_zones(s, zones))
            row["tid_method"] = "hr"
        elif row["session_type"] != "lift":
            # No usable HR (games are played with the watch off): the whole session by its effort.
            row.update(tid_from_effort(rpe, minutes))
            row["tid_method"] = "session_rpe"
        else:
            row.update({"low_min": None, "mid_min": None, "high_min": None, "tid_method": None})

        # A6: TRIMP for HR-tracked endurance work, session-RPE for everything (calibrated later).
        row["srpe"] = round((rpe if rpe is not None else impute_rpe(w["avg_hr"], zones.max_hr if zones else None))
                            * minutes, 1)
        row["trimp"] = None
        if zones is not None and row["session_type"] not in SRPE_ONLY:
            if has_samples:
                row["trimp"] = round(banister_trimp(s["hr"].to_numpy(), seconds_per_sample(s["ts"]) / 60, resting,
                                                    zones.max_hr, profile.sex), 1)
            elif w["avg_hr"]:
                row["trimp"] = round(banister_trimp(np.array([w["avg_hr"]]), np.array([minutes]), resting,
                                                    zones.max_hr, profile.sex), 1)

        if w["sport"] == "run":
            if bins is not None and ref_hr:
                pts = bins[bins["minute"] >= p["warmup_skip_minutes"]].rename(columns={"speed_mps": "speed"})
                if "speed" in pts:
                    row["pace_at_ref_hr_s_km"] = pace_at_ref_hr(pts[["hr", "speed"]], ref_hr)
            elif ref_hr and not splits.empty:
                row["pace_at_ref_hr_s_km"] = pace_at_ref_hr(splits[splits["workout_id"] == w["id"]], ref_hr)
            if bins is not None:
                row["gap_s_km"] = grade_adjusted_pace(bins)
                if row["session_type"] in ("easy", "long", "tempo"):
                    row["decoupling_pct"], row["hr_drift_pct"] = decoupling(bins, minutes, p)

        # A7: weather at the session's midpoint, at home or (travel days) the rounded start point.
        point = session_point({**w, "travel": w["date"] in travel}, home)
        wx = weather_at(conn, point, w["start_ts"] + timedelta(seconds=w["duration_s"] / 2)) if point else None
        for k in ("temp_c", "dew_point_c", "wind_kmh", "precip_mm"):
            row[k] = wx[k] if wx else None
        row["weather_source"] = wx["source"] if wx else None
        rows.append(row)
    return rows


def build_session_features(conn: psycopg.Connection, since: date | None = None) -> int:
    """Rebuild sessions on or after `since`, any session not yet built, and everything after a zones change."""
    profile = load_profile(conn)
    zones = current_zones(conn)
    execute(conn, """DELETE FROM analytics.session_features f USING core.workout w
                     WHERE w.id = f.workout_id AND w.superseded_by IS NOT NULL""")
    ids = [r["id"] for r in fetch_all(
        conn,
        """SELECT w.id FROM core.v_workout w LEFT JOIN analytics.session_features f ON f.workout_id = w.id
           WHERE f.workout_id IS NULL OR f.zones_version IS DISTINCT FROM %s
              OR f.weather_source = 'forecast'   -- until the archive replaces the stand-in
              OR (%s::date IS NOT NULL AND (w.start_ts AT TIME ZONE %s)::date >= %s)""",
        (zones.version if zones else None, since, profile.timezone, since))]
    rows = session_rows(conn, ids)
    return upsert(conn, "analytics.session_features", rows, conflict=["workout_id"]) if rows else 0


def recent_sessions(conn: psycopg.Connection, end: date, days: int = 28) -> pd.DataFrame:
    return fetch_df(conn, "SELECT * FROM analytics.session_features WHERE date BETWEEN %s AND %s ORDER BY date",
                    (end - timedelta(days=days), end))
