-- =====================================================================
-- Personal Health & Training System: schema v3 (PostgreSQL)
-- Full replacement for v2. Matches the project plan.
--
-- Changes from v2:
--   * Withings removed; weight is entered in the tracker app (source 'manual')
--   * Chest strap removed; games are logged manually in the app (is_game)
--   * Overlapping workouts resolved by source priority (garmin > manual)
--   * New: muscle_group, exercise, exercise_muscle; lift_set -> exercise
--   * New: injury, injury_checkin, injury_restriction
--   * rule.min_history_days keeps baseline rules dormant until data exists
--   * daily_features: wear coverage, partial-day flag, injury flags,
--     run performance; new weekly_muscle_volume and lift_progress tables
--   * Starter rule set seeded in shadow mode
--
-- Principle: CODE decides, LLM only phrases.
-- Nightly: ingest -> core -> daily_features -> grade yesterday ->
--          rules/flags -> recommendation + actions -> LLM message
-- Weekly:  hypotheses -> insights -> parameter tuning proposals
-- All loads are upserts on natural keys, so any step can be re-run.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS knowledge;

-- =====================================================================
-- RAW
-- =====================================================================
CREATE TABLE raw.ingest (
    id           BIGSERIAL PRIMARY KEY,
    source       TEXT NOT NULL CHECK (source IN ('garmin', 'garmin_fit', 'cronometer_import', 'app')),
    endpoint     TEXT NOT NULL,            -- 'sleep', 'hrv', 'activities', 'daily_summary', ...
    for_date     DATE,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload      JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    UNIQUE (source, endpoint, for_date, payload_hash)
);

-- =====================================================================
-- CORE: profile, goals, targets
-- =====================================================================
CREATE TABLE core.profile (
    id           SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    birth_date   DATE,
    sex          TEXT,
    height_cm    NUMERIC(5,1),
    timezone     TEXT NOT NULL DEFAULT 'UTC',   -- set to your local zone
    ref_hr_bpm   SMALLINT,                      -- reference HR for pace-at-HR metric
    notes        TEXT
);

