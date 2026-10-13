// Demo mode: an in-memory synthetic athlete that answers every /api call the app
// makes, so the UI can be designed without Postgres or the FastAPI backend.
// Loaded only when Vite runs with `--mode demo` (npm run demo). Production builds
// never import it. State resets on reload.

import {
  ApiError, type Action, type Exercise, type Experiment, type Food, type FoodEntry, type Hypothesis, type Injury,
  type Journal, type LiftSet, type Meal, type Planned, type Profile, type Proposals, type Recommendation,
  type Goal, type GoalProgress, type Rule, type SavedMeal, type Target, type TargetProposal, type Workout,
} from "../api";
import { addDays, isoDate, parseIso } from "../dates";

// ------------------------------------------------------------------ helpers
const TODAY = isoDate();
const day = (n: number) => addDays(TODAY, n);
const at = (iso: string, h: number, m = 0) => {
  const d = parseIso(iso);
  d.setHours(h, m, 0, 0);
  return d.toISOString();
};
const localDate = (ts: string) => isoDate(new Date(ts));
let seed = 11;
const rand = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
const jitter = (n: number) => (rand() * 2 - 1) * n;
let lastId = 5000;
const newId = () => ++lastId;
const r1 = (v: number) => Math.round(v * 10) / 10;

// ------------------------------------------------------------------ profile and targets
let profile: Profile = {
  birth_date: "1996-04-12", sex: "male", height_cm: 183, timezone: "America/Chicago", ref_hr_bpm: 145,
  display_units: "imperial",
  training_prefs: { runs_per_week: 3, lifts_per_week: 2, min_rest_days: 1, long_run_day: "sun", generate_plan: true },
  notes: null,
};

const targets: Target[] = [
  { id: 1, nutrient: "kcal", basis: "per_day", evaluation: "daily", min_value: 2600, max_value: 2900, source_ref: "my goal", active: true },
  { id: 2, nutrient: "protein_g", basis: "per_kg_bodyweight", evaluation: "daily", min_value: 1.6, max_value: null, source_ref: "ISSN position stand", active: true },
  { id: 3, nutrient: "fiber_g", basis: "per_day", evaluation: "daily", min_value: 30, max_value: null, source_ref: "NIH", active: true },
  { id: 4, nutrient: "sodium_mg", basis: "per_day", evaluation: "weekly_avg", min_value: null, max_value: 2300, source_ref: "NIH", active: true },
  { id: 5, nutrient: "magnesium_mg", basis: "per_day", evaluation: "weekly_avg", min_value: 400, max_value: null, source_ref: "NIH", active: true },
];

// ------------------------------------------------------------------ weight (90 days, slow cut)
type WeightRow = { ts: string; date: string; weight_kg: number; source: string; weight_kg_smoothed: number | null };
const weights: WeightRow[] = [];
{
  let ema: number | null = null;
  for (let i = 90; i >= 1; i--) {
    if (rand() < 0.12) continue; // a few skipped mornings
    const kg = 82.6 - (90 - i) * 0.019 + jitter(0.55);
    ema = ema == null ? kg : ema + 0.1 * (kg - ema);
    weights.push({ ts: at(day(-i), 7, 5), date: day(-i), weight_kg: r1(kg), source: "manual", weight_kg_smoothed: r1(ema) });
  }
}
const currentKg = () => weights.at(-1)?.weight_kg ?? 81;

// ------------------------------------------------------------------ food
const F = (id: number, name: string, brand: string | null, serving_desc: string, kcal: number, protein_g: number,
  carbs_g: number, fat_g: number, fiber_g = 0, extra: Partial<Food> = {}): Food => ({
  id, name, brand, barcode: null, serving_desc, serving_g: null, kcal, protein_g, carbs_g, fat_g, fiber_g,
  sugar_g: null, sodium_mg: null, caffeine_mg: null, alcohol_g: null, micros: null, origin: "manual", verified: true, ...extra,
});
const foods: Food[] = [
  F(1, "Greek yogurt, plain 2%", "Fage", "1 cup (227 g)", 190, 20, 9, 5),
  F(2, "Rolled oats", null, "1/2 cup dry", 150, 5, 27, 3, 4),
  F(3, "Banana", null, "1 medium", 105, 1.3, 27, 0.4, 3.1),
  F(4, "Eggs, scrambled", null, "2 large", 182, 12.6, 2, 13.4),
  F(5, "Chicken breast, grilled", null, "6 oz", 280, 52, 0, 6),
  F(6, "White rice, cooked", null, "1 cup", 205, 4.3, 45, 0.4, 0.6),
  F(7, "Broccoli, steamed", null, "1 cup", 55, 3.7, 11, 0.6, 5.1),
  F(8, "Salmon fillet", null, "5 oz", 290, 31, 0, 18),
  F(9, "Whey protein shake", "Optimum Nutrition", "1 scoop + milk", 250, 32, 15, 6),
  F(10, "Peanut butter toast", null, "2 slices", 380, 14, 36, 20, 5),
  F(11, "Apple", null, "1 medium", 95, 0.5, 25, 0.3, 4.4),
  F(12, "Almonds", null, "1 oz", 164, 6, 6, 14, 3.5),
  F(13, "Cold brew coffee", null, "16 oz", 5, 0.3, 0, 0, 0, { caffeine_mg: 200 }),
  F(14, "Burrito bowl", "Chipotle", "1 bowl", 760, 45, 80, 26, 14, { sodium_mg: 1900 }),
  F(15, "Pasta with meat sauce", null, "1.5 cups", 620, 32, 78, 18, 6),
  F(16, "Sweet potato, baked", null, "1 medium", 115, 2, 27, 0.2, 4),
  F(17, "IPA", null, "12 oz", 200, 2, 18, 0, 0, { alcohol_g: 17 }),
  F(18, "Granola bar", "KIND", "1 bar", 200, 6, 16, 13, 3),
];

const entries: FoodEntry[] = [];
function addEntry(catalog_id: number, servings: number, meal: Meal, logged_ts: string, date?: string): FoodEntry {
  const f = foods.find((x) => x.id === catalog_id)!;
  const n = (v: number | null) => (v == null ? null : r1(v * servings));
  const e: FoodEntry = {
    id: newId(), logged_ts, date: date ?? localDate(logged_ts), meal, name: f.name, brand: f.brand,
    serving_desc: f.serving_desc, servings, catalog_id, kcal: n(f.kcal), protein_g: n(f.protein_g),
    carbs_g: n(f.carbs_g), fat_g: n(f.fat_g), fiber_g: n(f.fiber_g), caffeine_mg: n(f.caffeine_mg), alcohol_g: n(f.alcohol_g),
  };
  entries.push(e);
  return e;
}
type Plan = [Meal, number, number, [number, number][]][]; // meal, hour, minute, [food, servings]
const DAY_PLANS: Plan[] = [
  [["breakfast", 7, 40, [[2, 1], [1, 1], [3, 1], [13, 1]]], ["lunch", 12, 30, [[5, 1], [6, 1.5], [7, 1]]],
    ["snack", 15, 45, [[11, 1], [12, 1]]], ["dinner", 19, 10, [[8, 1], [16, 1], [7, 1]]], ["snack", 21, 0, [[1, 1]]]],
  [["breakfast", 7, 55, [[4, 1.5], [10, 1], [13, 1]]], ["lunch", 12, 15, [[14, 1]]],
    ["post_workout", 17, 30, [[9, 1], [3, 1]]], ["dinner", 19, 40, [[15, 1.5]]]],
  [["breakfast", 7, 30, [[2, 1], [1, 1], [3, 1], [13, 1]]], ["lunch", 12, 45, [[15, 1]]],
    ["snack", 16, 0, [[18, 1], [11, 1]]], ["dinner", 19, 0, [[5, 1], [6, 1], [7, 1]]], ["snack", 21, 15, [[9, 1]]]],
];
// Yesterday was game day: about 2715 kcal and 108 g protein, which matches today's coaching.
const YESTERDAY: Plan = [
  ["breakfast", 8, 10, [[2, 1], [3, 1], [13, 1]]], ["lunch", 12, 20, [[15, 1]]], ["snack", 15, 30, [[10, 1], [11, 1]]],
  ["pre_workout", 17, 15, [[18, 1]]], ["dinner", 21, 0, [[14, 1], [17, 2]]],
];
const logPlan = (date: string, plan: Plan) => {
  for (const [meal, h, m, items] of plan) for (const [fid, s] of items) addEntry(fid, s, meal, at(date, h, m), date);
};
for (let i = 7; i >= 2; i--) logPlan(day(-i), DAY_PLANS[i % DAY_PLANS.length]);
logPlan(day(-1), YESTERDAY);
logPlan(TODAY, [["breakfast", 7, 40, [[2, 1], [1, 1], [3, 1], [13, 1]]], ["lunch", 12, 30, [[14, 1]]]]);

