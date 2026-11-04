-- F2: the session library. Every session the planner can place, with its purpose, target
-- effort, duration, expected load on each A6 channel, prerequisites, and a recovery cost
-- (a prior for now; D5 replaces it with what my own HRV and soreness show). Plus the
-- exercises those sessions need, a sore-shoulder tag and check-in, and the physio prompt.
-- Re-running is safe.

CREATE TABLE IF NOT EXISTS knowledge.session_library (
    code              TEXT PRIMARY KEY,       -- prefixes match session_features.type_from_code
    name              TEXT NOT NULL,
    category          TEXT NOT NULL CHECK (category IN
                        ('endurance', 'speed_jump', 'ultimate', 'strength', 'prehab', 'mobility')),
    purpose           TEXT NOT NULL,
    sport             TEXT NOT NULL,          -- planned_session.sport: run, speed, strength, prehab, mobility, throwing
    plan_type         TEXT NOT NULL CHECK (plan_type IN ('easy', 'moderate', 'hard', 'strength')),
    hard              BOOLEAN NOT NULL,       -- counts as a hard day for spacing
    addon             BOOLEAN NOT NULL DEFAULT FALSE,  -- added on to another session (strides, Nordics, ...)
    target            TEXT NOT NULL,          -- zone or effort, in words
    target_pct_lo     SMALLINT,               -- % of max HR, e.g. 90-95 for 4 x 4; NULL when effort-steered
    target_pct_hi     SMALLINT,
    rpe               SMALLINT NOT NULL CHECK (rpe BETWEEN 1 AND 10),
    duration_min_lo   SMALLINT NOT NULL,
    duration_min_hi   SMALLINT NOT NULL,
    -- Expected load on the A6 channels (internal load = minutes x RPE x my calibration factor)
    plyo_contacts     SMALLINT NOT NULL DEFAULT 0,
    sprint_m          SMALLINT NOT NULL DEFAULT 0,   -- prescribed distance at full speed
    shoulder_min      SMALLINT NOT NULL DEFAULT 0,
    activities        TEXT[] NOT NULL DEFAULT '{}',  -- matched against injury restrictions (running, sprinting, ...)
    exercises         JSONB NOT NULL DEFAULT '[]',   -- [{exercise, sets, reps, note?, swap?: {shoulder: name}}]
    structure         TEXT NOT NULL,          -- how to do it
    equipment         TEXT[] NOT NULL DEFAULT '{}',  -- '{}' = none
    travel_ok         BOOLEAN NOT NULL DEFAULT FALSE,-- short and equipment-light enough for a travel day
    outdoor           BOOLEAN NOT NULL DEFAULT FALSE,
    weather_swap      TEXT CHECK (weather_swap IN ('indoor', 'strength')),  -- icy or very cold (A7)
    phases            TEXT[],                 -- NULL = any phase
    prerequisites     JSONB NOT NULL DEFAULT '{}',   -- see planning/library.py
    progression_group TEXT,                   -- vo2, copenhagen: one level at a time
    level             SMALLINT,
    day_order         SMALLINT NOT NULL,      -- within a day: sprints and jumps before lifting
    -- Recovery cost: hours before another hard session or a game. Prior until D5 learns it.
    recovery_hours    SMALLINT NOT NULL DEFAULT 0,
    recovery_hrv_swc  NUMERIC(5,2),           -- D5: HRV change over the next 1-3 days, in SWC units
    recovery_source   TEXT NOT NULL DEFAULT 'prior' CHECK (recovery_source IN ('prior', 'learned')),
    recovery_n        INT,
    source_ref        TEXT,
    active            BOOLEAN NOT NULL DEFAULT TRUE
);

-- Planned sessions name their library entry, plus small add-ons done the same day.
ALTER TABLE core.planned_session
    ADD COLUMN IF NOT EXISTS session_code TEXT REFERENCES knowledge.session_library(code),
    ADD COLUMN IF NOT EXISTS addons       TEXT[];

