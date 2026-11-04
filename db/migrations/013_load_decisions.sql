-- D1: stop using ACWR for decisions. The classic ratio counts the latest week in both
-- terms, which alone creates a spurious correlation (Windt & Gabbett 2019; Lolli et al.),
-- and acute and chronic load each carry their own information. Decisions now use the
-- weekly load change, absolute chronic load, and recovery status; ACWR stays on the
-- dashboard as a descriptive number. Re-running is safe.

ALTER TABLE analytics.daily_features
    ADD COLUMN IF NOT EXISTS acute_ewma           NUMERIC(8,1),  -- 7-day EWMA of internal load (per day)
    ADD COLUMN IF NOT EXISTS chronic_ewma         NUMERIC(8,1),  -- 28-day EWMA ending 7 days ago (uncoupled)
    ADD COLUMN IF NOT EXISTS load_week            NUMERIC(8,1),  -- internal load, last 7 days
    ADD COLUMN IF NOT EXISTS load_week_prev       NUMERIC(8,1),  -- the 7 days before that
    ADD COLUMN IF NOT EXISTS load_week_change_pct NUMERIC(6,1),
    ADD COLUMN IF NOT EXISTS run_load_week        NUMERIC(8,1),
    ADD COLUMN IF NOT EXISTS run_load_week_prev   NUMERIC(8,1),
    ADD COLUMN IF NOT EXISTS plyo_week            NUMERIC(7,1),
    ADD COLUMN IF NOT EXISTS plyo_week_prev       NUMERIC(7,1),
    ADD COLUMN IF NOT EXISTS sprint_week          NUMERIC(7,1),
    ADD COLUMN IF NOT EXISTS sprint_week_prev     NUMERIC(7,1);

-- Ramp guards: week-over-week caps, with a small absolute allowance so a low winter base
-- can still grow. Live from the week of Nov 9 (brief D1).
INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('TRAIN_RUN_RAMP', 'training', 'Running load up more than the weekly cap',
   'Cap week-over-week running load increases; small absolute increases are allowed from a low base.',
   'heuristic', 'Windt & Gabbett 2019 (use load change, not ACWR)', 14, 'active'),
  ('TRAIN_CHANNEL_RAMP', 'training', 'Sprint metres or jump contacts up more than their weekly cap',
   'Sprinting and plyometrics stress hamstrings and tendons in ways HR-based load misses.',
   'heuristic', 'Brief A6/D1; hamstring re-injury risk with sprint exposure spikes', 14, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('TRAIN_RUN_RAMP',     'max_weekly_increase_pct', 10,  10,   5,   25),
  ('TRAIN_RUN_RAMP',     'low_base_allowance',      60,  60,   0,  200),  -- internal load units a week
  ('TRAIN_CHANNEL_RAMP', 'sprint_increase_pct',     15,  15,   5,   40),
  ('TRAIN_CHANNEL_RAMP', 'sprint_allowance_m',     100, 100,   0,  400),
  ('TRAIN_CHANNEL_RAMP', 'plyo_increase_pct',       15,  15,   5,   40),
  ('TRAIN_CHANNEL_RAMP', 'plyo_allowance',          40,  40,   0,  200)
ON CONFLICT (rule_id, name) DO NOTHING;

-- Hypotheses built on ACWR are retired; weekly change replaces them.
UPDATE analytics.hypothesis SET status = 'retired' WHERE exposure LIKE 'acwr%';
INSERT INTO analytics.hypothesis (exposure, outcome, lag_days, rationale, status) VALUES
  ('load_week_change_pct >= 20', 'hrv',      1, 'A week-over-week load jump and next-morning HRV', 'queued'),
  ('load_week_change_pct >= 20', 'soreness', 1, 'A week-over-week load jump and soreness', 'queued')
ON CONFLICT (exposure, outcome, lag_days) DO NOTHING;
