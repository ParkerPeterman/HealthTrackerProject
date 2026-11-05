-- F1 (in season): taper before tournaments. A week with two or more games in a day, or games on
-- back-to-back days, keeps its key sessions but nothing hard in the 3 days before, shortens the
-- rest by the taper factor, and aims the load at the chronic load times it. Re-running is safe.

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_PLAN', 'taper_factor', 0.7, 0.7, 0.4, 1.0),  -- load target and non-key durations in a taper week
  ('ANALYSIS_PLAN', 'taper_days',   3,   3,   2,   5)     -- no hard session this many days before a tournament
ON CONFLICT (rule_id, name) DO NOTHING;
