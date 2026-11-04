-- D4: intensity distribution. Weekly time in a three-zone model across every session,
-- games included (by effort when there is no heart rate); lifting is left out because
-- it isn't endurance intensity. Aim: pyramidal, starting at about 75-80% easy time.
-- A 2024 network meta-analysis (Rosenblat et al.) found no VO2 max or time-trial difference
-- between polarized and pyramidal training, with recreational athletes possibly better
-- served by pyramidal. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.intensity_weekly (
    week_start  DATE PRIMARY KEY,         -- Monday
    low_min     NUMERIC(7,1) NOT NULL,
    mid_min     NUMERIC(7,1) NOT NULL,
    high_min    NUMERIC(7,1) NOT NULL,
    easy_pct    NUMERIC(5,1),
    pattern     TEXT CHECK (pattern IN ('pyramidal', 'polarized', 'threshold', 'moderate_heavy', 'too_little')),
    hr_share    NUMERIC(4,2),             -- share of minutes from heart rate (rest by effort)
    n_sessions  SMALLINT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE analytics.daily_features
    ADD COLUMN IF NOT EXISTS zone_min_14d NUMERIC(7,1),   -- endurance minutes, last 14 days
    ADD COLUMN IF NOT EXISTS easy_pct_14d NUMERIC(5,1),
    ADD COLUMN IF NOT EXISTS mid_pct_14d  NUMERIC(5,1);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('TRAIN_INTENSITY_MIX', 'training', 'Too little easy time, too much moderately hard time',
   'The common recreational pattern is most running at moderately hard; pyramidal starts at 75-80% easy.',
   'research', 'Rosenblat et al., Sports Medicine (training intensity distribution network meta-analysis)', 14, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('TRAIN_INTENSITY_MIX', 'easy_target_pct',    75, 75, 60, 90),
  ('TRAIN_INTENSITY_MIX', 'easy_target_hi_pct', 80, 80, 65, 95),
  ('TRAIN_INTENSITY_MIX', 'moderate_flag_pct',  25, 25, 15, 40),
  ('TRAIN_INTENSITY_MIX', 'min_minutes',       150, 150, 60, 400)   -- two weeks with less can't be judged
ON CONFLICT (rule_id, name) DO NOTHING;
