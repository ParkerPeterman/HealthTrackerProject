-- H3: medication analysis in the weekly job, shadow for the first month. Sensitive.
--   * a stable dose is part of the baseline; a dose change is a known change point (A3 reset)
--   * missed-dose effects: B1 estimates for the missed day and the two after, on HRV, resting
--     HR, sleep, mood, and energy, with weak priors centred on no effect (the evidence is mixed)
--   * dose time against sleep duration and my sleep rating (never "take it in the morning")
--   * travel and alcohol are controls; if nearly all misses fall on travel days, the effect is
--     reported as not separable from travel
-- Medication is never a lever, an experiment, or something to change. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.medication_effect (
    computed_on   DATE NOT NULL,
    medication_id INT NOT NULL REFERENCES core.medication(id) ON DELETE CASCADE,
    exposure      TEXT NOT NULL,          -- med_missed | med_dose_time
    outcome       TEXT NOT NULL,
    lag_days      SMALLINT NOT NULL,
    n_days        INT NOT NULL,
    n_exposed     INT,                    -- missed days used
    estimate      NUMERIC(10,3),
    lo80 NUMERIC(10,3), hi80 NUMERIC(10,3), lo95 NUMERIC(10,3), hi95 NUMERIC(10,3),
    p_meaningful  NUMERIC(4,3),
    travel_share  NUMERIC(4,3),           -- share of missed days that were travel days
    prior_source  TEXT,
    PRIMARY KEY (computed_on, medication_id, exposure, outcome, lag_days)
);
COMMENT ON TABLE analytics.medication_effect IS 'sensitive';

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_MED_ANALYSIS', 'analysis', 'Medication findings (approved templates 6-8) in coaching (H3)', NULL, 'research',
   'Brief H3', 28, 'shadow')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_MEDICATION', 'travel_share_confounded', 0.7, 0.7, 0.5, 1.0),
  ('ANALYSIS_MEDICATION', 'min_missed_days',          3,   3,   2,  10),
  ('ANALYSIS_MEDICATION', 'finding_min_p',          0.6, 0.6, 0.6, 0.95)   -- "possibly" or stronger
ON CONFLICT (rule_id, name) DO NOTHING;
