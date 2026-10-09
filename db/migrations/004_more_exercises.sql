-- More leg exercises for the starter library. Re-running is safe.

INSERT INTO core.exercise (name, movement_pattern, is_main_lift) VALUES
  ('Quad extension',         'other', FALSE),
  ('Seated hamstring curl',  'other', FALSE),
  ('Lying hamstring curl',   'other', FALSE),
  ('Goblet squat',           'squat', FALSE)
ON CONFLICT (name) DO NOTHING;

INSERT INTO core.exercise_muscle (exercise_id, muscle_group, role, set_credit)
SELECT e.id, v.muscle, v.role, v.credit
FROM (VALUES
  ('Quad extension',        'quads',      'primary',   1.0),
  ('Seated hamstring curl', 'hamstrings', 'primary',   1.0),
  ('Lying hamstring curl',  'hamstrings', 'primary',   1.0),
  ('Goblet squat',          'quads',      'primary',   1.0),
  ('Goblet squat',          'glutes',     'primary',   1.0),
  ('Goblet squat',          'core',       'secondary', 0.5)
) AS v(name, muscle, role, credit)
JOIN core.exercise e ON e.name = v.name
ON CONFLICT (exercise_id, muscle_group) DO NOTHING;