-- ------------------------------------------------------------------ exercises
INSERT INTO core.exercise (name, movement_pattern, is_main_lift, notes) VALUES
  ('Trap-bar deadlift',            'hinge',           TRUE,  NULL),
  ('Landmine press',               'other',           FALSE, 'Angled press: the shoulder-friendly swap for overhead pressing'),
  ('Copenhagen adduction L1',      'core',            FALSE, 'Short lever: top knee on the bench'),
  ('Copenhagen adduction L2',      'core',            FALSE, 'Long lever: top ankle on the bench, partial range'),
  ('Copenhagen adduction L3',      'core',            FALSE, 'Long lever, full range: lower leg rises to the bench'),
  ('Band external rotation',       'other',           FALSE, 'Elbow at the side, rotate out'),
  ('Band internal rotation',       'other',           FALSE, NULL),
  ('Band pull-apart',              'horizontal_pull', FALSE, NULL),
  ('Prone Y-T-W raise',            'other',           FALSE, 'Scapular control'),
  ('Countermovement jump',         'plyometric',      FALSE, 'Best of 3 is the A8 test'),
  ('Broad jump',                   'plyometric',      FALSE, NULL),
  ('Depth jump',                   'plyometric',      FALSE, 'Pre-season only'),
  ('Single-leg balance',           'other',           FALSE, 'Seconds, not reps; doesn''t count toward muscle volume'),
  ('Single-leg calf raise',        'other',           FALSE, NULL),
  ('Single-leg Romanian deadlift', 'hinge',           FALSE, NULL),
  ('Step-up',                      'lunge',           FALSE, NULL),
  ('Push-up',                      'horizontal_push', FALSE, NULL)
ON CONFLICT (name) DO NOTHING;

INSERT INTO core.exercise_muscle (exercise_id, muscle_group, role, set_credit)
SELECT e.id, v.muscle, v.role, v.credit
FROM (VALUES
  ('Trap-bar deadlift',            'quads',      'primary',   1.0),
  ('Trap-bar deadlift',            'glutes',     'primary',   1.0),
  ('Trap-bar deadlift',            'hamstrings', 'secondary', 0.5),
  ('Trap-bar deadlift',            'upper_back', 'secondary', 0.5),
  ('Landmine press',               'shoulders',  'primary',   1.0),
  ('Landmine press',               'chest',      'secondary', 0.5),
  ('Landmine press',               'triceps',    'secondary', 0.5),
  ('Copenhagen adduction L1',      'adductors',  'primary',   1.0),
  ('Copenhagen adduction L1',      'core',       'secondary', 0.5),
  ('Copenhagen adduction L2',      'adductors',  'primary',   1.0),
  ('Copenhagen adduction L2',      'core',       'secondary', 0.5),
  ('Copenhagen adduction L3',      'adductors',  'primary',   1.0),
  ('Copenhagen adduction L3',      'core',       'secondary', 0.5),
  ('Band external rotation',       'shoulders',  'primary',   1.0),
  ('Band internal rotation',       'shoulders',  'primary',   1.0),
  ('Band pull-apart',              'upper_back', 'primary',   1.0),
  ('Band pull-apart',              'shoulders',  'secondary', 0.5),
  ('Prone Y-T-W raise',            'upper_back', 'primary',   1.0),
  ('Prone Y-T-W raise',            'shoulders',  'secondary', 0.5),
  ('Countermovement jump',         'quads',      'secondary', 0.5),
  ('Countermovement jump',         'glutes',     'secondary', 0.5),
  ('Countermovement jump',         'calves',     'secondary', 0.5),
  ('Broad jump',                   'glutes',     'secondary', 0.5),
  ('Broad jump',                   'quads',      'secondary', 0.5),
  ('Broad jump',                   'hamstrings', 'secondary', 0.5),
  ('Depth jump',                   'quads',      'secondary', 0.5),
  ('Depth jump',                   'calves',     'secondary', 0.5),
  ('Single-leg calf raise',        'calves',     'primary',   1.0),
  ('Single-leg Romanian deadlift', 'hamstrings', 'primary',   1.0),
  ('Single-leg Romanian deadlift', 'glutes',     'primary',   1.0),
  ('Step-up',                      'quads',      'primary',   1.0),
  ('Step-up',                      'glutes',     'primary',   1.0),
  ('Push-up',                      'chest',      'primary',   1.0),
  ('Push-up',                      'triceps',    'secondary', 0.5),
  ('Push-up',                      'shoulders',  'secondary', 0.5)
) AS v(name, muscle, role, credit)
JOIN core.exercise e ON e.name = v.name
ON CONFLICT (exercise_id, muscle_group) DO NOTHING;

