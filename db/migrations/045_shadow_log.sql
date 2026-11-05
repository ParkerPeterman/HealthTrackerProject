-- Section 13: a weekly log of where the old and new logic disagree (HRV status, illness,
-- the daily adjustment, readiness, effect estimates, rule grading), for the owner. Evidence
-- for switching a shadow component on; it changes nothing by itself. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.shadow_log (
    week_start  DATE NOT NULL,
    component   TEXT NOT NULL,
    n_compared  INT NOT NULL,
    n_disagree  INT NOT NULL,
    examples    JSONB NOT NULL DEFAULT '[]',
    summary     TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (week_start, component)
);