const completeDays = new Set<string>([1, 2, 3, 4, 5, 6, 7].map((i) => day(-i)));

const savedMeals: { id: number; name: string; items: { catalog_id: number; servings: number }[] }[] = [
  { id: 1, name: "Usual breakfast", items: [{ catalog_id: 2, servings: 1 }, { catalog_id: 1, servings: 1 }, { catalog_id: 3, servings: 1 }, { catalog_id: 13, servings: 1 }] },
  { id: 2, name: "Post-lift shake", items: [{ catalog_id: 9, servings: 1 }, { catalog_id: 3, servings: 1 }] },
];

// ------------------------------------------------------------------ exercises
const EX: [string, string, boolean, [string, "primary" | "secondary"][]][] = [
  ["Back squat", "squat", true, [["quads", "primary"], ["glutes", "secondary"]]],
  ["Romanian deadlift", "hinge", true, [["hamstrings", "primary"], ["glutes", "secondary"]]],
  ["Bench press", "horizontal_push", true, [["chest", "primary"], ["triceps", "secondary"], ["shoulders", "secondary"]]],
  ["Overhead press", "vertical_push", true, [["shoulders", "primary"], ["triceps", "secondary"]]],
  ["Pull-up", "vertical_pull", false, [["lats", "primary"], ["biceps", "secondary"]]],
  ["Barbell row", "horizontal_pull", false, [["upper_back", "primary"], ["lats", "secondary"]]],
  ["Bulgarian split squat", "lunge", false, [["quads", "primary"], ["glutes", "primary"]]],
  ["Hip thrust", "hinge", false, [["glutes", "primary"], ["hamstrings", "secondary"]]],
  ["Standing calf raise", "other", false, [["calves", "primary"]]],
  ["Copenhagen plank", "core", false, [["adductors", "primary"], ["core", "secondary"]]],
  ["Plank", "core", false, [["core", "primary"]]],
  ["Dumbbell curl", "other", false, [["biceps", "primary"]]],
  ["Triceps pushdown", "other", false, [["triceps", "primary"]]],
  ["Goblet squat", "squat", false, [["quads", "primary"], ["glutes", "secondary"]]],
  ["Lying leg curl", "other", false, [["hamstrings", "primary"]]],
  ["Leg extension", "other", false, [["quads", "primary"]]],
];
const exercises: Omit<Exercise, "last_used">[] = EX.map(([name, movement_pattern, is_main_lift, ms], i) => ({
  id: i + 1, name, movement_pattern, is_main_lift,
  muscles: ms.map(([muscle_group, role]) => ({ muscle_group, role, set_credit: role === "primary" ? 1 : 0.5 })),
}));
const exId = (name: string) => exercises.find((e) => e.name === name)!.id;

// ------------------------------------------------------------------ workouts
const workouts: Workout[] = [];
function makeSets(workoutId: number, spec: [string, number, number, number][]): LiftSet[] {
  const sets: LiftSet[] = [];
  for (const [name, kg, reps, n] of spec) {
    for (let s = 1; s <= n; s++) {
      sets.push({ id: newId(), workout_id: workoutId, exercise_id: exId(name), exercise: name, set_no: s, reps,
        weight_kg: kg, rir: s === n ? 1 : 2, is_warmup: false });
    }
  }
  return sets;
}
const LOWER: [string, number, number, number][] = [["Back squat", 120, 5, 4], ["Romanian deadlift", 95, 8, 3],
  ["Bulgarian split squat", 24, 8, 3], ["Standing calf raise", 60, 12, 3]];
const UPPER: [string, number, number, number][] = [["Bench press", 85, 6, 4], ["Barbell row", 75, 8, 3],
  ["Overhead press", 50, 8, 3], ["Pull-up", 0, 8, 3]];
type Kind = "game" | "easy" | "quality" | "long" | "lower" | "upper";
const SCHEDULE: [number, Kind][] = [[-1, "game"], [-2, "easy"], [-3, "upper"], [-4, "quality"], [-6, "lower"],
  [-7, "long"], [-8, "game"], [-10, "easy"], [-11, "upper"], [-13, "game"], [-14, "lower"], [-15, "long"],
  [-17, "quality"], [-18, "upper"], [-20, "game"], [-21, "lower"]];
for (const [offset, kind] of SCHEDULE) {
  const id = newId();
  const date = day(offset);
  const base = { id, date, feel: null, notes: null, merged_from: null, max_hr: null, splits: [], sets: [] } as unknown as Workout;
  if (kind === "game") {
    const min = 90 + Math.round(jitter(6));
    Object.assign(base, { source: "manual", sport: "ultimate", is_game: true, start_ts: at(date, 18, 30), duration_s: min * 60,
      distance_m: null, avg_hr: null, rpe: 8, rpe_source: "manual", session_load: 8 * min,
      notes: offset === -13 ? "Felt a pull in the left hamstring on a deep sprint" : "League game" });
  } else if (kind === "lower" || kind === "upper") {
    const sets = makeSets(id, kind === "lower" ? LOWER : UPPER);
    Object.assign(base, { source: "manual", sport: "strength", is_game: false, start_ts: at(date, 17, 15), duration_s: 3600,
      distance_m: null, avg_hr: null, rpe: 7, rpe_source: "manual", session_load: 420, sets });
  } else {
    const km = kind === "easy" ? 5.6 : kind === "quality" ? 8.2 : 15.8;
    const paceS = kind === "easy" ? 352 : kind === "quality" ? 318 : 340; // seconds per km
    const hr = kind === "easy" ? 138 : kind === "quality" ? 158 : 145;
    const rpe = kind === "easy" ? 3 : kind === "quality" ? 7 : 5;
    const dur = Math.round(km * paceS);
    const splits = Array.from({ length: Math.floor(km) }, (_, i) => ({
      split_no: i + 1, distance_m: 1000, duration_s: Math.round(paceS + jitter(kind === "quality" ? 22 : 9)),
      avg_hr: Math.round(hr + jitter(5) + i * 0.6),
    }));
    Object.assign(base, { source: "garmin", sport: "run", is_game: false, start_ts: at(date, 7, 0), duration_s: dur,
      distance_m: km * 1000, avg_hr: hr, max_hr: hr + 18, rpe, rpe_source: "garmin",
      session_load: Math.round((rpe * dur) / 60), splits });
  }
  workouts.push(base);
}

// ------------------------------------------------------------------ plan (next two weeks)
const P = (offset: number, session_type: Planned["session_type"], sport: string, min: number, rpe: number,
  notes: string, focus: string[] | null = null, origin: Planned["origin"] = "generated"): Planned => ({
  id: newId(), date: day(offset), sport, session_type, planned_duration_min: min, planned_rpe: rpe,
  focus_muscles: focus, status: "planned", origin, notes,
});
const planned: Planned[] = [
  P(0, "easy", "run", 30, 3, "recovery jog"),
  P(1, "strength", "strength", 60, 6, "lower-body strength", ["quads", "glutes", "hamstrings"]),
  P(2, "moderate", "run", 45, 5, "steady aerobic run"),
  P(3, "strength", "strength", 50, 6, "upper-body strength", ["chest", "upper_back", "shoulders"]),
  P(4, "game", "ultimate", 90, 8, "league game", null, "manual"),
  P(5, "moderate", "run", 80, 4, "long run"),
  P(7, "hard", "run", 50, 7, "quality run: intervals or tempo"),
  P(8, "strength", "strength", 60, 6, "lower-body strength", ["quads", "glutes", "hamstrings"]),
  P(11, "game", "ultimate", 90, 8, "league game", null, "manual"),
  P(12, "moderate", "run", 80, 4, "long run"),
];

