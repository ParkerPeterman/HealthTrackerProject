-- B4: lagged effects. A 'lagged' hypothesis spreads its exposure over the first lag and the
-- three days before it with an exponential decay (tau chosen by model evidence: 0 = the first
-- lag only, 0.5, 1, 2, 4 days), so one coefficient covers a four-day effect. The three
-- separate had_game -> HRV lags (1, 2, 3) are what this replaces in spirit; they stay for
-- comparison. Re-running is safe.

ALTER TABLE analytics.hypothesis DROP CONSTRAINT IF EXISTS hypothesis_kind_check;
ALTER TABLE analytics.hypothesis ADD CONSTRAINT hypothesis_kind_check CHECK (kind IN ('binary', 'dose', 'lagged'));
-- The same exposure, outcome, and lag can now be both a single-lag and a lagged hypothesis.
ALTER TABLE analytics.hypothesis DROP CONSTRAINT IF EXISTS hypothesis_exposure_outcome_lag_days_key;
DO $$ BEGIN
    ALTER TABLE analytics.hypothesis ADD CONSTRAINT hypothesis_exposure_outcome_lag_kind_key
        UNIQUE (exposure, outcome, lag_days, kind);
EXCEPTION WHEN duplicate_table OR duplicate_object THEN NULL;
END $$;

ALTER TABLE analytics.effect_estimate
    ADD COLUMN IF NOT EXISTS lag_profile JSONB;  -- {tau, first_lag, per_sd, log_ev, weights, lags: [...], total}

INSERT INTO analytics.hypothesis (exposure, outcome, lag_days, rationale, status, kind) VALUES
  ('session_load',   'hrv',        1, 'B4: a big session lowers HRV over the next mornings, fading', 'active', 'lagged'),
  ('session_load',   'resting_hr', 1, 'B4: a big session raises resting HR over the next mornings, fading', 'active', 'lagged'),
  ('had_game',       'hrv',        1, 'B4: a game lowers HRV over the next mornings, fading (replaces three separate lags)', 'active', 'lagged'),
  ('had_game',       'resting_hr', 1, 'B4: a game raises resting HR over the next mornings, fading', 'active', 'lagged'),
  ('alcohol_drinks', 'hrv',        1, 'B4: whether drinks lower HRV beyond the next morning', 'active', 'lagged')
ON CONFLICT (exposure, outcome, lag_days, kind) DO NOTHING;