-- ------------------------------------------------------------------ the library
-- Durations, sets, and reps are starting defaults, not research findings; the sources
-- say where a session's design comes from. day_order: 10 sprints/jumps, 20 runs, 30 lifts,
-- 40 injury prevention, 50 mobility.
INSERT INTO knowledge.session_library (code, name, category, purpose, sport, plan_type, hard, addon, target,
    target_pct_lo, target_pct_hi, rpe, duration_min_lo, duration_min_hi, plyo_contacts, sprint_m, shoulder_min, activities,
    exercises, structure, equipment, travel_ok, outdoor, weather_swap, phases, prerequisites, progression_group,
    level, day_order, recovery_hours, source_ref) VALUES
-- Endurance
  ('easy_run', 'Easy run', 'endurance', 'Aerobic volume without fatigue', 'run', 'easy', FALSE, FALSE,
   'Easy: zone 1-2, able to talk in full sentences', NULL, NULL, 3, 30, 45, 0, 0, 0, '{running}', '[]',
   'Relaxed pace the whole way. If HR drifts above zone 2, slow down or walk briefly.',
   '{}', TRUE, TRUE, 'indoor', NULL, '{}', NULL, NULL, 20, 0, NULL),
  ('long_run', 'Easy long run', 'endurance', 'Aerobic durability; decoupling marker on steady runs (D3)', 'run', 'moderate',
   FALSE, FALSE, 'Easy: zone 1-2', NULL, NULL, 4, 60, 75, 0, 0, 0, '{running}', '[]',
   'Steady and easy. Flat or gently rolling route, so pace at HR and decoupling can be read.',
   '{}', TRUE, TRUE, 'indoor', NULL, '{}', NULL, NULL, 20, 24, NULL),
  ('strides', 'Strides', 'endurance', 'Leg speed and running form at low cost', 'run', 'easy', FALSE, TRUE,
   'Fast but relaxed, not a sprint', NULL, NULL, 4, 5, 8, 0, 0, 0, '{running}', '[]',
   'After an easy run: 4-6 x 20 s smooth and fast, walk back to full recovery between.',
   '{}', TRUE, TRUE, NULL, NULL, '{}', NULL, NULL, 21, 0, NULL),
  ('tempo_run', 'Tempo run', 'endurance', 'Threshold: sustain a comfortably hard pace', 'run', 'hard', TRUE, FALSE,
   'Comfortably hard: zone 3-4, short phrases only', NULL, NULL, 6, 45, 60, 0, 0, 0, '{running}', '[]',
   '15 min easy, 2 x 10-15 min at threshold with 3 min easy between, 10 min easy.',
   '{}', FALSE, TRUE, 'indoor', NULL, '{"hrv_band": "not_below"}', NULL, NULL, 20, 48, NULL),
  ('hill_repeats', 'Hill repeats', 'endurance', 'Strength-endurance and economy uphill', 'run', 'hard', TRUE, FALSE,
   'Hard uphill: zone 4', NULL, NULL, 7, 40, 50, 0, 0, 0, '{running}', '[]',
   '15 min easy, 6-10 x 60-90 s hard uphill, jog down to recover, 10 min easy.',
   '{}', FALSE, TRUE, 'indoor', NULL, '{"hrv_band": "not_below", "no_tags_days": {"hamstring_tight": 2}}', NULL, NULL,
   20, 48, NULL),
  ('vo2_3x3', 'VO2 intervals 3 x 3 min', 'endurance', 'Raise VO2 max: first step toward 4 x 4', 'run', 'hard', TRUE, FALSE,
   'Hard: 90-95% of max HR', 90, 95, 8, 40, 45, 0, 0, 0, '{running}', '[]',
   '15 min easy, 3 x 3 min hard with 3 min jogging at about 70% between, 10 min easy. Wrist HR lags: steer the first minute of each rep by effort or pace and use HR only from minute 2 on.',
   '{}', FALSE, TRUE, 'indoor', NULL, '{"hrv_band": "not_below"}', 'vo2', 1, 20, 48,
   'Helgerud et al. 2007; Hov et al. 2023'),
  ('vo2_4x3', 'VO2 intervals 4 x 3 min', 'endurance', 'Raise VO2 max: building toward 4 x 4', 'run', 'hard', TRUE, FALSE,
   'Hard: 90-95% of max HR', 90, 95, 8, 42, 48, 0, 0, 0, '{running}', '[]',
   '15 min easy, 4 x 3 min hard with 3 min jogging at about 70% between, 10 min easy. Steer the first minute of each rep by effort; HR from minute 2 on.',
   '{}', FALSE, TRUE, 'indoor', NULL, '{"hrv_band": "not_below"}', 'vo2', 2, 20, 48,
   'Helgerud et al. 2007; Hov et al. 2023'),
  ('vo2_3x4', 'VO2 intervals 3 x 4 min', 'endurance', 'Raise VO2 max: building toward 4 x 4', 'run', 'hard', TRUE, FALSE,
   'Hard: 90-95% of max HR', 90, 95, 8, 43, 48, 0, 0, 0, '{running}', '[]',
   '15 min easy, 3 x 4 min hard with 3 min jogging at about 70% between, 10 min easy. Steer the first minute of each rep by effort; HR from minute 2 on.',
   '{}', FALSE, TRUE, 'indoor', NULL, '{"hrv_band": "not_below"}', 'vo2', 3, 20, 48,
   'Helgerud et al. 2007; Hov et al. 2023'),
  ('vo2_4x4', 'VO2 intervals 4 x 4 min', 'endurance', 'Raise VO2 max (the 4 x 4 protocol)', 'run', 'hard', TRUE, FALSE,
   'Hard: 90-95% of max HR', 90, 95, 8, 50, 55, 0, 0, 0, '{running}', '[]',
   '15 min easy, 4 x 4 min at 90-95% of max HR with 3 min active recovery at about 70%, 10 min easy. Steer the first minute of each rep by effort or pace; HR counts from minute 2 to 4.',
   '{}', FALSE, TRUE, 'indoor', NULL, '{"hrv_band": "not_below"}', 'vo2', 4, 20, 48,
   'Helgerud et al. 2007 (4 x 4 beat long slow and threshold running); Hov et al. 2023'),
