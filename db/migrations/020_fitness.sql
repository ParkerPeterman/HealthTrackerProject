-- D3: tracking aerobic fitness. Each steady run's pace at the reference HR, adjusted to
-- reference conditions (temperature, dew point, climb, duration), feeds a local-linear-trend
-- state-space model; the output is the underlying trend with an interval. Garmin's VO2 max
-- estimate is smoothed the same way as a secondary marker (it depends on the max HR set in
-- the Garmin profile). Aerobic decoupling on long steady runs is the durability marker.
-- Gate: about 10 qualifying runs. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.fitness_trend (
    date            DATE NOT NULL,
    metric          TEXT NOT NULL CHECK (metric IN ('aerobic_pace', 'vo2max')),
    observed        NUMERIC(7,2),        -- that day's value (a run's pace at ref HR, or Garmin's VO2 max)
    adjusted        NUMERIC(7,2),        -- observed, at reference conditions (pace only)
    level           NUMERIC(7,2),        -- smoothed underlying fitness
    level_lo80      NUMERIC(7,2),
    level_hi80      NUMERIC(7,2),
    slope_week      NUMERIC(6,2),        -- change per week (pace: s/km; negative = fitter)
    slope_lo80      NUMERIC(6,2),
    slope_hi80      NUMERIC(6,2),
    n_obs           INT NOT NULL,        -- observations up to this date
    model           TEXT NOT NULL,       -- local_linear_trend | local_level
    PRIMARY KEY (date, metric)
);

CREATE TABLE IF NOT EXISTS analytics.fitness_model (
    computed_on  DATE NOT NULL,
    metric       TEXT NOT NULL,
    n_obs        INT NOT NULL,
    covariates   JSONB,                   -- adjustment coefficients and their reference values
    params       JSONB,                   -- fitted variances
    PRIMARY KEY (computed_on, metric)
);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('ANALYSIS_FITNESS', 'analysis', 'Aerobic fitness trend (D3): qualifying runs, reference conditions, decoupling', NULL,
   'heuristic', 'Pace at reference HR; aerobic decoupling (coaching rule of thumb: under about 5%)', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_FITNESS', 'min_runs',           10, 10,  5, 30),   -- the gate
  ('ANALYSIS_FITNESS', 'min_run_minutes',    20, 20, 15, 60),
  ('ANALYSIS_FITNESS', 'decoupling_good_pct', 5,  5,  3, 10),
  ('ANALYSIS_FITNESS', 'ref_temp_c',         10, 10,  0, 20),
  ('ANALYSIS_FITNESS', 'ref_dew_c',          10, 10,  0, 20),
  ('ANALYSIS_FITNESS', 'ref_minutes',        45, 45, 20, 90),
  ('ANALYSIS_FITNESS', 'prior_sd',          0.5, 0.5, 0.1, 2.0)  -- covariate prior: Normal(0, 0.5 SD), brief B2
ON CONFLICT (rule_id, name) DO NOTHING;
