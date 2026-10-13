-- Goals that drive the system. A body-weight goal sets the calorie and protein
-- targets (through proposals I approve); performance, health, and habit goals are
-- tracked against daily_features. Re-running is safe.

ALTER TABLE core.goal
    ADD COLUMN IF NOT EXISTS metric        TEXT,          -- tracked measure, see healthtracker/goals/metrics.py
    ADD COLUMN IF NOT EXISTS direction     TEXT CHECK (direction IN ('decrease', 'increase', 'maintain')),
    ADD COLUMN IF NOT EXISTS start_value   NUMERIC(10,2), -- metric value when the goal was set
    ADD COLUMN IF NOT EXISTS start_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    ADD COLUMN IF NOT EXISTS rate_per_week NUMERIC(6,3),  -- body_weight: planned change in kg/week (negative = losing)
    ADD COLUMN IF NOT EXISTS days_per_week SMALLINT CHECK (days_per_week BETWEEN 1 AND 7),  -- habit: days to hit it
    ADD COLUMN IF NOT EXISTS exercise_id   BIGINT REFERENCES core.exercise(id),             -- e1RM goals
    ADD COLUMN IF NOT EXISTS achieved_date DATE,
    ADD COLUMN IF NOT EXISTS created_at    TIMESTAMPTZ NOT NULL DEFAULT now();

-- Weight goals from before metrics existed track the weight trend.
UPDATE core.goal SET metric = 'weight_kg' WHERE goal_type = 'body_weight' AND metric IS NULL;

-- One active body-weight goal at a time: it is what sets the calorie target.
UPDATE core.goal SET active = false
WHERE goal_type = 'body_weight' AND active
  AND id <> (SELECT max(id) FROM core.goal WHERE goal_type = 'body_weight' AND active);
CREATE UNIQUE INDEX IF NOT EXISTS goal_one_active_weight ON core.goal (goal_type)
    WHERE active AND goal_type = 'body_weight';

-- Calorie and protein target changes worked out from a weight goal. Nothing
-- changes until I approve; setting targets from the goal screen is logged here too.
CREATE TABLE IF NOT EXISTS analytics.target_proposal (
    id          BIGSERIAL PRIMARY KEY,
    goal_id     BIGINT REFERENCES core.goal(id) ON DELETE CASCADE,
    nutrient    TEXT NOT NULL,
    basis       TEXT NOT NULL CHECK (basis IN ('per_day', 'per_kg_bodyweight')),
    old_min     NUMERIC(8,2),
    old_max     NUMERIC(8,2),
    new_min     NUMERIC(8,2),
    new_max     NUMERIC(8,2),
    reason      TEXT NOT NULL,
    details     JSONB,                    -- the energy estimate behind it
    proposed_by TEXT NOT NULL DEFAULT 'goal_job' CHECK (proposed_by IN ('goal_job', 'manual')),
    approved    BOOLEAN,                  -- NULL = awaiting my approval
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS target_proposal_pending ON analytics.target_proposal (nutrient) WHERE approved IS NULL;
