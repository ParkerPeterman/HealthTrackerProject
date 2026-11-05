-- B1-B3: small-data inference. Each hypothesis gets a Bayesian effect estimate (conjugate
-- Normal-Inverse-Gamma after AR(1) prewhitening) with 80% and 95% intervals, the probability
-- of direction, P(|effect| > the smallest worthwhile change), days used, and the days until
-- that probability would reach 0.8 if the estimate holds. Priors come from published research
-- (B2) and are wide; without a source the prior is Normal(0, 0.5 SD). Doses replace yes/no
-- splits where possible (B3). Shadow mode until COMP_BAYES_EFFECTS is turned on; the OLS+HAC
-- tests stay as a cross-check. Re-running is safe.

CREATE TABLE IF NOT EXISTS knowledge.effect_priors (
    exposure    TEXT NOT NULL,          -- hypothesis exposure expression, e.g. 'alcohol_drinks >= 1' or 'alcohol_drinks'
    outcome     TEXT NOT NULL,
    lag_days    SMALLINT NOT NULL,
    mean_sd     NUMERIC(5,2) NOT NULL,  -- standardized: outcome SDs (per exposure SD for doses)
    sd_sd       NUMERIC(5,2) NOT NULL,
    source      TEXT NOT NULL,
    notes       TEXT,
    PRIMARY KEY (exposure, outcome, lag_days)
);

-- Directions from the cited research; the magnitude is the conventional "small" effect
-- (0.2 SD, Cohen 1988) with a wide SD of 0.5, so the data dominate quickly.
INSERT INTO knowledge.effect_priors (exposure, outcome, lag_days, mean_sd, sd_sd, source, notes) VALUES
  ('last_caffeine_hour >= 14', 'sleep_score', 1, -0.2, 0.5, 'Drake et al. 2013, J Clin Sleep Med (caffeine 0, 3, or 6 h before bed disrupts sleep)', 'direction only'),
  ('last_caffeine_hour >= 14', 'sleep_total_min', 1, -0.2, 0.5, 'Drake et al. 2013, J Clin Sleep Med', 'direction only'),
  ('caffeine_mg >= 300', 'sleep_score', 1, -0.2, 0.5, 'Drake et al. 2013, J Clin Sleep Med (400 mg)', 'direction only'),
  ('caffeine_hours_before_bed', 'sleep_total_min', 1, 0.2, 0.5, 'Drake et al. 2013, J Clin Sleep Med', 'more hours between caffeine and bed: longer sleep'),
  ('alcohol_drinks >= 1', 'hrv', 1, -0.2, 0.5, 'Pietilä et al. 2018, JMIR Ment Health (dose-dependent HRV drop during sleep)', 'direction only'),
  ('alcohol_drinks >= 2', 'hrv', 1, -0.2, 0.5, 'Pietilä et al. 2018, JMIR Ment Health', 'direction only'),
  ('alcohol_drinks', 'hrv', 1, -0.2, 0.5, 'Pietilä et al. 2018, JMIR Ment Health', 'per drink'),
  ('alcohol_drinks >= 1', 'resting_hr', 1, 0.2, 0.5, 'Pietilä et al. 2018, JMIR Ment Health (higher HR during sleep)', 'direction only'),
  ('alcohol_drinks', 'resting_hr', 1, 0.2, 0.5, 'Pietilä et al. 2018, JMIR Ment Health', 'per drink'),
  ('alcohol_drinks >= 1', 'sleep_score', 1, -0.2, 0.5, 'Ebrahim et al. 2013, Alcohol Clin Exp Res (alcohol and sleep review)', 'direction only'),
  ('had_game', 'hrv', 1, -0.2, 0.5, 'Plews et al. 2013, Sports Med (HRV falls after hard training)', 'direction only'),
  ('sleep_total_min < 390', 'energy', 0, -0.2, 0.5, 'Pilcher & Huffcutt 1996, Sleep (sleep loss and mood/performance meta-analysis)', 'direction only'),
  ('sleep_total_min < 390', 'mood', 0, -0.2, 0.5, 'Pilcher & Huffcutt 1996, Sleep', 'direction only')
ON CONFLICT (exposure, outcome, lag_days) DO NOTHING;

