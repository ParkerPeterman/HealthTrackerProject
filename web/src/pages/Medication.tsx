import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Medication, type MedicationCard, type MedicationToday, type MedStatus } from "../api";
import { useRun } from "../components/ui";
import { isoDate } from "../dates";

/** Shared query: the Today card, the coach card's medication lines, and the tab badge all read it. */
export function useMedicationToday(date: string) {
  return useQuery({
    queryKey: ["medication", date],
    queryFn: () => api.get<MedicationToday>(`/medication/today?day=${date}`),
    refetchInterval: 5 * 60_000,
  });
}

const hhmm = (iso: string) => {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};

/** H1: "<name> today: Taken / Not yet", and yesterday's question when it has no answer. */
export function MedicationCards({ date }: { date: string }) {
  const { data } = useMedicationToday(date);
  if (!data?.medications.length) return null;
  return <>{data.medications.map((m) => <MedCard key={m.id} m={m} date={date} />)}</>;
}

function MedCard({ m, date }: { m: MedicationCard; date: string }) {
  const qc = useQueryClient();
  const run = useRun();
  const yesterday = new Date(`${date}T12:00:00`);
  yesterday.setDate(yesterday.getDate() - 1);
  const yDate = isoDate(yesterday);
  const set = (day: string, status: MedStatus | null, takenAt?: string) => run(async () => {
    if (status === null) await api.del(`/medications/${m.id}/log/${day}`);
    else await api.put(`/medications/${m.id}/log/${day}`, { status, taken_at: takenAt ?? null });
    qc.invalidateQueries({ queryKey: ["medication"] });
  });
  const taken = m.today?.status === "taken";
  return (
    <div className="card tight stack" style={{ gap: 10, ...(m.badge ? { borderColor: "var(--warn)" } : {}) }}>
      <div className="spread">
        <span className="small" style={{ fontWeight: 550 }}>{m.name} today</span>
        {m.badge && <span className="badge tint" style={{ ["--hue" as string]: "var(--warn)" }}>due</span>}
      </div>
      <div className="chips" role="radiogroup" aria-label={`${m.name} today`}>
        <button type="button" role="radio" aria-checked={taken} className={`chip${taken ? " on" : ""}`}
                onClick={() => !taken && set(date, "taken")}>Taken</button>
        <button type="button" role="radio" aria-checked={!taken} className={`chip${!taken ? " on" : ""}`}
                onClick={() => taken && set(date, null)}>Not yet</button>
      </div>
      {taken && m.today?.taken_at && (
        <label className="row small" style={{ gap: 8 }}>Taken at
          <input type="time" defaultValue={hhmm(m.today.taken_at)} style={{ width: "auto" }}
                 onBlur={(e) => e.target.value && e.target.value !== hhmm(m.today!.taken_at!) &&
                   set(date, "taken", new Date(`${date}T${e.target.value}:00`).toISOString())} />
        </label>
      )}
      {m.ask_yesterday && (
        <div className="stack" style={{ gap: 6 }}>
          <span className="tiny muted">Yesterday</span>
          <div className="chips" role="group" aria-label={`${m.name} yesterday`}>
            <button type="button" className="chip" onClick={() => set(yDate, "taken")}>Taken</button>
            <button type="button" className="chip" onClick={() => set(yDate, "missed")}>Missed</button>
            <button type="button" className="chip" onClick={() => set(yDate, "unknown")}>Don't remember</button>
          </div>
        </div>
      )}
      <span className="tiny muted">{m.week}</span>
    </div>
  );
}

/** H4: the approved template lines, shown after the coaching text (the LLM never sees them). */
export function MedicationLines({ date }: { date: string }) {
  const { data } = useMedicationToday(date);
  const lines = (data?.medications ?? []).flatMap((m) => m.messages.filter((x) => x.template !== "weekly_count"));
  if (!lines.length) return null;
  return (
    <div className="stack" style={{ gap: 6, marginTop: 10 }}>
      {lines.map((l) => <p key={l.template} className="small" style={{ margin: 0 }}>{l.text}</p>)}
    </div>
  );
}

