"""core -> analytics.daily_features: one row per local day.

SQL pulls per-day facts from each core table; pandas does the rolling work
(baselines that skip illness/travel/injury days, z-scores, training load).
The whole history is recomputed each run (a few hundred rows) and only the
requested window is written, so any day can be rebuilt safely.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import psycopg

from healthtracker.config import get_settings
from healthtracker.db import fetch_df, fetch_one, upsert
from healthtracker.features.intensity import rolling_shares, session_zones
from healthtracker.features.sleep import regularity
from healthtracker.features.zones import hr_max
from healthtracker.planning.prehab import prehab_params
from healthtracker.planning.season import phase_on, phases
from healthtracker.profile import Profile, load_profile

FEATURE_COLUMNS = [
    "wear_minutes", "is_partial_day", "food_log_complete", "exclude_from_baseline",
    "sleep_score", "sleep_total_min", "sleep_midpoint_min", "hrv", "resting_hr", "avg_stress",
    "body_battery_low", "n_workouts", "session_load", "had_game", "days_since_game",
    "days_since_hard", "acute_load_7d", "chronic_load_28d", "acwr", "next_game_in_days",
    "run_km_7d", "pace_at_ref_hr_s_km", "vo2max", "weight_kg", "weight_kg_smoothed", "kcal",
    "protein_g", "protein_g_per_kg", "carbs_g", "fat_g", "fiber_g", "last_meal_hour",
    "caffeine_mg", "last_caffeine_hour", "alcohol_drinks", "mood", "energy", "soreness",
    "injury_active", "max_pain_activity", "hrv_7d_avg", "hrv_30d_avg", "hrv_z_30d",
    "rhr_z_30d", "sleep_score_z_30d", "load_z_28d",
    # A4 data quality and context
    "watch_overnight", "sleep_plausible", "is_sick", "is_travel", "tag_alcohol", "tag_stress", "tag_dizzy",
    "tag_dizzy_training", "tag_hamstring", "tag_groin", "tz_offset_min", "tz_change", "dst_week",
    "ln_hrv", "resp_rate", "load_internal",
    # D1 decisions: uncoupled EWMAs and week-over-week change per channel
    "acute_ewma", "chronic_ewma", "load_week", "load_week_prev", "load_week_change_pct", "run_load_week",
    "run_load_week_prev", "plyo_week", "plyo_week_prev", "sprint_week", "sprint_week_prev",
    "zone_min_14d", "easy_pct_14d", "mid_pct_14d", "season_phase",
    # F2 shoulders
    "tag_shoulder", "shoulder_pain",
    # E1 sleep regularity
    "sri_7d", "sri_28d", "sri_pairs_28d", "midpoint_sd_7d", "midpoint_sd_28d", "weekend_gap_min_28d",
    # E2 my own sleep rating
    "sleep_quality",
    # B3 doses
    "bedtime_hour", "training_min", "caffeine_hours_before_bed",
    # E3 sleep drivers
    "train_end_hour", "water_late_ml",
]

# One-tap tags (behavior_event codes) -> daily flag columns.
TAG_FLAGS = {"is_sick": {"sick"}, "is_travel": {"travel"}, "tag_dizzy": {"dizzy", "dizzy_training"},
             "tag_dizzy_training": {"dizzy_training"}, "tag_hamstring": {"hamstring_tight"},
             "tag_groin": {"groin_tweak"}}
# Plausible overnight values; anything outside is a sensor or sync problem, not physiology.
PLAUSIBLE = {"sleep_total_min": (180, 840), "hrv": (10, 250), "sleep_rhr": (30, 110)}

GRAMS_ALCOHOL_PER_DRINK = 14.0  # US standard drink


# ------------------------------------------------------------------ inputs
def _hard_rpe_threshold(conn: psycopg.Connection) -> float:
    row = fetch_one(conn, """SELECT value FROM analytics.rule_parameter
                             WHERE rule_id = 'TRAIN_POST_GAME' AND name = 'hard_rpe_threshold'""")
    return float(row["value"]) if row else 7.0


def estimate_hr_max(conn: psycopg.Connection, profile: Profile | None = None, on: date | None = None) -> float | None:
    """Max HR from the current zones (D3), else the highest recorded session max. Never an age formula."""
    return hr_max(conn)


def impute_rpe(avg_hr: float | None, hr_max: float | None) -> float:
    """sRPE needs RPE; when a session has none, estimate it from %HRmax (CR-10 style)."""
    if avg_hr is None or np.isnan(avg_hr) or not hr_max:
        return 5.0
    pct = avg_hr / hr_max
    for limit, rpe in ((0.60, 2), (0.70, 3), (0.80, 5), (0.87, 7), (0.93, 8)):
        if pct < limit:
            return float(rpe)
    return 9.0


def load_workouts(conn: psycopg.Connection, tz: str) -> pd.DataFrame:
    return fetch_df(
        conn,
        """SELECT id, source, sport, is_game, rpe, duration_s, distance_m, avg_hr, max_hr,
                  session_load, start_ts, (start_ts AT TIME ZONE %s)::date AS date
           FROM core.v_workout""",
        (tz,),
    )


def _daily_food(conn: psycopg.Connection, tz: str) -> pd.DataFrame:
    return fetch_df(
        conn,
        """WITH f AS (
             SELECT n.*,
                    extract(epoch FROM (n.logged_ts AT TIME ZONE %(tz)s)
                                       - n.date::timestamp) / 3600.0 AS local_hour
             FROM core.v_food_nutrients n)
           SELECT f.date,
                  sum(kcal) AS kcal, sum(protein_g) AS protein_g, sum(carbs_g) AS carbs_g,
                  sum(fat_g) AS fat_g, sum(fiber_g) AS fiber_g,
                  sum(coalesce(caffeine_mg, 0)) AS caffeine_mg,
                  sum(coalesce(alcohol_g, 0)) AS alcohol_g,
                  max(local_hour) FILTER (WHERE coalesce(kcal, 0) >= 50) AS last_meal_hour,
                  max(local_hour) FILTER (WHERE coalesce(caffeine_mg, 0) > 0) AS last_caffeine_hour
           FROM f GROUP BY f.date""",
        {"tz": tz},
    )


def pace_at_ref_hr(points: pd.DataFrame, ref_hr: float) -> float | None:
    """Fit speed ~ HR over recent run segments; return predicted pace (s/km) at ref_hr.

    Lower is fitter. Returns None without enough spread or when ref_hr would be
    extrapolated more than 8 bpm beyond the observed range.
    """
    pts = points.dropna(subset=["hr", "speed"])
    pts = pts[(pts.hr >= 100) & (pts.speed > 1.5)]
    if len(pts) < 6:
        return None
    lo, hi = pts.hr.min(), pts.hr.max()
    if hi - lo >= 8 and lo - 8 <= ref_hr <= hi + 8:
        slope, intercept = np.polyfit(pts.hr.astype(float), pts.speed.astype(float), 1)
        if slope > 0:
            speed = intercept + slope * ref_hr
            if speed > 0:
                return round(1000.0 / speed, 1)
    near = pts[(pts.hr - ref_hr).abs() <= 5]
    if len(near) >= 3:
        return round(1000.0 / float(near.speed.median()), 1)
    return None


def _run_points(conn: psycopg.Connection, tz: str) -> pd.DataFrame:
    """(date, hr, speed) points per run: 1-min bins from FIT samples when present, else splits."""
    samples = fetch_df(
        conn,
        """SELECT w.id AS workout_id, (w.start_ts AT TIME ZONE %(tz)s)::date AS date,
                  avg(s.hr) AS hr, avg(s.speed_mps) AS speed
           FROM core.hr_sample s JOIN core.v_workout w ON w.id = s.workout_id
           WHERE w.sport = 'run' AND s.ts >= w.start_ts + interval '5 minutes'
           GROUP BY w.id, date, date_trunc('minute', s.ts)""",
        {"tz": tz},
    )
    splits = fetch_df(
        conn,
        """SELECT w.id AS workout_id, (w.start_ts AT TIME ZONE %(tz)s)::date AS date,
                  r.avg_hr AS hr, r.distance_m / r.duration_s AS speed
           FROM core.run_split r JOIN core.v_workout w ON w.id = r.workout_id
           WHERE w.sport = 'run' AND r.distance_m >= 400 AND r.split_no > 1""",
        {"tz": tz},
    )
    if not samples.empty:
        splits = splits[~splits.workout_id.isin(samples.workout_id.unique())]
    out = pd.concat([samples, splits], ignore_index=True)
    if not out.empty:
        out["date"] = pd.to_datetime(out["date"])
    return out


# ------------------------------------------------------------------ rolling helpers
def _prior_baseline(s: pd.Series, eligible: pd.Series, window: int, min_periods: int,
                    max_age_days: int = 90):
    """Mean/SD of the last `window` eligible observations strictly before each day.

    Counting eligible observations (not calendar days) keeps a baseline available
    right after a long illness or injury, instead of blanking it for a month.
    """
    clean = s.where(eligible).dropna()
    if clean.empty:
        nan = pd.Series(np.nan, index=s.index)
        return nan, nan
    roll = clean.rolling(window, min_periods=min_periods)
    mean = roll.mean().reindex(s.index).ffill(limit=max_age_days).shift(1)
    std = roll.std().reindex(s.index).ffill(limit=max_age_days).shift(1)
    return mean, std


def _z(value: pd.Series, mean: pd.Series, std: pd.Series) -> pd.Series:
    z = (value - mean) / std
    return z.where(std > 1e-6).round(2)


def _days_since(flag: pd.Series) -> pd.Series:
    idx = pd.Series(np.where(flag.fillna(False), np.arange(len(flag)), np.nan), index=flag.index)
    last = idx.ffill()
    return (pd.Series(np.arange(len(flag)), index=flag.index) - last)


# ------------------------------------------------------------------ build
def compute_features(conn: psycopg.Connection, end: date) -> pd.DataFrame:
    s = get_settings()
    profile = load_profile(conn)
    tz = profile.timezone

    first = fetch_one(
        conn,
        """SELECT least(
             (SELECT min(date) FROM core.sleep), (SELECT min(date) FROM core.daily_wellness),
             (SELECT min((start_ts AT TIME ZONE %(tz)s)::date) FROM core.v_workout),
             (SELECT min(date) FROM core.food_entry), (SELECT min(date) FROM core.journal),
             (SELECT min((ts AT TIME ZONE %(tz)s)::date) FROM core.body_measurement)) AS d""",
        {"tz": tz},
    )["d"]
    if first is None or first > end:
        return pd.DataFrame(columns=["date", *FEATURE_COLUMNS])

    idx = pd.date_range(first, end, freq="D", name="date")
    df = pd.DataFrame(index=idx)

    def join(frame: pd.DataFrame, cols: list[str] | None = None) -> None:
        nonlocal df
        if frame.empty:
            for c in cols or []:
                df[c] = np.nan
            return
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.set_index("date")
        if cols:
            frame = frame[cols]
        df = df.join(frame, how="left")

    # sleep (date = wake-up date)
    # Sleep timing is on the local clock where I slept (Garmin's local timestamps), else my home zone.
    sleep = fetch_df(
        conn,
        """SELECT date, sleep_score, total_min AS sleep_total_min, overnight_hrv AS hrv,
                  resting_hr AS sleep_rhr, tz_offset_min, avg_resp_rate AS resp_rate,
                  round(extract(epoch FROM (
                      coalesce((sleep_start AT TIME ZONE 'UTC') + make_interval(mins => tz_offset_min),
                               sleep_start AT TIME ZONE %(tz)s)
                      + ((sleep_end - sleep_start) / 2) - date::timestamp)) / 60) AS sleep_midpoint_min,
                  round((extract(epoch FROM (
                      coalesce((sleep_start AT TIME ZONE 'UTC') + make_interval(mins => tz_offset_min),
                               sleep_start AT TIME ZONE %(tz)s) - (date - 1)::timestamp)) / 3600)::numeric, 2)
                      AS bedtime_hour
           FROM core.sleep""",
        {"tz": tz},
    )
    join(sleep, ["sleep_score", "sleep_total_min", "hrv", "sleep_rhr", "sleep_midpoint_min", "tz_offset_min",
                 "resp_rate", "bedtime_hour"])
    df["ln_hrv"] = np.log(df["hrv"].astype(float).where(df["hrv"] > 0)).round(3)

    well = fetch_df(conn, """SELECT date, wear_minutes, resting_hr AS day_rhr, avg_stress,
                                    body_battery_low, vo2max FROM core.daily_wellness""")
    join(well, ["wear_minutes", "day_rhr", "avg_stress", "body_battery_low", "vo2max"])
    df["resting_hr"] = df["sleep_rhr"].fillna(df["day_rhr"])
    df["vo2max"] = df["vo2max"].ffill(limit=60)

    # workouts and load
    hard_thr = _hard_rpe_threshold(conn)
    hr_max_now = estimate_hr_max(conn)
    w = load_workouts(conn, tz)
    if not w.empty:
        w["rpe_eff"] = [r if pd.notna(r) else impute_rpe(h, hr_max_now) for r, h in zip(w.rpe, w.avg_hr, strict=False)]
        w["load"] = w["session_load"].fillna(w["rpe_eff"] * w["duration_s"] / 60.0)
        w["hard"] = w["is_game"] | (w["rpe_eff"] >= hard_thr)
        w["run_km"] = np.where(w["sport"] == "run", w["distance_m"].fillna(0) / 1000.0, 0.0)
        w["manual_game"] = w["is_game"] & (w["source"] == "manual")
        local_start = pd.to_datetime(w["start_ts"], utc=True).dt.tz_convert(tz)
        w["end_hour"] = (local_start.dt.hour + local_start.dt.minute / 60 + w["duration_s"] / 3600).round(2)
        per_day = w.groupby("date").agg(
            n_workouts=("id", "count"), session_load=("load", "sum"), had_game=("is_game", "any"),
            any_hard=("hard", "any"), run_km=("run_km", "sum"), manual_game=("manual_game", "any"),
            training_min=("duration_s", lambda x: x.sum() / 60.0),
            train_end_hour=("end_hour", "max"),
        ).reset_index()
        join(per_day)
    else:
        for c in ("n_workouts", "session_load", "had_game", "any_hard", "run_km", "manual_game", "training_min",
                  "train_end_hour"):
            df[c] = np.nan
    df["training_min"] = df["training_min"].fillna(0.0).astype(float).round(1)
    df["n_workouts"] = df["n_workouts"].fillna(0).astype(int)
    df["session_load"] = df["session_load"].fillna(0.0).astype(float).round(1)
    for c in ("had_game", "any_hard", "manual_game"):
        df[c] = df[c].astype("boolean").fillna(False).astype(bool)
    df["run_km"] = df["run_km"].fillna(0.0).astype(float)

    df["days_since_game"] = _days_since(df["had_game"])
    df["days_since_hard"] = _days_since(df["any_hard"] | df["had_game"])
    df["acute_load_7d"] = df["session_load"].rolling(7, min_periods=1).sum().round(1)
    days_seen = pd.Series(np.arange(1, len(df) + 1), index=df.index).clip(upper=28)
    chronic = df["session_load"].rolling(28, min_periods=1).sum() / (days_seen / 7.0)
    df["chronic_load_28d"] = chronic.round(1)
    enough = days_seen >= 14
    df["acwr"] = (df["acute_load_7d"] / chronic).where(enough & (chronic > 0)).round(2)
    df["run_km_7d"] = df["run_km"].rolling(7, min_periods=1).sum().round(2)

    # A6: the one internal load scale (TRIMP, or session-RPE calibrated to it), from load_daily.
    internal = fetch_df(conn, "SELECT date, value AS load_internal FROM analytics.load_daily WHERE channel = 'internal'")
    join(internal, ["load_internal"])
    df["load_internal"] = df["load_internal"].astype(float).fillna(0.0).round(1)
    load_decisions(df, conn)
    season = phases(conn)  # F1
    df["season_phase"] = [phase_on(season, d.date()) for d in df.index]
    shares = rolling_shares(session_zones(conn), df.index)  # D4
    for c in shares.columns:
        df[c] = shares[c].astype(float)

    # planned games ahead
    games = fetch_df(conn, """SELECT date FROM core.planned_session
                              WHERE session_type = 'game' AND status <> 'skipped'""")
    game_dates = sorted(pd.to_datetime(games["date"]).tolist()) if not games.empty else []
    nxt = []
    for d in df.index:
        ahead = [g for g in game_dates if g >= d]
        nxt.append((ahead[0] - d).days if ahead else np.nan)
    df["next_game_in_days"] = nxt

    # running performance: rolling 28-day pace at reference HR
    ref_hr = float(profile.ref_hr_bpm) if profile.ref_hr_bpm else (round(0.75 * hr_max_now) if hr_max_now else None)
    pts = _run_points(conn, tz)
    paces = []
    for d in df.index:
        if pts.empty or ref_hr is None:
            paces.append(np.nan)
            continue
        window = pts[(pts.date > d - pd.Timedelta(days=28)) & (pts.date <= d)]
        p = pace_at_ref_hr(window, ref_hr) if len(window) else None
        paces.append(np.nan if p is None else p)
    df["pace_at_ref_hr_s_km"] = paces

    # body weight: first reading of the local day, EWMA-smoothed (alpha 0.1)
    weights = fetch_df(
        conn,
        """SELECT DISTINCT ON ((ts AT TIME ZONE %(tz)s)::date)
                  (ts AT TIME ZONE %(tz)s)::date AS date, weight_kg
           FROM core.body_measurement ORDER BY (ts AT TIME ZONE %(tz)s)::date, ts""",
        {"tz": tz},
    )
    join(weights, ["weight_kg"])
    measured = df["weight_kg"].dropna()
    if not measured.empty:
        smooth = measured.ewm(alpha=0.1, adjust=False).mean()
        df["weight_kg_smoothed"] = smooth.reindex(df.index).ffill(limit=14).round(2)
    else:
        df["weight_kg_smoothed"] = np.nan

    # nutrition: only for days marked complete; missing never means zero
    food = _daily_food(conn, tz)
    join(food, None if not food.empty else
         ["kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "caffeine_mg", "alcohol_g",
          "last_meal_hour", "last_caffeine_hour"])
    status = fetch_df(conn, "SELECT date, complete AS food_log_complete FROM core.food_day_status")
    join(status, ["food_log_complete"])
    df["food_log_complete"] = df["food_log_complete"].astype("boolean").fillna(False).astype(bool)
    nutr = ["kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "caffeine_mg", "alcohol_g",
            "last_meal_hour", "last_caffeine_hour"]
    for c in nutr:
        df[c] = df[c].astype(float).where(df["food_log_complete"])
    for c in ("kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "caffeine_mg"):
        df[c] = df[c].round(1)
    df["last_meal_hour"] = df["last_meal_hour"].round(1)
    df["last_caffeine_hour"] = df["last_caffeine_hour"].round(1)
    # E3: water from 20:00 on; unknown (not zero) on days with no water logged at all.
    water = fetch_df(conn, """SELECT (ts AT TIME ZONE %(tz)s)::date AS date,
                                     sum(ml) FILTER (WHERE extract(hour FROM ts AT TIME ZONE %(tz)s) >= 20) AS late
                              FROM core.water_entry GROUP BY 1""", {"tz": tz})
    df["water_late_ml"] = (pd.Series(water["late"].fillna(0).astype(float).to_numpy(), index=pd.to_datetime(water["date"]))
                           .reindex(df.index) if not water.empty else np.nan)
    # B3: this day's last caffeine to that night's bedtime (the night ending tomorrow).
    df["caffeine_hours_before_bed"] = (df["bedtime_hour"].astype(float).shift(-1)
                                       - df["last_caffeine_hour"].astype(float)).round(2)
    df["alcohol_drinks"] = (df["alcohol_g"] / GRAMS_ALCOHOL_PER_DRINK).round(1)
    wt = df["weight_kg_smoothed"].fillna(df["weight_kg"])
    df["protein_g_per_kg"] = (df["protein_g"] / wt).round(2)

    # The journal's sleep rating is for the night before the morning it's logged (the wake date).
    journal = fetch_df(conn, """SELECT date, mood, energy, soreness, stress AS journal_stress, sleep_quality
                                FROM core.journal""")
    join(journal, ["mood", "energy", "soreness", "journal_stress", "sleep_quality"])

    context_flags(df, conn, tz)
    df = df.join(regularity(conn, tz, df.index, df["sleep_midpoint_min"]))

    # exclusions and injuries
    excl = fetch_df(
        conn,
        """SELECT DISTINCT (e.ts AT TIME ZONE %(tz)s)::date AS date, TRUE AS excl
           FROM core.behavior_event e JOIN core.event_type t ON t.code = e.type_code
           WHERE t.exclude_from_baseline""",
        {"tz": tz},
    )
    join(excl, ["excl"])
    df["exclude_from_baseline"] = df["excl"].astype("boolean").fillna(False).astype(bool)

    inj = fetch_df(conn, """SELECT onset_date, resolved_date, status FROM core.injury""")
    active = pd.Series(False, index=df.index)
    for r in inj.itertuples():
        start = pd.Timestamp(r.onset_date)
        stop = pd.Timestamp(r.resolved_date) if pd.notna(r.resolved_date) else (
            pd.Timestamp.max if r.status != "resolved" else start)
        active |= (df.index >= start) & (df.index < stop)
    df["injury_active"] = active
    pain = fetch_df(conn, "SELECT date, max(pain_activity) AS max_pain_activity FROM core.injury_checkin GROUP BY date")
    join(pain, ["max_pain_activity"])

    # data coverage
    df["is_partial_day"] = df["wear_minutes"].isna() | (df["wear_minutes"] < s.partial_day_wear_min) | df["manual_game"]
    df["avg_stress"] = df["avg_stress"].where(~df["is_partial_day"])
    df["body_battery_low"] = df["body_battery_low"].where(~df["is_partial_day"])

    # baselines vs my own history (exclusion and injury days skipped)
    eligible = ~(df["exclude_from_baseline"] | df["injury_active"])
    hrv_clean = df["hrv"].where(eligible).dropna()
    # The 7-day line is a short display trend: it skips sick/travel days but keeps injury days.
    hrv_trend = df["hrv"].where(~df["exclude_from_baseline"]).dropna()
    df["hrv_7d_avg"] = hrv_trend.rolling(7, min_periods=3).mean().reindex(df.index).ffill(limit=3).round(1)
    df["hrv_30d_avg"] = hrv_clean.rolling(30, min_periods=10).mean().reindex(df.index).ffill(limit=30).round(1)
    m, sd = _prior_baseline(df["hrv"], eligible, 30, 10)
    df["hrv_z_30d"] = _z(df["hrv"], m, sd)
    m, sd = _prior_baseline(df["resting_hr"], eligible, 30, 10)
    df["rhr_z_30d"] = _z(df["resting_hr"], m, sd)
    m, sd = _prior_baseline(df["sleep_score"], eligible, 30, 10)
    df["sleep_score_z_30d"] = _z(df["sleep_score"], m, sd)
    m, sd = _prior_baseline(df["session_load"], eligible, 28, 14)
    df["load_z_28d"] = _z(df["session_load"], m, sd)

    out = df[FEATURE_COLUMNS].reset_index()
    out["date"] = out["date"].dt.date
    return out


def weekly_change(this_week: pd.Series, last_week: pd.Series) -> pd.Series:
    """Week-over-week change in %, undefined when last week had nothing."""
    return ((this_week - last_week) / last_week * 100).where(last_week > 0).round(1)


def load_decisions(df: pd.DataFrame, conn: psycopg.Connection) -> None:
    """D1: acute and chronic load as separate EWMAs, the chronic one ending before the acute week
    (no coupling), and week-over-week totals for internal load, running, sprints, and jumps."""
    load = df["load_internal"]
    df["acute_ewma"] = load.ewm(span=7, adjust=False).mean().round(1)
    # Chronic: 28-day EWMA of everything up to 7 days ago, so the latest week isn't in both terms.
    df["chronic_ewma"] = load.ewm(span=28, adjust=False).mean().shift(7).round(1)
    df.loc[df.index[:21], "chronic_ewma"] = np.nan  # too little history to call it chronic
    channels = fetch_df(conn, """SELECT date, channel, value FROM analytics.load_daily
                                 WHERE channel IN ('internal_run', 'plyo_contacts', 'sprint_m')""")
    wide = pd.DataFrame(index=df.index)
    if not channels.empty:
        channels["date"] = pd.to_datetime(channels["date"])
        wide = channels.pivot_table(index="date", columns="channel", values="value", aggfunc="sum").reindex(df.index)
    for src, name in (("load_internal", "load_week"), ("internal_run", "run_load_week"),
                      ("plyo_contacts", "plyo_week"), ("sprint_m", "sprint_week")):
        daily = (df[src] if src in df else wide[src] if src in wide else pd.Series(0.0, index=df.index))
        week = daily.astype(float).fillna(0.0).rolling(7, min_periods=7).sum()
        df[name] = week.round(1)
        df[f"{name}_prev"] = week.shift(7).round(1)
    df["load_week_change_pct"] = weekly_change(df["load_week"], df["load_week_prev"])


def _home_offsets(index: pd.DatetimeIndex, tz: str) -> pd.Series:
    """Home zone's UTC offset (minutes) at 23:00 the evening before each wake date."""
    zone = ZoneInfo(tz)
    return pd.Series([zone.utcoffset(datetime.combine(d.date() - timedelta(days=1), time(23))).total_seconds() / 60
                      for d in index], index=index)


