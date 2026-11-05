-- E4: long-term trends and weekly activity against the WHO 2020 guidelines. Monthly
-- state-space trends for resting HR, HRV, Garmin VO2 max, and weight (gate: 60 days); weekly
-- moderate and vigorous minutes (Garmin intensity minutes plus manual sessions by effort) and
-- muscle-strengthening days. Change points come from G3. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.long_trend (
    date        DATE NOT NULL,
    metric      TEXT NOT NULL CHECK (metric IN ('resting_hr', 'hrv', 'vo2max', 'weight_kg')),
    level       NUMERIC(8,2),
    lo80        NUMERIC(8,2),
    hi80        NUMERIC(8,2),
    slope_month NUMERIC(8,3),
    slope_lo80  NUMERIC(8,3),
    slope_hi80  NUMERIC(8,3),
    n_obs       INT NOT NULL,
    PRIMARY KEY (date, metric)
);

CREATE TABLE IF NOT EXISTS analytics.health_weekly (
    week_start      DATE PRIMARY KEY,
    moderate_min    INT NOT NULL,
    vigorous_min    INT NOT NULL,
    equivalent_min  INT NOT NULL,        -- moderate + 2 x vigorous
    strength_days   SMALLINT NOT NULL,
    meets_aerobic   BOOLEAN NOT NULL,    -- 150+ equivalent minutes
    meets_strength  BOOLEAN NOT NULL,    -- 2+ muscle-strengthening days
    days_with_data  SMALLINT NOT NULL
);
