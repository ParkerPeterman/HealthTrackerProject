-- H3: a missed-dose finding needs at least 8 missed days in its estimate. The Section 13
-- evaluation of the demo athlete (nothing planted) produced a "likely" finding from two missed
-- days that happened to follow a trip. Below the minimum the message stays "too early to tell".
-- Re-running is safe.

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_MEDICATION', 'min_missed_for_finding', 8, 8, 4, 30)
ON CONFLICT (rule_id, name) DO NOTHING;
