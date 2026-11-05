-- E2 / N5: measurement quality is explicit. Each feature has a tier; a poor-tier feature
-- cannot drive an active rule on its own, and no insight may rest on a poor-tier measure
-- (sleep stage minutes, Body Battery, stress score) alone. For sleep the order of trust is:
-- duration and timing, then my own sleep-quality rating, then stages.
-- Starting tiers from the brief; I can revise them. Re-running is safe.

CREATE TABLE IF NOT EXISTS knowledge.feature_quality (
    feature  TEXT PRIMARY KEY,
    tier     TEXT NOT NULL CHECK (tier IN ('good', 'fair', 'poor')),
    note     TEXT
);

INSERT INTO knowledge.feature_quality (feature, tier, note) VALUES
  -- good
  ('resting_hr', 'good', NULL), ('rhr_z_30d', 'good', NULL), ('hrv', 'good', 'nightly average'),
  ('ln_hrv', 'good', NULL), ('hrv_z_30d', 'good', NULL), ('hrv_7d_avg', 'good', NULL),
  ('sleep_total_min', 'good', 'wrist trackers detect sleep well but miss wake, so duration runs a little long'),
  ('pace_at_ref_hr_s_km', 'good', NULL), ('run_km_7d', 'good', NULL), ('weight_kg', 'good', 'logged'),
  ('weight_kg_smoothed', 'good', NULL), ('session_load', 'good', NULL), ('load_internal', 'good', NULL),
  ('acute_load_7d', 'good', NULL), ('load_z_28d', 'good', NULL), ('load_week_change_pct', 'good', NULL),
  ('run_load_week', 'good', NULL), ('sprint_week', 'good', 'logged'), ('plyo_week', 'good', 'logged'),
  ('easy_pct_14d', 'good', NULL), ('e1rm', 'good', 'logged lifts'), ('hard_sets', 'good', 'logged lifts'),
  ('performance_test', 'good', 'logged tests'), ('max_pain_activity', 'good', 'logged check-ins'),
  -- fair
  ('sleep_midpoint_min', 'fair', 'sleep timing'), ('sri_7d', 'fair', 'sleep timing'), ('sri_28d', 'fair', 'sleep timing'),
  ('midpoint_sd_7d', 'fair', 'sleep timing'), ('midpoint_sd_28d', 'fair', 'sleep timing'),
  ('weekend_gap_min_28d', 'fair', 'sleep timing'), ('bedtime_hour', 'fair', 'sleep timing'),
  ('sleep_quality', 'fair', 'my own rating: second in the order of trust for sleep'),
  ('sleep_score', 'fair', 'Garmin composite; partly built on stage estimates'),
  ('vo2max', 'fair', 'Garmin estimate; depends on the max HR in the Garmin profile'),
  ('had_game', 'fair', 'game minutes x effort'), ('days_since_game', 'fair', NULL),
  ('kcal', 'fair', 'food log, complete days only'), ('protein_g', 'fair', 'food log, complete days only'),
  ('protein_g_per_kg', 'fair', 'food log, complete days only'), ('carbs_g', 'fair', 'food log, complete days only'),
  ('fat_g', 'fair', 'food log, complete days only'), ('fiber_g', 'fair', 'food log, complete days only'),
  ('caffeine_mg', 'fair', 'food log, complete days only'), ('last_caffeine_hour', 'fair', 'food log, complete days only'),
  ('alcohol_drinks', 'fair', 'food log, complete days only'), ('last_meal_hour', 'fair', 'food log, complete days only'),
  ('micronutrients', 'fair', 'food log, complete days only'), ('mood', 'fair', 'journal'), ('energy', 'fair', 'journal'),
  ('soreness', 'fair', 'journal'), ('tag_groin', 'fair', 'one-tap tag'), ('tag_shoulder', 'fair', 'tag or check-in'),
  ('shoulder_pain', 'fair', 'check-in'),
  -- poor
  ('deep_min', 'poor', 'sleep stage minutes: wrist staging overestimates light and underestimates deep sleep'),
  ('rem_min', 'poor', 'sleep stage minutes'), ('light_min', 'poor', 'sleep stage minutes'),
  ('awake_min', 'poor', 'wrist trackers rarely detect wake'),
  ('body_battery_low', 'poor', 'Garmin Body Battery'), ('avg_stress', 'poor', 'Garmin stress score'),
  ('food_incomplete_day', 'poor', 'food logs on days not marked complete (never used)')
ON CONFLICT (feature) DO NOTHING;

-- What each rule decides from, so activation can check tiers.
ALTER TABLE analytics.rule ADD COLUMN IF NOT EXISTS inputs TEXT[];
UPDATE analytics.rule SET inputs = v.inputs FROM (VALUES
  ('NUTR_PROTEIN_LOW', '{protein_g_per_kg}'::text[]), ('NUTR_CALORIE_DRIFT', '{kcal}'), ('NUTR_MICRO_GAP', '{micronutrients}'),
  ('TRAIN_POST_GAME', '{had_game,session_load}'), ('RECOV_HIGH_LOAD_LOW_HRV', '{load_z_28d,hrv_z_30d}'),
  ('RECOV_ILLNESS_FLAG', '{rhr_z_30d,hrv_z_30d}'), ('TRAIN_LOAD_RAMP', '{acute_load_7d}'),
  ('TRAIN_RUN_RAMP', '{run_load_week}'), ('TRAIN_CHANNEL_RAMP', '{sprint_week,plyo_week}'),
  ('TRAIN_INTENSITY_MIX', '{easy_pct_14d}'), ('TRAIN_MUSCLE_VOLUME', '{hard_sets}'), ('TRAIN_MUSCLE_RECOVERY', '{hard_sets}'),
  ('TRAIN_PRE_GAME_LEGS', '{hard_sets}'), ('TRAIN_LIFT_PLATEAU', '{e1rm}'), ('SLEEP_SHORT', '{sleep_total_min}'),
  ('SLEEP_CAFFEINE_CUTOFF', '{last_caffeine_hour,sleep_score}'), ('INJ_RESTRICTION', '{max_pain_activity}'),
  ('INJ_RETURN_TO_PLAY', '{max_pain_activity}'), ('INJ_SEE_PROFESSIONAL', '{max_pain_activity}'),
  ('INJ_PHYSIO_PROMPT', '{tag_groin,tag_shoulder}'), ('INJ_SHOULDER_SWAP', '{tag_shoulder,shoulder_pain}')
) AS v(rule_id, inputs) WHERE analytics.rule.rule_id = v.rule_id AND analytics.rule.inputs IS NULL;

-- E2: the journal's sleep-quality rating replaces motivation in the app (motivation history is kept).
ALTER TABLE core.journal ADD COLUMN IF NOT EXISTS sleep_quality SMALLINT CHECK (sleep_quality BETWEEN 1 AND 5);
ALTER TABLE analytics.daily_features ADD COLUMN IF NOT EXISTS sleep_quality SMALLINT;

INSERT INTO analytics.hypothesis (exposure, outcome, lag_days, rationale, status) VALUES
  ('sleep_total_min < 420', 'sleep_quality', 0, 'Do short nights on the watch match how I rate the night?', 'queued'),
  ('last_caffeine_hour >= 14', 'sleep_quality', 1, 'Late caffeine and my own sleep rating', 'queued')
ON CONFLICT (exposure, outcome, lag_days) DO NOTHING;
