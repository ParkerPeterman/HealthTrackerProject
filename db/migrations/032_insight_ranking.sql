-- C1-C4: insight quality. Candidates (B1 estimates plus thresholds, timing, interactions,
-- progress, and regularity) are scored by P(meaningful) x novelty x actionability x priority
-- weight + a recency boost; at most three a week are shown, and an insight returns only when
-- it moved materially. Every component is stored for audit. Section 13: a two-option rating
-- ("new to me" / "knew it") on shown insights. Shadow until COMP_INSIGHT_RANKING is turned on
-- (brief: Jan 4). Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.weekly_insight (
    week_start    DATE NOT NULL,
    key           TEXT NOT NULL,          -- stable across weeks: effect:<hypothesis id>, threshold:..., progress:...
    kind          TEXT NOT NULL CHECK (kind IN ('effect', 'threshold', 'timing', 'interaction', 'progress',
                                                'regularity', 'anomaly', 'follow_through')),
    text          TEXT NOT NULL,          -- rendered by code from the record
    p             NUMERIC(4,3) NOT NULL,
    estimate      NUMERIC(10,3),
    novelty       NUMERIC(4,3) NOT NULL,
    actionability NUMERIC(4,3) NOT NULL,
    priority      NUMERIC(4,3) NOT NULL,
    recency       NUMERIC(4,3) NOT NULL,
    moved         BOOLEAN NOT NULL,
    score         NUMERIC(6,4) NOT NULL,
    shown         BOOLEAN NOT NULL,
    record        JSONB NOT NULL,
    rating        TEXT CHECK (rating IN ('new', 'knew')),
    PRIMARY KEY (week_start, key)
);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_INSIGHT_RANKING', 'analysis', 'Ranked weekly insights (1-3) in coaching and the app (C1-C4)', NULL, 'heuristic',
   'Brief C1-C4', 30, 'shadow')
ON CONFLICT (rule_id) DO NOTHING;
