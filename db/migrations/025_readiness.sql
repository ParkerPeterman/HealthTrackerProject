-- G1 v1: an in-house readiness score, 0-100 with its top drivers. Shadow mode: computed and
-- stored nightly, not shown in coaching until COMP_READINESS_V1 is turned on (brief: Jan 4).
-- Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.readiness_daily (
    date          DATE PRIMARY KEY,
    score         SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
    composite     NUMERIC(6,3) NOT NULL,     -- weighted mean of the signed z-scores
    n_components  SMALLINT NOT NULL,
    components    JSONB NOT NULL,            -- signed z per component (positive = more ready)
    drivers       JSONB NOT NULL,            -- the top three, for the "why"
    version       TEXT NOT NULL,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_READINESS_V1', 'analysis', 'In-house readiness score in coaching (G1 v1)', NULL, 'heuristic',
   'Dawes 1979 (unit-weighted composites)', 28, 'shadow'),
  ('ANALYSIS_READINESS', 'analysis', 'Readiness score weights (equal to start) and the flag''s size', NULL, 'heuristic',
   'Dawes 1979', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_READINESS', 'w_hrv',        1, 1, 0, 3),
  ('ANALYSIS_READINESS', 'w_resting_hr', 1, 1, 0, 3),
  ('ANALYSIS_READINESS', 'w_sleep',      1, 1, 0, 3),
  ('ANALYSIS_READINESS', 'w_regularity', 1, 1, 0, 3),
  ('ANALYSIS_READINESS', 'w_load',       1, 1, 0, 3),
  ('ANALYSIS_READINESS', 'w_soreness',   1, 1, 0, 3),
  ('ANALYSIS_READINESS', 'w_energy',     1, 1, 0, 3),
  ('ANALYSIS_READINESS', 'w_flag',       1, 1, 0, 3),
  ('ANALYSIS_READINESS', 'flag_z',     1.5, 1.5, 0, 3)
ON CONFLICT (rule_id, name) DO NOTHING;