/** Settings: medications, dated dose changes, and the crisis line. */
export function MedicationSettings() {
  const qc = useQueryClient();
  const run = useRun();
  const { data } = useQuery({ queryKey: ["medications"], queryFn: () => api.get<Medication[]>("/medications") });
  const [draft, setDraft] = useState<{ name: string; dose_mg: string; usual_time: string }>({ name: "", dose_mg: "", usual_time: "" });
  const [change, setChange] = useState<Record<number, { date: string; dose: string }>>({});
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["medications"] });
    qc.invalidateQueries({ queryKey: ["medication"] });
  };
  return (
    <section className="card stack">
      <h3>Medication</h3>
      <p className="tiny muted" style={{ margin: 0 }}>
        A one-tap daily check on Today. This stays on this machine: it is never sent to the coaching model, the dashboard
        hides it unless you turn it on, and there are no push notifications. The app never gives medication advice.
      </p>
      {(data ?? []).map((m) => {
        const c = change[m.id] ?? { date: isoDate(), dose: "" };
        return (
          <div key={m.id} className="stack" style={{ gap: 8, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
            <div className="spread">
              <span className="small" style={{ fontWeight: 550 }}>{m.name}{m.dose_mg != null ? ` · ${m.dose_mg} mg` : ""}</span>
              <label className="row tiny"><input type="checkbox" checked={m.active} onChange={(e) => run(async () => {
                await api.put(`/medications/${m.id}`, { ...m, active: e.target.checked });
                refresh();
              })} /> active</label>
            </div>
            {m.changes.map((x) => (
              <span key={x.id} className="tiny muted">{x.date}: {x.old_dose_mg ?? "?"} → {x.new_dose_mg} mg{x.note ? ` (${x.note})` : ""}</span>
            ))}
            <div className="grid2">
              <label className="field">Dose changed on<input type="date" value={c.date}
                     onChange={(e) => setChange({ ...change, [m.id]: { ...c, date: e.target.value } })} /></label>
              <label className="field">New dose (mg)<input inputMode="decimal" value={c.dose}
                     onChange={(e) => setChange({ ...change, [m.id]: { ...c, dose: e.target.value } })} /></label>
            </div>
            <button className="btn small" disabled={!(parseFloat(c.dose) > 0)} onClick={() => run(async () => {
              await api.post(`/medications/${m.id}/dose-change`, { date: c.date, new_dose_mg: parseFloat(c.dose) });
              setChange({ ...change, [m.id]: { date: isoDate(), dose: "" } });
              refresh();
            }, "Dose change saved")}>Save dose change</button>
            <p className="tiny muted" style={{ margin: 0 }}>A dose change restarts "your normal" for HRV, resting HR, and sleep for a few weeks.</p>
          </div>
        );
      })}
      <div className="stack" style={{ gap: 8, paddingTop: 10, borderTop: "1px solid var(--line)" }}>
        <div className="grid2">
          <label className="field">Name<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
          <label className="field">Dose (mg)<input inputMode="decimal" value={draft.dose_mg} onChange={(e) => setDraft({ ...draft, dose_mg: e.target.value })} /></label>
        </div>
        <label className="field">Usual time (optional; otherwise learned)<input type="time" value={draft.usual_time}
               onChange={(e) => setDraft({ ...draft, usual_time: e.target.value })} /></label>
        <button className="btn" disabled={!draft.name.trim()} onClick={() => run(async () => {
          await api.post("/medications", { name: draft.name.trim(), dose_mg: parseFloat(draft.dose_mg) || null,
            usual_time: draft.usual_time || null });
          setDraft({ name: "", dose_mg: "", usual_time: "" });
          refresh();
        }, "Medication added")}>Add medication</button>
      </div>
      <p className="small" style={{ margin: 0 }}>
        If you're in crisis, call or text <a href="tel:988">988</a> (Suicide &amp; Crisis Lifeline, US).
      </p>
    </section>
  );
}
