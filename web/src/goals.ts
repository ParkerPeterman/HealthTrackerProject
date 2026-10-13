// Goal status labels and value formatting, shared by the Goals page and the Today card.
import type { GoalStatus } from "./api";
import { distUnit, showWeight, weightUnit, type Units } from "./units";

export const STATUS: Record<GoalStatus, { label: string; hue: string }> = {
  achieved: { label: "achieved", hue: "signal" },
  ahead: { label: "ahead", hue: "good" },
  on_track: { label: "on track", hue: "good" },
  behind: { label: "behind", hue: "warn" },
  in_progress: { label: "in progress", hue: "muted" },
  no_data: { label: "no data yet", hue: "muted" },
  not_tracked: { label: "not tracked", hue: "muted" },
};

export const KM_PER_MI = 1.609344;
export const clock = (minutes: number) => {
  const s = Math.round(minutes * 60);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
/** "21:30" → 21.5 minutes; a bare number is taken as minutes. */
export const parseClock = (txt: string): number | null => {
  const m = txt.trim().match(/^(\d+):(\d{1,2})$/);
  if (m) return Number(m[1]) + Number(m[2]) / 60;
  const n = parseFloat(txt);
  return Number.isFinite(n) ? n : null;
};

export function fmtMetric(metric: string | null, v: number | null | undefined, units: Units): string {
  if (v == null || metric == null) return "–";
  switch (metric) {
    case "weight_kg": return `${showWeight(v, units)} ${weightUnit(units)}`;
    case "e1rm_kg": return `${showWeight(v, units, 0)} ${weightUnit(units)}`;
    case "run_5k_min": return clock(v);
    case "pace_at_ref_hr_s_km": return `${clock((units === "imperial" ? v * KM_PER_MI : v) / 60)}/${distUnit(units)}`;
    case "vo2max": return v.toFixed(1);
    case "resting_hr": return `${Math.round(v)} bpm`;
    case "hrv": return `${Math.round(v)} ms`;
    case "sleep_total_min": return `${Math.floor(v / 60)}h${String(Math.round(v % 60)).padStart(2, "0")}`;
    case "protein_g_per_kg": return `${v.toFixed(1)} g/kg`;
    case "alcohol_drinks": return `${v} drinks`;
    default: return String(v);
  }
}
