-- G4: unusual days explained. A morning whose HRV, resting HR, or sleep is 2+ robust SDs from my
-- trailing 28 days gets its drivers from the record: data quality and context flags (A4, E5),
-- my own credible effect estimates at their lags (B1) with how much each would move it, and
-- anything else unusual the day before. Unusual days and G3 shifts become 'anomaly' candidates
-- in the weekly ranking. Shadow with the ranking. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.anomaly (
    date             DATE NOT NULL,
    metric           TEXT NOT NULL CHECK (metric IN ('hrv', 'resting_hr', 'sleep_total_min')),
    value            NUMERIC(8,2) NOT NULL,
    baseline         NUMERIC(8,2) NOT NULL,   -- trailing 28-day median, the day left out
    z                NUMERIC(6,2) NOT NULL,   -- robust (MAD) SDs
    drivers          JSONB NOT NULL,          -- [{kind: quality|context|effect|unusual, what, ...}]
    explained_share  NUMERIC(4,2) NOT NULL,   -- share of the deviation my own estimates account for
    summary          TEXT NOT NULL,
    computed_on      DATE NOT NULL,
    PRIMARY KEY (date, metric)
);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_ANOMALIES', 'analysis', 'Explain unusual days from their record (G4)', NULL, 'heuristic', 'Brief G4', 28, 'shadow')
ON CONFLICT (rule_id) DO NOTHING;
