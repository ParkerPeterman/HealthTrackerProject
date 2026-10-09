"""Rule logic on hand-built contexts (no database)."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd

from healthtracker.profile import Profile
from healthtracker.recommend.builder import dominated_actions
from healthtracker.rules import catalog
from healthtracker.rules.base import Action, RuleContext, RuleResult
from healthtracker.rules.engine import Evaluated, evaluate

D = date(2026, 10, 8)
PARAMS = {
    "NUTR_PROTEIN_LOW": {"min_g_per_kg": 1.4},
    "TRAIN_POST_GAME": {"hard_rpe_threshold": 7, "recovery_days": 1},
    "RECOV_ILLNESS_FLAG": {"rhr_z_threshold": 1.5, "hrv_z_threshold": -1.5},
    "RECOV_HIGH_LOAD_LOW_HRV": {"load_z_threshold": 1.0, "hrv_z_threshold": -1.0, "easy_max_rpe": 4},
    "SLEEP_SHORT": {"min_minutes": 420, "consecutive_nights": 3},
    "SLEEP_CAFFEINE_CUTOFF": {"cutoff_hour": 14},
    "TRAIN_PRE_GAME_LEGS": {"max_lower_hard_sets": 0},
    "INJ_RETURN_TO_PLAY": {"max_pain_activity": 3, "sessions_required": 3},
    "INJ_SEE_PROFESSIONAL": {"pain_increase": 2},
    "TRAIN_LOAD_RAMP": {"max_weekly_increase_pct": 10},
}


def features(rows: dict[date, dict]) -> pd.DataFrame:
    return pd.DataFrame.from_dict(rows, orient="index")


def ctx(**kw) -> RuleContext:
    base = {"date": D, "features": features({}), "params": PARAMS, "profile": Profile(timezone="America/Chicago")}
    base.update(kw)
    return RuleContext(**base)


def workouts(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["start_ts"] = pd.to_datetime(df["start_ts"], utc=True)
    return df


def test_protein_low_fires_on_training_day_with_complete_log():
    y = D - timedelta(days=1)
    f = features({y: {"n_workouts": 1, "had_game": False, "food_log_complete": True, "protein_g": 90,
                      "protein_g_per_kg": 1.1, "weight_kg_smoothed": 82.0}})
    r = catalog.nutr_protein_low(ctx(features=f), PARAMS["NUTR_PROTEIN_LOW"])
    assert r.fired and r.actions[0].metric == "protein_g"
    assert r.actions[0].target_value == 115  # ceil(1.4 * 82 / 5) * 5
    incomplete = features({y: {"n_workouts": 1, "food_log_complete": False, "protein_g_per_kg": None}})
    assert catalog.nutr_protein_low(ctx(features=incomplete), PARAMS["NUTR_PROTEIN_LOW"]).status == "no_data"


def test_post_game_rule_caps_effort_and_escalates_on_planned_hard_session():
    w = workouts([{"date": D - timedelta(days=1), "start_ts": "2026-10-07T19:00:00Z", "is_game": True,
                   "rpe_eff": 8.0, "sport": "ultimate"}])
    r = catalog.train_post_game(ctx(workouts=w), PARAMS["TRAIN_POST_GAME"])
    assert r.fired and r.severity == 2 and r.actions[0].target_value == 6
    plan = [{"date": D, "session_type": "hard", "sport": "run", "status": "planned", "planned_rpe": 8}]
    r = catalog.train_post_game(ctx(workouts=w, planned=plan), PARAMS["TRAIN_POST_GAME"])
    assert r.severity == 3
    easy = workouts([{"date": D - timedelta(days=1), "start_ts": "2026-10-07T19:00:00Z", "is_game": False,
                      "rpe_eff": 4.0, "sport": "run"}])
    assert not catalog.train_post_game(ctx(workouts=easy), PARAMS["TRAIN_POST_GAME"]).fired


def test_illness_flag_needs_both_signals():
    both = features({D: {"rhr_z_30d": 2.0, "hrv_z_30d": -2.0, "resting_hr": 58, "hrv": 40}})
    one = features({D: {"rhr_z_30d": 2.0, "hrv_z_30d": 0.1, "resting_hr": 58, "hrv": 60}})
    assert catalog.recov_illness_flag(ctx(features=both), PARAMS["RECOV_ILLNESS_FLAG"]).fired
    assert not catalog.recov_illness_flag(ctx(features=one), PARAMS["RECOV_ILLNESS_FLAG"]).fired


def test_sleep_short_requires_consecutive_nights_and_sets_bedtime():
    short = features({D - timedelta(days=i): {"sleep_total_min": 380} for i in range(3)})
    r = catalog.sleep_short(ctx(features=short, typical_wake_hour=6.5), PARAMS["SLEEP_SHORT"])
    assert r.fired and len(r.actions) == 2
    assert r.actions[1].metric == "bedtime_hour" and r.actions[1].target_value == 23.25  # 6.5 - 7h15 + 24
    broken = features({D: {"sleep_total_min": 380}, D - timedelta(days=1): {"sleep_total_min": 450},
                       D - timedelta(days=2): {"sleep_total_min": 380}})
    assert not catalog.sleep_short(ctx(features=broken), PARAMS["SLEEP_SHORT"]).fired


def test_pre_game_legs():
    plan = [{"date": D, "session_type": "strength", "sport": "strength", "status": "planned",
             "focus_muscles": ["quads", "glutes"]},
            {"date": D + timedelta(days=1), "session_type": "game", "sport": "ultimate", "status": "planned"}]
    r = catalog.train_pre_game_legs(ctx(planned=plan), PARAMS["TRAIN_PRE_GAME_LEGS"])
    assert r.fired and r.actions[0].metric == "lower_hard_sets_day"
    upper = [{**plan[0], "focus_muscles": ["chest"]}, plan[1]]
    assert not catalog.train_pre_game_legs(ctx(planned=upper), PARAMS["TRAIN_PRE_GAME_LEGS"]).fired


def injury(checkins, **kw):
    base = {"id": 1, "body_part": "ankle", "side": "left", "status": "active", "seen_professional": False,
            "restrictions": [], "checkins": checkins}
    base.update(kw)
    return base


def checkin(days_ago, pain, swelling=False, fn=3):
    return {"date": D - timedelta(days=days_ago), "pain_rest": max(0, pain - 2), "pain_activity": pain,
            "function_score": fn, "swelling": swelling}


def test_return_to_play_holds_until_three_low_pain_sessions():
    hold = injury([checkin(3, 5), checkin(2, 3), checkin(1, 2)])
    feats = features({D - timedelta(days=1): {"acute_load_7d": 1800.0}})
    r = catalog.inj_return_to_play(ctx(injuries=[hold], features=feats), PARAMS["INJ_RETURN_TO_PLAY"])
    assert r.fired and r.severity == 2 and r.actions[0].target_value == 1800
    ok = injury([checkin(3, 3), checkin(2, 2), checkin(1, 1)])
    r = catalog.inj_return_to_play(ctx(injuries=[ok], features=feats), PARAMS["INJ_RETURN_TO_PLAY"])
    assert r.severity == 1 and not r.actions


def test_see_professional_on_swelling_or_worsening_pain():
    swollen = injury([checkin(1, 2, swelling=True)])
    worse = injury([checkin(6, 2), checkin(3, 3), checkin(1, 5)])
    seen = injury([checkin(1, 2, swelling=True)], seen_professional=True)
    p = PARAMS["INJ_SEE_PROFESSIONAL"]
    assert catalog.inj_see_professional(ctx(injuries=[swollen]), p).severity == 3
    assert "pain up" in catalog.inj_see_professional(ctx(injuries=[worse]), p).details["flagged"][0]["reasons"][0]
    assert not catalog.inj_see_professional(ctx(injuries=[seen]), p).fired


def test_restriction_conflicts_with_plan():
    r = {"id": 9, "target_type": "activity", "activity": "cutting", "max_rpe": 0, "exercise_id": None,
         "muscle_group": None, "movement_pattern": None, "start_date": D - timedelta(days=2), "end_date": None}
    plan = [{"date": D, "session_type": "game", "sport": "ultimate", "status": "planned", "planned_rpe": 8}]
    res = catalog.inj_restriction(ctx(injuries=[injury([], restrictions=[r])], planned=plan), {})
    assert res.fired and res.severity == 3 and res.actions[0].metric == "activity_rpe:cutting"


def test_load_ramp():
    days = {D - timedelta(days=i): {"session_load": 300.0 if i <= 7 else 200.0} for i in range(1, 29)}
    r = catalog.train_load_ramp(ctx(features=features(days)), PARAMS["TRAIN_LOAD_RAMP"])
    assert r.fired and r.details["pct_increase"] == 50.0


def test_engine_keeps_rules_dormant_and_isolates_errors():
    rules = [{"rule_id": "RECOV_ILLNESS_FLAG", "domain": "recovery", "status": "shadow", "description": "",
              "evidence_level": "research", "min_history_days": 21},
             {"rule_id": "TRAIN_POST_GAME", "domain": "training", "status": "active", "description": "",
              "evidence_level": "heuristic", "min_history_days": 0}]
    bad = ctx(history_days=5, workouts=pd.DataFrame({"oops": [1]}))  # missing columns -> rule error
    out = {e.rule_id: e for e in evaluate(bad, rules)}
    assert out["RECOV_ILLNESS_FLAG"].result.status == "dormant"
    assert out["TRAIN_POST_GAME"].result.status == "error"


def test_dominated_actions_hide_the_looser_cap():
    strict = Action("RPE 4 or less", "max_session_rpe", "<=", 4, D)
    loose = Action("nothing above RPE 6", "max_session_rpe", "<=", 6, D)
    other = Action("protein", "protein_g", ">=", 120, D)
    fired = [Evaluated("A", "recovery", "active", "", "", RuleResult(True, 2, actions=[strict])),
             Evaluated("B", "training", "active", "", "", RuleResult(True, 2, actions=[loose, other]))]
    hidden = dominated_actions(fired)
    assert id(loose) in hidden and id(strict) not in hidden and id(other) not in hidden


def test_muscle_recovery_detects_clash():
    hs = pd.DataFrame({"date": [D - timedelta(days=1)] * 3,
                       "start_ts": pd.to_datetime([datetime(2026, 10, 7, 23, tzinfo=UTC)] * 3),
                       "muscle_group": ["quads"] * 3, "set_credit": [1.0] * 3, "body_region": ["lower"] * 3})
    plan = [{"date": D, "session_type": "strength", "sport": "strength", "status": "planned",
             "focus_muscles": ["quads", "chest"]}]
    r = catalog.train_muscle_recovery(ctx(hard_sets=hs, planned=plan), {"min_hours": 48})
    assert r.fired and r.details["clash"] == ["quads"]


def test_micro_gap_ignores_nutrients_without_label_data():
    days = [D - timedelta(days=i) for i in range(1, 8)]
    nd = pd.DataFrame({"date": days * 2, "nutrient": ["fiber_g"] * 7 + ["iron_mg"] * 7,
                       "amount": [20.0] * 7 + [2.0] * 7})
    targets = [{"nutrient": n, "basis": "per_day", "evaluation": "weekly_avg", "min_value": v, "max_value": None}
               for n, v in (("fiber_g", 38), ("iron_mg", 8))]
    p = {"min_logged_days": 4}
    full = catalog.nutr_micro_gap(ctx(nutrients_daily=nd, nutrient_targets=targets), p)
    assert {g["nutrient"] for g in full.details["gaps"]} == {"fiber_g", "iron_mg"}
    sparse = catalog.nutr_micro_gap(ctx(nutrients_daily=nd, nutrient_targets=targets,
                                        nutrient_coverage={"fiber_g": 1.0, "iron_mg": 0.3}), p)
    assert [g["nutrient"] for g in sparse.details["gaps"]] == ["fiber_g"]
    assert sparse.details["low_data_coverage"] == ["iron_mg"]
