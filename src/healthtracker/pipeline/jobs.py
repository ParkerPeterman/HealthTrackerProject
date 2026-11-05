"""Nightly, weekly, and backfill jobs.

Nightly (runs in the morning, after the watch syncs last night's sleep):
  ingest -> core -> zones -> session features -> load -> daily_features -> baselines
  -> grade earlier actions -> rules/flags -> recommendation + actions -> LLM message
Weekly: hypotheses -> insights -> parameter tuning + rule promotion proposals
  -> experiments -> goal check (calorie/protein target proposals) -> next week's plan
Every load is an upsert on a natural key, so any step can be re-run.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from healthtracker.config import get_settings
from healthtracker.db import connect
from healthtracker.features.baselines import build_metric_status
from healthtracker.features.daily import build_daily_features
from healthtracker.features.intensity import build_intensity_weekly
from healthtracker.features.lifting import build_lift_progress, build_weekly_muscle_volume
from healthtracker.features.load import build_load
from healthtracker.features.sessions import build_session_features
from healthtracker.features.zones import raise_from_observed
from healthtracker.goals.targets import run_goal_check
from healthtracker.ingest.garmin import pull
from healthtracker.llm.phrase import phrase
from healthtracker.pipeline.runner import JobReport, run_step
from healthtracker.planning.season import ensure_season
from healthtracker.profile import load_profile
from healthtracker.recommend.builder import write_recommendation
from healthtracker.recommend.grading import grade_actions
from healthtracker.recommend.metrics import MetricResolver
from healthtracker.rules.engine import run_rules
from healthtracker.stats.bayes import run_effects
from healthtracker.stats.experiments import update_experiments
from healthtracker.stats.insights import run_insights
from healthtracker.stats.promotion import run_promotion
from healthtracker.stats.tuning import run_tuning
from healthtracker.transform.garmin import load_all

log = logging.getLogger("healthtracker.jobs")


def local_today() -> date:
    with connect() as conn:
        tz = load_profile(conn).tz
    return datetime.now(tz).date()


def _features_step(d: date, start: date | None = None) -> int:
    with connect() as conn:
        ensure_season(conn, local_today())  # F1: defaults until I set my first game
        tz = load_profile(conn).timezone
        n = build_daily_features(conn, d, start)
        since = start or d - timedelta(days=21)
        n += build_weekly_muscle_volume(conn, tz, since)
        n += build_lift_progress(conn, tz, since)
        return n


def _baselines_step(d: date, start: date | None = None) -> int:
    with connect() as conn:
        return build_metric_status(conn, d, start)


def _zones_step() -> int:
    """Raise max HR if a recorded session held a higher, checked value (D3)."""
    with connect() as conn:
        return raise_from_observed(conn, load_profile(conn).timezone)


def _sessions_step(since: date | None) -> int:
    """Session features (A5): the window, anything new, and everything after a zones change."""
    with connect() as conn:
        return build_session_features(conn, since)


def _readiness_step(d: date) -> int:
    """G1 v1 (shadow): after the medication messages, since a flagged day lowers the score."""
    from healthtracker.features.readiness import build_readiness

    with connect() as conn:
        return build_readiness(conn, d, days=14)


def _fitness_step(d: date) -> int:
    from healthtracker.features.fitness import build_fitness

    with connect() as conn:
        return build_fitness(conn, d)


def _load_step(d: date) -> int:
    """One internal load scale and the daily channels (A6)."""
    with connect() as conn:
        return build_load(conn, d) + build_intensity_weekly(conn)


def _weather_step(d: date) -> int:
    """A7: Open-Meteo forecast and archive gaps (rounded points and dates only)."""
    from healthtracker.ingest.weather import pull_weather

    with connect() as conn:
        return pull_weather(conn, d)


def _medication_step(d: date) -> int:
    from healthtracker.medication.features import build_medication_daily

    with connect() as conn:
        return build_medication_daily(conn, d)


def _medication_messages(d: date) -> int:
    """After the recommendation and apart from it: the LLM never sees medication data."""
    from healthtracker.medication.messages import build_messages

    with connect() as conn:
        return build_messages(conn, d)


def _rules_and_recommendation(d: date) -> tuple[int, int]:
    with connect() as conn:
        ctx, results = run_rules(conn, d)
        flags = len(results)
        n = write_recommendation(conn, ctx, results)
    return flags, n


def nightly(d: date | None = None, skip_ingest: bool = False, use_llm: bool | None = None) -> JobReport:
    s = get_settings()
    d = d or local_today()
    report = JobReport("nightly")
    lookback = d - timedelta(days=s.garmin_lookback_days)

    if not skip_ingest:
        run_step("nightly", "ingest_garmin", lambda: pull(lookback, d), report)
        run_step("nightly", "weather", lambda: _weather_step(d), report)

    def transform() -> int:
        with connect() as conn:
            return load_all(conn, lookback - timedelta(days=1), d)

    run_step("nightly", "core", transform, report)
    run_step("nightly", "zones", _zones_step, report)
    run_step("nightly", "sessions", lambda: _sessions_step(lookback - timedelta(days=1)), report)
    run_step("nightly", "load", lambda: _load_step(d), report)
    run_step("nightly", "daily_features", lambda: _features_step(d), report)
    run_step("nightly", "baselines", lambda: _baselines_step(d), report)
    run_step("nightly", "fitness", lambda: _fitness_step(d), report)
    run_step("nightly", "medication_features", lambda: _medication_step(d), report)

    def grade() -> int:
        from healthtracker.planning.weekly import link_planned

        with connect() as conn:
            return grade_actions(conn, d) + link_planned(conn, d - timedelta(days=7), d)

    run_step("nightly", "grade", grade, report)

    state: dict = {}

    def rules() -> int:
        state["flags"], state["rec"] = _rules_and_recommendation(d)
        return state["flags"]

    run_step("nightly", "rules", rules, report)
    run_step("nightly", "recommendation", lambda: state.get("rec", 0), report)
    run_step("nightly", "medication_messages", lambda: _medication_messages(d), report)
    run_step("nightly", "readiness", lambda: _readiness_step(d), report)

    def llm() -> int:
        with connect() as conn:
            return 1 if phrase(conn, d, use_llm=use_llm) else 0

    run_step("nightly", "llm_message", llm, report)
    return report


def weekly(d: date | None = None) -> JobReport:
    from healthtracker.planning.weekly import grade_week_plan, write_week_plan

    d = d or local_today()
    report = JobReport("weekly")

    def step(fn):
        def run() -> int:
            with connect() as conn:
                return fn(conn, d)
        return run

    run_step("weekly", "effects", step(run_effects), report)  # B1 (shadow), before the OLS cross-check
    run_step("weekly", "insights", step(run_insights), report)
    run_step("weekly", "parameter_tuning", step(run_tuning), report)
    run_step("weekly", "rule_promotion", step(run_promotion), report)
    run_step("weekly", "experiments", step(update_experiments), report)
    run_step("weekly", "goal_check", step(run_goal_check), report)
    next_monday = d + timedelta(days=(7 - d.weekday()) % 7 or 7)
    run_step("weekly", "plan_grade", step(lambda conn, _d: grade_week_plan(conn, next_monday - timedelta(days=7))),
             report)
    run_step("weekly", "week_plan", step(lambda conn, _d: write_week_plan(conn, next_monday)), report)
    return report


def backfill(start: date, end: date, llm_days: int = 0, weekly_every: int = 7) -> JobReport:
    """Rebuild everything for [start, end] from what is already in raw/core.

    No Garmin fetch (use `healthtracker garmin-backfill` for that). The LLM is
    used only for the last `llm_days` days; earlier days get the template.
    Weekly jobs run on each Sunday in range so insights and proposals evolve
    the way they would have live.
    """
    report = JobReport("backfill")

    def transform() -> int:
        with connect() as conn:
            return load_all(conn, None, end)

    run_step("backfill", "core", transform, report)

    def weather() -> int:
        from healthtracker.ingest.weather import load_weather

        with connect() as conn:
            return load_weather(conn)

    run_step("backfill", "weather", weather, report)
    run_step("backfill", "zones", _zones_step, report)
    run_step("backfill", "sessions", lambda: _sessions_step(start - timedelta(days=60)), report)
    run_step("backfill", "load", lambda: _load_step(end), report)

    def features() -> int:
        with connect() as conn:
            tz = load_profile(conn).timezone
            n = build_daily_features(conn, end, start=start - timedelta(days=60))
            n += build_weekly_muscle_volume(conn, tz, None)
            n += build_lift_progress(conn, tz, None)
            return n

    run_step("backfill", "daily_features", features, report)
    run_step("backfill", "baselines", lambda: _baselines_step(end, start - timedelta(days=60)), report)
    run_step("backfill", "medication_features", lambda: _medication_step(end), report)
    run_step("backfill", "fitness", lambda: _fitness_step(end), report)

    def days() -> int:
        from healthtracker.planning.weekly import grade_week_plan, write_week_plan

        total, d = 0, start
        llm_from = end - timedelta(days=llm_days - 1) if llm_days > 0 else end + timedelta(days=1)
        while d <= end:
            with connect() as conn:
                total += grade_actions(conn, d, resolver=MetricResolver(conn))
            _, n = _rules_and_recommendation(d)
            total += n + _medication_messages(d)
            with connect() as conn:
                phrase(conn, d, use_llm=d >= llm_from)
            if weekly_every and d.weekday() == 6 and (d - start).days >= 27:
                with connect() as conn:
                    run_effects(conn, d)
                    run_insights(conn, d)
                    run_tuning(conn, d)
                    run_promotion(conn, d)
                    update_experiments(conn, d)
                    run_goal_check(conn, d)
                    grade_week_plan(conn, d - timedelta(days=6))
                    write_week_plan(conn, d + timedelta(days=1))
            d += timedelta(days=1)
        return total

    run_step("backfill", "daily_loop", days, report)

    def readiness() -> int:
        from healthtracker.features.readiness import build_readiness

        with connect() as conn:
            return build_readiness(conn, end, days=(end - start).days + 1)

    run_step("backfill", "readiness", readiness, report)
    return report
