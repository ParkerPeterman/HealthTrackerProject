-- A3: baselines that don't skew themselves; A2: HRV and resting-HR status bands.
-- Re-running is safe.

ALTER TABLE analytics.daily_features
    ADD COLUMN IF NOT EXISTS ln_hrv    NUMERIC(5,3),   -- ln(nightly HRV in ms)
    ADD COLUMN IF NOT EXISTS resp_rate NUMERIC(4,1);   -- overnight breaths per minute

-- One row per day and metric. The baseline always leaves out the acute (latest 7-day)
-- window it is compared against, uses median and MAD, and says how many eligible
-- days it rests on (base_n) and how mature it is (0-1).
CREATE TABLE IF NOT EXISTS analytics.metric_status (
    date         DATE NOT NULL,
    metric       TEXT NOT NULL,          -- 'ln_hrv', 'resting_hr', 'sleep_total_min', 'sleep_midpoint_min', 'resp_rate'
    value        NUMERIC(9,3),           -- that day's value
    acute        NUMERIC(9,3),           -- 7-day rolling mean (highest and lowest night trimmed when 5+ nights)
    acute_n      SMALLINT,
    cv_7d        NUMERIC(6,2),           -- coefficient of variation over the 7 days, %
    base_center  NUMERIC(9,3),           -- median of eligible days in the baseline window
    base_scale   NUMERIC(9,3),           -- 1.4826 x MAD
    base_n       SMALLINT,               -- eligible days the baseline rests on
    swc          NUMERIC(9,3),           -- smallest worthwhile change (bands only)
    band_lo      NUMERIC(9,3),
    band_hi      NUMERIC(9,3),
    status       TEXT CHECK (status IN ('below', 'within', 'above')),
    cv_trend     TEXT CHECK (cv_trend IN ('rising', 'stable', 'falling')),
    maturity     NUMERIC(4,2),           -- 0-1: how settled "normal" is (resets after a medication dose change)
    widened      BOOLEAN NOT NULL DEFAULT FALSE,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (date, metric)
);

INSERT INTO analytics.rule (rule_id, domain, description, evidence_level, source_ref, min_history_days, status) VALUES
  ('ANALYSIS_BASELINE', 'analysis', 'Baseline and HRV/resting-HR band settings', 'research',
   'Plews et al. (7-day ln rMSSD vs SWC); robust baselines', 0, 'active'),
  -- Component switch: the bands reach coaching only when this is active (about 30 days of data).
  ('COMP_HRV_BANDS', 'analysis', 'Show HRV and resting-HR normal bands in coaching (A2)', 'research',
   'Plews et al.', 30, 'shadow')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_BASELINE', 'acute_days',          7,   7,   5,  10),
  ('ANALYSIS_BASELINE', 'acute_min_nights',    4,   4,   3,   6),
  ('ANALYSIS_BASELINE', 'baseline_days',      60,  60,  28,  90),
  ('ANALYSIS_BASELINE', 'band_min_days',      28,  28,  14,  60),   -- eligible days before a band is drawn
  ('ANALYSIS_BASELINE', 'swc_factor',        0.5, 0.5, 0.2, 1.0),  -- SWC = factor x SD of the 7-day average
  ('ANALYSIS_BASELINE', 'reset_widen_days',   28,  28,   7,  56),   -- after a medication dose change (H3)
  ('ANALYSIS_BASELINE', 'reset_widen_factor', 2.0, 2.0, 1.0, 3.0)
ON CONFLICT (rule_id, name) DO NOTHING;
