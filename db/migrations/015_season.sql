-- F1: season phases, a first-class input to planning and rule thresholds. I set the
-- first game; the other phases default from it (base -> holiday deload -> VO2 build ->
-- pre-season -> in-season -> transition). Re-running is safe.

CREATE TABLE IF NOT EXISTS core.season (
    id          SERIAL PRIMARY KEY,
    first_game  DATE NOT NULL,
    season_end  DATE,                    -- last game, when known
    base_start  DATE NOT NULL,           -- when structured base training starts
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.season_phase (
    season_id   INT NOT NULL REFERENCES core.season(id) ON DELETE CASCADE,
    phase       TEXT NOT NULL CHECK (phase IN ('base', 'deload', 'vo2_build', 'preseason', 'in_season', 'transition')),
    start_date  DATE NOT NULL,
    end_date    DATE,                    -- NULL = open-ended (in-season until the last game is set)
    source      TEXT NOT NULL DEFAULT 'default' CHECK (source IN ('default', 'manual')),
    PRIMARY KEY (season_id, phase)
);

ALTER TABLE analytics.daily_features ADD COLUMN IF NOT EXISTS season_phase TEXT;
