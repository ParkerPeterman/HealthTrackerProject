import { useQuery } from "@tanstack/react-query";
import { api, PHASE_LABEL, type LibraryData, type LibrarySession, type LibraryState, type LiftSuggestion } from "../api";
import { useUnits } from "../components/ui";
import { fromKg, weightUnit, type Units } from "../units";

/** A weight on the plates' step: whole numbers when they are whole. */
function plateWeight(kg: number, u: Units): string {
  const v = Math.round(fromKg(kg, u) * 10) / 10;
  return `${Number.isInteger(v) ? v.toFixed(0) : v.toFixed(1)} ${weightUnit(u)}`;
}

/** F5: "Next 82.5 kg × 6 (last 80 kg × 6; RIR 2 last time: +2.5%)". */
function liftHint(h: LiftSuggestion, u: Units): string {
  if (h.last_kg <= 0) return `Next ${h.next_reps} reps (last ${h.last_reps}; ${h.why})`;
  return `Next ${plateWeight(h.next_kg, u)} × ${h.next_reps} (last ${plateWeight(h.last_kg, u)} × ${h.last_reps}; ${h.why})`;
}

/** F2: one library session as it applies on `date`: target, structure, exercises with today's swaps. */
export function LibraryDetail({ code, date, addons }: { code: string; date: string; addons?: string[] | null }) {
  const { data, error } = useQuery({
    queryKey: ["session-library", date],
    queryFn: () => api.get<LibraryData>(`/session-library?day=${date}`),
  });
  if (error) return <p className="tiny muted" style={{ margin: 0 }}>Can't load the session details.</p>;
  if (!data) return <p className="tiny muted" style={{ margin: 0 }}>Loading…</p>;
  const s = data.sessions.find((x) => x.code === code);
  if (!s) return null;
  const extra = (addons ?? []).map((a) => data.sessions.find((x) => x.code === a)).filter(Boolean) as LibrarySession[];
  return (
    <div className="stack" style={{ gap: 12 }}>
      <SessionBody s={s} state={data.state} />
      {extra.map((a) => (
        <div key={a.code} className="stack" style={{ gap: 8, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
          <div className="small" style={{ fontWeight: 550 }}>Plus: {a.name}</div>
          <SessionBody s={a} state={data.state} compact />
        </div>
      ))}
    </div>
  );
}

function SessionBody({ s, state, compact }: { s: LibrarySession; state: LibraryState; compact?: boolean }) {
  const units = useUnits();
  const load = s.expected_load;
  const deloadSets = state.deload_week && s.category === "strength";
  const loadBits = [
    `${s.duration_min_lo}–${s.duration_min_hi} min`, `RPE ${s.rpe}`,
    load.sprint_m ? `${load.sprint_m} m sprinting` : null, load.plyo_contacts ? `${load.plyo_contacts} jump contacts` : null,
    load.shoulder_min ? `${load.shoulder_min} min throwing` : null,
  ].filter(Boolean);
  return (
    <>
      {!compact && <p className="small" style={{ margin: 0 }}>{s.purpose}</p>}
      <div className="tiny muted">{loadBits.join(" · ")}</div>
      <div className="small">
        <span style={{ fontWeight: 550 }}>Target: </span>{s.target}
        {s.hr_target && (
          <span> ({s.hr_target.bpm[0]}–{s.hr_target.bpm[1]} bpm
            {s.hr_target.provisional ? `, provisional until a max HR test confirms ${s.hr_target.max_hr}` : ""})</span>
        )}
      </div>
      <p className="small" style={{ margin: 0 }}>{s.structure}</p>
      {s.exercises.length > 0 && (
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
          {s.exercises.map((e) => (
            <li key={e.exercise}>
              {e.exercise}: {deloadSets ? Math.max(1, e.sets - 1) : e.sets} × {e.reps}{e.note ? ` (${e.note})` : ""}
              {e.suggestion && <div className="tiny muted">{liftHint(e.suggestion, units)}</div>}
            </li>
          ))}
        </ul>
      )}
      {deloadSets && <div className="tiny muted">Deload week: the same weights, one set fewer on each lift.</div>}
      {s.changes.map((c) => <div key={c} className="tiny warn">{c}</div>)}
      {s.progression_group === "copenhagen" && (
        <div className="tiny muted">Copenhagen level {state.copenhagen_level}: {state.copenhagen_reason}.</div>
      )}
      {s.reasons.length > 0 && <div className="tiny bad">Doesn't fit today: {s.reasons.join("; ")}.</div>}
      {!compact && s.source_ref && <div className="tiny muted">Based on {s.source_ref}.</div>}
    </>
  );
}

/** Pick a library session for a manual plan. Sessions that don't fit the day are listed last. */
export function LibraryPicker({ date, value, onPick }: {
  date: string; value: string | null | undefined; onPick: (s: LibrarySession | null) => void;
}) {
  const { data } = useQuery({
    queryKey: ["session-library", date],
    queryFn: () => api.get<LibraryData>(`/session-library?day=${date}`),
  });
  const main = (data?.sessions ?? []).filter((s) => !s.addon);
  const sorted = [...main.filter((s) => s.fits), ...main.filter((s) => !s.fits)];
  return (
    <label className="field">From the library{data ? ` (${PHASE_LABEL[data.state.phase]} phase)` : ""}
      <select value={value ?? ""} onChange={(e) => onPick(sorted.find((s) => s.code === e.target.value) ?? null)}>
        <option value="">None</option>
        {sorted.map((s) => (
          <option key={s.code} value={s.code}>{s.name}{s.fits ? "" : " (doesn't fit this day)"}</option>
        ))}
      </select>
    </label>
  );
}