// ------------------------------------------------------------------ body
const injuries: Injury[] = [
  {
    id: 1, body_part: "hamstring", side: "left", injury_type: "strain", onset_date: day(-13),
    mechanism: "pull on a deep sprint during a game", status: "recovering", resolved_date: null,
    seen_professional: false, notes: null, checked_in_today: false,
    checkins: [[2, 0, 4], [2, 0, 4], [3, 1, 4], [3, 1, 3], [4, 1, 3], [5, 2, 3]].map(([pa, pr, fn], i) => ({
      date: day(-1 - i), pain_activity: pa, pain_rest: pr, function_score: fn, swelling: false, note: null,
    })),
    restrictions: [
      { id: 1, target_type: "activity", exercise_id: null, exercise_name: null, muscle_group: null, movement_pattern: null,
        activity: "sprinting", max_rpe: 0, start_date: day(-12), end_date: day(7), origin: "manual" },
      { id: 2, target_type: "exercise", exercise_id: exId("Romanian deadlift"), exercise_name: "Romanian deadlift",
        muscle_group: null, movement_pattern: null, activity: null, max_rpe: 6, start_date: day(-12), end_date: null, origin: "manual" },
    ],
  },
  {
    id: 2, body_part: "ankle", side: "right", injury_type: "sprain", onset_date: day(-140), mechanism: "rolled it on a cut",
    status: "resolved", resolved_date: day(-112), seen_professional: true, notes: null, checked_in_today: false,
    checkins: [], restrictions: [],
  },
];

const journal: Journal[] = Array.from({ length: 14 }, (_, i) => {
  const d = day(-1 - i);
  const v = (b: number) => Math.max(1, Math.min(5, Math.round(b + jitter(1.1))));
  return { date: d, energy: v(3.6), mood: v(3.9), soreness: v(i === 0 ? 3.4 : 2.3), stress: v(2.4), motivation: v(3.8),
    note: i === 3 ? "Slept badly, neighbours" : null };
});

const events = [
  { id: 1, ts: at(day(-9), 9, 0), type_code: "travel", value: null, note: "Flight to Denver" },
  { id: 2, ts: at(day(-5), 20, 0), type_code: "sauna", value: 15, note: null },
  { id: 3, ts: at(day(-3), 14, 30), type_code: "nap", value: 20, note: null },
  ...[0, 1, 2, 3, 4, 5].map((i) => ({ id: 10 + i, ts: at(day(-i), 8, 0), type_code: "supplement", value: null, note: "creatine 5 g" })),
];

// ------------------------------------------------------------------ coach
const RULES: [string, string, string, string, number, Rule["status"]][] = [
  ["NUTR_PROTEIN_LOW", "nutrition", "Protein per kg under target on a training day", "guideline", 0, "active"],
  ["NUTR_CALORIE_DRIFT", "nutrition", "7-day average calories far from goal", "heuristic", 7, "active"],
  ["NUTR_MICRO_GAP", "nutrition", "Weekly average below target for a tracked micronutrient", "guideline", 7, "shadow"],
  ["TRAIN_POST_GAME", "training", "Game or hard session yesterday: no hard session today", "heuristic", 0, "active"],
  ["RECOV_HIGH_LOAD_LOW_HRV", "recovery", "Yesterday's load high and HRV below baseline", "heuristic", 21, "shadow"],
  ["RECOV_ILLNESS_FLAG", "recovery", "Resting HR up and HRV down together vs baseline", "research", 21, "shadow"],
  ["TRAIN_LOAD_RAMP", "training", "Weekly load up more than threshold vs previous weeks", "heuristic", 28, "shadow"],
  ["TRAIN_MUSCLE_VOLUME", "training", "Weekly hard sets per muscle group outside target range", "research", 7, "shadow"],
  ["TRAIN_MUSCLE_RECOVERY", "training", "Same muscle group trained hard within minimum hours", "heuristic", 0, "shadow"],
  ["TRAIN_PRE_GAME_LEGS", "training", "Heavy lower-body session planned the day before a game", "heuristic", 0, "active"],
  ["SLEEP_SHORT", "sleep", "Sleep below minimum for several consecutive nights", "guideline", 0, "shadow"],
  ["INJ_RESTRICTION", "injury", "Planned exercise or activity conflicts with an active restriction", "safety", 0, "active"],
  ["INJ_RETURN_TO_PLAY", "injury", "Load increase allowed only if activity pain stays low for several sessions", "heuristic", 0, "active"],
  ["INJ_SEE_PROFESSIONAL", "injury", "Swelling, cannot bear weight, or pain worsening over a week: see a professional", "safety", 0, "active"],
  ["TRAIN_LIFT_PLATEAU", "training", "Estimated 1RM on a main lift flat for several weeks", "heuristic", 21, "shadow"],
  ["SLEEP_CAFFEINE_CUTOFF", "sleep", "Last caffeine later than cutoff (activate only once my data supports it)", "personal_validated", 60, "shadow"],
];
const PARAMS: [string, string, number, number, number][] = [
  ["NUTR_PROTEIN_LOW", "min_g_per_kg", 1.4, 1.2, 2.2], ["NUTR_CALORIE_DRIFT", "max_pct_from_goal", 10, 5, 20],
  ["NUTR_CALORIE_DRIFT", "min_logged_days", 5, 4, 7], ["TRAIN_POST_GAME", "hard_rpe_threshold", 7, 6, 9],
  ["RECOV_HIGH_LOAD_LOW_HRV", "load_z_threshold", 1, 0.5, 2], ["RECOV_HIGH_LOAD_LOW_HRV", "hrv_z_threshold", -1, -2, -0.5],
  ["RECOV_ILLNESS_FLAG", "rhr_z_threshold", 1.5, 1, 2.5], ["RECOV_ILLNESS_FLAG", "hrv_z_threshold", -1.5, -2.5, -1],
  ["TRAIN_LOAD_RAMP", "max_weekly_increase_pct", 10, 5, 20], ["TRAIN_MUSCLE_VOLUME", "min_hard_sets", 10, 6, 14],
  ["TRAIN_MUSCLE_VOLUME", "max_hard_sets", 20, 14, 26], ["TRAIN_MUSCLE_RECOVERY", "min_hours", 48, 24, 72],
  ["SLEEP_SHORT", "min_minutes", 420, 360, 480], ["SLEEP_SHORT", "consecutive_nights", 3, 2, 5],
  ["INJ_RETURN_TO_PLAY", "max_pain_activity", 3, 2, 4], ["INJ_RETURN_TO_PLAY", "sessions_required", 3, 2, 5],
  ["TRAIN_LIFT_PLATEAU", "weeks_flat", 3, 2, 6], ["SLEEP_CAFFEINE_CUTOFF", "cutoff_hour", 14, 10, 18],
];
const PERF: Record<string, [number, number, number, number]> = {
  NUTR_PROTEIN_LOW: [31, 0.68, 0.21, -0.12], TRAIN_POST_GAME: [19, 0.84, 0.32, -0.41],
  SLEEP_SHORT: [23, 0.78, 0.48, -0.05], NUTR_CALORIE_DRIFT: [12, 0.58, 0.08, 0.02], TRAIN_PRE_GAME_LEGS: [6, 1, 0.27, 0],
};
const rules: Rule[] = RULES.map(([rule_id, domain, description, evidence_level, min_history_days, status]) => {
  const p = PERF[rule_id];
  return {
    rule_id, domain, description, evidence_level, status, min_history_days,
    n_actions: p?.[0] ?? null, followed_rate: p?.[1] ?? null,
    avg_outcome_when_followed: p?.[2] ?? null, avg_outcome_when_not: p?.[3] ?? null,
    parameters: PARAMS.filter((x) => x[0] === rule_id).map(([, name, value, min_allowed, max_allowed]) => ({
      name, value, default_value: value, min_allowed, max_allowed,
    })),
  };
});

const proposals: Proposals = {
  parameters: [{
    id: 1, rule_id: "NUTR_PROTEIN_LOW", name: "min_g_per_kg", old_value: 1.4, new_value: 1.6,
    reason: "Mornings after you reached 1.6 g/kg, soreness was 0.4 lower on the 1-5 scale (31 vs 26 days, replicated in both halves of your data).",
    description: "Protein per kg under target on a training day", changed_at: at(day(-4), 20, 5),
  }],
  statuses: [{
    id: 1, rule_id: "SLEEP_SHORT", old_status: "shadow", new_status: "active",
    reason: "Graded in shadow for 23 days: when you followed it, next-day energy was +0.5 vs your baseline. It has earned a place in your coaching.",
    description: "Sleep below minimum for several consecutive nights", changed_at: at(day(-4), 20, 5),
  }],
  history: [
    { kind: "status", rule_id: "TRAIN_POST_GAME", name: null, old: "shadow", new: "active", approved: true, proposed_by: "weekly job",
      reason: "followed 84% of the time; HRV next day +0.32 SD when followed", changed_at: at(day(-18), 20, 3) },
    { kind: "parameter", rule_id: "NUTR_CALORIE_DRIFT", name: "max_pct_from_goal", old: "15", new: "10", approved: false,
      proposed_by: "weekly job", reason: "tighter band matched energy better", changed_at: at(day(-25), 20, 4) },
  ],
};

