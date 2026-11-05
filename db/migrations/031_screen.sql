-- B6: a weekly exploratory screen over exposure x outcome x lag (0-3 days) with strong
-- shrinkage, so only effects with a lot of support stand out. Anything flagged is labeled
-- exploratory and becomes a candidate self-experiment, never an active rule. Medication and
-- poor-tier measures are never scanned. Shadow. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.screen_result (
    computed_on  DATE NOT NULL,
    exposure     TEXT NOT NULL,
    outcome      TEXT NOT NULL,
    lag_days     SMALLINT NOT NULL,
    n_days       INT NOT NULL,
    estimate     NUMERIC(10,4),
    lo80 NUMERIC(10,4), hi80 NUMERIC(10,4),
    p_meaningful NUMERIC(4,3),
    flagged      BOOLEAN NOT NULL,     -- stands out despite the shrinkage
    known        BOOLEAN NOT NULL,     -- already a registered hypothesis
    candidate    BOOLEAN NOT NULL,     -- a B7 design can test it
    PRIMARY KEY (computed_on, exposure, outcome, lag_days)
);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('ANALYSIS_SCREEN', 'analysis', 'Exploratory screen settings (B6)', NULL, 'heuristic',
   'Strong shrinkage in place of a horseshoe prior (conjugate, exact)', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_SCREEN', 'prior_sd', 0.2, 0.2, 0.05, 0.5),  -- 0.1 hid a real per-drink effect in 90 demo days
  ('ANALYSIS_SCREEN', 'flag_p',   0.5, 0.5, 0.3,  0.9),
  ('ANALYSIS_SCREEN', 'max_lag',    3,   3,   1,    5)
ON CONFLICT (rule_id, name) DO NOTHING;
