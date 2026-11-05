-- F7: return to ultimate. Pre-season builds max-velocity sprinting and cutting back up before
-- full games: weeks 1-2 accelerations only, weeks 3-4 add max velocity and cutting, week 5 on
-- everything. Each library session names the first stage it fits; sprint metres are scaled
-- 0.5 / 0.75 / 1.0 by stage before the ramp caps. Re-running is safe.

ALTER TABLE knowledge.session_library
    ADD COLUMN IF NOT EXISTS return_stage SMALLINT NOT NULL DEFAULT 1 CHECK (return_stage BETWEEN 1 AND 3);

UPDATE knowledge.session_library SET return_stage = 2
 WHERE code IN ('maxv_sprints', 'repeated_sprints', 'cod_drills');   -- top speed, or cutting
UPDATE knowledge.session_library SET return_stage = 3
 WHERE code = 'game_like_intervals';                                  -- points: sprints and cuts together

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_PLAN', 'return_maxv_week', 3, 3, 2, 6),   -- pre-season week that adds max velocity and cutting
  ('ANALYSIS_PLAN', 'return_full_week', 5, 5, 3, 8)    -- pre-season week that adds game-like work
ON CONFLICT (rule_id, name) DO NOTHING;