def dst_weeks(index: pd.DatetimeIndex, tz: str) -> pd.Series:
    """True for every day of a Monday-Sunday week in which the home zone changes its UTC offset."""
    zone = ZoneInfo(tz)
    weeks: dict[date, bool] = {}
    out = []
    for d in index:
        monday = d.date() - timedelta(days=d.weekday())
        if monday not in weeks:
            offsets = {zone.utcoffset(datetime.combine(monday + timedelta(days=k), time(12))) for k in range(7)}
            weeks[monday] = len(offsets) > 1
        out.append(weeks[monday])
    return pd.Series(out, index=index, dtype=bool)


def context_flags(df: pd.DataFrame, conn: psycopg.Connection, tz: str) -> None:
    """A4: tags, data quality, time zones, and DST weeks, added to `df` in place."""
    events = fetch_df(conn, """SELECT DISTINCT (ts AT TIME ZONE %(tz)s)::date AS date, type_code
                               FROM core.behavior_event""", {"tz": tz})
    if not events.empty:
        events["date"] = pd.to_datetime(events["date"])
    for col, codes in TAG_FLAGS.items():
        days = set(events.loc[events["type_code"].isin(codes), "date"]) if not events.empty else set()
        df[col] = df.index.isin(list(days))
    tagged = (set(events.loc[events["type_code"] == "alcohol", "date"]) if not events.empty else set())
    df["tag_alcohol"] = df.index.isin(list(tagged)) | (df["alcohol_drinks"].fillna(0) > 0)
    stress_tag = (set(events.loc[events["type_code"] == "high_stress", "date"]) if not events.empty else set())
    df["tag_stress"] = df.index.isin(list(stress_tag)) | (df["journal_stress"].fillna(0) >= 4)
    # F2: a sore-shoulder tag, or that day's shoulder check-in at or above the pain threshold
    pain = fetch_df(conn, """SELECT (ts AT TIME ZONE %(tz)s)::date AS date, max(value) AS pain FROM core.behavior_event
                             WHERE type_code = 'shoulder_pain' AND value IS NOT NULL GROUP BY 1""", {"tz": tz})
    pain_by_day = pd.Series(pain["pain"].astype(float).to_numpy(), index=pd.to_datetime(pain["date"])) \
        if not pain.empty else pd.Series(dtype=float)
    df["shoulder_pain"] = pain_by_day.reindex(df.index)
    sore = set(events.loc[events["type_code"] == "shoulder_sore", "date"]) if not events.empty else set()
    df["tag_shoulder"] = df.index.isin(list(sore)) | (df["shoulder_pain"].fillna(0) >= prehab_params(conn)["shoulder_pain_threshold"])

    df["watch_overnight"] = df["sleep_total_min"].notna() & df["hrv"].notna()
    ok = pd.Series(True, index=df.index)
    for col, (lo, hi) in PLAUSIBLE.items():
        v = df[col].astype(float)
        ok &= v.isna() | v.between(lo, hi)
    df["sleep_plausible"] = ok.astype("boolean").where(df["sleep_total_min"].notna(), pd.NA)

    home = _home_offsets(df.index, tz)
    away = (df["tz_offset_min"].astype(float) - home).abs() >= 30
    df["tz_change"] = away.fillna(False).astype(bool)
    df["dst_week"] = dst_weeks(df.index, tz)


