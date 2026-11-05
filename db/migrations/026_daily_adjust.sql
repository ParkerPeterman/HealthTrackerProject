-- F4: daily adjustment. Each morning: keep today's hard session, downgrade it (intervals
-- become easy, sprints become technique, heavy lifts back off), swap it with a later easy
-- day, or rest, from the 7-day HRV band, resting HR, last night's sleep, and journal
-- soreness and energy. In one study of recreational runners, high-intensity work only
-- when the 7-day HRV sat inside its band improved 3,000 m speed with less hard training; a
-- meta-analysis found a small, non-significant performance edge but better vagal HRV and
-- fewer non-responders. So: shadow first (hidden, graded actions), and conservative.
-- The medication readiness flag acts through its own approved template (shadow), never
-- through this rule, so no medication data reaches the coaching LLM. Re-running is safe.

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days,
                            status, inputs) VALUES
  ('TRAIN_DAILY_ADJUST', 'training', 'Adjust today''s hard session to recovery: keep, downgrade, swap, or rest',
   'High-intensity work only while the 7-day HRV is inside its band; back off further when other markers agree.',
   'research', 'HRV-guided training (Heikura 2015; HRV-guided training meta-analysis)', 21, 'shadow',
   '{ln_hrv,resting_hr,sleep_total_min,soreness,energy}')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('TRAIN_DAILY_ADJUST', 'hard_rpe',       7,   7,   6,   9),
  ('TRAIN_DAILY_ADJUST', 'short_sleep_min', 390, 390, 300, 450),
  ('TRAIN_DAILY_ADJUST', 'soreness_high',  4,   4,   3,   5),
  ('TRAIN_DAILY_ADJUST', 'energy_low',     2,   2,   1,   3),
  ('TRAIN_DAILY_ADJUST', 'flags_for_rest', 2,   2,   2,   4),
  ('TRAIN_DAILY_ADJUST', 'easy_rpe',       4,   4,   2,   5)
ON CONFLICT (rule_id, name) DO NOTHING;