-- Speed and jump
  ('accel_sprints', 'Acceleration sprints', 'speed_jump', 'Get faster over the first steps', 'speed', 'hard', TRUE, FALSE,
   'Maximal effort, full recovery', NULL, NULL, 6, 20, 30, 0, 120, 0, '{running,sprinting}', '[]',
   'Thorough warm-up with drills. 6-8 x 10-20 m from a standing start, walk back, at least 90 s between. Stop if a rep is slower or anything tightens.',
   '{}', FALSE, TRUE, 'strength', NULL, '{"no_tags_days": {"hamstring_tight": 2}}', NULL, NULL, 10, 48, NULL),
  ('maxv_sprints', 'Max-velocity sprints', 'speed_jump', 'Top speed', 'speed', 'hard', TRUE, FALSE,
   'Maximal effort, full recovery', NULL, NULL, 7, 25, 35, 0, 125, 0, '{running,sprinting}', '[]',
   'Warm-up plus 2-3 build-ups. 4-6 x flying 20-30 m after a 20 m build-up, 2-3 min walking between. Raise sprint volume gradually (ramp cap) rather than avoiding it.',
   '{}', FALSE, TRUE, 'strength', NULL, '{"no_tags_days": {"hamstring_tight": 2}}', NULL, NULL, 10, 48, NULL),
  ('jump_cmj_broad', 'Countermovement and broad jumps', 'speed_jump', 'Jump higher and farther', 'speed', 'hard', TRUE,
   FALSE, 'Maximal effort, full recovery', NULL, NULL, 6, 15, 20, 29, 0, 0, '{jumping}',
   '[{"exercise": "Countermovement jump", "sets": 4, "reps": "5"}, {"exercise": "Broad jump", "sets": 3, "reps": "3"}]',
   'Every rep at full effort, about 60 s between sets. Before any lifting.',
   '{}', FALSE, FALSE, NULL, NULL, '{}', NULL, NULL, 11, 24, NULL),
  ('depth_jumps', 'Depth jumps', 'speed_jump', 'Reactive strength', 'speed', 'hard', TRUE, FALSE,
   'Short ground contact, maximal effort', NULL, NULL, 7, 10, 15, 15, 0, 0, '{jumping}',
   '[{"exercise": "Depth jump", "sets": 3, "reps": "5"}]',
   'From a 30 cm box: step off, land, jump straight up as fast as possible. Full recovery between sets.',
   '{box}', FALSE, FALSE, NULL, '{preseason}', '{}', NULL, NULL, 12, 48, NULL),
