-- D3: versioned heart-rate zones. Every HR-based feature (time in zones, TRIMP, intensity
-- distribution) is recomputed for all history with the newest version, so a confirmed
-- max HR from the field test fixes past sessions too. Re-running is safe.

-- Per-second cadence and altitude from FIT files: cadence lets a max HR candidate be
-- checked against cadence lock; altitude gives grade-adjusted pace.
ALTER TABLE core.hr_sample
    ADD COLUMN IF NOT EXISTS cadence_spm SMALLINT,
    ADD COLUMN IF NOT EXISTS altitude_m  NUMERIC(7,1);

CREATE TABLE IF NOT EXISTS core.athlete_zones (
    version     SERIAL PRIMARY KEY,
    valid_from  DATE NOT NULL,
    max_hr      SMALLINT NOT NULL CHECK (max_hr BETWEEN 120 AND 240),
    source      TEXT NOT NULL CHECK (source IN ('self_report', 'field_test', 'observed', 'manual')),
    confirmed   BOOLEAN NOT NULL DEFAULT FALSE,   -- FALSE = provisional
    test_id     BIGINT REFERENCES core.performance_test(id) ON DELETE SET NULL,
    workout_id  BIGINT REFERENCES core.workout(id) ON DELETE SET NULL,
    checks      JSONB,                            -- max HR acceptance checks, when data was checked
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Analysis settings live in rule_parameter like rule thresholds (tunable, with history).
-- They hang off 'analysis' pseudo-rules that the rules engine skips (no rule function).
ALTER TABLE analytics.rule DROP CONSTRAINT IF EXISTS rule_domain_check;
ALTER TABLE analytics.rule ADD CONSTRAINT rule_domain_check
    CHECK (domain IN ('training', 'nutrition', 'sleep', 'recovery', 'injury', 'body', 'experiment',
                      'analysis', 'medication'));

INSERT INTO analytics.rule (rule_id, domain, description, evidence_level, source_ref, min_history_days, status) VALUES
  ('ANALYSIS_ZONES', 'analysis', 'Heart-rate zone settings and max HR acceptance checks', 'research',
   'Seiler & Kjerland 2006 (three-zone model); wrist HR cadence lock', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  -- Three-zone model boundaries as % of max HR, standing in for the two ventilatory
  -- thresholds until a lab test gives real ones.
  ('ANALYSIS_ZONES', 'z1_upper_pct_max',   80,  80,  70,  85),
  ('ANALYSIS_ZONES', 'z2_upper_pct_max',   88,  88,  84,  92),
  -- A max HR candidate must be held this long, rise without sensor jumps, and not track cadence.
  ('ANALYSIS_ZONES', 'sustain_s',          30,  30,  15,  60),
  ('ANALYSIS_ZONES', 'max_jump_bpm',       10,  10,   5,  20),
  ('ANALYSIS_ZONES', 'cadence_margin_bpm',  3,   3,   1,   6)
ON CONFLICT (rule_id, name) DO NOTHING;
