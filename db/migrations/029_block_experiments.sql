-- B7: randomized N-of-1 experiments in blocks of 3-4 days (Mon-Thu, Fri-Sun) to limit
-- carryover, both conditions as actions (e.g. caffeine cutoff at noon vs 3 pm), analyzed with
-- a Bayesian model that allows for a trend, carryover on the first day of a switched block,
-- autocorrelation, and travel as a covariate. One at a time; no logging beyond a tap; never
-- anything that conflicts with an injury restriction; never medication. Re-running is safe.

ALTER TABLE analytics.experiment
    ADD COLUMN IF NOT EXISTS design_code TEXT,
    ADD COLUMN IF NOT EXISTS result      JSONB;

-- Priors for the designs' contrasts (condition A minus B), in outcome SDs.
INSERT INTO knowledge.effect_priors (exposure, outcome, lag_days, mean_sd, sd_sd, source, notes) VALUES
  ('experiment:caffeine_cutoff', 'sleep_total_min', 1, 0.2, 0.5, 'Drake et al. 2013, J Clin Sleep Med',
   'an earlier cutoff: longer sleep (direction only)')
ON CONFLICT (exposure, outcome, lag_days) DO NOTHING;
