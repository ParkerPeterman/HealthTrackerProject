-- E3: the sleep drivers model. One multi-exposure Bayesian model per sleep outcome (duration,
-- midpoint, HRV, overnight resting HR, my sleep rating), so effects aren't double-counted:
-- caffeine dose and timing, alcohol, last meal time, training load and time of day, late
-- water, and travel. Medication dose time is analyzed only in the sensitive H3 model and is
-- never a lever. Gate: 30 days (B1's intervals carry the uncertainty before then); shadow
-- until COMP_SLEEP_DRIVERS is turned on (brief: about Dec 1). Re-running is safe.

ALTER TABLE analytics.daily_features
    ADD COLUMN IF NOT EXISTS train_end_hour NUMERIC(4,2),   -- when the day's last session ended, local hours
    ADD COLUMN IF NOT EXISTS water_late_ml  NUMERIC(6,1);   -- water logged from 20:00 on (NULL when none was logged that day)

CREATE TABLE IF NOT EXISTS analytics.sleep_driver (
    computed_on   DATE NOT NULL,
    outcome       TEXT NOT NULL,
    exposure      TEXT NOT NULL,
    n_days        INT NOT NULL,
    estimate      NUMERIC(10,3),        -- outcome units per unit of exposure (per day for yes/no)
    lo80 NUMERIC(10,3), hi80 NUMERIC(10,3), lo95 NUMERIC(10,3), hi95 NUMERIC(10,3),
    p_direction   NUMERIC(4,3),
    p_meaningful  NUMERIC(4,3),
    prior_source  TEXT,
    PRIMARY KEY (computed_on, outcome, exposure)
);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_SLEEP_DRIVERS', 'analysis', 'Sleep drivers model in insights (E3)', NULL, 'research',
   'One multi-exposure model per outcome (brief E3)', 30, 'shadow')
ON CONFLICT (rule_id) DO NOTHING;