-- Ultimate-specific
  ('repeated_sprints', 'Repeated sprints', 'ultimate', 'Repeat sprint ability for points', 'run', 'hard', TRUE, FALSE,
   'Maximal effort, short rest', NULL, NULL, 8, 35, 45, 0, 360, 0, '{running,sprinting}', '[]',
   'Warm-up plus build-ups. 2 sets of 6 x 30 m all-out, 20-25 s between reps, 4 min between sets.',
   '{}', FALSE, TRUE, 'strength', '{preseason,in_season}', '{"hrv_band": "not_below", "no_tags_days": {"hamstring_tight": 2}}',
   NULL, NULL, 10, 48, NULL),
  ('cod_drills', 'Change of direction', 'ultimate', 'Cutting speed and control', 'speed', 'hard', TRUE, FALSE,
   'Controlled, then full speed', NULL, NULL, 6, 25, 35, 0, 80, 0, '{running,cutting}', '[]',
   '45, 90 and 180 degree cuts: 2 reps each way at 70%, then 6-8 at full speed with full recovery.',
   '{}', FALSE, TRUE, 'strength', '{preseason,in_season}', '{"no_tags_days": {"groin_tweak": 3, "hamstring_tight": 2}}',
   NULL, NULL, 10, 48, NULL),
  ('game_like_intervals', 'Game-like intervals', 'ultimate', 'Conditioning that looks like points', 'run', 'hard', TRUE,
   FALSE, 'Hard: mixed sprinting, cutting and jogging', NULL, NULL, 8, 35, 45, 0, 200, 0, '{running,sprinting,cutting}', '[]',
   '6-8 x 2-3 min of cuts, sprints and jogging like a point, 2 min easy between.',
   '{}', FALSE, TRUE, 'strength', '{preseason,in_season}', '{"hrv_band": "not_below", "no_tags_days": {"groin_tweak": 3}}',
   NULL, NULL, 10, 48, NULL),
  ('throwing', 'Throwing', 'ultimate', 'Throwing volume, counted toward shoulder load', 'throwing', 'easy', FALSE, FALSE,
   'Easy to moderate', NULL, NULL, 3, 20, 40, 0, 0, 30, '{throwing}', '[]',
   'Warm up the shoulders first (bands). Short to long throws, both sides, both hands.',
   '{disc}', FALSE, TRUE, NULL, '{preseason,in_season}', '{"shoulder_ok": true}', NULL, NULL, 25, 0, NULL),
