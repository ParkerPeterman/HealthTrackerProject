-- D5: learned response by session type. For each type, HRV and resting HR over the next two
-- days (in SWC units), next-day soreness and energy, shared across types with partial pooling.
-- Gate: about 90 days. Once COMP_SESSION_RESPONSE is live the library's recovery cost becomes
-- the learned one and the planner spaces sessions from games by it. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.session_response (
    computed_on     DATE NOT NULL,
    session_type    TEXT NOT NULL,
    outcome         TEXT NOT NULL,       -- hrv_swc, rhr_swc (next 2 days), soreness_next, energy_next
    n_sessions      INT NOT NULL,
    estimate        NUMERIC(8,3),        -- vs a day without that session type
    lo80 NUMERIC(8,3), hi80 NUMERIC(8,3),
    pooled_estimate NUMERIC(8,3),        -- shared across session types (B5)
    p_direction     NUMERIC(4,3),
    PRIMARY KEY (computed_on, session_type, outcome)
);

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('COMP_SESSION_RESPONSE', 'analysis', 'Learned recovery cost per session type feeds the planner (D5)', NULL,
   'heuristic', 'Brief D5', 90, 'shadow')
ON CONFLICT (rule_id) DO NOTHING;
