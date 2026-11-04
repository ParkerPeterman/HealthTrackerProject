-- A4: daily data-quality and context flags, one-tap tags, and per-hypothesis exclusions.
-- Re-running is safe.

-- Device local time minus UTC at sleep start (from Garmin's local timestamps), so
-- sleep timing uses the clock where I slept and time-zone changes can be flagged.
ALTER TABLE core.sleep ADD COLUMN IF NOT EXISTS tz_offset_min SMALLINT;

-- One-tap tags on the journal card. None of them leave baselines; sick and travel already do.
INSERT INTO core.event_type (code, unit, exclude_from_baseline, note) VALUES
  ('alcohol',         'drinks', FALSE, 'quick tag; the food log holds the dose'),
  ('high_stress',     NULL,     FALSE, 'quick tag'),
  ('dizzy',           NULL,     FALSE, 'quick tag'),
  ('dizzy_training',  NULL,     FALSE, 'dizziness during training'),
  ('hamstring_tight', NULL,     FALSE, 'quick tag'),
  ('groin_tweak',     NULL,     FALSE, 'one-tap injury event')
ON CONFLICT (code) DO NOTHING;

ALTER TABLE analytics.daily_features
    ADD COLUMN IF NOT EXISTS watch_overnight    BOOLEAN,   -- a night with sleep and HRV recorded
    ADD COLUMN IF NOT EXISTS sleep_plausible    BOOLEAN,   -- NULL when there is no night to judge
    ADD COLUMN IF NOT EXISTS is_sick            BOOLEAN,
    ADD COLUMN IF NOT EXISTS is_travel          BOOLEAN,
    ADD COLUMN IF NOT EXISTS tag_alcohol        BOOLEAN,   -- tagged, or drinks in the food log
    ADD COLUMN IF NOT EXISTS tag_stress         BOOLEAN,   -- tagged, or journal stress 4-5
    ADD COLUMN IF NOT EXISTS tag_dizzy          BOOLEAN,
    ADD COLUMN IF NOT EXISTS tag_dizzy_training BOOLEAN,
    ADD COLUMN IF NOT EXISTS tag_hamstring      BOOLEAN,
    ADD COLUMN IF NOT EXISTS tag_groin          BOOLEAN,
    ADD COLUMN IF NOT EXISTS tz_offset_min      SMALLINT,  -- where I slept, vs UTC
    ADD COLUMN IF NOT EXISTS tz_change          BOOLEAN,   -- slept in another time zone
    ADD COLUMN IF NOT EXISTS dst_week           BOOLEAN;   -- the Mon-Sun week has a daylight-saving change

-- Which days each hypothesis leaves out (sick, travel, injury, implausible, partial,
-- tz_change, dst_week, alcohol). The default matches what was excluded before, plus
-- nights with implausible sleep values.
ALTER TABLE analytics.hypothesis
    ADD COLUMN IF NOT EXISTS excludes TEXT[] NOT NULL DEFAULT '{sick,travel,injury,implausible}';