-- Strength
  ('strength_lower_heavy', 'Heavy lower-body strength', 'strength', 'Force for sprinting and jumping', 'strength',
   'strength', TRUE, FALSE, 'Heavy: 2 reps in reserve', NULL, NULL, 7, 45, 60, 0, 0, 0, '{lifting}',
   '[{"exercise": "Back squat", "sets": 4, "reps": "3-5", "alt": "Trap-bar deadlift"}, {"exercise": "Romanian deadlift", "sets": 3, "reps": "6-8"}, {"exercise": "Bulgarian split squat", "sets": 3, "reps": "6-8"}]',
   'After any sprints or jumps. Squat or trap-bar deadlift as the main lift, 2-3 min rest on it.',
   '{barbell}', FALSE, FALSE, NULL, NULL, '{}', NULL, NULL, 30, 48, NULL),
  ('strength_upper', 'Upper-body push and pull', 'strength', 'Balanced upper-body strength', 'strength', 'strength',
   FALSE, FALSE, 'Moderate: 2 reps in reserve', NULL, NULL, 6, 40, 50, 0, 0, 0, '{lifting}',
   '[{"exercise": "Bench press", "sets": 3, "reps": "5-8"}, {"exercise": "Overhead press", "sets": 3, "reps": "6-8", "swap": {"shoulder": "Landmine press"}}, {"exercise": "Pull-up", "sets": 3, "reps": "2 short of failure"}, {"exercise": "Barbell row", "sets": 3, "reps": "8"}]',
   'Alternate push and pull. Overhead pressing becomes landmine pressing while the shoulders are sore.',
   '{barbell}', FALSE, FALSE, NULL, NULL, '{}', NULL, NULL, 30, 24, NULL),
  ('strength_single_leg', 'Single-leg strength', 'strength', 'Single-leg force and control', 'strength', 'strength',
   FALSE, FALSE, 'Moderate: 2 reps in reserve', NULL, NULL, 6, 25, 35, 0, 0, 0, '{lifting}',
   '[{"exercise": "Bulgarian split squat", "sets": 3, "reps": "8"}, {"exercise": "Single-leg Romanian deadlift", "sets": 3, "reps": "8"}, {"exercise": "Step-up", "sets": 2, "reps": "10"}, {"exercise": "Single-leg calf raise", "sets": 3, "reps": "12"}]',
   'Each side. Dumbbells or bodyweight.',
   '{dumbbells}', FALSE, FALSE, NULL, NULL, '{}', NULL, NULL, 30, 24, NULL),
  ('strength_power', 'Power: fast heavy lifts', 'strength', 'Turn strength into speed', 'strength', 'strength', TRUE,
   FALSE, 'Fast reps, 3 in reserve', NULL, NULL, 7, 40, 50, 9, 0, 0, '{lifting,jumping}',
   '[{"exercise": "Box jump", "sets": 3, "reps": "3"}, {"exercise": "Trap-bar deadlift", "sets": 4, "reps": "3", "note": "as fast as possible"}, {"exercise": "Bulgarian split squat", "sets": 3, "reps": "5"}, {"exercise": "Landmine press", "sets": 3, "reps": "6"}, {"exercise": "Pull-up", "sets": 3, "reps": "5"}]',
   'Jumps first. Move the bar fast; stop the set when speed drops.',
   '{barbell,box}', FALSE, FALSE, NULL, '{vo2_build,preseason}', '{}', NULL, NULL, 30, 48, NULL),
  ('strength_maintenance', 'Maintenance strength', 'strength', 'Keep strength through the season', 'strength',
   'strength', FALSE, FALSE, 'Moderate: 3 reps in reserve', NULL, NULL, 5, 30, 40, 0, 0, 0, '{lifting}',
   '[{"exercise": "Trap-bar deadlift", "sets": 3, "reps": "3-5"}, {"exercise": "Bulgarian split squat", "sets": 2, "reps": "6"}, {"exercise": "Bench press", "sets": 2, "reps": "5"}, {"exercise": "Pull-up", "sets": 2, "reps": "6"}]',
   'Short and crisp; never to failure in-season.',
   '{barbell}', FALSE, FALSE, NULL, '{in_season,transition}', '{}', NULL, NULL, 30, 24, NULL),
