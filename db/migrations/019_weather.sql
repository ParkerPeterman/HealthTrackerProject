-- A7: weather from Open-Meteo. Free for non-commercial use, no key, attribution required
-- ("Weather data by Open-Meteo.com"). Only coordinates rounded to 0.1 degree (about 11 km)
-- and dates are sent; GPS tracks never leave the machine. Every response is cached in
-- raw.ingest, so recomputing gives the same result. Re-running is safe.

ALTER TABLE raw.ingest DROP CONSTRAINT IF EXISTS ingest_source_check;
ALTER TABLE raw.ingest ADD CONSTRAINT ingest_source_check
    CHECK (source IN ('garmin', 'garmin_fit', 'cronometer_import', 'app', 'open_meteo'));

-- The archive (ERA5 reanalysis) lags real time by several days; until it has a day, the
-- forecast endpoint's recent past stands in. Archive rows always win.
CREATE TABLE IF NOT EXISTS core.weather_hourly (
    lat          NUMERIC(4,1) NOT NULL,
    lon          NUMERIC(5,1) NOT NULL,
    ts           TIMESTAMPTZ NOT NULL,
    temp_c       NUMERIC(4,1),
    dew_point_c  NUMERIC(4,1),
    wind_kmh     NUMERIC(5,1),
    precip_mm    NUMERIC(5,1),
    snowfall_cm  NUMERIC(5,1),
    weather_code SMALLINT,
    source       TEXT NOT NULL CHECK (source IN ('archive', 'forecast')),
    PRIMARY KEY (lat, lon, ts)
);

CREATE TABLE IF NOT EXISTS core.weather_daily (
    lat           NUMERIC(4,1) NOT NULL,
    lon           NUMERIC(5,1) NOT NULL,
    date          DATE NOT NULL,          -- local date
    tmin_c        NUMERIC(4,1),
    tmax_c        NUMERIC(4,1),
    precip_mm     NUMERIC(5,1),
    snowfall_cm   NUMERIC(5,1),
    wind_max_kmh  NUMERIC(5,1),
    weather_code  SMALLINT,
    source        TEXT NOT NULL CHECK (source IN ('archive', 'forecast')),
    PRIMARY KEY (lat, lon, date)
);

-- A5: the weather at each outdoor session's midpoint (home, or the rounded start point on a travel day).
ALTER TABLE analytics.session_features
    ADD COLUMN IF NOT EXISTS temp_c         NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS dew_point_c    NUMERIC(4,1),
    ADD COLUMN IF NOT EXISTS wind_kmh       NUMERIC(5,1),
    ADD COLUMN IF NOT EXISTS precip_mm      NUMERIC(5,1),
    ADD COLUMN IF NOT EXISTS weather_source TEXT;

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('ANALYSIS_WEATHER', 'analysis', 'Weather: archive lag and the icy / very cold thresholds for plan swaps', NULL,
   'heuristic', 'Open-Meteo (ERA5 archive, forecast API)', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_WEATHER', 'archive_lag_days',   6,   6,   2,  14),
  ('ANALYSIS_WEATHER', 'very_cold_tmax_c', -10, -10, -25,   0),   -- outdoor intervals move indoors
  ('ANALYSIS_WEATHER', 'icy_tmin_c',         0,   0,  -5,   3),   -- with precipitation: icy
  ('ANALYSIS_WEATHER', 'icy_precip_mm',      1,   1, 0.2,   5)
ON CONFLICT (rule_id, name) DO NOTHING;
