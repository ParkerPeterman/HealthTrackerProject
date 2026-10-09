import type { Exercise } from "./api";

// Display order for muscle groups. An exercise is filed under the first of its
// primary muscles in this order (so a squat sits under quads, not glutes).
export const MUSCLE_ORDER = ["chest", "shoulders", "triceps", "upper_back", "lats", "biceps",
  "quads", "hamstrings", "glutes", "calves", "adductors", "core"];

export const muscleLabel = (m: string) =>
  m === "upper_back" ? "Upper back / traps" : m.charAt(0).toUpperCase() + m.slice(1).replace("_", " ");

export function groupOf(e: Exercise): string {
  const rank = (role: "primary" | "secondary") => e.muscles.filter((m) => m.role === role)
    .map((m) => m.muscle_group).sort((a, b) => MUSCLE_ORDER.indexOf(a) - MUSCLE_ORDER.indexOf(b));
  return rank("primary")[0] ?? rank("secondary")[0] ?? "other";
}

/** "Chest, Shoulders ½, Triceps ½": primary muscles first, then secondary. */
export const muscleSummary = (e: Exercise) => [...e.muscles]
  .sort((a, b) => (a.role === b.role ? 0 : a.role === "primary" ? -1 : 1)
    || MUSCLE_ORDER.indexOf(a.muscle_group) - MUSCLE_ORDER.indexOf(b.muscle_group))
  .map((m) => muscleLabel(m.muscle_group) + (m.role === "primary" ? "" : " ½")).join(", ");

/** Exercises grouped by muscle, groups in MUSCLE_ORDER, names alphabetical; empty groups dropped. */
export function groupByMuscle(list: Exercise[]): [string, Exercise[]][] {
  const m = new Map<string, Exercise[]>();
  for (const e of list) {
    const g = groupOf(e);
    m.set(g, [...(m.get(g) ?? []), e]);
  }
  return [...MUSCLE_ORDER, "other"].filter((g) => m.has(g))
    .map((g) => [g, m.get(g)!.sort((a, b) => a.name.localeCompare(b.name))]);
}