-- Injury prevention (add-ons unless done alone on a travel day)
  ('copenhagen_l1', 'Copenhagen adduction, level 1', 'prehab', 'Groin: adductor strength', 'prehab', 'easy', FALSE, TRUE,
   'Controlled, a little hard by the last rep', NULL, NULL, 4, 5, 8, 0, 0, 0, '{}',
   '[{"exercise": "Copenhagen adduction L1", "sets": 2, "reps": "6-10 each side"}]',
   'Side plank with the top knee on a bench or chair; lift the hips and lower leg, lower slowly.',
   '{}', TRUE, FALSE, NULL, NULL, '{"no_tags_days": {"groin_tweak": 3}}', 'copenhagen', 1, 40, 0,
   'Harøy et al. 2019 (Adductor Strengthening Programme: 41% fewer groin problems)'),
  ('copenhagen_l2', 'Copenhagen adduction, level 2', 'prehab', 'Groin: adductor strength', 'prehab', 'easy', FALSE, TRUE,
   'Controlled, hard by the last rep', NULL, NULL, 5, 5, 8, 0, 0, 0, '{}',
   '[{"exercise": "Copenhagen adduction L2", "sets": 2, "reps": "6-10 each side"}]',
   'Top ankle on the bench (long lever), partial range.',
   '{}', TRUE, FALSE, NULL, NULL, '{"no_tags_days": {"groin_tweak": 3}}', 'copenhagen', 2, 40, 0,
   'Harøy et al. 2019'),
  ('copenhagen_l3', 'Copenhagen adduction, level 3', 'prehab', 'Groin: adductor strength', 'prehab', 'easy', FALSE, TRUE,
   'Controlled, hard by the last rep', NULL, NULL, 6, 5, 8, 0, 0, 0, '{}',
   '[{"exercise": "Copenhagen adduction L3", "sets": 3, "reps": "6-12 each side"}]',
   'Long lever, full range: the lower leg rises to touch the bench each rep.',
   '{}', TRUE, FALSE, NULL, NULL, '{"no_tags_days": {"groin_tweak": 3}}', 'copenhagen', 3, 40, 0,
   'Harøy et al. 2019'),
  ('nordics', 'Nordic hamstring curls', 'prehab', 'Hamstring: eccentric strength (a previous strain is a top risk factor)',
   'prehab', 'easy', FALSE, TRUE, 'Slow lowering, hard by the last rep', NULL, NULL, 6, 5, 8, 0, 0, 0, '{}',
   '[{"exercise": "Nordic curl", "sets": 2, "reps": "3-6"}]',
   'Feet anchored, lower as slowly as you can, push back up with the hands. 1-2 times a week all year.',
   '{}', FALSE, FALSE, NULL, NULL, '{"no_tags_days": {"hamstring_tight": 2}}', NULL, NULL, 40, 24,
   'Ultimate injury epidemiology (Khoo et al. 2021): Nordics and balance training'),
  ('shoulder_bands', 'Rotator cuff and scapular work', 'prehab', 'Shoulders: cuff strength and scapular control', 'prehab',
   'easy', FALSE, TRUE, 'Light band, controlled', NULL, NULL, 3, 10, 15, 0, 0, 0, '{}',
   '[{"exercise": "Band external rotation", "sets": 2, "reps": "12-15 each side"}, {"exercise": "Band internal rotation", "sets": 2, "reps": "12-15 each side"}, {"exercise": "Band pull-apart", "sets": 2, "reps": "15"}, {"exercise": "Prone Y-T-W raise", "sets": 2, "reps": "8"}]',
   'Light enough to stay pain-free. 2-3 times a week; a band fits in a bag, so it works on travel days.',
   '{band}', TRUE, FALSE, NULL, NULL, '{}', NULL, NULL, 40, 0,
   'Shoulder injuries are common in ultimate (ultimate injury epidemiology, brief F2)'),
  ('balance_ankle', 'Balance and ankle work', 'prehab', 'Ankle and knee control', 'prehab', 'easy', FALSE, TRUE,
   'Easy', NULL, NULL, 3, 8, 10, 0, 0, 0, '{}',
   '[{"exercise": "Single-leg balance", "sets": 3, "reps": "30 s each side"}, {"exercise": "Single-leg calf raise", "sets": 3, "reps": "12"}]',
   'Balance on one leg; progress to eyes closed or a soft surface.',
   '{}', TRUE, FALSE, NULL, NULL, '{}', NULL, NULL, 40, 0,
   'Khoo et al. 2021 (balance and proprioception training in ultimate)'),
  ('mobility', 'Mobility', 'mobility', 'Range of motion and an easy day', 'mobility', 'easy', FALSE, FALSE,
   'Easy', NULL, NULL, 2, 15, 20, 0, 0, 0, '{}', '[]',
   'Hips, ankles, thoracic spine and shoulders; slow, nothing forced.',
   '{}', TRUE, FALSE, NULL, NULL, '{}', NULL, NULL, 50, 0, NULL)
