-- F6: general health inside the weekly plan. The plan counts its WHO aerobic minutes and
-- strength days, lengthens easy runs toward 150 minutes inside the ramp caps, notes low sleep
-- regularity, and states the protein target. Re-running is safe.

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_PLAN', 'sri_low',          70, 70, 40, 90),   -- 28-day SRI below this gets a note in the plan (a starting point, not a clinical cutoff)
  ('ANALYSIS_PLAN', 'weekend_gap_min',  60, 60, 30, 180)   -- weekend sleep midpoint later than weekdays by more than this: a note
ON CONFLICT (rule_id, name) DO NOTHING;
