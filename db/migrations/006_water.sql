-- Water intake: one row per drink logged from the app. The day comes from the
-- timestamp in the profile's time zone, like weights. Re-running is safe.

CREATE TABLE IF NOT EXISTS core.water_entry (
    id  BIGSERIAL PRIMARY KEY,
    ts  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ml  NUMERIC(6,1) NOT NULL CHECK (ml > 0 AND ml <= 5000)
);
CREATE INDEX IF NOT EXISTS water_entry_ts ON core.water_entry (ts);
