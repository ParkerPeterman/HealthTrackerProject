-- G1 v2: readiness weights learned from how sessions went (effort vs plan, pace at the reference
-- HR vs the trend, my rating), priors at v1's equal weights. Fitted weekly; each day is scored by
-- the latest model fitted before it. v2 replaces v1 only past the gate and when it predicts
-- held-out sessions clearly better (MAE 2% lower, P >= 0.95 paired by day), and only once
-- COMP_READINESS_V2 is live (brief: shadow from Feb 8). The medication flag keeps v1's fixed
-- weight and flagged days are left out of the learning. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.readiness_model (
    computed_on  DATE PRIMARY KEY,
    n_sessions   INT NOT NULL,          -- session days used (flagged days left out)
    span_days    INT NOT NULL,
    gate         JSONB NOT NULL,        -- {span_days, n_sessions, needs_days, needs_sessions, open}
    weights      JSONB NOT NULL,        -- per component: beta (outcome SDs per SD, never negative), lo80, hi80, relative (mean 1), dropped
    parts        JSONB NOT NULL,        -- session days with each outcome part
    backtest     JSONB NOT NULL,        -- {n, mae_v1, mae_v2, p_better}
    adopted      BOOLEAN NOT NULL,      -- v2 clearly better than v1 (shown only once the switch is live)
    reason       TEXT NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE analytics.readiness_daily
    ADD COLUMN IF NOT EXISTS score_v1 SMALLINT CHECK (score_v1 BETWEEN 0 AND 100),
    ADD COLUMN IF NOT EXISTS v2       JSONB;  -- {score, composite, drivers, model_on, adopted}; score/version = the one in use

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_READINESS_V2', 'analysis', 'Learned readiness weights replace v1 once they predict sessions better (G1 v2)', NULL,
   'research', 'Conjugate NIG with priors at v1''s equal weights; rolling-origin backtest vs v1', 90, 'shadow')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('ANALYSIS_READINESS', 'v2_min_days',     90, 90, 60, 365),  -- days from the first to the last session day used
  ('ANALYSIS_READINESS', 'v2_min_sessions', 40, 40, 20, 300)   -- session days with an outcome and a v1 score
ON CONFLICT (rule_id, name) DO NOTHING;