const hypotheses: (Hypothesis & { protocol: string })[] = [
  { id: 1, exposure: "sleep_total_min >= 450", outcome: "hrv_z_30d", lag_days: 1, status: "tested", insight_status: "supported",
    summary: "After nights of 7.5 h or more, your HRV the next morning is 0.35 SD higher (95% CI 0.12 to 0.58). Replicated in both halves of your data.",
    n_exposed: 64, n_unexposed: 98, can_experiment: true, protocol: "In bed early enough for 7.5 h of sleep" },
  { id: 2, exposure: "protein_g_per_kg >= 1.6", outcome: "soreness", lag_days: 1, status: "tested", insight_status: "supported",
    summary: "On days you eat 1.6 g/kg of protein or more, next-morning soreness is 0.4 lower (95% CI 0.15 to 0.66).",
    n_exposed: 31, n_unexposed: 26, can_experiment: true, protocol: "Reach 1.6 g/kg of protein" },
  { id: 3, exposure: "last_caffeine_hour >= 14", outcome: "sleep_score", lag_days: 0, status: "tested", insight_status: "weak",
    summary: "Caffeine after 2 pm goes with a sleep score 3.1 points lower. Not significant after correction (p = 0.09).",
    n_exposed: 22, n_unexposed: 71, can_experiment: true, protocol: "No caffeine after 12:00" },
  { id: 4, exposure: "alcohol_drinks >= 2", outcome: "resting_hr", lag_days: 1, status: "tested", insight_status: "weak",
    summary: "Two or more drinks go with resting HR 2.4 bpm higher the next morning. Effect is consistent but the sample is small.",
    n_exposed: 14, n_unexposed: 140, can_experiment: false, protocol: "" },
  { id: 5, exposure: "sauna_min >= 10", outcome: "hrv_z_30d", lag_days: 1, status: "waiting", insight_status: "insufficient_data",
    summary: null, n_exposed: 6, n_unexposed: 150, can_experiment: false, protocol: "" },
  { id: 6, exposure: "nap_min >= 15", outcome: "energy", lag_days: 0, status: "waiting", insight_status: "insufficient_data",
    summary: null, n_exposed: 9, n_unexposed: 140, can_experiment: false, protocol: "" },
];
const experiments: Experiment[] = [{
  id: 1, hypothesis_id: 2, exposure: "carbs_g_per_kg >= 5", outcome: "energy", design: "randomized paired days, 14 days",
  start_date: day(-60), end_date: day(-47), status: "done", assignment: {}, protocol: { action_text: "5 g/kg carbs" },
  result_summary: "Higher-carb days: energy +0.3 (95% CI −0.1 to 0.7). Inconclusive, so not adopted.",
}];

// ------------------------------------------------------------------ recommendations
const TODAY_TEXT = `**Training & recovery**

Recovery: HRV 61 ms (+0.4 SD vs baseline), resting HR 48, sleep 7h32 (score 82).

- Keep today easy: nothing above RPE 6, since you played a game (RPE 8) yesterday
- Hamstring: no sprinting until ${parseIso(day(7)).toLocaleDateString(undefined, { month: "short", day: "numeric" })}. Easy running is fine while activity pain stays at 3 or lower

**Nutrition**

Yesterday: 2715 kcal, 108 g protein.

- Add a protein-rich snack today: yesterday was 1.3 g/kg, under your 1.4 g/kg target`;

const PAST_TEXTS = [
  "**Training & recovery**\n\nRecovery: HRV 57 ms (-0.2 SD vs baseline), resting HR 49, sleep 7h05 (score 76).\n\n- Game day: eat a carb-rich meal 3-4 hours before\n\n**Nutrition**\n\nYesterday: 2840 kcal, 151 g protein. No nutrition flags today.",
  "**Training & recovery**\n\nRecovery: HRV 63 ms (+0.6 SD vs baseline), resting HR 47, sleep 8h02 (score 88). No training flags today.\n\n**Nutrition**\n\nYesterday: 2650 kcal, 139 g protein. No nutrition flags today.",
  "**Training & recovery**\n\nRecovery: HRV 52 ms (-1.1 SD vs baseline), resting HR 51, sleep 6h20 (score 64).\n\n- HRV is low after a heavy week: swap today's intervals for an easy 30 min\n- Aim for 7 h or more tonight: three short nights in a row\n\n**Nutrition**\n\nYesterday's food log isn't marked complete.",
];
const ACTIONS: [string, string, string, string][] = [
  ["TRAIN_POST_GAME", "training", "Keep today easy: nothing above RPE 6", "max_rpe"],
  ["NUTR_PROTEIN_LOW", "nutrition", "Reach 1.4 g/kg protein", "protein_g_per_kg"],
  ["SLEEP_SHORT", "sleep", "Aim for 7 h or more tonight", "sleep_total_min"],
];
const mkAction = (i: number, d: string, graded: boolean): Action => {
  const [rule_id, domain, action_text, metric] = ACTIONS[i % ACTIONS.length];
  const followed = graded ? rand() < 0.75 : null;
  return { id: newId(), rule_id, domain, action_text, metric, evaluate_on: addDays(d, 1), followed,
    actual_value: graded ? r1(metric === "max_rpe" ? (followed ? 5 : 7) : metric === "sleep_total_min" ? 400 + jitter(40) : 1.3 + jitter(0.3)) : null,
    outcome_delta: graded ? r1(jitter(0.6)) : null };
};
const todayRec: Recommendation = {
  id: 900, date: TODAY, kind: "daily", rendered_text: TODAY_TEXT, rating: null, output_check: "passed", llm_model: "qwen3:14b",
  actions: [mkAction(0, TODAY, false), mkAction(1, TODAY, false)],
  flags: [{ rule_id: "TRAIN_POST_GAME", domain: "training", severity: 2, description: "Game or hard session yesterday" }],
};
const recommendations: Recommendation[] = [todayRec, ...Array.from({ length: 12 }, (_, i) => {
  const d = day(-1 - i);
  return {
    id: 899 - i, date: d, kind: "daily" as const, rendered_text: PAST_TEXTS[i % PAST_TEXTS.length],
    rating: i % 3 === 1 ? null : 3 + (i % 3), output_check: "passed" as const, llm_model: "qwen3:14b",
    actions: i % 3 === 1 ? [] : [mkAction(i, d, true), mkAction(i + 1, d, true)], flags: [],
  };
})];

const STATUS = {
  recovery: { hrv_ms: 61, hrv_vs_baseline_sd: 0.4, hrv_30d_avg_ms: 58, resting_hr_bpm: 48, resting_hr_vs_baseline_sd: -0.6,
    sleep_last_night: "7h32", sleep_score: 82 },
  training: { load_yesterday: 744, load_7d: 2310, weekly_avg_load_28d: 2480, acute_chronic_ratio: 0.93, days_since_game: 1,
    next_game_in_days: 4, run_mi_7d: 11.4, vo2max: 52.1 },
  nutrition_yesterday: { kcal: 2715, protein_g: 108, protein_g_per_kg: 1.3, carbs_g: 331, fat_g: 92, fiber_g: 29,
    caffeine_mg: 200, last_caffeine: "8 am", alcohol_drinks: 2.4 },
};

let pipelineRunning = false;