def build_daily_features(conn: psycopg.Connection, end: date, start: date | None = None) -> int:
    """Recompute features through `end` and upsert days in [start, end]."""
    df = compute_features(conn, end)
    if df.empty:
        return 0
    if start is None:
        start = end - timedelta(days=get_settings().feature_rebuild_days)
    df = df[df["date"] >= start]
    int_cols = ["wear_minutes", "sleep_score", "sleep_total_min", "sleep_midpoint_min",
                "resting_hr", "avg_stress", "body_battery_low", "n_workouts", "days_since_game",
                "days_since_hard", "next_game_in_days", "mood", "energy", "soreness",
                "max_pain_activity", "tz_offset_min", "sri_pairs_28d", "shoulder_pain", "sleep_quality"]
    rows = []
    for rec in df.to_dict("records"):
        row = {}
        for k, v in rec.items():
            if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NA:
                row[k] = None
            elif k in int_cols:
                row[k] = int(round(float(v)))
            elif isinstance(v, np.bool_ | bool):
                row[k] = bool(v)
            elif isinstance(v, np.floating | float):
                row[k] = float(v)
            else:
                row[k] = v
        row["computed_at"] = pd.Timestamp.now(tz="UTC").to_pydatetime()
        rows.append(row)
    return upsert(conn, "analytics.daily_features", rows, conflict=["date"])
