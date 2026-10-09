// Thin fetch wrapper. The API speaks metric; the UI converts for display.

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
      else if (Array.isArray(data.detail)) msg = data.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body ?? {}),
  del: <T>(path: string) => request<T>("DELETE", path),
};

// ------------------------------------------------------------------ types
export type Meal = "breakfast" | "lunch" | "dinner" | "snack" | "pre_workout" | "post_workout";

export interface Profile {
  birth_date: string | null;
  sex: "male" | "female" | "other" | null;
  height_cm: number | null;
  timezone: string;
  ref_hr_bpm: number | null;
  display_units: "imperial" | "metric";
  training_prefs: {
    runs_per_week: number;
    lifts_per_week: number;
    min_rest_days: number;
    long_run_day: string;
    generate_plan: boolean;
  };
  notes: string | null;
}

export interface Food {
  id: number;
  name: string;
  brand: string | null;
  barcode: string | null;
  serving_desc: string | null;
  serving_g: number | null;
  kcal: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  sugar_g: number | null;
  sodium_mg: number | null;
  caffeine_mg: number | null;
  alcohol_g: number | null;
  micros: Record<string, number> | null;
  origin?: string;
  verified?: boolean;
  last_servings?: number;
  last_meal?: Meal;
  found_in?: "catalog" | "open_food_facts";
}

export interface FoodEntry {
  id: number;
  logged_ts: string;
  date: string;
  meal: Meal | null;
  name: string;
  brand: string | null;
  serving_desc: string | null;
  servings: number;
  catalog_id: number;
  kcal: number | null;
  protein_g: number | null;
  carbs_g: number | null;
  fat_g: number | null;
  fiber_g: number | null;
  caffeine_mg: number | null;
  alcohol_g: number | null;
}

export interface FoodDay {
  date: string;
  complete: boolean;
  entries: FoodEntry[];
  totals: Record<string, number>;
  targets: { nutrient: string; min: number | null; max: number | null; evaluation: string }[];
}

export interface SavedMeal {
  id: number;
  name: string;
  kcal: number;
  protein_g: number;
  items: { catalog_id: number; servings: number; name: string }[];
}

export interface LiftSet {
  id: number;
  workout_id: number;
  exercise_id: number;
  exercise: string;
  set_no: number;
  reps: number;
  weight_kg: number;
  rir: number | null;
  is_warmup: boolean;
}

export interface Workout {
  id: number;
  source: "garmin" | "manual";
  sport: string;
  is_game: boolean;
  start_ts: string;
  date?: string;
  duration_s: number;
  distance_m: number | null;
  avg_hr: number | null;
  max_hr: number | null;
  rpe: number | null;
  rpe_source: string | null;
  feel: number | null;
  session_load: number | null;
  notes: string | null;
  n_sets?: number;
  merged_from?: number[] | null;
  sets?: LiftSet[];
  splits?: { split_no: number; distance_m: number; duration_s: number; avg_hr: number | null }[];
}

export interface Exercise {
  id: number;
  name: string;
  movement_pattern: string;
  is_main_lift: boolean;
  muscles: { muscle_group: string; role: "primary" | "secondary"; set_credit: number }[];
  last_used: string | null;
}

export interface Planned {
  id: number;
  date: string;
  sport: string;
  session_type: "game" | "hard" | "moderate" | "easy" | "strength" | "rest";
  planned_duration_min: number | null;
  planned_rpe: number | null;
  focus_muscles: string[] | null;
  status: "planned" | "done" | "skipped" | "modified";
  origin: "manual" | "generated";
  notes: string | null;
}

export interface Checkin {
  date: string;
  pain_rest: number | null;
  pain_activity: number | null;
  function_score: number | null;
  swelling: boolean | null;
  note: string | null;
}

