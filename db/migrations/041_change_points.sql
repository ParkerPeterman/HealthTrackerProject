-- G3: Bayesian online change points on resting HR, ln HRV, pace at the reference HR, weight,
-- and the sleep midpoint, with candidate causes from tags, flags, phases, and logs. A dose
-- change near a shift is linked only in the sensitive table below. Shadow until pre-season.
-- Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.change_point (
    id           BIGSERIAL PRIMARY KEY,
    computed_on  DATE NOT NULL,
    metric       TEXT NOT NULL CHECK (metric IN ('resting_hr', 'ln_hrv', 'aerobic_pace', 'weight_kg', 'sleep_midpoint')),
    date         DATE NOT NULL,          -- the first measurement of the new segment
    prob         NUMERIC(4,3) NOT NULL,  -- P(a segment began within two measurements of it)
    mean_before  NUMERIC(10,3) NOT NULL,
    mean_after   NUMERIC(10,3) NOT NULL,
    shift        NUMERIC(10,3) NOT NULL,
    shift_sd     NUMERIC(6,2) NOT NULL,
    n_before     INT NOT NULL,
    n_after      INT NOT NULL,
    summary      TEXT NOT NULL,          -- "Something shifted around Mar 02: resting HR went up by about 3.1 bpm (0.9 SD)"
    causes       JSONB NOT NULL DEFAULT '[]',  -- [{kind: flag|log|phase, what, days?|before/after/direction}]
    UNIQUE (metric, date)
);

-- Sensitive (H5): a medication dose change within two weeks of a shift. Never joined into the
-- general record, the recommendation snapshot, or the LLM prompt; not shown until a wording
-- for it is approved.
CREATE TABLE IF NOT EXISTS analytics.change_point_medication (
    change_point_id      BIGINT NOT NULL REFERENCES analytics.change_point(id) ON DELETE CASCADE,
    medication_change_id INT NOT NULL REFERENCES core.medication_change(id) ON DELETE CASCADE,
    days_from_change     INT NOT NULL,   -- shift date minus dose-change date
    PRIMARY KEY (change_point_id, medication_change_id)
);
COMMENT ON TABLE analytics.change_point_medication IS 'sensitive';

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_CHANGE_POINTS', 'analysis', 'Report shifts in resting HR, HRV, aerobic pace, weight, and sleep timing (G3)', NULL,
   'research', 'Adams & MacKay 2007 (Bayesian online change-point detection)', 60, 'shadow')
ON CONFLICT (rule_id) DO NOTHING;
