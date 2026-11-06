-- D2 with fitted time constants. Once runs span fit_taus_days (about 5 months) and number
-- fit_taus_runs, every pair on a grid (fitness 21-60 days, fatigue 3-14) is scored by model
-- evidence plus log-normal priors centred on 42 and 7 days. The best pair replaces 42/7 only when
-- its rolling-origin backtest (re-picking the pair before each origin) is clearly better on the
-- same runs: MAE 5% lower and P >= 0.95 paired run by run. tau_fit keeps the verdict either way.
-- Still shadow (COMP_FITNESS_FATIGUE; brief: fitted constants in spring). Re-running is safe.

ALTER TABLE analytics.fitness_fatigue
    ADD COLUMN IF NOT EXISTS tau_source TEXT NOT NULL DEFAULT 'fixed' CHECK (tau_source IN ('fixed', 'fitted')),
    ADD COLUMN IF NOT EXISTS tau_fit    JSONB;  -- {gate, best, best_weight, mean, top, backtest, mae_fixed, p_better, used, reason}

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('ANALYSIS_D2', 'analysis', 'When the fitness-fatigue model may fit its own time constants (D2)', NULL, 'research',
   'Banister et al. 1975; time constants fitted with priors, kept only if they forecast better', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_D2', 'fit_taus_days', 150, 150, 120, 365),  -- days from the first to the last steady run
  ('ANALYSIS_D2', 'fit_taus_runs', 40,  40,  30,  200)   -- steady runs with a pace at the reference HR
ON CONFLICT (rule_id, name) DO NOTHING;
