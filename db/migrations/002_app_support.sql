-- =====================================================================
-- 002: additions needed by the app, rules engine, and learning loop.
-- Everything here extends v3; nothing in 001 is changed in meaning.
-- =====================================================================

-- Profile: display units and training preferences used by the weekly planner.
ALTER TABLE core.profile
    ADD COLUMN display_units  TEXT NOT NULL DEFAULT 'imperial'
                              CHECK (display_units IN ('imperial', 'metric')),
    ADD COLUMN training_prefs JSONB NOT NULL DEFAULT
        '{"runs_per_week": 3, "lifts_per_week": 2, "min_rest_days": 1,
          "long_run_day": "sun", "generate_plan": true}'::jsonb;

INSERT INTO core.profile (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- Rules may now come from a running self-experiment.
ALTER TABLE analytics.rule DROP CONSTRAINT rule_domain_check;
ALTER TABLE analytics.rule ADD CONSTRAINT rule_domain_check
    CHECK (domain IN ('training','nutrition','sleep','recovery','injury','body','experiment'));

INSERT INTO analytics.rule (rule_id, domain, description, evidence_level, min_history_days, status) VALUES
  ('EXPERIMENT_PROTOCOL', 'experiment', 'Follow today''s assigned self-experiment condition', 'research', 0, 'active');

-- Experiments carry the protocol the daily job turns into an action.
--   {"metric": "last_caffeine_hour", "comparator": "<=", "target_value": 12,
--    "action_text": "No caffeine after 12:00 today", "outcome": "sleep_score", "outcome_lag": 1}
ALTER TABLE analytics.experiment ADD COLUMN protocol JSONB;

-- Output check details for the LLM step (which checks ran, why one failed).
ALTER TABLE analytics.recommendation ADD COLUMN llm_check JSONB;

-- Weekly job can also propose promoting or retiring a rule. Approved in the app.
CREATE TABLE analytics.rule_status_change (
    id          BIGSERIAL PRIMARY KEY,
    rule_id     TEXT NOT NULL REFERENCES analytics.rule(rule_id),
    old_status  TEXT NOT NULL,
    new_status  TEXT NOT NULL CHECK (new_status IN ('shadow','active','retired')),
    reason      TEXT,
    evidence    JSONB,
    proposed_by TEXT NOT NULL DEFAULT 'manual' CHECK (proposed_by IN ('manual','tuning_job')),
    approved    BOOLEAN,                   -- NULL = awaiting my approval
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Extra tunable parameters for starter rules (same bounds discipline as v3).
INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('NUTR_MICRO_GAP',       'min_logged_days',      4,   4,   3,   7),
  ('TRAIN_POST_GAME',      'recovery_days',        1,   1,   1,   3),
  ('TRAIN_PRE_GAME_LEGS',  'max_lower_hard_sets',  0,   0,   0,   6),
  ('INJ_SEE_PROFESSIONAL', 'pain_increase',        2,   2,   1,   4),
  ('TRAIN_LIFT_PLATEAU',   'min_improvement_pct',  1.0, 1.0, 0.5, 3.0),
  ('RECOV_HIGH_LOAD_LOW_HRV', 'easy_max_rpe',      4,   4,   3,   5);

-- Sport-nutrition default that does not depend on sex or age (ISSN 2017).
-- Sex/age-specific micronutrient targets are loaded from the app's settings.
INSERT INTO core.nutrient_target (nutrient, basis, evaluation, min_value, max_value, source_ref) VALUES
  ('protein_g', 'per_kg_bodyweight', 'daily', 1.4, 2.0,
   'ISSN position stand: protein and exercise (Jager et al., 2017)');

-- Hypotheses drawn from known findings. Exposures are "<column> <op> <number>"
-- or a boolean column; outcomes are daily_features columns. lag_days aligns the
-- outcome row to the exposure row (sleep on date D is the night ending on D).
INSERT INTO analytics.hypothesis (exposure, outcome, lag_days, rationale, status) VALUES
  ('last_caffeine_hour >= 14', 'sleep_score',      1, 'Caffeine half-life ~5h; late intake delays and lightens sleep', 'queued'),
  ('last_caffeine_hour >= 14', 'sleep_total_min',  1, 'Late caffeine shortens total sleep', 'queued'),
  ('last_caffeine_hour >= 14', 'hrv',              1, 'Late caffeine raises overnight sympathetic tone', 'queued'),
  ('caffeine_mg >= 300',       'sleep_score',      1, 'High daily caffeine dose and sleep quality', 'queued'),
  ('alcohol_drinks >= 1',      'hrv',              1, 'Alcohol suppresses next-morning HRV', 'queued'),
  ('alcohol_drinks >= 2',      'hrv',              1, 'Dose response: 2+ drinks and HRV', 'queued'),
  ('alcohol_drinks >= 1',      'resting_hr',       1, 'Alcohol raises overnight resting HR', 'queued'),
  ('alcohol_drinks >= 1',      'sleep_score',      1, 'Alcohol fragments sleep', 'queued'),
  ('last_meal_hour >= 21',     'sleep_score',      1, 'Late large meals and sleep quality', 'queued'),
  ('last_meal_hour >= 21',     'resting_hr',       1, 'Late digestion keeps overnight HR elevated', 'queued'),
  ('had_game',                 'hrv',              1, 'Game load suppresses HRV the next morning', 'queued'),
  ('had_game',                 'hrv',              2, 'HRV still suppressed two days after a game', 'queued'),
  ('had_game',                 'hrv',              3, 'HRV recovery by day three after a game', 'queued'),
  ('had_game',                 'resting_hr',       1, 'Resting HR elevated after a game', 'queued'),
  ('had_game',                 'soreness',         1, 'Soreness the day after a game', 'queued'),
  ('had_game',                 'sleep_score',      1, 'Late games and that night''s sleep', 'queued'),
  ('session_load >= 500',      'hrv',              1, 'High single-day load and next-morning HRV', 'queued'),
  ('acwr >= 1.3',              'hrv',              1, 'Load spikes (ACWR >= 1.3) and recovery', 'queued'),
  ('acwr >= 1.3',              'soreness',         1, 'Load spikes and soreness', 'queued'),
  ('sleep_total_min < 390',    'energy',           0, 'Short sleep (<6.5h) and same-day energy', 'queued'),
  ('sleep_total_min < 390',    'mood',             0, 'Short sleep and mood', 'queued'),
  ('sleep_total_min < 390',    'avg_stress',       0, 'Short sleep and daytime Garmin stress', 'queued'),
  ('sleep_total_min < 390',    'kcal',             0, 'Short sleep and calorie intake', 'queued'),
  ('sleep_score < 60',         'energy',           0, 'Poor sleep score and energy', 'queued'),
  ('sleep_midpoint_min >= 240','energy',           0, 'Late sleep timing (midpoint after 4am) and energy', 'queued'),
  ('protein_g_per_kg < 1.4',   'soreness',         1, 'Low protein and next-day soreness', 'queued'),
  ('protein_g_per_kg < 1.4',   'energy',           1, 'Low protein and next-day energy', 'queued'),
  ('kcal < 2000',              'energy',           1, 'Low intake and next-day energy', 'queued'),
  ('carbs_g < 200',            'energy',           1, 'Low carbohydrate and next-day energy', 'queued'),
  ('fiber_g >= 30',            'sleep_score',      1, 'Higher fiber and sleep quality', 'queued'),
  ('hrv_z_30d <= -1',          'energy',           0, 'Low HRV vs baseline and subjective energy', 'queued'),
  ('hrv_z_30d <= -1',          'mood',             0, 'Low HRV vs baseline and mood', 'queued'),
  ('rhr_z_30d >= 1',           'energy',           0, 'Elevated resting HR and energy', 'queued'),
  ('days_since_hard >= 2',     'energy',           0, 'Two+ days of easier training and energy', 'queued'),
  ('run_km_7d >= 25',          'soreness',         0, 'Weekly running volume and soreness', 'queued'),
  ('injury_active',            'mood',             0, 'Active injury and mood', 'queued');
