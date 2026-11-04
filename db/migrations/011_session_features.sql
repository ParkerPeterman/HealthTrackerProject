-- A5: one row per session. Endurance analysis needs more than one point per day:
-- each run is its own observation. Re-running is safe.

ALTER TABLE core.workout
    ADD COLUMN IF NOT EXISTS plyo_contacts INT CHECK (plyo_contacts >= 0),         -- logged foot contacts (A6)
    ADD COLUMN IF NOT EXISTS sprint_m      NUMERIC(7,1) CHECK (sprint_m >= 0),     -- logged distance above 90% max speed (A6)
    ADD COLUMN IF NOT EXISTS session_code  TEXT,                                   -- session library code, when known (F2)
    -- Start point rounded to 0.1 degree (about 11 km), only for the weather lookup (A7).
    -- GPS tracks are never stored or sent anywhere.
    ADD COLUMN IF NOT EXISTS start_lat     NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS start_lon     NUMERIC(5,1);

-- A view's SELECT * is fixed when it is created: recreate it so it carries the new columns.
CREATE OR REPLACE VIEW core.v_workout AS
SELECT * FROM core.workout WHERE superseded_by IS NULL;

CREATE TABLE IF NOT EXISTS analytics.session_features (
    workout_id       BIGINT PRIMARY KEY REFERENCES core.workout(id) ON DELETE CASCADE,
    date             DATE NOT NULL,
    sport            TEXT NOT NULL,
    session_type     TEXT NOT NULL CHECK (session_type IN
                       ('easy', 'long', 'tempo', 'intervals', 'vo2', 'speed_jump', 'game', 'lift', 'other')),
    type_source      TEXT NOT NULL,           -- 'logged' (game/lift/library code) or 'hr' / 'summary' (inferred)
    duration_min     NUMERIC(6,1) NOT NULL,
    distance_m       NUMERIC(10,1),
    hr_source        TEXT NOT NULL CHECK (hr_source IN ('samples', 'summary', 'none')),
    avg_hr           SMALLINT,
    z1_min NUMERIC(6,1), z2_min NUMERIC(6,1), z3_min NUMERIC(6,1), z4_min NUMERIC(6,1), z5_min NUMERIC(6,1),
    low_min          NUMERIC(6,1),            -- three-zone model (D4): easy
    mid_min          NUMERIC(6,1),            --   between the thresholds
    high_min         NUMERIC(6,1),            --   hard
    tid_method       TEXT,                    -- 'hr' (time in zones) | 'session_rpe' (whole session by effort)
    pace_at_ref_hr_s_km NUMERIC(6,1),         -- this run's pace at the reference HR
    gap_s_km         NUMERIC(6,1),            -- grade-adjusted pace (Minetti cost of running)
    decoupling_pct   NUMERIC(5,2),            -- pace:HR, first vs second half, steady runs >= 40 min
    hr_drift_pct     NUMERIC(5,2),
    plyo_contacts    INT,
    sprint_m         NUMERIC(7,1),
    zones_version    INT,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS session_features_date ON analytics.session_features (date);

INSERT INTO analytics.rule (rule_id, domain, description, evidence_level, source_ref, min_history_days, status) VALUES
  ('ANALYSIS_SESSIONS', 'analysis', 'Session classification and run-quality settings', 'heuristic',
   'Minetti et al. 2002 (cost of running on slopes); aerobic decoupling', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_SESSIONS', 'decoupling_min_minutes', 40, 40, 30, 90),
  ('ANALYSIS_SESSIONS', 'warmup_skip_minutes',    10, 10,  5, 20),
  ('ANALYSIS_SESSIONS', 'long_run_minutes',       75, 75, 50, 120),
  ('ANALYSIS_SESSIONS', 'tempo_block_minutes',    15, 15, 10,  30),
  ('ANALYSIS_SESSIONS', 'vo2_pct_max',            90, 90, 85,  95)
ON CONFLICT (rule_id, name) DO NOTHING;
