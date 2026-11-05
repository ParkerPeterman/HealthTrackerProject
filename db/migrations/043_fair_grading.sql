-- G5: fair grading of advice. Each action logs a coarse decision-time state (HRV band, a short
-- night, a travel or game day); followed and not-followed days are compared only within the
-- same state, with intervals, and rule promotion uses that matched comparison. Older actions
-- take their state from the recommendation's input snapshot. Re-running is safe.

ALTER TABLE analytics.recommendation_action
    ADD COLUMN IF NOT EXISTS state JSONB;   -- {hrv_low, short_sleep, busy} on the decision day

CREATE TABLE IF NOT EXISTS analytics.advice_grade (
    computed_on         DATE NOT NULL,
    rule_id             TEXT NOT NULL REFERENCES analytics.rule(rule_id),
    metric              TEXT,
    outcome_metric      TEXT,
    n_followed          INT NOT NULL,
    n_not               INT NOT NULL,
    raw_diff            NUMERIC(10,3),        -- followed minus not, unmatched (biased)
    n_matched_followed  INT NOT NULL,
    n_matched_not       INT NOT NULL,
    strata              SMALLINT NOT NULL,
    strata_used         SMALLINT NOT NULL,    -- states with both followed and not-followed days
    matched_diff        NUMERIC(10,3),        -- within-state difference, weighted by followed days
    se                  NUMERIC(10,4),
    lo80 NUMERIC(10,3), hi80 NUMERIC(10,3), lo95 NUMERIC(10,3), hi95 NUMERIC(10,3),
    p_benefit           NUMERIC(4,3),         -- P(following helps)
    PRIMARY KEY (computed_on, rule_id)
);
