-- E1: sleep regularity (Phillips Sleep Regularity Index from sleep windows and naps), the
-- spread of the sleep midpoint, and the weekday-vs-weekend midpoint gap, each over 7 and 28
-- days. Gate: 14 days. Re-running is safe.

ALTER TABLE analytics.daily_features
    ADD COLUMN IF NOT EXISTS sri_7d              NUMERIC(5,1),
    ADD COLUMN IF NOT EXISTS sri_28d             NUMERIC(5,1),
    ADD COLUMN IF NOT EXISTS sri_pairs_28d       SMALLINT,      -- day pairs with both nights recorded
    ADD COLUMN IF NOT EXISTS midpoint_sd_7d      NUMERIC(5,1),  -- minutes
    ADD COLUMN IF NOT EXISTS midpoint_sd_28d     NUMERIC(5,1),
    ADD COLUMN IF NOT EXISTS weekend_gap_min_28d NUMERIC(5,1);  -- weekend midpoint minus weekday, minutes

-- Sleep regularity reaches coaching once 14 nights exist (brief E1, live from the week of Nov 9).
INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_SLEEP_REGULARITY', 'analysis', 'Show sleep regularity (SRI, midpoint spread, weekend gap) in coaching (E1)',
   NULL, 'research', 'Phillips et al. 2017 (SRI); Windred et al. 2024 (UK Biobank)', 14, 'active')
ON CONFLICT (rule_id) DO NOTHING;
