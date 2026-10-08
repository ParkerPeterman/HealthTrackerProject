"""Rule primitives: what a rule sees (RuleContext) and what it returns (RuleResult)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from healthtracker.profile import Profile
from healthtracker.units import Display

COMPARATORS = ("<", "<=", ">", ">=", "=", "between")


@dataclass
class Action:
    """A checkable instruction. The grading job compares `metric` on `evaluate_on`."""

    action_text: str
    metric: str
    comparator: str
    target_value: float | None
    evaluate_on: date
    target_value_high: float | None = None
    outcome_metric: str | None = None  # "<daily_features column>@<lag days>", graded later

    def __post_init__(self) -> None:
        if self.comparator not in COMPARATORS:
            raise ValueError(f"bad comparator {self.comparator}")


@dataclass
class RuleResult:
    fired: bool
    severity: int | None = None  # 1 info, 2 caution, 3 important
    details: dict = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    status: str = "evaluated"  # evaluated | no_data | dormant | error


@dataclass
class RuleContext:
    """Everything a rule may look at for one day. Built from the DB by context.py,
    or by hand in tests."""

    date: date
    features: pd.DataFrame  # index: datetime.date, columns: daily_features
    params: dict[str, dict[str, float]]
    profile: Profile = field(default_factory=Profile)
    planned: list[dict] = field(default_factory=list)  # planned_session rows near date
    workouts: pd.DataFrame = field(default_factory=pd.DataFrame)  # v_workout rows, local `date`, `rpe_eff`
    hard_sets: pd.DataFrame = field(default_factory=pd.DataFrame)  # date, start_ts, muscle_group, set_credit, body_region
    lifted_recently: bool = False
    injuries: list[dict] = field(default_factory=list)  # with "checkins" and "restrictions" lists
    nutrient_targets: list[dict] = field(default_factory=list)
    nutrients_daily: pd.DataFrame = field(default_factory=pd.DataFrame)  # date, nutrient, amount (complete days)
    lift_progress: pd.DataFrame = field(default_factory=pd.DataFrame)  # date, exercise_id, name, best_e1rm_kg
    experiments: list[dict] = field(default_factory=list)
    typical_wake_hour: float | None = None  # median local wake time, last 14 nights
    last_fired: dict[str, date] = field(default_factory=dict)  # rule_id -> last fired, past 14 days
    history_days: int = 0

    def fired_within(self, rule_id: str, days: int) -> bool:
        last = self.last_fired.get(rule_id)
        return last is not None and (self.date - last).days < days

    @property
    def display(self) -> Display:
        return Display(self.profile.display_units)

    def row(self, d: date) -> dict[str, Any]:
        if self.features.empty or d not in self.features.index:
            return {}
        rec = self.features.loc[d].to_dict()
        return {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in rec.items()}

    @property
    def today(self) -> dict[str, Any]:
        return self.row(self.date)

    @property
    def yesterday(self) -> dict[str, Any]:
        return self.row(self.date - timedelta(days=1))

    def window(self, start: date, end: date) -> pd.DataFrame:
        if self.features.empty:
            return self.features
        return self.features[(self.features.index >= start) & (self.features.index <= end)]

    def planned_on(self, d: date) -> list[dict]:
        return [p for p in self.planned if p["date"] == d and p["status"] != "skipped"]


RuleFn = Callable[[RuleContext, dict[str, float]], RuleResult]


@dataclass
class RuleSpec:
    rule_id: str
    fn: RuleFn
    outcome: str | None  # default outcome "<metric>@<lag>"


REGISTRY: dict[str, RuleSpec] = {}


def rule(rule_id: str, outcome: str | None = None) -> Callable[[RuleFn], RuleFn]:
    def deco(fn: RuleFn) -> RuleFn:
        REGISTRY[rule_id] = RuleSpec(rule_id, fn, outcome)
        return fn

    return deco