export interface Restriction {
  id: number;
  target_type: "exercise" | "muscle_group" | "movement_pattern" | "activity";
  exercise_id: number | null;
  exercise_name?: string | null;
  muscle_group: string | null;
  movement_pattern: string | null;
  activity: string | null;
  max_rpe: number | null;
  start_date: string;
  end_date: string | null;
  origin: string;
}

export interface Injury {
  id: number;
  body_part: string;
  side: string | null;
  injury_type: string;
  onset_date: string;
  mechanism: string | null;
  status: "active" | "recovering" | "resolved";
  resolved_date: string | null;
  seen_professional: boolean;
  notes: string | null;
  checkins: Checkin[];
  restrictions: Restriction[];
  checked_in_today: boolean;
}

export interface Action {
  id: number;
  rule_id: string;
  domain: string;
  action_text: string;
  metric: string;
  evaluate_on: string;
  followed: boolean | null;
  actual_value: number | null;
  outcome_delta: number | null;
}

export interface Flag {
  rule_id: string;
  domain: string;
  severity: 1 | 2 | 3;
  description: string;
}

export interface Recommendation {
  id: number;
  date: string;
  kind: "daily" | "weekly";
  rendered_text: string | null;
  rating: number | null;
  output_check: "passed" | "failed_fallback_used" | null;
  llm_model: string | null;
  actions: Action[];
  flags: Flag[];
}

export interface Today {
  date: string;
  recommendation: Recommendation | null;
  status: {
    recovery?: Record<string, number | string>;
    training?: Record<string, number | string>;
    nutrition_yesterday?: Record<string, number | string>;
  } | null;
  week_plan: { id: number; date: string; rendered_text: string } | null;
  journal: Journal | null;
  weight: { weight_kg: number; ts: string } | null;
  food: { kcal: number | null; protein_g: number | null; n: number; complete: boolean; yesterday_complete: boolean };
  planned: Planned[];
  injuries: { id: number; body_part: string; side: string | null; status: string; checked_in: boolean }[];
  pending_proposals: number;
  last_run: { job: string; started_at: string; ok: boolean; failed_steps: string | null } | null;
}

export interface Journal {
  date: string;
  mood: number | null;
  energy: number | null;
  soreness: number | null;
  stress: number | null;
  motivation: number | null;
  note: string | null;
}

export interface Target {
  id: number;
  nutrient: string;
  basis: "per_day" | "per_kg_bodyweight";
  evaluation: "daily" | "weekly_avg";
  min_value: number | null;
  max_value: number | null;
  source_ref: string | null;
  active: boolean;
}

export interface Rule {
  rule_id: string;
  domain: string;
  description: string;
  evidence_level: string;
  status: "shadow" | "active" | "retired";
  min_history_days: number;
  n_actions: number | null;
  followed_rate: number | null;
  avg_outcome_when_followed: number | null;
  avg_outcome_when_not: number | null;
  parameters: { name: string; value: number; default_value: number; min_allowed: number | null; max_allowed: number | null }[];
}

export interface Proposals {
  parameters: { id: number; rule_id: string; name: string; old_value: number; new_value: number; reason: string; description: string; changed_at: string }[];
  statuses: { id: number; rule_id: string; old_status: string; new_status: string; reason: string; description: string; changed_at: string }[];
  history: { kind: string; rule_id: string; name: string | null; old: string; new: string; approved: boolean; proposed_by: string; reason: string; changed_at: string }[];
}

export interface Hypothesis {
  id: number;
  exposure: string;
  outcome: string;
  lag_days: number;
  status: string;
  insight_status: "supported" | "weak" | "insufficient_data" | null;
  summary: string | null;
  n_exposed: number | null;
  n_unexposed: number | null;
  can_experiment: boolean;
}

export interface Experiment {
  id: number;
  hypothesis_id: number;
  exposure: string;
  outcome: string;
  design: string;
  start_date: string;
  end_date: string;
  status: "planned" | "running" | "done" | "abandoned";
  assignment: Record<string, "treatment" | "control">;
  protocol: { action_text: string } | null;
  result_summary: string | null;
}
