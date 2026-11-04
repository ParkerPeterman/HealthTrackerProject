-- A8: performance tests logged from the Train tab (max HR, 5 km time trial, sprints,
-- jumps). Values are stored in base units: bpm, seconds, centimetres. Re-running is safe.

CREATE TABLE IF NOT EXISTS core.performance_test (
    id          BIGSERIAL PRIMARY KEY,
    date        DATE NOT NULL,
    test        TEXT NOT NULL CHECK (test IN ('max_hr', 'time_trial_5k', 'sprint_10m', 'sprint_30m',
                                             'cmj', 'broad_jump')),
    value       NUMERIC(8,2) NOT NULL CHECK (value > 0),  -- the result that counts (best trial)
    unit        TEXT NOT NULL,                            -- 'bpm', 's', 'cm'
    trials      NUMERIC(8,2)[],                           -- every attempt, when there were several
    chest_strap BOOLEAN NOT NULL DEFAULT FALSE,           -- max HR: chest strap rather than wrist
    method      TEXT,                                     -- 'slow-motion video', 'partner stopwatch', 'jump app', 'wall reach'
    conditions  TEXT,                                     -- surface, warm-up, how it felt
    workout_id  BIGINT REFERENCES core.workout(id) ON DELETE SET NULL,
    notes       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS performance_test_by_test ON core.performance_test (test, date);