// ------------------------------------------------------------------ goals
type MockGoal = Omit<Goal, "progress">;
const G = (g: Partial<MockGoal> & Pick<MockGoal, "id" | "goal_type" | "description" | "metric">): MockGoal => ({
  direction: null, target_value: null, unit: null, target_date: null, rate_per_week: null, days_per_week: null,
  exercise_id: null, start_value: null, start_date: day(-30), achieved_date: null, active: true, ...g,
});
const goals: MockGoal[] = [
  // Planned 0.25 kg/week but the trend only moves ~0.13: behind, so the weekly check proposes fewer calories.
  G({ id: 1, goal_type: "body_weight", description: "Lose to 175 lb", metric: "weight_kg", direction: "decrease",
      target_value: 79.4, unit: "kg", rate_per_week: -0.25, start_value: 81.9, start_date: day(-56) }),
  G({ id: 2, goal_type: "performance", description: "5K in 21:00", metric: "run_5k_min", target_value: 21, unit: "min",
      start_value: 23.4, start_date: day(-56), target_date: day(70) }),
  G({ id: 3, goal_type: "habit", description: "Sleep 7.5 h, 5 nights a week", metric: "sleep_total_min", target_value: 450,
      unit: "min", days_per_week: 5, start_date: day(-30) }),
  G({ id: 4, goal_type: "health", description: "Resting HR 46 bpm", metric: "resting_hr", target_value: 46, unit: "bpm",
      start_value: 51, start_date: day(-40), target_date: day(50) }),
  G({ id: 5, goal_type: "performance", description: "Bench 210 lb 1RM", metric: "e1rm_kg", target_value: 95.3,
      unit: "kg", exercise_id: 3, start_value: 88, start_date: day(-120), achieved_date: day(-21) }),
];
const SLEEP_7D = [470, 455, 405, 480, 462, 430, 452]; // oldest first, ends today
const LEVEL_NOW: Record<string, number> = { run_5k_min: 22.2, resting_hr: 47.4, hrv: 60.6, vo2max: 52.1, pace_at_ref_hr_s_km: 331 };
const EXPENDITURE = { kcal: 2840, source: "data" as const, intake_kcal: 2690, trend_kg_per_week: -0.136, logged_days: 24,
  weight_days: 25, window_days: 28, activity_factor: null };

const trendNow = () => weights.at(-1)?.weight_kg_smoothed ?? currentKg();
const bestE1rm = (exerciseId: number | null) => {
  const sets = workouts.flatMap((w) => w.sets ?? []).filter((s) => s.exercise_id === exerciseId);
  const best = Math.max(0, ...sets.map((s) => s.weight_kg * (1 + s.reps / 30)));
  return best ? r1(best) : null;
};
function trendSlope(): number {
  const w = weights.filter((x) => x.date >= day(-21) && x.weight_kg_smoothed != null);
  if (w.length < 2) return 0;
  const xs = w.map((x) => (Date.parse(x.date) - Date.parse(w[0].date)) / 86_400_000);
  const ys = w.map((x) => x.weight_kg_smoothed!);
  const mx = xs.reduce((a, b) => a + b) / xs.length;
  const my = ys.reduce((a, b) => a + b) / ys.length;
  const slope = xs.reduce((a, x, i) => a + (x - mx) * (ys[i] - my), 0) / xs.reduce((a, x) => a + (x - mx) ** 2, 0);
  return slope * 7;
}
const projected = (current: number, target: number, ratePerWeek: number) => {
  const gap = target - current;
  if (!ratePerWeek || (gap > 0) !== (ratePerWeek > 0) || Math.abs(ratePerWeek) < 0.02) return null;
  const days = (gap / ratePerWeek) * 7;
  return days > 3 * 365 ? null : day(Math.round(days));
};

function progressOf(g: MockGoal): GoalProgress {
  const elapsed = Math.max(0, (Date.parse(TODAY) - Date.parse(g.start_date)) / 86_400_000);
  if (g.metric === "weight_kg") {
    const current = trendNow();
    const start = g.start_value ?? current;
    const target = g.target_value ?? start;
    const rate = trendSlope();
    if (g.direction === "maintain") {
      const off = Math.abs(current - target);
      return { status: off <= 1 ? "on_track" : "behind", current, start, target, pct: Math.max(0, 1 - off), rate_per_week: rate };
    }
    const sign = target < start ? -1 : 1;
    const pct = Math.min(1, Math.max(0, (current - start) / (target - start || 1)));
    if ((current - target) * sign >= 0) return { status: "achieved", current, start, target, pct: 1, rate_per_week: rate };
    const planned = g.rate_per_week ?? 0;
    const expected = sign < 0 ? Math.max(start + (planned * elapsed) / 7, target) : Math.min(start + (planned * elapsed) / 7, target);
    const gap = (current - expected) * sign;
    const tol = Math.max(0.45, Math.abs(planned));
    return { status: gap > tol ? "ahead" : gap < -tol ? "behind" : "on_track", current, start, target, pct, expected,
      rate_per_week: rate, projected_date: projected(current, target, rate) };
  }
  if (g.metric === "sleep_total_min") {
    const days = SLEEP_7D.map((v, i) => ({ date: day(i - 6), value: v, hit: v >= (g.target_value ?? 450) }));
    const hits = days.filter((d) => d.hit).length;
    const needed = g.days_per_week ?? 7;
    return { status: hits >= needed ? "on_track" : "behind", current: hits, pct: Math.min(1, hits / needed), days, hits, needed };
  }
  if (g.metric && ["food_log_complete", "training_day", "protein_g_per_kg", "alcohol_drinks"].includes(g.metric)) {
    const days = Array.from({ length: 7 }, (_, i) => {
      const d = day(i - 6);
      const hit = g.metric === "food_log_complete" ? completeDays.has(d)
        : g.metric === "training_day" ? workouts.some((w) => w.date === d) : i % 3 !== 0;
      return { date: d, value: null, hit };
    });
    const hits = days.filter((d) => d.hit).length;
    const needed = g.days_per_week ?? 7;
    return { status: hits >= needed ? "on_track" : "behind", current: hits, pct: Math.min(1, hits / needed), days, hits, needed };
  }
  const current = g.metric === "e1rm_kg" ? bestE1rm(g.exercise_id) : LEVEL_NOW[g.metric ?? ""] ?? null;
  const better = g.metric === "e1rm_kg" || g.metric === "hrv" || g.metric === "vo2max" ? 1 : -1;
  const { start_value: start, target_value: target } = g;
  if (current == null || target == null) return { status: "no_data", current, pct: null };
  if ((current - target) * better >= 0) return { status: "achieved", current, start, target, pct: 1 };
  if (start == null) return { status: "in_progress", current, start, target, pct: null };
  const pct = Math.min(1, Math.max(0, (current - start) / (target - start)));
  if (!g.target_date) return { status: "in_progress", current, start, target, pct };
  const share = elapsed / ((Date.parse(g.target_date) - Date.parse(g.start_date)) / 86_400_000);
  const expected = start + (target - start) * Math.min(1, share);
  const gap = (current - expected) * better;
  const tol = 0.1 * Math.abs(target - start);
  return { status: gap > tol ? "ahead" : gap < -tol ? "behind" : "on_track", current, start, target, pct, expected };
}
const withProgress = (g: MockGoal): Goal => ({ ...g, progress: progressOf(g) });
const ORDER: Record<string, number> = { behind: 0, on_track: 1, ahead: 1, in_progress: 2, no_data: 3, achieved: 5 };

function planFor(rate: number, targetKg: number | null) {
  const w = trendNow();
  const maxLoss = 0.01 * w;
  const maxGain = 0.005 * w;
  let r = Math.min(Math.max(rate, -maxLoss), maxGain);
  if ((r * 7700) / 7 < -1000) r = (-1000 * 7) / 7700;
  const raw = EXPENDITURE.kcal + (r * 7700) / 7;
  const mid = Math.round(Math.max(raw, 1500) / 50) * 50;
  const expected = ((mid - EXPENDITURE.kcal) * 7) / 7700;
  return {
    current_weight_kg: w, expenditure: EXPENDITURE, projected_date: targetKg != null ? projected(w, targetKg, expected) : null,
    plan: { kcal_min: mid - 100, kcal_max: mid + 100, kcal_mid: mid, requested_rate: rate, rate_kg_per_week: r,
      expected_rate: expected, capped: Math.abs(r - rate) > 1e-9, floor_applied: raw < 1500,
      protein_g_per_kg: r < 0 ? 2 : 1.6, max_loss_kg_per_week: maxLoss, max_gain_kg_per_week: maxGain },
  };
}
const targetProposals: TargetProposal[] = [{
  id: 1, goal_id: 1, goal_description: "Lose to 175 lb", nutrient: "kcal", basis: "per_day", old_min: 2600, old_max: 2900,
  new_min: 2450, new_max: 2650, changed_at: at(day(-4), 20, 5),
  reason: "Over the last 28 days you averaged 2,690 kcal on 24 complete days while your trend weight moved −0.30 lb/week, "
    + "so you burn about 2,840 kcal a day. To lose 0.55 lb/week toward 175.0 lb, eat 2,450-2,650 kcal with 2 g/kg protein.",
}];
function setTarget(nutrient: string, basis: Target["basis"], min: number | null, max: number | null, source: string) {
  for (const t of targets) if (t.nutrient === nutrient) t.active = false;
  targets.push({ id: newId(), nutrient, basis, evaluation: "daily", min_value: min, max_value: max, source_ref: source, active: true });
}

