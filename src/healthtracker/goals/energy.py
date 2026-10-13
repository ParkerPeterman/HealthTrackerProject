"""Energy balance from my own logs: what I actually burn, and what to eat for a weight goal.

Expenditure = average intake on complete food-log days minus the energy stored or
lost, read off the slope of the smoothed weight trend at ~7,700 kcal per kg (the
classic ~3,500 kcal/lb rule). Real tissue changes mix fat, lean mass, and water,
so this is an estimate; the weekly job re-checks it as data accumulates. Until
there are two weeks of complete logs, a Mifflin-St Jeor estimate stands in.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from healthtracker.profile import Profile

KCAL_PER_KG = 7700.0
WINDOW_DAYS = 28
MIN_LOGGED_DAYS = 14
MIN_WEIGHT_DAYS = 10
MAX_LOSS_PCT_PER_WEEK = 1.0   # of body weight; faster loss costs lean mass and training quality
MAX_GAIN_PCT_PER_WEEK = 0.5   # faster gain is mostly fat
MAX_DEFICIT_KCAL = 1000
CALORIE_FLOOR = {"male": 1500, "female": 1200}
BAND_KCAL = 100               # target = mid ± band
# Protein in g/kg: the high end of the ISSN range during a deficit helps keep lean mass;
# 1.6 g/kg covers gaining and maintaining (Jäger et al. 2017, ISSN position stand).
PROTEIN_LOSING = 2.0
PROTEIN_OTHERWISE = 1.6


@dataclass
class Expenditure:
    kcal: float                    # estimated daily expenditure
    source: str                    # "data" (my logs + weight trend) or "formula"
    intake_kcal: float | None = None
    trend_kg_per_week: float | None = None
    logged_days: int = 0
    weight_days: int = 0
    window_days: int = WINDOW_DAYS
    activity_factor: float | None = None

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _window(features: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Rows with start <= date < end, whatever the index type (date or Timestamp)."""
    if features.empty:
        return features
    idx = pd.to_datetime(pd.Index(features.index))
    keep = (idx >= pd.Timestamp(start)) & (idx < pd.Timestamp(end))
    out = features[keep].copy()
    out.index = idx[keep]
    return out.sort_index()


def trend_slope_kg_per_week(features: pd.DataFrame, as_of: date, days: int = 21, min_points: int = 7) -> float | None:
    """Least-squares slope of the smoothed weight over the last `days` days."""
    w = _window(features, as_of - timedelta(days=days), as_of + timedelta(days=1))
    if w.empty or "weight_kg_smoothed" not in w:
        return None
    s = w["weight_kg_smoothed"].astype(float).dropna()
    if len(s) < min_points:
        return None
    x = np.asarray((s.index - s.index[0]).days, dtype=float)
    if x[-1] - x[0] < 7:
        return None
    return float(np.polyfit(x, s.to_numpy(dtype=float), 1)[0] * 7)


def from_data(features: pd.DataFrame, as_of: date, window: int = WINDOW_DAYS) -> Expenditure | None:
    """Expenditure from the last `window` days of logs, or None if there isn't enough yet."""
    f = _window(features, as_of - timedelta(days=window), as_of)
    if f.empty:
        return None
    complete = f["food_log_complete"].fillna(False).astype(bool)
    intake = f.loc[complete, "kcal"].astype(float).dropna()
    weight = f["weight_kg_smoothed"].astype(float).dropna()
    if len(intake) < MIN_LOGGED_DAYS or len(weight) < MIN_WEIGHT_DAYS:
        return None
    x = np.asarray((weight.index - weight.index[0]).days, dtype=float)
    if x[-1] - x[0] < 14:
        return None
    slope_per_day = float(np.polyfit(x, weight.to_numpy(dtype=float), 1)[0])
    mean_intake = float(intake.mean())
    return Expenditure(
        kcal=mean_intake - slope_per_day * KCAL_PER_KG, source="data", intake_kcal=mean_intake,
        trend_kg_per_week=slope_per_day * 7, logged_days=len(intake), weight_days=len(weight), window_days=window,
    )


def activity_factor(weekly_training_min: float) -> float:
    for limit, factor in ((150, 1.4), (300, 1.55), (450, 1.7)):
        if weekly_training_min < limit:
            return factor
    return 1.8


def from_formula(profile: Profile, weight_kg: float | None, on: date, weekly_training_min: float) -> Expenditure | None:
    """Mifflin-St Jeor resting rate x an activity factor from recent training time."""
    age = profile.age_on(on)
    if not weight_kg or not profile.height_cm or age is None:
        return None
    sex_term = {"male": 5.0, "female": -161.0}.get(profile.sex or "", -78.0)
    bmr = 10 * weight_kg + 6.25 * profile.height_cm - 5 * age + sex_term
    factor = activity_factor(weekly_training_min)
    return Expenditure(kcal=bmr * factor, source="formula", activity_factor=factor, window_days=28)


@dataclass
class CaloriePlan:
    kcal_min: int
    kcal_max: int
    kcal_mid: int
    requested_rate: float          # kg/week asked for
    rate_kg_per_week: float        # after the safety caps
    expected_rate: float           # what the rounded target should produce
    capped: bool
    floor_applied: bool
    protein_g_per_kg: float
    max_loss_kg_per_week: float
    max_gain_kg_per_week: float

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def plan_for(exp: Expenditure, rate_kg_per_week: float, weight_kg: float, sex: str | None) -> CaloriePlan:
    max_loss = MAX_LOSS_PCT_PER_WEEK / 100 * weight_kg
    max_gain = MAX_GAIN_PCT_PER_WEEK / 100 * weight_kg
    rate = min(max(rate_kg_per_week, -max_loss), max_gain)
    if rate * KCAL_PER_KG / 7 < -MAX_DEFICIT_KCAL:
        rate = -MAX_DEFICIT_KCAL * 7 / KCAL_PER_KG
    capped = abs(rate - rate_kg_per_week) > 1e-9
    target = exp.kcal + rate * KCAL_PER_KG / 7
    floor = CALORIE_FLOOR.get(sex or "", 1200)
    floor_applied = target < floor
    mid = int(round(max(target, floor) / 50.0) * 50)
    return CaloriePlan(
        kcal_min=mid - BAND_KCAL, kcal_max=mid + BAND_KCAL, kcal_mid=mid, requested_rate=rate_kg_per_week,
        rate_kg_per_week=rate, expected_rate=(mid - exp.kcal) * 7 / KCAL_PER_KG, capped=capped,
        floor_applied=floor_applied, protein_g_per_kg=PROTEIN_LOSING if rate < 0 else PROTEIN_OTHERWISE,
        max_loss_kg_per_week=max_loss, max_gain_kg_per_week=max_gain,
    )


def projected_date(as_of: date, current: float, target: float, rate_kg_per_week: float) -> date | None:
    """When the trend reaches `target` at `rate`, if it is heading that way (within three years)."""
    gap = target - current
    if abs(gap) < 1e-6:
        return as_of
    if rate_kg_per_week == 0 or (gap > 0) != (rate_kg_per_week > 0) or abs(rate_kg_per_week) < 0.02:
        return None
    days = gap / rate_kg_per_week * 7
    return as_of + timedelta(days=round(days)) if days <= 3 * 365 else None
