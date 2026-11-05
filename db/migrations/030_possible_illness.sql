-- E5: possible-illness flag. HRV below its band, resting HR above its band, and (if ingested)
-- raised overnight breathing rate, with no load spike to explain them. Message: "Your body may
-- be fighting something; take an easy day." Never a diagnosis. Shadow first. Re-running is safe.

INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days,
                            status, inputs) VALUES
  ('RECOV_POSSIBLE_ILLNESS', 'recovery', 'HRV below and resting HR above their bands with no load spike: an easy day',
   'Wearable resting HR, HRV, and breathing-rate changes often come before illness symptoms.',
   'research', 'Radin et al. 2020, Lancet Digit Health; Mishra et al. 2020, Nat Biomed Eng', 30, 'shadow',
   '{ln_hrv,resting_hr,resp_rate}')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('RECOV_POSSIBLE_ILLNESS', 'resp_z',           1.5, 1.5, 1.0, 3.0),   -- breathing rate above my baseline by this many SDs
  ('RECOV_POSSIBLE_ILLNESS', 'load_spike_pct',    25,  25,  10,  60),   -- weekly load rise that would explain it instead
  ('RECOV_POSSIBLE_ILLNESS', 'easy_rpe',           3,   3,   2,   4)
ON CONFLICT (rule_id, name) DO NOTHING;

INSERT INTO knowledge.feature_quality (feature, tier, note) VALUES
  ('resp_rate', 'fair', 'overnight breathing rate (Garmin)')
ON CONFLICT (feature) DO NOTHING;
