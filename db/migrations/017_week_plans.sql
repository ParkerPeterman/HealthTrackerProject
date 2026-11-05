-- F3: the weekly plan generator. Plans are built from the season phase's template, placed
-- by a greedy placer under spacing rules, injury restrictions, and the ramp caps, and stored
-- as versions. Each planned session carries one structured reason and its expected load on
-- every A6 channel. Last week's plan is graded on adherence and response. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.week_plan (
    id            BIGSERIAL PRIMARY KEY,
    week_start    DATE NOT NULL,              -- Monday
    version       INT NOT NULL,
    phase         TEXT NOT NULL,
    inputs        JSONB NOT NULL,             -- everything the generator saw
    summary       JSONB NOT NULL,             -- targets, caps, planned totals, dropped slots
    plan_hash     TEXT NOT NULL,              -- same inputs, same plan: no new version
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_at TIMESTAMPTZ,
    -- graded after the week
    graded_at     TIMESTAMPTZ,
    adherence_pct NUMERIC(5,1),               -- planned (non-optional) sessions done
    key_done      SMALLINT,
    key_planned   SMALLINT,
    load_planned  NUMERIC(8,1),
    load_actual   NUMERIC(8,1),
    response      JSONB,                      -- HRV and resting-HR status at the end of the week, soreness, flags
    UNIQUE (week_start, version)
);
CREATE INDEX IF NOT EXISTS week_plan_current ON analytics.week_plan (week_start) WHERE superseded_at IS NULL;

ALTER TABLE core.planned_session
    ADD COLUMN IF NOT EXISTS plan_id               BIGINT REFERENCES analytics.week_plan(id),
    ADD COLUMN IF NOT EXISTS reason                JSONB,     -- why this session, this day, this size
    ADD COLUMN IF NOT EXISTS optional              BOOLEAN NOT NULL DEFAULT FALSE,  -- Fri-Sun bonus slots
    ADD COLUMN IF NOT EXISTS key                   BOOLEAN NOT NULL DEFAULT FALSE,  -- the week's priority sessions
    ADD COLUMN IF NOT EXISTS planned_internal_load NUMERIC(7,1),
    ADD COLUMN IF NOT EXISTS planned_sprint_m      SMALLINT,
    ADD COLUMN IF NOT EXISTS planned_plyo_contacts SMALLINT;

-- Minutes of hard work inside a session (the intervals, not the warm-up), for the planned
-- intensity mix (D4). Everything else in a run counts as easy.
ALTER TABLE knowledge.session_library ADD COLUMN IF NOT EXISTS hard_min SMALLINT NOT NULL DEFAULT 0;
UPDATE knowledge.session_library SET hard_min = v.m
FROM (VALUES ('vo2_3x3', 9), ('vo2_4x3', 12), ('vo2_3x4', 12), ('vo2_4x4', 16), ('tempo_run', 25),
             ('hill_repeats', 12), ('repeated_sprints', 6), ('game_like_intervals', 18)) AS v(code, m)
WHERE session_library.code = v.code;

-- The day after a weekend game: sprint technique instead of maximal sprints.
INSERT INTO knowledge.session_library (code, name, category, purpose, sport, plan_type, hard, addon, target,
    target_pct_lo, target_pct_hi, rpe, duration_min_lo, duration_min_hi, plyo_contacts, sprint_m, shoulder_min, activities,
    exercises, structure, equipment, travel_ok, outdoor, weather_swap, phases, prerequisites, progression_group,
    level, day_order, recovery_hours, source_ref) VALUES
  ('speed_technique', 'Sprint technique', 'speed_jump', 'Sprint mechanics without maximal efforts', 'speed', 'easy',
   FALSE, FALSE, 'Smooth, up to about 80% speed', NULL, NULL, 4, 15, 20, 0, 0, 0, '{running}', '[]',
   'A-skips, B-skips, wall drills, then 4-6 relaxed build-ups to about 80%. Nothing maximal.',
   '{}', TRUE, TRUE, NULL, NULL, '{}', NULL, NULL, 10, 0, NULL)
ON CONFLICT (code) DO NOTHING;

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('ANALYSIS_PLAN', 'analysis', 'Weekly plan generator settings', NULL, 'heuristic',
   'Brief F3; ramp caps from TRAIN_RUN_RAMP and TRAIN_CHANNEL_RAMP', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_PLAN', 'progression_factor',  1.05, 1.05, 1.0,  1.15),  -- target vs chronic load when recovery is normal
  ('ANALYSIS_PLAN', 'caution_factor',      0.85, 0.85, 0.6,  1.0),   -- after recovery flags or HRV below its band
  ('ANALYSIS_PLAN', 'deload_factor',       0.65, 0.65, 0.4,  0.9),
  ('ANALYSIS_PLAN', 'total_allowance',      100,  100,   0,  400),   -- absolute weekly increase allowed from a low base
  ('ANALYSIS_PLAN', 'min_sprint_m',          40,   40,  20,  100),   -- below this a sprint session becomes technique
  ('ANALYSIS_PLAN', 'min_plyo_contacts',     10,   10,   5,   30),
  ('ANALYSIS_PLAN', 'strict_spacing',         0,    0,   0,    1)    -- 1: no hard day ever next to another
ON CONFLICT (rule_id, name) DO NOTHING;
