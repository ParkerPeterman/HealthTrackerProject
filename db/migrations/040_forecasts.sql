-- G2: forecasts. A local linear trend per series (scale weight, pace at the reference HR,
-- VO2 max, the performance tests), plus D2's pace under the planned load. Each is
-- backtested from rolling origins against a naive forecast and shown only once it beats it
-- (and COMP_FORECASTS is live: shadow until the season). Goal arrival dates come from the
-- stored path. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.forecast (
    computed_on        DATE NOT NULL,
    metric             TEXT NOT NULL,       -- weight_kg, aerobic_pace, vo2max, time_trial_5k (min), sprint_10m, ...
    model              TEXT NOT NULL CHECK (model IN ('local_linear_trend', 'fitness_fatigue')),
    n_obs              INT NOT NULL,
    last_date          DATE NOT NULL,
    last_value         NUMERIC(10,3),
    horizon_days       SMALLINT NOT NULL,
    test_horizon_days  SMALLINT NOT NULL,
    headline           JSONB NOT NULL,      -- {date, mean, lo80, hi80}: one measurement at the horizon
    path               JSONB,               -- {start, mean[], lo80[], hi80[]}: the level, daily for a year
    backtest_n         INT NOT NULL,
    mae_model          NUMERIC(10,3),
    mae_naive          NUMERIC(10,3),
    coverage80         NUMERIC(4,2),        -- share of backtests inside the 80% interval
    beats_naive        BOOLEAN NOT NULL,
    PRIMARY KEY (computed_on, metric, model)
);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_FORECASTS', 'analysis', 'Show forecasts and goal arrival dates that beat a naive forecast (G2)', NULL,
   'heuristic', 'Brief G2 and section 13 (rolling-origin backtests)', 60, 'shadow')
ON CONFLICT (rule_id) DO NOTHING;