ON CONFLICT (code) DO NOTHING;

-- ------------------------------------------------------------------ shoulders
-- A sore-shoulder tag, and a 0-10 shoulder check-in shown each day while the shoulders are
-- flagged (a sore tag or check-in in the last week). An active shoulder injury uses the
-- injury check-in instead.
INSERT INTO core.event_type (code, unit, exclude_from_baseline, note) VALUES
  ('shoulder_sore', NULL,   FALSE, 'quick tag'),
  ('shoulder_pain', '0-10', FALSE, 'daily shoulder check-in while the shoulders are flagged')
ON CONFLICT (code) DO NOTHING;

ALTER TABLE analytics.daily_features
    ADD COLUMN IF NOT EXISTS tag_shoulder   BOOLEAN,    -- sore tag, or check-in at or above the threshold
    ADD COLUMN IF NOT EXISTS shoulder_pain  SMALLINT;   -- that day's check-in (0-10)

-- ------------------------------------------------------------------ rules
INSERT INTO analytics.rule (rule_id, domain, description, rationale, evidence_level, source_ref, min_history_days, status) VALUES
  ('INJ_PHYSIO_PROMPT', 'injury', 'Groin or shoulder problems keep coming back: suggest a physiotherapist assessment',
   'Groin and shoulder problems both recur. Shown once, and again only if they become more frequent.',
   'heuristic', 'Brief F2 (physio prompt)', 0, 'active'),
  ('INJ_SHOULDER_SWAP', 'injury', 'Shoulders sore on a lifting day: landmine presses instead of overhead pressing',
   'Keeps pressing in the plan while taking the shoulder out of the overhead position.',
   'heuristic', 'Brief F2 (shoulders)', 0, 'active'),
  ('ANALYSIS_PREHAB', 'analysis', 'Injury-prevention settings: shoulder flag, Copenhagen progression', NULL, 'heuristic',
   'Harøy et al. 2019 (Copenhagen progression)', 0, 'active')
ON CONFLICT (rule_id) DO NOTHING;

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('INJ_PHYSIO_PROMPT', 'window_days',          28, 28, 14, 90),
  ('INJ_PHYSIO_PROMPT', 'min_events',            2,  2,  1,  6),   -- tweaks or sore-shoulder days in the window
  ('INJ_PHYSIO_PROMPT', 'min_days_between',     28, 28,  7, 90),
  ('ANALYSIS_PREHAB',   'shoulder_flag_days',    7,  7,  3, 21),
  ('ANALYSIS_PREHAB',   'shoulder_pain_threshold', 3, 3, 1,  6),   -- check-in at or above this counts as sore
  ('ANALYSIS_PREHAB',   'shoulder_clear_checkins', 2, 2, 1,  5),   -- low check-ins in a row that clear the flag
  ('ANALYSIS_PREHAB',   'copenhagen_sessions_per_level', 6, 6, 3, 12),
  ('ANALYSIS_PREHAB',   'copenhagen_tweak_days', 14, 14, 7, 42)    -- a groin tweak this recent steps a level down
ON CONFLICT (rule_id, name) DO NOTHING;