CREATE TABLE core.goal (
    id           BIGSERIAL PRIMARY KEY,
    goal_type    TEXT NOT NULL CHECK (goal_type IN ('body_weight','performance','health','habit')),
    description  TEXT NOT NULL,
    target_value NUMERIC(10,2),
    unit         TEXT,
    target_date  DATE,
    active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE core.nutrient_target (
    id           BIGSERIAL PRIMARY KEY,
    nutrient     TEXT NOT NULL,            -- 'protein_g', 'fiber_g', 'kcal', 'iron_mg'
    basis        TEXT NOT NULL CHECK (basis IN ('per_day', 'per_kg_bodyweight')),
    evaluation   TEXT NOT NULL DEFAULT 'daily' CHECK (evaluation IN ('daily', 'weekly_avg')),
    min_value    NUMERIC(8,2),
    max_value    NUMERIC(8,2),
    source_ref   TEXT,
    valid_from   DATE NOT NULL DEFAULT CURRENT_DATE,
    active       BOOLEAN NOT NULL DEFAULT TRUE
);

-- =====================================================================
-- CORE: Garmin wellness
-- =====================================================================
CREATE TABLE core.sleep (
    date            DATE PRIMARY KEY,      -- wake-up date
    sleep_start     TIMESTAMPTZ,
    sleep_end       TIMESTAMPTZ,
    total_min       INT,
    deep_min        INT,
    rem_min         INT,
    light_min       INT,
    awake_min       INT,
    sleep_score     INT,
    overnight_hrv   NUMERIC(6,1),
    hrv_status      TEXT,
    resting_hr      INT,
    avg_spo2        NUMERIC(4,1),
    avg_resp_rate   NUMERIC(4,1),
    source          TEXT NOT NULL DEFAULT 'garmin'
);

CREATE TABLE core.daily_wellness (
    date               DATE PRIMARY KEY,
    steps              INT,
    active_kcal        INT,
    total_kcal         INT,
    resting_hr         INT,
    avg_stress         INT,
    body_battery_high  INT,
    body_battery_low   INT,
    intensity_min_mod  INT,
    intensity_min_vig  INT,
    wear_minutes       INT,                -- minutes with all-day HR data
    vo2max             NUMERIC(4,1),       -- Garmin estimate, when updated
    source             TEXT NOT NULL DEFAULT 'garmin'
);

-- =====================================================================
-- CORE: training
-- =====================================================================

-- One row per workout or game. Games are logged manually (watch is off).
-- When two sources overlap in time, the lower-priority row points to the
-- kept row via superseded_by and is ignored downstream. Priority: garmin > manual.
CREATE TABLE core.workout (
    id             BIGSERIAL PRIMARY KEY,
    source         TEXT NOT NULL CHECK (source IN ('garmin', 'manual')),
    source_id      TEXT,                   -- Garmin activity id; NULL for manual
    sport          TEXT NOT NULL,          -- 'ultimate', 'run', 'strength', 'bike', ...
    is_game        BOOLEAN NOT NULL DEFAULT FALSE,
    start_ts       TIMESTAMPTZ NOT NULL,
    duration_s     INT NOT NULL CHECK (duration_s > 0),
    distance_m     NUMERIC(10,1),
    avg_hr         INT,
    max_hr         INT,
    avg_cadence    INT,
    elev_gain_m    NUMERIC(7,1),
    kcal           INT,
    device_load    NUMERIC(7,1),           -- Garmin training load, if present
    rpe            SMALLINT CHECK (rpe BETWEEN 1 AND 10),
    rpe_source     TEXT CHECK (rpe_source IN ('garmin', 'manual')),
    feel           SMALLINT,               -- Garmin feel, normalized on ingest
    session_load   NUMERIC(8,1) GENERATED ALWAYS AS (rpe * duration_s / 60.0) STORED,
    superseded_by  BIGINT REFERENCES core.workout(id),
    notes          TEXT,
    UNIQUE (source, source_id)
);
CREATE INDEX ON core.workout (start_ts);

-- Workouts that count (overlaps removed)
CREATE VIEW core.v_workout AS
SELECT * FROM core.workout WHERE superseded_by IS NULL;

-- Optional per-second HR from FIT files (runs recorded on the watch)
CREATE TABLE core.hr_sample (
    workout_id  BIGINT NOT NULL REFERENCES core.workout(id) ON DELETE CASCADE,
    ts          TIMESTAMPTZ NOT NULL,
    hr          SMALLINT NOT NULL,
    speed_mps   NUMERIC(5,2),
    PRIMARY KEY (workout_id, ts)
);

-- Per-km or per-lap splits for runs
CREATE TABLE core.run_split (
    workout_id  BIGINT NOT NULL REFERENCES core.workout(id) ON DELETE CASCADE,
    split_no    SMALLINT NOT NULL,
    distance_m  NUMERIC(8,1) NOT NULL,
    duration_s  INT NOT NULL,
    avg_hr      INT,
    avg_cadence INT,
    PRIMARY KEY (workout_id, split_no)
);

-- Muscle groups: shared vocabulary for lifts, volume rules, and injuries
CREATE TABLE core.muscle_group (
    code        TEXT PRIMARY KEY,          -- 'quads', 'hamstrings', 'glutes', 'chest', ...
    body_region TEXT NOT NULL CHECK (body_region IN ('upper','lower','core'))
);

CREATE TABLE core.exercise (
    id               BIGSERIAL PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    movement_pattern TEXT NOT NULL CHECK (movement_pattern IN
                       ('squat','hinge','lunge','horizontal_push','vertical_push',
                        'horizontal_pull','vertical_pull','carry','core','plyometric','other')),
    is_main_lift     BOOLEAN NOT NULL DEFAULT FALSE,   -- tracked for estimated 1RM
    notes            TEXT
);

-- Which muscles an exercise works. Secondary muscles count as partial sets.
CREATE TABLE core.exercise_muscle (
    exercise_id   BIGINT NOT NULL REFERENCES core.exercise(id) ON DELETE CASCADE,
    muscle_group  TEXT NOT NULL REFERENCES core.muscle_group(code),
    role          TEXT NOT NULL CHECK (role IN ('primary','secondary')),
    set_credit    NUMERIC(3,2) NOT NULL DEFAULT 1.0,  -- e.g. 1.0 primary, 0.5 secondary
    PRIMARY KEY (exercise_id, muscle_group)
);

CREATE TABLE core.lift_set (
    id           BIGSERIAL PRIMARY KEY,
    workout_id   BIGINT NOT NULL REFERENCES core.workout(id) ON DELETE CASCADE,
    exercise_id  BIGINT NOT NULL REFERENCES core.exercise(id),
    set_no       SMALLINT NOT NULL,
    reps         SMALLINT NOT NULL CHECK (reps > 0),
    weight_kg    NUMERIC(6,2) NOT NULL CHECK (weight_kg >= 0),
    rir          SMALLINT CHECK (rir BETWEEN 0 AND 10),  -- reps in reserve
    is_warmup    BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (workout_id, exercise_id, set_no)
);

-- Planned sessions and scheduled games
CREATE TABLE core.planned_session (
    id                   BIGSERIAL PRIMARY KEY,
    date                 DATE NOT NULL,
    sport                TEXT NOT NULL,
    session_type         TEXT NOT NULL CHECK (session_type IN
                           ('game','hard','moderate','easy','strength','rest')),
    planned_duration_min INT,
    planned_rpe          SMALLINT CHECK (planned_rpe BETWEEN 1 AND 10),
    planned_load         NUMERIC(8,1) GENERATED ALWAYS AS (planned_rpe * planned_duration_min) STORED,
    focus_muscles        TEXT[],           -- muscle_group codes for strength days
    status               TEXT NOT NULL DEFAULT 'planned'
                           CHECK (status IN ('planned','done','skipped','modified')),
    linked_workout_id    BIGINT REFERENCES core.workout(id),
    origin               TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual','generated')),
    recommendation_id    BIGINT,           -- FK added below
    notes                TEXT
);
CREATE INDEX ON core.planned_session (date);

-- =====================================================================
-- CORE: injuries
-- =====================================================================
CREATE TABLE core.injury (
    id            BIGSERIAL PRIMARY KEY,
    body_part     TEXT NOT NULL,           -- 'knee', 'ankle', 'shoulder', 'hamstring'
    side          TEXT CHECK (side IN ('left','right','both','n/a')),
    injury_type   TEXT NOT NULL CHECK (injury_type IN
                    ('strain','sprain','tendon','contusion','soreness','other')),
    onset_date    DATE NOT NULL,
    mechanism     TEXT,                    -- 'cut on layout', 'gradual onset'
    status        TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','recovering','resolved')),
    resolved_date DATE,
    seen_professional BOOLEAN NOT NULL DEFAULT FALSE,
    notes         TEXT
);

-- Daily check-in for each active injury (0-10 pain, 1-5 function)
CREATE TABLE core.injury_checkin (
    date            DATE NOT NULL,
    injury_id       BIGINT NOT NULL REFERENCES core.injury(id) ON DELETE CASCADE,
    pain_rest       SMALLINT CHECK (pain_rest BETWEEN 0 AND 10),
    pain_activity   SMALLINT CHECK (pain_activity BETWEEN 0 AND 10),
    function_score  SMALLINT CHECK (function_score BETWEEN 1 AND 5),
    swelling        BOOLEAN,
    note            TEXT,
    PRIMARY KEY (date, injury_id)
);

-- What to avoid or limit while an injury is active.
-- Exactly one target column is set, matching target_type.
CREATE TABLE core.injury_restriction (
    id               BIGSERIAL PRIMARY KEY,
    injury_id        BIGINT NOT NULL REFERENCES core.injury(id) ON DELETE CASCADE,
    target_type      TEXT NOT NULL CHECK (target_type IN
                       ('exercise','muscle_group','movement_pattern','activity')),
    exercise_id      BIGINT REFERENCES core.exercise(id),
    muscle_group     TEXT REFERENCES core.muscle_group(code),
    movement_pattern TEXT,
    activity         TEXT,                 -- 'sprinting', 'jumping', 'cutting', 'ultimate'
    max_rpe          SMALLINT CHECK (max_rpe BETWEEN 0 AND 10),  -- 0 = avoid entirely
    start_date       DATE NOT NULL,
    end_date         DATE,                 -- NULL = until lifted
    origin           TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual','professional','rule')),
    CHECK (
      (target_type = 'exercise'         AND exercise_id IS NOT NULL) OR
      (target_type = 'muscle_group'     AND muscle_group IS NOT NULL) OR
      (target_type = 'movement_pattern' AND movement_pattern IS NOT NULL) OR
      (target_type = 'activity'         AND activity IS NOT NULL)
    )
);

-- =====================================================================
-- CORE: body, food, behaviors, journal, labs
-- =====================================================================
CREATE TABLE core.body_measurement (
    ts         TIMESTAMPTZ PRIMARY KEY,
    weight_kg  NUMERIC(5,2) NOT NULL,
    source     TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE core.food_catalog (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    brand         TEXT,
    barcode       TEXT UNIQUE,
    serving_desc  TEXT,
    serving_g     NUMERIC(7,1),
    kcal          NUMERIC(7,1),            -- all values PER SERVING
    protein_g     NUMERIC(6,1),
    carbs_g       NUMERIC(6,1),
    fat_g         NUMERIC(6,1),
    fiber_g       NUMERIC(6,1),
    sugar_g       NUMERIC(6,1),
    sodium_mg     NUMERIC(7,1),
    caffeine_mg   NUMERIC(6,1),            -- enter by hand for regular items
    alcohol_g     NUMERIC(6,1),
    micros        JSONB,                   -- {"iron_mg": 3.2, "magnesium_mg": 80}
    origin        TEXT NOT NULL DEFAULT 'manual'
                    CHECK (origin IN ('manual','open_food_facts','label_scan','cronometer_import')),
    verified      BOOLEAN NOT NULL DEFAULT FALSE
);

-- Saved meals for one-tap logging
CREATE TABLE core.saved_meal (
    id    BIGSERIAL PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);
CREATE TABLE core.saved_meal_item (
    saved_meal_id BIGINT NOT NULL REFERENCES core.saved_meal(id) ON DELETE CASCADE,
    catalog_id    BIGINT NOT NULL REFERENCES core.food_catalog(id),
    servings      NUMERIC(6,2) NOT NULL DEFAULT 1,
    PRIMARY KEY (saved_meal_id, catalog_id)
);

CREATE TABLE core.food_entry (
    id          BIGSERIAL PRIMARY KEY,
    logged_ts   TIMESTAMPTZ NOT NULL,
    date        DATE NOT NULL,             -- set by app using profile.timezone
    meal        TEXT CHECK (meal IN ('breakfast','lunch','dinner','snack','pre_workout','post_workout')),
    catalog_id  BIGINT NOT NULL REFERENCES core.food_catalog(id),
    servings    NUMERIC(6,2) NOT NULL DEFAULT 1 CHECK (servings > 0)
);
CREATE INDEX ON core.food_entry (date);

-- Marks a day's food log as complete, so missing days are not read as zero
CREATE TABLE core.food_day_status (
    date      DATE PRIMARY KEY,
    complete  BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE VIEW core.v_food_nutrients AS
SELECT e.id, e.logged_ts, e.date, e.meal, c.name,
       e.servings * c.kcal        AS kcal,
       e.servings * c.protein_g   AS protein_g,
       e.servings * c.carbs_g     AS carbs_g,
       e.servings * c.fat_g       AS fat_g,
       e.servings * c.fiber_g     AS fiber_g,
       e.servings * c.sugar_g     AS sugar_g,
       e.servings * c.sodium_mg   AS sodium_mg,
       e.servings * c.caffeine_mg AS caffeine_mg,
       e.servings * c.alcohol_g   AS alcohol_g,
       c.micros, e.servings
FROM core.food_entry e
JOIN core.food_catalog c ON c.id = e.catalog_id;

-- Non-food events: naps, illness, travel, supplements, sauna
CREATE TABLE core.event_type (
    code                 TEXT PRIMARY KEY,
    unit                 TEXT,
    exclude_from_baseline BOOLEAN NOT NULL DEFAULT FALSE,   -- e.g. illness, travel
    note                 TEXT
);
CREATE TABLE core.behavior_event (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL,
    type_code  TEXT NOT NULL REFERENCES core.event_type(code),
    value      NUMERIC(8,2),
    note       TEXT
);
CREATE INDEX ON core.behavior_event (type_code, ts);

CREATE TABLE core.journal (
    date        DATE PRIMARY KEY,
    mood        SMALLINT CHECK (mood BETWEEN 1 AND 5),
    energy      SMALLINT CHECK (energy BETWEEN 1 AND 5),
    soreness    SMALLINT CHECK (soreness BETWEEN 1 AND 5),
    stress      SMALLINT CHECK (stress BETWEEN 1 AND 5),
    motivation  SMALLINT CHECK (motivation BETWEEN 1 AND 5),
    note        TEXT
);

CREATE TABLE core.lab_result (
    id        BIGSERIAL PRIMARY KEY,
    date      DATE NOT NULL,
    marker    TEXT NOT NULL,
    value     NUMERIC(10,3) NOT NULL,
    unit      TEXT NOT NULL,
    ref_low   NUMERIC(10,3),
    ref_high  NUMERIC(10,3),
    UNIQUE (date, marker)
);

-- =====================================================================
-- ANALYTICS: features
-- =====================================================================
CREATE TABLE analytics.daily_features (
    date                   DATE PRIMARY KEY,

    -- data coverage
    wear_minutes           INT,
    is_partial_day         BOOLEAN,        -- all-day metrics unreliable (e.g. game without watch)
    food_log_complete      BOOLEAN,
    exclude_from_baseline  BOOLEAN,        -- illness, travel, etc.

    -- recovery
    sleep_score            INT,
    sleep_total_min        INT,
    sleep_midpoint_min     INT,
    hrv                    NUMERIC(6,1),
    resting_hr             INT,
    avg_stress             INT,            -- NULL when is_partial_day
    body_battery_low       INT,            -- NULL when is_partial_day

    -- training load
    n_workouts             SMALLINT,
    session_load           NUMERIC(8,1),
    had_game               BOOLEAN,
    days_since_game        SMALLINT,
    days_since_hard        SMALLINT,
    acute_load_7d          NUMERIC(8,1),
    chronic_load_28d       NUMERIC(8,1),
    acwr                   NUMERIC(5,2),
    next_game_in_days      SMALLINT,       -- from planned_session

    -- running performance
    run_km_7d              NUMERIC(6,2),
    pace_at_ref_hr_s_km    NUMERIC(6,1),   -- rolling estimate; lower = fitter
    vo2max                 NUMERIC(4,1),

    -- body
    weight_kg              NUMERIC(5,2),
    weight_kg_smoothed     NUMERIC(5,2),

    -- nutrition (NULL when food log incomplete)
    kcal                   NUMERIC(7,1),
    protein_g              NUMERIC(6,1),
    protein_g_per_kg       NUMERIC(4,2),
    carbs_g                NUMERIC(6,1),
    fat_g                  NUMERIC(6,1),
    fiber_g                NUMERIC(6,1),
    last_meal_hour         NUMERIC(4,1),
    caffeine_mg            NUMERIC(6,1),
    last_caffeine_hour     NUMERIC(4,1),
    alcohol_drinks         NUMERIC(4,1),

    -- journal
    mood                   SMALLINT,
    energy                 SMALLINT,
    soreness               SMALLINT,

    -- injuries
    injury_active          BOOLEAN,
    max_pain_activity      SMALLINT,

    -- baselines and deviations vs MY history (exclusion days skipped)
    hrv_7d_avg             NUMERIC(6,1),
    hrv_30d_avg            NUMERIC(6,1),
    hrv_z_30d              NUMERIC(5,2),
    rhr_z_30d              NUMERIC(5,2),
    sleep_score_z_30d      NUMERIC(5,2),
    load_z_28d             NUMERIC(5,2),

    computed_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Weekly hard sets per muscle group (sets with RIR <= 3, warmups excluded)
CREATE TABLE analytics.weekly_muscle_volume (
    week_start    DATE NOT NULL,           -- Monday
    muscle_group  TEXT NOT NULL REFERENCES core.muscle_group(code),
    hard_sets     NUMERIC(5,1) NOT NULL,   -- set_credit applied
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (week_start, muscle_group)
);

-- Best estimated 1-rep max per main lift per day (Epley, reps <= 10)
CREATE TABLE analytics.lift_progress (
    date          DATE NOT NULL,
    exercise_id   BIGINT NOT NULL REFERENCES core.exercise(id),
    best_e1rm_kg  NUMERIC(6,1) NOT NULL,
    top_set_kg    NUMERIC(6,2),
    top_set_reps  SMALLINT,
    PRIMARY KEY (date, exercise_id)
);

-- Helper view: estimated 1RM per working set
CREATE VIEW analytics.v_set_e1rm AS
SELECT s.id, w.start_ts::date AS date, s.exercise_id, s.weight_kg, s.reps, s.rir,
       ROUND(s.weight_kg * (1 + s.reps / 30.0), 1) AS e1rm_kg
FROM core.lift_set s
JOIN core.v_workout w ON w.id = s.workout_id
WHERE NOT s.is_warmup AND s.reps <= 10;

-- =====================================================================
-- ANALYTICS: rules engine
-- =====================================================================
CREATE TABLE analytics.rule (
    rule_id          TEXT PRIMARY KEY,
    domain           TEXT NOT NULL CHECK (domain IN ('training','nutrition','sleep','recovery','injury','body')),
    description      TEXT NOT NULL,
    rationale        TEXT,
    evidence_level   TEXT NOT NULL CHECK (evidence_level IN
                       ('guideline','research','heuristic','personal_validated','safety')),
    source_ref       TEXT,
    status           TEXT NOT NULL DEFAULT 'shadow' CHECK (status IN ('shadow','active','retired')),
    min_history_days INT NOT NULL DEFAULT 0,   -- dormant until this much data exists
    reviewed_at      DATE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE analytics.rule_parameter (
    rule_id        TEXT NOT NULL REFERENCES analytics.rule(rule_id),
    name           TEXT NOT NULL,
    value          NUMERIC(10,3) NOT NULL,
    default_value  NUMERIC(10,3) NOT NULL,
    min_allowed    NUMERIC(10,3),
    max_allowed    NUMERIC(10,3),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     TEXT NOT NULL DEFAULT 'manual' CHECK (updated_by IN ('manual','tuning_job')),
    PRIMARY KEY (rule_id, name),
    CHECK (min_allowed IS NULL OR value >= min_allowed),
    CHECK (max_allowed IS NULL OR value <= max_allowed)
);

CREATE TABLE analytics.rule_parameter_history (
    id          BIGSERIAL PRIMARY KEY,
    rule_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    old_value   NUMERIC(10,3),
    new_value   NUMERIC(10,3) NOT NULL,
    reason      TEXT,
    proposed_by TEXT NOT NULL DEFAULT 'manual' CHECK (proposed_by IN ('manual','tuning_job')),
    approved    BOOLEAN,                   -- NULL = awaiting my approval
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE analytics.daily_flag (
    date      DATE NOT NULL,
    rule_id   TEXT NOT NULL REFERENCES analytics.rule(rule_id),
    fired     BOOLEAN NOT NULL,
    severity  SMALLINT CHECK (severity BETWEEN 1 AND 3),   -- 1 info, 2 caution, 3 important
    details   JSONB,                       -- inputs and thresholds at evaluation time
    PRIMARY KEY (date, rule_id)
);

-- =====================================================================
-- ANALYTICS: hypotheses, insights, experiments
-- =====================================================================
CREATE TABLE analytics.hypothesis (
    id              BIGSERIAL PRIMARY KEY,
    exposure        TEXT NOT NULL,         -- column in daily_features
    outcome         TEXT NOT NULL,
    lag_days        SMALLINT NOT NULL DEFAULT 0,
    rationale       TEXT,
    min_n_per_group INT NOT NULL DEFAULT 20,
    status          TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','active','retired')),
    UNIQUE (exposure, outcome, lag_days)
);

CREATE TABLE analytics.insight (
    id             BIGSERIAL PRIMARY KEY,
    hypothesis_id  BIGINT NOT NULL REFERENCES analytics.hypothesis(id),
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    window_start   DATE,
    window_end     DATE,
    n_exposed      INT NOT NULL,
    n_unexposed    INT NOT NULL,
    effect_size    NUMERIC(8,3),
    ci_low         NUMERIC(8,3),
    ci_high        NUMERIC(8,3),
    p_value        NUMERIC(8,6),
    p_adjusted     NUMERIC(8,6),           -- Benjamini-Hochberg
    method         TEXT,                   -- 'ols_hac', 'block_bootstrap', ...
    controlled_for TEXT[],
    replicated     BOOLEAN,
    status         TEXT NOT NULL CHECK (status IN ('supported','weak','insufficient_data')),
    summary        TEXT
);

CREATE TABLE analytics.experiment (
    id             BIGSERIAL PRIMARY KEY,
    hypothesis_id  BIGINT REFERENCES analytics.hypothesis(id),
    design         TEXT NOT NULL,
    start_date     DATE NOT NULL,
    end_date       DATE,
    assignment     JSONB,                  -- {"2026-11-03": "treatment", ...}
    status         TEXT NOT NULL DEFAULT 'planned'
                     CHECK (status IN ('planned','running','done','abandoned')),
    result_summary TEXT
);

-- =====================================================================
-- ANALYTICS: recommendations and graded actions
-- =====================================================================
CREATE TABLE analytics.recommendation (
    id              BIGSERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'daily' CHECK (kind IN ('daily','weekly')),
    input_snapshot  JSONB NOT NULL,        -- exactly what the code and LLM saw
    decisions       JSONB NOT NULL,        -- structured rule output
    llm_model       TEXT,
    prompt_version  TEXT,
    rendered_text   TEXT,
    output_check    TEXT CHECK (output_check IN ('passed','failed_fallback_used')),
    shown_to_user   BOOLEAN NOT NULL DEFAULT TRUE,
    rating          SMALLINT CHECK (rating BETWEEN 1 AND 5),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (date, kind)
);

ALTER TABLE core.planned_session
  ADD CONSTRAINT fk_planned_session_rec
  FOREIGN KEY (recommendation_id) REFERENCES analytics.recommendation(id);

CREATE TABLE analytics.recommendation_action (
    id                 BIGSERIAL PRIMARY KEY,
    recommendation_id  BIGINT NOT NULL REFERENCES analytics.recommendation(id) ON DELETE CASCADE,
    rule_id            TEXT NOT NULL REFERENCES analytics.rule(rule_id),
    visible            BOOLEAN NOT NULL DEFAULT TRUE,   -- FALSE for shadow rules
    action_text        TEXT NOT NULL,
    metric             TEXT NOT NULL,
    comparator         TEXT NOT NULL CHECK (comparator IN ('<','<=','>','>=','=','between')),
    target_value       NUMERIC(10,2),
    target_value_high  NUMERIC(10,2),
    evaluate_on        DATE NOT NULL,
    -- filled by the grading job
    actual_value       NUMERIC(10,2),
    followed           BOOLEAN,
    evaluated_at       TIMESTAMPTZ,
    outcome_metric     TEXT,
    outcome_delta      NUMERIC(10,2),
    user_note          TEXT
);
CREATE INDEX ON analytics.recommendation_action (evaluate_on);

CREATE VIEW analytics.v_rule_performance AS
SELECT rule_id,
       count(*)                                       AS n_actions,
       avg(followed::int)                             AS followed_rate,
       avg(outcome_delta) FILTER (WHERE followed)     AS avg_outcome_when_followed,
       avg(outcome_delta) FILTER (WHERE NOT followed) AS avg_outcome_when_not
FROM analytics.recommendation_action
WHERE followed IS NOT NULL
GROUP BY rule_id;

-- =====================================================================
-- OPS
-- =====================================================================
CREATE TABLE analytics.pipeline_run (
    id            BIGSERIAL PRIMARY KEY,
    job           TEXT NOT NULL CHECK (job IN ('nightly','weekly','backfill')),
    step          TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    status        TEXT CHECK (status IN ('ok','failed')),
    rows_written  INT,
    error         TEXT
);

-- =====================================================================
-- KNOWLEDGE (optional, Phase 5)
-- =====================================================================
CREATE TABLE knowledge.document (
    id          BIGSERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    source_url  TEXT,
    publisher   TEXT,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed    BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE knowledge.chunk (
    id          BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES knowledge.document(id) ON DELETE CASCADE,
    chunk_no    INT NOT NULL,
    content     TEXT NOT NULL,
    -- embedding VECTOR(768),              -- requires CREATE EXTENSION vector;
    UNIQUE (document_id, chunk_no)
);

-- =====================================================================
-- SEED DATA
-- =====================================================================
INSERT INTO core.muscle_group (code, body_region) VALUES
  ('quads','lower'), ('hamstrings','lower'), ('glutes','lower'), ('calves','lower'),
  ('adductors','lower'), ('chest','upper'), ('upper_back','upper'), ('lats','upper'),
  ('shoulders','upper'), ('biceps','upper'), ('triceps','upper'), ('core','core');

INSERT INTO core.event_type (code, unit, exclude_from_baseline, note) VALUES
  ('nap', 'min', FALSE, NULL),
  ('sick', NULL, TRUE, 'illness day'),
  ('travel', NULL, TRUE, NULL),
  ('sauna', 'min', FALSE, NULL),
  ('supplement', NULL, FALSE, 'name in note');

-- Starter rules: all begin in shadow mode. Safety rules can be activated immediately.
INSERT INTO analytics.rule (rule_id, domain, description, evidence_level, min_history_days, status) VALUES
  ('NUTR_PROTEIN_LOW',       'nutrition', 'Protein per kg under target on a training day', 'guideline', 0, 'shadow'),
  ('NUTR_CALORIE_DRIFT',     'nutrition', '7-day average calories far from goal', 'heuristic', 7, 'shadow'),
  ('NUTR_MICRO_GAP',         'nutrition', 'Weekly average below target for a tracked micronutrient', 'guideline', 7, 'shadow'),
  ('TRAIN_POST_GAME',        'training',  'Game or hard session yesterday: no hard session today', 'heuristic', 0, 'shadow'),
  ('RECOV_HIGH_LOAD_LOW_HRV','recovery',  'Yesterday''s load high and HRV below baseline', 'heuristic', 21, 'shadow'),
  ('RECOV_ILLNESS_FLAG',     'recovery',  'Resting HR up and HRV down together vs baseline', 'research', 21, 'shadow'),
  ('TRAIN_LOAD_RAMP',        'training',  'Weekly load up more than threshold vs previous weeks', 'heuristic', 28, 'shadow'),
  ('TRAIN_MUSCLE_VOLUME',    'training',  'Weekly hard sets per muscle group outside target range', 'research', 7, 'shadow'),
  ('TRAIN_MUSCLE_RECOVERY',  'training',  'Same muscle group trained hard within minimum hours', 'heuristic', 0, 'shadow'),
  ('TRAIN_PRE_GAME_LEGS',    'training',  'Heavy lower-body session planned the day before a game', 'heuristic', 0, 'shadow'),
  ('SLEEP_SHORT',            'sleep',     'Sleep below minimum for several consecutive nights', 'guideline', 0, 'shadow'),
  ('INJ_RESTRICTION',        'injury',    'Planned exercise or activity conflicts with an active restriction', 'safety', 0, 'active'),
  ('INJ_RETURN_TO_PLAY',     'injury',    'Load increase allowed only if activity pain stays low for several sessions', 'heuristic', 0, 'active'),
  ('INJ_SEE_PROFESSIONAL',   'injury',    'Swelling, cannot bear weight, or pain worsening over a week: see a professional', 'safety', 0, 'active'),
  ('TRAIN_LIFT_PLATEAU',     'training',  'Estimated 1RM on a main lift flat for several weeks', 'heuristic', 21, 'shadow'),
  ('SLEEP_CAFFEINE_CUTOFF',  'sleep',     'Last caffeine later than cutoff (activate only once my data supports it)', 'personal_validated', 60, 'shadow');

INSERT INTO analytics.rule_parameter (rule_id, name, value, default_value, min_allowed, max_allowed) VALUES
  ('NUTR_PROTEIN_LOW',        'min_g_per_kg',          1.4,  1.4,  1.2,  2.2),
  ('NUTR_CALORIE_DRIFT',      'max_pct_from_goal',     10,   10,   5,    20),
  ('NUTR_CALORIE_DRIFT',      'min_logged_days',       5,    5,    4,    7),
  ('TRAIN_POST_GAME',         'hard_rpe_threshold',    7,    7,    6,    9),
  ('RECOV_HIGH_LOAD_LOW_HRV', 'load_z_threshold',      1.0,  1.0,  0.5,  2.0),
  ('RECOV_HIGH_LOAD_LOW_HRV', 'hrv_z_threshold',      -1.0, -1.0, -2.0, -0.5),
  ('RECOV_ILLNESS_FLAG',      'rhr_z_threshold',       1.5,  1.5,  1.0,  2.5),
  ('RECOV_ILLNESS_FLAG',      'hrv_z_threshold',      -1.5, -1.5, -2.5, -1.0),
  ('TRAIN_LOAD_RAMP',         'max_weekly_increase_pct', 10, 10,   5,    20),
  ('TRAIN_MUSCLE_VOLUME',     'min_hard_sets',         10,   10,   6,    14),
  ('TRAIN_MUSCLE_VOLUME',     'max_hard_sets',         20,   20,   14,   26),
  ('TRAIN_MUSCLE_RECOVERY',   'min_hours',             48,   48,   24,   72),
  ('SLEEP_SHORT',             'min_minutes',           420,  420,  360,  480),
  ('SLEEP_SHORT',             'consecutive_nights',    3,    3,    2,    5),
  ('INJ_RETURN_TO_PLAY',      'max_pain_activity',     3,    3,    2,    4),
  ('INJ_RETURN_TO_PLAY',      'sessions_required',     3,    3,    2,    5),
  ('TRAIN_LIFT_PLATEAU',      'weeks_flat',            3,    3,    2,    6),
  ('SLEEP_CAFFEINE_CUTOFF',   'cutoff_hour',           14,   14,   10,   18);
