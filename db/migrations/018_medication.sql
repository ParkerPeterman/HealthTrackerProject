-- H1, H2, H4 (display), H5: a daily medication check, adherence features, and the
-- approved message templates. Generic tables, so another medication can be added later.
--
-- Privacy (H5): every table here is sensitive. They stay out of daily_features, the
-- recommendation snapshot, and the LLM prompt; the dashboard shows them only behind an
-- opt-in toggle; demo screenshots never include them. Medication is never advised on,
-- never an experiment, and never ranked as something to change.
-- Re-running is safe.

CREATE TABLE IF NOT EXISTS core.medication (
    id                 SERIAL PRIMARY KEY,
    name               TEXT NOT NULL,
    dose_mg            NUMERIC(7,2),
    start_date         DATE NOT NULL,
    active             BOOLEAN NOT NULL DEFAULT TRUE,
    usual_time         TIME,                -- set by me; otherwise learned from logged times
    reminder_delay_min SMALLINT NOT NULL DEFAULT 120,  -- in-app badge this long after the usual time
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.medication_change (
    id            SERIAL PRIMARY KEY,
    medication_id INT NOT NULL REFERENCES core.medication(id) ON DELETE CASCADE,
    date          DATE NOT NULL,
    old_dose_mg   NUMERIC(7,2),
    new_dose_mg   NUMERIC(7,2),
    note          TEXT
);

-- One row per day I answered. No row means unknown: an unknown day is never counted as missed.
CREATE TABLE IF NOT EXISTS core.medication_log (
    medication_id INT NOT NULL REFERENCES core.medication(id) ON DELETE CASCADE,
    date          DATE NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('taken', 'missed', 'unknown')),
    taken_at      TIMESTAMPTZ,              -- tapped time, editable
    logged_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (medication_id, date)
);

-- H2 features, kept apart from daily_features so they can't reach the LLM payload.
CREATE TABLE IF NOT EXISTS analytics.medication_daily (
    medication_id          INT NOT NULL REFERENCES core.medication(id) ON DELETE CASCADE,
    date                   DATE NOT NULL,
    med_status             TEXT NOT NULL CHECK (med_status IN ('taken', 'missed', 'unknown')),
    consecutive_missed     SMALLINT NOT NULL,     -- recorded misses in a row; unknown days break the run
    adherence_7d           NUMERIC(4,3),          -- taken / recorded days (unknown left out)
    known_7d               SMALLINT NOT NULL,
    adherence_28d          NUMERIC(4,3),
    known_28d              SMALLINT NOT NULL,
    dose_mg                NUMERIC(7,2),
    days_since_dose_change INT,
    dose_time_local        NUMERIC(4,2),          -- hours after local midnight
    dose_time_sd_14d       NUMERIC(4,2),
    computed_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (medication_id, date)
);

-- H4: every medication line comes from an approved template (medication/messages.py) with
-- code-computed values. Added after the LLM output guard: the LLM never sees them.
CREATE TABLE IF NOT EXISTS analytics.medication_message (
    date          DATE NOT NULL,
    medication_id INT NOT NULL REFERENCES core.medication(id) ON DELETE CASCADE,
    template      TEXT NOT NULL,
    params        JSONB NOT NULL,
    text          TEXT NOT NULL,
    visible       BOOLEAN NOT NULL,          -- false while its switch is in shadow
    PRIMARY KEY (date, medication_id, template)
);

COMMENT ON TABLE core.medication IS 'sensitive';
COMMENT ON TABLE core.medication_change IS 'sensitive';
COMMENT ON TABLE core.medication_log IS 'sensitive';
COMMENT ON TABLE analytics.medication_daily IS 'sensitive';
COMMENT ON TABLE analytics.medication_message IS 'sensitive';

-- Switches. The adherence display and the two safety templates are live now; the readiness
-- adjustment runs in shadow for two weeks first (brief H4).
INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_MED_DISPLAY', 'analysis', 'Medication: daily tick, weekly count, and the missed-days message (H4)', NULL,
   'safety', 'Brief H4 (approved templates 1-2)', 0, 'active'),
  ('COMP_MED_SAFETY', 'analysis', 'Medication: dizziness-in-training and low-mood messages (H4)', NULL,
   'safety', 'Brief H4 (approved templates 4-5)', 0, 'active'),
  ('COMP_MED_READINESS', 'analysis', 'Medication: missed doses plus low energy, mood, or dizziness lower readiness (H4)',
   NULL, 'heuristic', 'Brief H4 (approved template 3)', 14, 'shadow'),
  ('ANALYSIS_MEDICATION', 'analysis', 'Medication message thresholds', NULL, 'safety', 'Brief H4', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_MEDICATION', 'missed_in_a_row',      2, 2, 2, 5),
  ('ANALYSIS_MEDICATION', 'low_mood_days',        3, 3, 2, 7),
  ('ANALYSIS_MEDICATION', 'low_energy_max',       2, 2, 1, 3),   -- journal energy at or below this is "low"
  ('ANALYSIS_MEDICATION', 'low_mood_max',         2, 2, 1, 3),
  ('ANALYSIS_MEDICATION', 'weekly_count_weekday', 0, 0, 0, 6),   -- the weekly count on Mondays
  ('ANALYSIS_MEDICATION', 'learn_time_min_days',  5, 5, 3, 14)
ON CONFLICT (rule_id, name) DO NOTHING;
