-- Starter exercise library, so the lift logger isn't empty on a fresh install.
-- Traps count as upper_back (the schema has no separate trap group).
-- Re-running is safe: existing names and muscle links are left alone.

INSERT INTO core.exercise (name, movement_pattern, is_main_lift) VALUES
  -- Chest
  ('Bench press',               'horizontal_push', TRUE),
  ('Chest press',               'horizontal_push', FALSE),
  ('Chest fly',                 'horizontal_push', FALSE),
  -- Shoulders
  ('Overhead press',            'vertical_push',   TRUE),
  ('Lateral raise',             'other',           FALSE),
  ('Upright cable row',         'vertical_pull',   FALSE),
  -- Triceps
  ('Triceps pushdown',          'other',           FALSE),
  ('Overhead triceps extension','other',           FALSE),
  -- Upper back
  ('Barbell row',               'horizontal_pull', FALSE),
  ('Machine row',               'horizontal_pull', FALSE),
  ('Shrug',                     'other',           FALSE),
  -- Lats
  ('Pull-up',                   'vertical_pull',   FALSE),
  ('Cable pullover',            'vertical_pull',   FALSE),
  -- Biceps
  ('Dumbbell curl',             'other',           FALSE),
  -- Legs
  ('Back squat',                'squat',           TRUE),
  ('Bulgarian split squat',     'lunge',           FALSE),
  ('Deadlift',                  'hinge',           TRUE),
  ('Romanian deadlift',         'hinge',           FALSE),
  ('Nordic curl',               'hinge',           FALSE),
  ('Standing calf raise',       'other',           FALSE),
  ('Copenhagen plank',          'core',            FALSE),
  ('Box jump',                  'plyometric',      FALSE),
  -- Core
  ('Plank',                     'core',            FALSE)
ON CONFLICT (name) DO NOTHING;

INSERT INTO core.exercise_muscle (exercise_id, muscle_group, role, set_credit)
SELECT e.id, v.muscle, v.role, v.credit
FROM (VALUES
  ('Bench press',                'chest',      'primary',   1.0),
  ('Bench press',                'triceps',    'secondary', 0.5),
  ('Bench press',                'shoulders',  'secondary', 0.5),
  ('Chest press',                'chest',      'primary',   1.0),
  ('Chest press',                'triceps',    'secondary', 0.5),
  ('Chest press',                'shoulders',  'secondary', 0.5),
  ('Chest fly',                  'chest',      'primary',   1.0),
  ('Chest fly',                  'shoulders',  'secondary', 0.5),
  ('Overhead press',             'shoulders',  'primary',   1.0),
  ('Overhead press',             'triceps',    'secondary', 0.5),
  ('Lateral raise',              'shoulders',  'primary',   1.0),
  ('Upright cable row',          'shoulders',  'primary',   1.0),
  ('Upright cable row',          'upper_back', 'secondary', 0.5),
  ('Triceps pushdown',           'triceps',    'primary',   1.0),
  ('Overhead triceps extension', 'triceps',    'primary',   1.0),
  ('Barbell row',                'upper_back', 'primary',   1.0),
  ('Barbell row',                'lats',       'secondary', 0.5),
  ('Barbell row',                'biceps',     'secondary', 0.5),
  ('Machine row',                'upper_back', 'primary',   1.0),
  ('Machine row',                'lats',       'secondary', 0.5),
  ('Machine row',                'biceps',     'secondary', 0.5),
  ('Shrug',                      'upper_back', 'primary',   1.0),
  ('Pull-up',                    'lats',       'primary',   1.0),
  ('Pull-up',                    'biceps',     'secondary', 0.5),
  ('Cable pullover',             'lats',       'primary',   1.0),
  ('Dumbbell curl',              'biceps',     'primary',   1.0),
  ('Back squat',                 'quads',      'primary',   1.0),
  ('Back squat',                 'glutes',     'primary',   1.0),
  ('Back squat',                 'adductors',  'secondary', 0.5),
  ('Bulgarian split squat',      'quads',      'primary',   1.0),
  ('Bulgarian split squat',      'glutes',     'primary',   1.0),
  ('Deadlift',                   'hamstrings', 'primary',   1.0),
  ('Deadlift',                   'glutes',     'primary',   1.0),
  ('Deadlift',                   'upper_back', 'secondary', 0.5),
  ('Romanian deadlift',          'hamstrings', 'primary',   1.0),
  ('Romanian deadlift',          'glutes',     'secondary', 0.5),
  ('Nordic curl',                'hamstrings', 'primary',   1.0),
  ('Standing calf raise',        'calves',     'primary',   1.0),
  ('Copenhagen plank',           'adductors',  'primary',   1.0),
  ('Copenhagen plank',           'core',       'secondary', 0.5),
  ('Box jump',                   'quads',      'secondary', 0.5),
  ('Box jump',                   'calves',     'secondary', 0.5),
  ('Plank',                      'core',       'primary',   1.0)
) AS v(name, muscle, role, credit)
JOIN core.exercise e ON e.name = v.name
ON CONFLICT (exercise_id, muscle_group) DO NOTHING;
