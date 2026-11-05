-- D2: Banister fitness-fatigue model with fixed time constants (42 and 7 days), k1 and k2
-- fitted with priors, performance = pace at the reference HR at reference conditions (D3).
-- For comparing plans and forecasting, never ground truth; a forecast is shown only when the
-- rolling-origin backtest beats the naive forecast. Shadow (brief: fixed constants Jan 4,
-- fitted constants in spring). Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.fitness_fatigue (
    computed_on  DATE PRIMARY KEY,
    tau_fitness  NUMERIC(5,1) NOT NULL,
    tau_fatigue  NUMERIC(5,1) NOT NULL,
    k_fitness    NUMERIC(10,5),        -- s/km of pace per unit of fitness (negative = fitness helps)
    k_fatigue    NUMERIC(10,5),        -- s/km per unit of fatigue (positive = fatigue hurts)
    n_runs       INT NOT NULL,
    backtest_n   INT,
    mae_model    NUMERIC(7,2),         -- s/km
    mae_naive    NUMERIC(7,2),
    beats_naive  BOOLEAN,
    forecast     JSONB                 -- {weeks, weekly_load, pace_s_km, lo80, hi80}
);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_FITNESS_FATIGUE', 'analysis', 'Fitness-fatigue forecasts for plans (D2)', NULL, 'research',
   'Banister et al. 1975 (impulse-response model)', 42, 'shadow')
ON CONFLICT (rule_id) DO NOTHING;
