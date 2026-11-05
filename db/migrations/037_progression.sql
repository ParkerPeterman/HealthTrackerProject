-- F5: progression. Next week's load grows only after a graded week whose key sessions were done
-- and whose recovery held; otherwise it holds. Every Nth week of base, VO2 build, and
-- pre-season is a deload week (deload factor, low-end durations, VO2 level held, one set fewer
-- on the lifts). Lift suggestions follow the RIR of the last session. Re-running is safe.

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_PLAN', 'deload_every',      4,   4,  0,   8),   -- every Nth week of a phase is a deload; 0 or 1: none
  ('ANALYSIS_PLAN', 'progress_key_pct', 100, 100, 50, 100)   -- share of last week's key sessions done to progress
ON CONFLICT (rule_id, name) DO NOTHING;
