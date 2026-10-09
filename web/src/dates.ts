// Local-date helpers. Dates are "YYYY-MM-DD" strings in the phone's local zone,
// which matches the profile time zone for a single-user app.

export function isoDate(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function addDays(iso: string, n: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  return isoDate(new Date(y, m - 1, d + n));
}

export function parseIso(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function dayLabel(iso: string, today = isoDate()): string {
  if (iso === today) return "Today";
  if (iso === addDays(today, -1)) return "Yesterday";
  if (iso === addDays(today, 1)) return "Tomorrow";
  return parseIso(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export function timeLabel(ts: string): string {
  return new Date(ts).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** The meal that fits the current time of day. */
export function mealForNow(d: Date = new Date()): "breakfast" | "lunch" | "snack" | "dinner" {
  const h = d.getHours() + d.getMinutes() / 60;
  if (h < 10.5) return "breakfast";
  if (h < 14.5) return "lunch";
  if (h < 17) return "snack";
  if (h < 21) return "dinner";
  return "snack";
}

/** A datetime-local input value ("YYYY-MM-DDTHH:MM") for a Date. */
export function toLocalInput(d: Date): string {
  return `${isoDate(d)}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** A local datetime-input value on a given ISO date with the current clock time. */
export function onDateNow(iso: string): Date {
  const now = new Date();
  const d = parseIso(iso);
  d.setHours(now.getHours(), now.getMinutes(), 0, 0);
  return d;
}
