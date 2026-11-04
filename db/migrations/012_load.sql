-- A6: one internal load scale. HR-tracked sessions use Banister's TRIMP; games and lifts
-- use session-RPE (minutes x effort) calibrated onto the TRIMP scale from sessions that
-- have both. Plyometric contacts, sprint metres, and shoulder (throwing) minutes are
-- separate channels: they load tendons, hamstrings, and shoulders in ways HR misses.
-- Re-running is safe.

ALTER TABLE analytics.session_features
    ADD COLUMN IF NOT EXISTS trimp         NUMERIC(7,1),   -- Banister, from per-second or average HR
    ADD COLUMN IF NOT EXISTS srpe          NUMERIC(7,1),   -- RPE x minutes (RPE imputed from HR when missing)
    ADD COLUMN IF NOT EXISTS internal_load NUMERIC(7,1),   -- the one scale
    ADD COLUMN IF NOT EXISTS load_method   TEXT CHECK (load_method IN ('trimp', 'srpe_calibrated')),
    ADD COLUMN IF NOT EXISTS load_factor   NUMERIC(6,3);   -- calibration factor used (sRPE -> TRIMP units)

CREATE TABLE IF NOT EXISTS analytics.load_daily (
    date       DATE NOT NULL,
    channel    TEXT NOT NULL,          -- internal, internal_run, internal_game, internal_lift, internal_other,
                                       -- plyo_contacts, sprint_m, shoulder_min
    value      NUMERIC(9,1) NOT NULL,
    n_sessions SMALLINT NOT NULL,
    PRIMARY KEY (date, channel)
);

CREATE TABLE IF NOT EXISTS analytics.load_calibration (
    computed_on DATE PRIMARY KEY,
    factor      NUMERIC(6,3) NOT NULL,  -- TRIMP units per sRPE unit
    n_sessions  INT NOT NULL,
    method      TEXT NOT NULL CHECK (method IN ('median_ratio', 'default')),
    details     JSONB
);

ALTER TABLE analytics.daily_features ADD COLUMN IF NOT EXISTS load_internal NUMERIC(8,1);

INSERT INTO analytics.rule (rule_id, domain, description, evidence_level, source_ref, min_history_days, status) VALUES
  ('ANALYSIS_LOAD', 'analysis', 'Internal load: TRIMP and session-RPE calibration', 'research',
   'Banister 1991 (TRIMP); Foster et al. 2001 (session-RPE)', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  -- Until 5 sessions have both HR and a logged RPE, sRPE x 0.4 stands in: the Banister formula
  -- gives about 0.37-0.44 TRIMP per sRPE unit for easy to tempo running. My own ratio replaces it.
  ('ANALYSIS_LOAD', 'srpe_default_factor',   0.4, 0.4, 0.1, 1.0),
  ('ANALYSIS_LOAD', 'min_calibration_pairs',   5,   5,   3,  20),
  ('ANALYSIS_LOAD', 'calibration_days',      120, 120,  28, 365)
ON CONFLICT (rule_id, name) DO NOTHING;
