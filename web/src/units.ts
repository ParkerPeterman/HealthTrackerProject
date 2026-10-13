// Storage is metric; display follows the profile setting.
export type Units = "imperial" | "metric";

const KG_PER_LB = 0.45359237;
const M_PER_MI = 1609.344;

export const weightUnit = (u: Units) => (u === "imperial" ? "lb" : "kg");
export const distUnit = (u: Units) => (u === "imperial" ? "mi" : "km");

export function showWeight(kg: number | null | undefined, u: Units, digits = 1): string {
  if (kg == null) return "–";
  const v = u === "imperial" ? kg / KG_PER_LB : kg;
  return v.toFixed(digits);
}

export function toKg(value: number, u: Units): number {
  return u === "imperial" ? value * KG_PER_LB : value;
}

export function fromKg(kg: number, u: Units): number {
  return u === "imperial" ? kg / KG_PER_LB : kg;
}

export function showDistance(m: number | null | undefined, u: Units, digits = 2): string {
  if (m == null) return "–";
  return (u === "imperial" ? m / M_PER_MI : m / 1000).toFixed(digits);
}

export function showPace(distanceM: number | null, durationS: number, u: Units): string {
  if (!distanceM) return "–";
  const per = u === "imperial" ? M_PER_MI : 1000;
  const s = Math.round((durationS / distanceM) * per);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}/${distUnit(u)}`;
}

const ML_PER_OZ = 29.5735;

/** Water: fluid ounces in imperial, litres (or ml under 1 L) in metric. */
export function showVolume(ml: number, u: Units): string {
  if (u === "imperial") return `${Math.round(ml / ML_PER_OZ)} oz`;
  return ml >= 1000 ? `${(ml / 1000).toFixed(1)} L` : `${Math.round(ml)} ml`;
}
export const volumeToMl = (value: number, u: Units) => (u === "imperial" ? value * ML_PER_OZ : value);

/** A sensible plate-math step for the weight stepper. */
export const weightStep = (u: Units) => (u === "imperial" ? 5 : 2.5);

export function fmtDuration(seconds: number): string {
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${m} min`;
}

export function round(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v)) return "–";
  return v.toFixed(digits);
}