// ------------------------------------------------------------------ views
function foodDay(date: string) {
  const list = entries.filter((e) => e.date === date).sort((a, b) => a.logged_ts.localeCompare(b.logged_ts));
  const cols = ["kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "caffeine_mg", "alcohol_g"] as const;
  const totals = Object.fromEntries(cols.map((c) => [c, r1(list.reduce((s, e) => s + (e[c] ?? 0), 0))]));
  const kg = currentKg();
  const resolved = targets.filter((t) => t.active).map((t) => {
    const k = t.basis === "per_kg_bodyweight" ? kg : 1;
    return { nutrient: t.nutrient, min: t.min_value == null ? null : Math.round(t.min_value * k),
      max: t.max_value == null ? null : Math.round(t.max_value * k), evaluation: t.evaluation };
  });
  return { date, complete: completeDays.has(date), entries: list, totals, targets: resolved };
}

function workoutView(w: Workout): Workout {
  return { ...w, n_sets: (w.sets ?? []).filter((s) => !s.is_warmup).length };
}

function exerciseList(): Exercise[] {
  return exercises.map((e) => {
    const used = workouts.filter((w) => w.sets?.some((s) => s.exercise_id === e.id)).map((w) => w.start_ts).sort().at(-1);
    return { ...e, last_used: used ?? null };
  });
}

function weekPlanText(): string {
  const lines = planned.filter((p) => p.date >= TODAY && p.date <= day(6)).map((p) => {
    const dow = parseIso(p.date).toLocaleDateString(undefined, { weekday: "short" });
    return `- ${dow}: ${p.notes ?? p.sport} (${p.planned_duration_min} min, RPE ${p.planned_rpe})`;
  });
  return `**This week**\n\nPlanned around your games, the hamstring restriction, and two lifts.\n\n${lines.join("\n")}`;
}

function todayView() {
  const t = entries.filter((e) => e.date === TODAY);
  return {
    date: TODAY,
    recommendation: todayRec,
    status: STATUS,
    week_plan: { id: 800, date: day(-4), rendered_text: weekPlanText() },
    journal: journal.find((j) => j.date === TODAY) ?? null,
    weight: (() => {
      const w = weights.find((x) => x.date === TODAY);
      return w ? { weight_kg: w.weight_kg, ts: w.ts } : null;
    })(),
    food: {
      kcal: Math.round(t.reduce((s, e) => s + (e.kcal ?? 0), 0)), protein_g: Math.round(t.reduce((s, e) => s + (e.protein_g ?? 0), 0)),
      n: t.length, complete: completeDays.has(TODAY), yesterday_complete: completeDays.has(day(-1)),
    },
    planned: planned.filter((p) => p.date === TODAY && p.status !== "skipped"),
    injuries: injuries.filter((i) => i.status !== "resolved").map((i) => ({
      id: i.id, body_part: i.body_part, side: i.side, status: i.status, checked_in: i.checkins.some((c) => c.date === TODAY),
    })),
    pending_proposals: proposals.parameters.length + proposals.statuses.length + targetProposals.length,
    goals: goals.filter((g) => g.active && !g.achieved_date).map(withProgress)
      .sort((a, b) => (ORDER[a.progress.status] ?? 9) - (ORDER[b.progress.status] ?? 9))
      .map((g) => ({ id: g.id, goal_type: g.goal_type, description: g.description, metric: g.metric, status: g.progress.status, pct: g.progress.pct })),
    last_run: { job: "nightly", started_at: at(TODAY, 7, 30), ok: true, failed_steps: null },
  };
}

function saveGoal(b: Body, id: number | null) {
  if (b.goal_type === "body_weight" && b.direction !== "maintain" && b.target_value == null) {
    throw new ApiError(422, "a weight goal needs a target weight");
  }
  if (b.goal_type === "body_weight" && b.active) for (const g of goals) if (g.goal_type === "body_weight" && g.id !== id) g.active = false;
  const { apply_targets, ...fields } = b;
  const metric = b.goal_type === "body_weight" ? "weight_kg" : b.metric;
  const start = fields.start_value ?? (metric === "weight_kg" ? trendNow() : metric === "e1rm_kg" ? bestE1rm(b.exercise_id) : LEVEL_NOW[metric] ?? null);
  let goal: MockGoal;
  if (id == null) {
    goal = G({ ...fields, id: newId(), metric, start_value: start, start_date: TODAY } as MockGoal);
    goals.push(goal);
  } else {
    goal = byId(goals, id);
    Object.assign(goal, fields, { metric, start_value: start });
  }
  if (goal.goal_type === "body_weight" && goal.direction === "maintain" && goal.target_value == null) goal.target_value = start;
  let applied = null;
  if (apply_targets && goal.goal_type === "body_weight") {
    const p = planFor(goal.rate_per_week ?? 0, goal.target_value).plan;
    setTarget("kcal", "per_day", p.kcal_min, p.kcal_max, "weight goal");
    setTarget("protein_g", "per_kg_bodyweight", p.protein_g_per_kg, null, "weight goal");
    for (const t of [...targetProposals]) remove(targetProposals, t.id); // the new plan supersedes pending proposals
    applied = { plan: p, expenditure: EXPENDITURE };
  }
  return { ...withProgress(goal), applied_targets: applied };
}

// ------------------------------------------------------------------ router
type Body = Record<string, any>;
type Handler = (m: RegExpMatchArray, q: URLSearchParams, body: Body) => unknown;
const OK = { ok: true };
const notFound = () => { throw new ApiError(404, "Not found"); };
const byId = <T extends { id: number }>(list: T[], id: string | number) => list.find((x) => x.id === Number(id)) ?? notFound();
const remove = <T extends { id: number }>(list: T[], id: string | number) => {
  const i = list.findIndex((x) => x.id === Number(id));
  if (i >= 0) list.splice(i, 1);
  return OK;
};

const routes: [string, RegExp, Handler][] = [
  // profile, targets
  ["GET", /^\/profile$/, () => profile],
  ["PUT", /^\/profile$/, (_m, _q, b) => { profile = { ...profile, ...b } as Profile; return OK; }],
  ["GET", /^\/targets$/, () => targets],
  ["POST", /^\/targets$/, (_m, _q, b) => {
    const t = targets.find((x) => x.nutrient === b.nutrient && x.active);
    if (t) Object.assign(t, b);
    else targets.push({ id: newId(), active: true, ...b } as Target);
    return OK;
  }],
  ["DELETE", /^\/targets\/(\d+)$/, (m) => remove(targets, m[1])],
  ["POST", /^\/targets\/defaults$/, () => {
    for (const [nutrient, min] of [["iron_mg", 8], ["calcium_mg", 1000], ["potassium_mg", 3400], ["zinc_mg", 11], ["vitamin_d_mcg", 15]] as const) {
      if (!targets.some((t) => t.nutrient === nutrient)) {
        targets.push({ id: newId(), nutrient, basis: "per_day", evaluation: "weekly_avg", min_value: min, max_value: null, source_ref: "NIH", active: true });
      }
    }
    return OK;
  }],

  // today, coach
  ["GET", /^\/today$/, () => todayView()],
  ["GET", /^\/pipeline\/status$/, () => ({ running: pipelineRunning })],
  ["POST", /^\/pipeline\/run$/, () => {
    pipelineRunning = true;
    setTimeout(() => { pipelineRunning = false; }, 2500);
    return { started: "nightly" };
  }],
  ["GET", /^\/recommendations$/, (_m, q) => recommendations.slice(0, Number(q.get("limit") ?? 30))],
  ["PUT", /^\/recommendations\/(\d+)\/rating$/, (m, _q, b) => { byId(recommendations, m[1]).rating = b.rating; return OK; }],
  ["GET", /^\/proposals$/, () => ({ ...proposals, targets: targetProposals })],
  ["POST", /^\/proposals\/target\/(\d+)$/, (m, _q, b) => {
    const p = byId(targetProposals, m[1]);
    remove(targetProposals, m[1]);
    if (b.approve) setTarget(p.nutrient, p.basis, p.new_min, p.new_max, "weight goal (weekly check)");
    return OK;
  }],

  // goals
  ["GET", /^\/goals$/, () => [...goals].sort((a, b) => Number(b.active) - Number(a.active) || a.id - b.id).map(withProgress)],
  ["GET", /^\/goals\/metrics$/, () => ({
    metrics: [
      ["weight_kg", "Body weight", "kg", "lower", "level", ["body_weight"], trendNow()],
      ["run_5k_min", "5K time", "min", "lower", "level", ["performance"], LEVEL_NOW.run_5k_min],
      ["e1rm_kg", "Estimated 1RM", "kg", "higher", "level", ["performance"], null],
      ["pace_at_ref_hr_s_km", "Pace at reference HR", "s/km", "lower", "level", ["performance"], LEVEL_NOW.pace_at_ref_hr_s_km],
      ["vo2max", "VO2 max", "ml/kg/min", "higher", "level", ["performance", "health"], LEVEL_NOW.vo2max],
      ["resting_hr", "Resting HR, 7-day average", "bpm", "lower", "level", ["health"], LEVEL_NOW.resting_hr],
      ["hrv", "HRV, 7-day average", "ms", "higher", "level", ["health"], LEVEL_NOW.hrv],
      ["sleep_total_min", "Sleep", "min", "higher", "daily", ["habit"], null],
      ["protein_g_per_kg", "Protein", "g/kg", "higher", "daily", ["habit"], null],
      ["food_log_complete", "Complete food log", "day", "higher", "daily", ["habit"], null],
      ["training_day", "Train", "day", "higher", "daily", ["habit"], null],
      ["alcohol_drinks", "Alcohol", "drinks", "lower", "daily", ["habit"], null],
    ].map(([key, label, unit, better, kind, goal_types, current]) => ({ key, label, unit, better, kind, goal_types, current })),
    lifts: exercises.filter((e) => e.is_main_lift).map((e) => ({ id: e.id, name: e.name, best_e1rm_kg: bestE1rm(e.id) })),
  })],
  ["GET", /^\/goals\/plan$/, (_m, q) => planFor(Number(q.get("rate_kg_per_week") ?? 0), q.get("target_kg") ? Number(q.get("target_kg")) : null)],
  ["POST", /^\/goals$/, (_m, _q, b) => saveGoal(b, null)],
  ["PUT", /^\/goals\/(\d+)$/, (m, _q, b) => saveGoal(b, Number(m[1]))],
  ["DELETE", /^\/goals\/(\d+)$/, (m) => remove(goals, m[1])],
  ["POST", /^\/proposals\/(parameter|status)\/(\d+)$/, (m, _q, b) => {
    const list = m[1] === "parameter" ? proposals.parameters : proposals.statuses;
    const p = byId(list as { id: number }[], m[2]) as Proposals["parameters"][number] & Proposals["statuses"][number];
    remove(list as { id: number }[], m[2]);
    const rule = rules.find((r) => r.rule_id === p.rule_id);
    if (b.approve && rule && m[1] === "status") rule.status = p.new_status as Rule["status"];
    if (b.approve && rule && m[1] === "parameter") {
      const param = rule.parameters.find((x) => x.name === p.name);
      if (param) param.value = p.new_value;
    }
    proposals.history.unshift({
      kind: m[1], rule_id: p.rule_id, name: m[1] === "parameter" ? p.name : null,
      old: String(m[1] === "parameter" ? p.old_value : p.old_status), new: String(m[1] === "parameter" ? p.new_value : p.new_status),
      approved: !!b.approve, proposed_by: "weekly job", reason: p.reason, changed_at: new Date().toISOString(),
    });
    return OK;
  }],
  ["GET", /^\/rules$/, () => rules],
  ["PUT", /^\/rules\/(\w+)\/status$/, (m, _q, b) => {
    (rules.find((r) => r.rule_id === m[1]) ?? notFound()).status = b.status;
    return OK;
  }],
  ["PUT", /^\/rules\/(\w+)\/parameters\/(\w+)$/, (m, _q, b) => {
    const p = rules.find((r) => r.rule_id === m[1])?.parameters.find((x) => x.name === m[2]) ?? notFound();
    if ((p.min_allowed != null && b.value < p.min_allowed) || (p.max_allowed != null && b.value > p.max_allowed)) {
      throw new ApiError(400, `value must be within [${p.min_allowed}, ${p.max_allowed}]`);
    }
    p.value = b.value;
    return OK;
  }],
  ["GET", /^\/hypotheses$/, () => hypotheses],
  ["GET", /^\/experiments$/, () => experiments],
  ["POST", /^\/experiments$/, (_m, _q, b) => {
    if (experiments.some((e) => e.status === "running" || e.status === "planned")) {
      throw new ApiError(400, "finish or abandon the current experiment first (one at a time keeps results clean)");
    }
    const h = byId(hypotheses, b.hypothesis_id);
    const assignment: Record<string, "treatment" | "control"> = {};
    for (let i = 0; i < (b.days ?? 14); i += 2) {
      const first = rand() < 0.5 ? "treatment" : "control";
      assignment[day(1 + i)] = first;
      assignment[day(2 + i)] = first === "treatment" ? "control" : "treatment";
    }
    const e: Experiment = {
      id: newId(), hypothesis_id: h.id, exposure: h.exposure, outcome: h.outcome, design: "randomized paired days, 14 days",
      start_date: day(1), end_date: day(b.days ?? 14), status: "planned", assignment,
      protocol: { action_text: h.protocol }, result_summary: null,
    };
    experiments.unshift(e);
    return { id: e.id };
  }],
  ["POST", /^\/experiments\/(\d+)\/abandon$/, (m) => { byId(experiments, m[1]).status = "abandoned"; return OK; }],

  // body
  ["GET", /^\/weights$/, (_m, q) => weights.filter((w) => w.date >= day(-Number(q.get("days") ?? 120)))],
  ["POST", /^\/weights$/, (_m, _q, b) => {
    const prev = weights.at(-1)?.weight_kg_smoothed ?? b.weight_kg;
    const ts = new Date().toISOString();
    weights.push({ ts, date: TODAY, weight_kg: r1(b.weight_kg), source: "manual", weight_kg_smoothed: r1(prev + 0.1 * (b.weight_kg - prev)) });
    return { ts, weight_kg: b.weight_kg };
  }],
  ["GET", /^\/journal$/, () => [...journal].sort((a, b) => b.date.localeCompare(a.date))],
  ["PUT", /^\/journal\/([\d-]+)$/, (m, _q, b) => {
    const j = journal.find((x) => x.date === m[1]);
    if (j) Object.assign(j, b);
    else journal.push({ date: m[1], mood: null, energy: null, soreness: null, stress: null, motivation: null, note: null, ...b });
    return OK;
  }],
  ["GET", /^\/injuries$/, (_m, q) => injuries
    .filter((i) => q.get("include_resolved") === "true" || i.status !== "resolved")
    .map((i) => ({ ...i, checked_in_today: i.checkins.some((c) => c.date === TODAY) }))],
  ["POST", /^\/injuries$/, (_m, _q, b) => {
    const id = newId();
    injuries.unshift({ id, checkins: [], restrictions: [], checked_in_today: false, ...b } as unknown as Injury);
    return { id };
  }],
  ["PUT", /^\/injuries\/(\d+)$/, (m, _q, b) => { Object.assign(byId(injuries, m[1]), b); return OK; }],
  ["PUT", /^\/injuries\/(\d+)\/checkins\/([\d-]+)$/, (m, _q, b) => {
    const i = byId(injuries, m[1]);
    i.checkins = [{ date: m[2], ...b } as Injury["checkins"][number], ...i.checkins.filter((c) => c.date !== m[2])];
    return OK;
  }],
  ["POST", /^\/injuries\/(\d+)\/restrictions$/, (m, _q, b) => {
    const ex = b.exercise_id ? exercises.find((e) => e.id === b.exercise_id) : null;
    byId(injuries, m[1]).restrictions.push({
      id: newId(), exercise_id: null, muscle_group: null, movement_pattern: null, activity: null, start_date: TODAY,
      end_date: null, origin: "manual", ...b, exercise_name: ex?.name ?? null,
    } as Injury["restrictions"][number]);
    return OK;
  }],
  ["DELETE", /^\/restrictions\/(\d+)$/, (m) => {
    for (const i of injuries) remove(i.restrictions, m[1]);
    return OK;
  }],
  ["GET", /^\/events$/, (_m, q) => events.filter((e) => localDate(e.ts) >= day(-Number(q.get("days") ?? 30)))
    .sort((a, b) => b.ts.localeCompare(a.ts))],
  ["POST", /^\/events$/, (_m, _q, b) => { events.push({ id: newId(), ...b } as (typeof events)[number]); return OK; }],
  ["DELETE", /^\/events\/(\d+)$/, (m) => remove(events, m[1])],

  // food
  ["GET", /^\/food-day\/([\d-]+)$/, (m) => foodDay(m[1])],
  ["PUT", /^\/food-day\/([\d-]+)\/complete$/, (m, _q, b) => {
    if (b.complete) completeDays.add(m[1]); else completeDays.delete(m[1]);
    return { date: m[1], complete: !!b.complete };
  }],
  ["GET", /^\/foods\/recent$/, (_m, q) => {
    const seen = new Map<number, Food>();
    for (const e of [...entries].sort((a, b) => b.logged_ts.localeCompare(a.logged_ts))) {
      if (!seen.has(e.catalog_id)) seen.set(e.catalog_id, { ...byId(foods, e.catalog_id), last_servings: e.servings, last_meal: e.meal ?? undefined });
    }
    return [...seen.values()].slice(0, Number(q.get("limit") ?? 25));
  }],
  ["GET", /^\/foods$/, (_m, q) => {
    const needle = (q.get("q") ?? "").toLowerCase();
    return foods.filter((f) => !needle || `${f.name} ${f.brand ?? ""}`.toLowerCase().includes(needle))
      .slice(0, Number(q.get("limit") ?? 40));
  }],
  ["GET", /^\/foods\/barcode\/(\d+)$/, (m) => {
    const known = foods.find((f) => f.barcode === m[1]);
    if (known) return { ...known, found_in: "catalog" };
    const f = F(newId(), "Protein bar, salted caramel", "Barebells", "1 bar (55 g)", 200, 20, 18, 7, 2, { barcode: m[1], origin: "open_food_facts", verified: false });
    foods.push(f);
    return { ...f, found_in: "open_food_facts" };
  }],
  ["POST", /^\/foods$/, (_m, _q, b) => {
    const f = F(newId(), b.name, b.brand, b.serving_desc, b.kcal, b.protein_g, b.carbs_g, b.fat_g, b.fiber_g ?? 0, {
      caffeine_mg: b.caffeine_mg, alcohol_g: b.alcohol_g, sodium_mg: b.sodium_mg,
    });
    foods.push(f);
    return f;
  }],
  ["POST", /^\/food-entries$/, (_m, _q, b) => addEntry(b.catalog_id, b.servings, b.meal, b.logged_ts, b.date)],
  ["PUT", /^\/food-entries\/(\d+)$/, (m, _q, b) => {
    const old = byId(entries, m[1]);
    remove(entries, m[1]);
    const e = addEntry(old.catalog_id, b.servings, b.meal, b.logged_ts);
    e.id = old.id;
    return e;
  }],
  ["DELETE", /^\/food-entries\/(\d+)$/, (m) => remove(entries, m[1])],
  ["GET", /^\/saved-meals$/, (): SavedMeal[] => savedMeals.map((m) => {
    const items = m.items.map((i) => ({ ...i, name: byId(foods, i.catalog_id).name }));
    const sum = (k: "kcal" | "protein_g") => Math.round(m.items.reduce((s, i) => s + (byId(foods, i.catalog_id)[k] ?? 0) * i.servings, 0));
    return { id: m.id, name: m.name, items, kcal: sum("kcal"), protein_g: sum("protein_g") };
  })],
  ["POST", /^\/saved-meals$/, (_m, _q, b) => {
    const items = b.items?.length ? b.items : entries.filter((e) => e.date === b.from_date && e.meal === b.from_meal)
      .map((e) => ({ catalog_id: e.catalog_id, servings: e.servings }));
    const id = newId();
    savedMeals.push({ id, name: b.name, items });
    return { id };
  }],
  ["DELETE", /^\/saved-meals\/(\d+)$/, (m) => remove(savedMeals, m[1])],
  ["POST", /^\/saved-meals\/(\d+)\/log$/, (m, _q, b) => {
    for (const i of byId(savedMeals, m[1]).items) addEntry(i.catalog_id, i.servings, b.meal, b.logged_ts, b.date);
    return OK;
  }],

  // training
  ["GET", /^\/workouts$/, (_m, q) => {
    const start = q.get("start") ?? day(-14);
    const end = q.get("end") ?? TODAY;
    return workouts.filter((w) => w.date! >= start && w.date! <= end).sort((a, b) => b.start_ts.localeCompare(a.start_ts)).map(workoutView);
  }],
  ["GET", /^\/workouts\/(\d+)$/, (m) => workoutView(byId(workouts, m[1]))],
  ["POST", /^\/workouts$/, (_m, _q, b) => {
    const w: Workout = {
      id: newId(), source: "manual", sport: b.sport, is_game: !!b.is_game, start_ts: b.start_ts, date: localDate(b.start_ts),
      duration_s: (b.duration_min ?? 60) * 60, distance_m: b.distance_m ?? null, avg_hr: null, max_hr: null, rpe: b.rpe ?? null,
      rpe_source: b.rpe ? "manual" : null, feel: null, session_load: b.rpe ? b.rpe * (b.duration_min ?? 60) : null,
      notes: b.notes ?? null, merged_from: null, sets: [], splits: [],
    };
    workouts.push(w);
    return w;
  }],
  ["PUT", /^\/workouts\/(\d+)$/, (m, _q, b) => {
    const w = byId(workouts, m[1]);
    const { duration_min, ...rest } = b;
    Object.assign(w, rest);
    if (duration_min) w.duration_s = duration_min * 60;
    if (b.rpe) w.rpe_source = "manual";
    w.session_load = w.rpe ? Math.round((w.rpe * w.duration_s) / 60) : null;
    return OK;
  }],
  ["DELETE", /^\/workouts\/(\d+)$/, (m) => remove(workouts, m[1])],
  ["GET", /^\/exercises$/, () => exerciseList()],
  ["POST", /^\/exercises$/, (_m, _q, b) => {
    if (exercises.some((e) => e.name.toLowerCase() === String(b.name).toLowerCase())) throw new ApiError(400, "an exercise with that name exists");
    const id = newId();
    exercises.push({ id, name: b.name, movement_pattern: b.movement_pattern, is_main_lift: !!b.is_main_lift, muscles: b.muscles ?? [] });
    return { id };
  }],
  ["GET", /^\/exercises\/(\d+)\/last$/, (m) => {
    const id = Number(m[1]);
    const w = workouts.filter((x) => x.sets?.some((s) => s.exercise_id === id)).sort((a, b) => b.start_ts.localeCompare(a.start_ts))[0];
    if (!w) return { sets: [] };
    const sets = w.sets!.filter((s) => s.exercise_id === id);
    const best = Math.max(...sets.map((s) => s.weight_kg * (1 + s.reps / 30)));
    return { date: w.start_ts, sets, best_e1rm_kg: best > 0 ? r1(best) : null };
  }],
  ["POST", /^\/workouts\/(\d+)\/sets$/, (m, _q, b) => {
    const w = byId(workouts, m[1]);
    w.sets = w.sets ?? [];
    const n = w.sets.filter((s) => s.exercise_id === b.exercise_id).length;
    w.sets.push({ id: newId(), workout_id: w.id, exercise: exercises.find((e) => e.id === b.exercise_id)?.name ?? "Exercise",
      set_no: n + 1, rir: null, is_warmup: false, ...b } as LiftSet);
    return OK;
  }],
  ["DELETE", /^\/sets\/(\d+)$/, (m) => {
    for (const w of workouts) if (w.sets) remove(w.sets, m[1]);
    return OK;
  }],
  ["GET", /^\/planned$/, (_m, q) => {
    const start = q.get("start") ?? TODAY;
    const end = q.get("end") ?? day(13);
    return planned.filter((p) => p.date >= start && p.date <= end).sort((a, b) => a.date.localeCompare(b.date));
  }],
  ["POST", /^\/planned$/, (_m, _q, b) => {
    const p = { id: newId(), status: "planned", origin: "manual", notes: null, focus_muscles: null, ...b } as Planned;
    planned.push(p);
    return { id: p.id };
  }],
  ["PUT", /^\/planned\/(\d+)$/, (m, _q, b) => { Object.assign(byId(planned, m[1]), b, { origin: "manual" }); return OK; }],
  ["DELETE", /^\/planned\/(\d+)$/, (m) => remove(planned, m[1])],
];

export async function mockRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  await new Promise((r) => setTimeout(r, 90 + rand() * 120));
  const url = new URL(path, "http://demo");
  for (const [verb, re, handler] of routes) {
    const m = url.pathname.match(re);
    if (verb === method && m) return structuredClone(handler(m, url.searchParams, (body ?? {}) as Body)) as T;
  }
  throw new ApiError(404, `Demo mode has no mock for ${method} ${url.pathname}`);
}