-- B3: dose exposures. 'binary' hypotheses keep the OLS cross-check; 'dose' ones run only here.
ALTER TABLE analytics.hypothesis ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'binary'
    CHECK (kind IN ('binary', 'dose'));

ALTER TABLE analytics.daily_features
    ADD COLUMN IF NOT EXISTS bedtime_hour              NUMERIC(4,2),  -- the night ending this day, hours after the previous midnight
    ADD COLUMN IF NOT EXISTS training_min              NUMERIC(6,1),
    ADD COLUMN IF NOT EXISTS caffeine_hours_before_bed NUMERIC(4,2);  -- this day's last caffeine to that night's bedtime

INSERT INTO analytics.hypothesis (exposure, outcome, lag_days, rationale, status, kind) VALUES
  ('alcohol_drinks', 'hrv', 1, 'Per drink: next-morning HRV', 'queued', 'dose'),
  ('alcohol_drinks', 'resting_hr', 1, 'Per drink: overnight resting HR', 'queued', 'dose'),
  ('caffeine_mg', 'sleep_score', 1, 'Per mg of caffeine: that night''s sleep', 'queued', 'dose'),
  ('caffeine_hours_before_bed', 'sleep_total_min', 1, 'Hours between the last caffeine and bed', 'queued', 'dose'),
  ('bedtime_hour', 'sleep_total_min', 0, 'Later bedtime and sleep duration', 'queued', 'dose'),
  ('training_min', 'hrv', 1, 'Training minutes and next-morning HRV', 'queued', 'dose')
ON CONFLICT (exposure, outcome, lag_days) DO NOTHING;

CREATE TABLE IF NOT EXISTS analytics.effect_estimate (
    computed_on   DATE NOT NULL,
    hypothesis_id BIGINT NOT NULL REFERENCES analytics.hypothesis(id),
    kind          TEXT NOT NULL,
    n_days        INT NOT NULL,
    rho           NUMERIC(4,3),          -- AR(1) coefficient used for prewhitening
    estimate      NUMERIC(10,3),         -- posterior median, outcome units (per exposed day, or per exposure unit)
    lo80 NUMERIC(10,3), hi80 NUMERIC(10,3), lo95 NUMERIC(10,3), hi95 NUMERIC(10,3),
    estimate_sd   NUMERIC(8,3),          -- standardized (outcome SDs)
    p_direction   NUMERIC(4,3),
    swc           NUMERIC(10,3),         -- outcome units
    p_meaningful  NUMERIC(4,3),          -- P(|effect| > SWC)
    days_to_80    INT,                   -- more days until P(meaningful) would reach 0.8; NULL if it can't
    prior_mean    NUMERIC(5,2), prior_sd NUMERIC(5,2), prior_source TEXT,
    model         TEXT NOT NULL,
    PRIMARY KEY (computed_on, hypothesis_id)
);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_BAYES_EFFECTS', 'analysis', 'Bayesian effect estimates drive insight confidence (B1)', NULL, 'research',
   'Conjugate Normal-Inverse-Gamma after AR(1) prewhitening', 0, 'shadow'),
  ('ANALYSIS_SWC', 'analysis', 'Smallest worthwhile change per outcome, in outcome SDs (N3)', NULL, 'research',
   'Hopkins: smallest worthwhile change = 0.2 SD', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_SWC', 'default',         0.2, 0.2, 0.05, 1.0),
  ('ANALYSIS_SWC', 'hrv',             0.2, 0.2, 0.05, 1.0),
  ('ANALYSIS_SWC', 'resting_hr',      0.2, 0.2, 0.05, 1.0),
  ('ANALYSIS_SWC', 'sleep_score',     0.2, 0.2, 0.05, 1.0),
  ('ANALYSIS_SWC', 'sleep_total_min', 0.2, 0.2, 0.05, 1.0),
  ('ANALYSIS_SWC', 'sleep_quality',   0.2, 0.2, 0.05, 1.0),
  ('ANALYSIS_SWC', 'energy',          0.2, 0.2, 0.05, 1.0),
  ('ANALYSIS_SWC', 'mood',            0.2, 0.2, 0.05, 1.0),
  ('ANALYSIS_SWC', 'soreness',        0.2, 0.2, 0.05, 1.0)
ON CONFLICT (rule_id, name) DO NOTHING;
