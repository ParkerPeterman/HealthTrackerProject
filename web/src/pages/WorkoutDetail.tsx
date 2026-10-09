import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type Exercise, type LiftSet, type Workout } from "../api";
import { Chips, Empty, Icon, Scale, Sheet, Stepper, useRun, useUnits } from "../components/ui";
import { dayLabel } from "../dates";
import { groupByMuscle, MUSCLE_ORDER, muscleLabel, muscleSummary } from "../exercises";
import { distUnit, fmtDuration, fromKg, showDistance, showPace, showWeight, toKg, weightStep, weightUnit } from "../units";

export default function WorkoutDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const run = useRun();
  const units = useUnits();
  const { data: w } = useQuery({ queryKey: ["workout", id], queryFn: () => api.get<Workout>(`/workouts/${id}`) });
  const [adding, setAdding] = useState<Exercise | null>(null);
  const [picker, setPicker] = useState(false);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["workout", id] });
    qc.invalidateQueries({ queryKey: ["workouts"] });
  };

  const groups = useMemo(() => {
    const m = new Map<number, { name: string; sets: LiftSet[] }>();
    for (const s of w?.sets ?? []) {
      const g = m.get(s.exercise_id) ?? { name: s.exercise, sets: [] };
      g.sets.push(s);
      m.set(s.exercise_id, g);
    }
    return [...m.entries()];
  }, [w]);

  if (!w) return <Empty>Loading…</Empty>;
  const patch = (body: Partial<Workout> & { duration_min?: number }) => run(async () => {
    await api.put(`/workouts/${w.id}`, body);
    refresh();
  });

  return (
    <div className="stack">
      <header className="topbar">
        <button className="btn ghost icon-btn" onClick={() => navigate(-1)} aria-label="Back"><Icon name="back" /></button>
        <div style={{ textAlign: "center" }}>
          <h1 style={{ textTransform: "capitalize" }}>{w.sport}</h1>
          <div className="sub">{dayLabel(w.start_ts.slice(0, 10))} · {new Date(w.start_ts).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}</div>
        </div>
        <span className={`badge ${w.source === "garmin" ? "garmin" : ""}`}>{w.source}</span>
      </header>

      <div className="card stack">
        <div className="tiles" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <div className="tile"><div className="label">Time</div><div className="value num">{fmtDuration(w.duration_s)}</div></div>
          {w.distance_m ? (
            <div className="tile"><div className="label">Distance</div>
              <div className="value num">{showDistance(w.distance_m, units)}<small>{distUnit(units)}</small></div></div>
          ) : <div className="tile"><div className="label">Load</div><div className="value num">{w.session_load ? Math.round(w.session_load) : "–"}</div></div>}
          <div className="tile"><div className="label">{w.avg_hr ? "Avg HR" : "Sets"}</div>
            <div className="value num">{w.avg_hr ?? w.sets?.filter((s) => !s.is_warmup).length ?? 0}</div></div>
        </div>
        <label className="field">Effort (RPE){w.rpe_source === "garmin" && <span className="muted"> · from your watch</span>}
          <Scale n={10} value={w.rpe} onChange={(v) => v && patch({ rpe: v })} label="effort" />
        </label>
        {w.source === "manual" && (
          <label className="field">Minutes
            <Stepper value={Math.round(w.duration_s / 60)} onChange={(v) => patch({ duration_min: v })} step={5} min={5} max={600} label="minutes" />
          </label>
        )}
        <label className="row small" style={{ cursor: "pointer" }}>
          <input type="checkbox" checked={w.is_game} onChange={(e) => patch({ is_game: e.target.checked })} />
          This was a game
        </label>
        {w.merged_from?.length ? <p className="tiny muted" style={{ margin: 0 }}>Merged with your manual entry (Garmin wins on overlapping sessions).</p> : null}
      </div>

      {w.splits && w.splits.length > 0 && (
        <div className="list">
          <div className="list-head"><span>Splits</span><span>pace · HR</span></div>
          {w.splits.map((s) => (
            <div key={s.split_no} className="list-row num small">
              <span className="grow">{s.split_no}. {showDistance(s.distance_m, units)} {distUnit(units)}</span>
              <span>{showPace(s.distance_m, s.duration_s, units)}</span>
              <span className="muted" style={{ width: 54, textAlign: "right" }}>{s.avg_hr ?? "–"} bpm</span>
            </div>
          ))}
        </div>
      )}

      {(w.sport === "strength" || groups.length > 0) && (
        <>
          <h3 className="section-title">Lifts</h3>
          {groups.length === 0 && <Empty>Add your first exercise.</Empty>}
          {groups.map(([exId, g]) => (
            <div key={exId} className="list">
              <div className="list-head"><span>{g.name}</span>
                <button className="btn small ghost" style={{ minHeight: 24, padding: 0 }}
                        onClick={() => setAdding({ id: exId, name: g.name } as Exercise)}>+ set</button>
              </div>
              {g.sets.map((s) => (
                <div key={s.id} className="list-row num small">
                  <span className="muted" style={{ width: 24 }}>{s.is_warmup ? "W" : s.set_no}</span>
                  <span className="grow"><b>{showWeight(s.weight_kg, units, units === "imperial" ? 0 : 1)}</b> {weightUnit(units)} × <b>{s.reps}</b></span>
                  <span className="muted">{s.rir != null ? `RIR ${s.rir}` : ""}</span>
                  <button className="btn ghost small danger" aria-label="Delete set" onClick={() => run(async () => {
                    await api.del(`/sets/${s.id}`);
                    refresh();
                  })}><Icon name="trash" size={16} /></button>
                </div>
              ))}
            </div>
          ))}
          <button className="btn primary" onClick={() => setPicker(true)}><Icon name="plus" size={18} /> Add exercise</button>
        </>
      )}

      {w.notes && <p className="small muted">{w.notes}</p>}
      {w.source === "manual" && (
        <button className="btn danger" onClick={() => window.confirm("Delete this workout?") && run(async () => {
          await api.del(`/workouts/${w.id}`);
          qc.invalidateQueries();
          navigate("/train");
        }, "Deleted")}>Delete workout</button>
      )}

      {picker && <ExercisePicker onClose={() => setPicker(false)} onPick={(e) => { setPicker(false); setAdding(e); }} />}
      {adding && <SetSheet workoutId={w.id} exercise={adding} units={units}
                           lastInSession={groups.find(([eid]) => eid === adding.id)?.[1].sets.at(-1)}
                           onClose={() => setAdding(null)} onSaved={refresh} />}
    </div>
  );
}

function SetSheet({ workoutId, exercise, units, lastInSession, onClose, onSaved }: {
  workoutId: number; exercise: Exercise; units: "imperial" | "metric"; lastInSession?: LiftSet;
  onClose: () => void; onSaved: () => void;
}) {
  const run = useRun();
  const { data: last } = useQuery({
    queryKey: ["exercise-last", exercise.id],
    queryFn: () => api.get<{ date?: string; sets: Omit<LiftSet, "id">[]; best_e1rm_kg?: number }>(`/exercises/${exercise.id}/last`),
  });
  const seed = lastInSession ?? last?.sets.filter((s) => !s.is_warmup).at(-1);
  const [weight, setWeight] = useState<number | null>(null);
  const [reps, setReps] = useState<number | null>(null);
  const [rir, setRir] = useState<number | null>(2);
  const [warmup, setWarmup] = useState(false);
  const w = weight ?? (seed ? Math.round(fromKg(seed.weight_kg, units) / weightStep(units)) * weightStep(units) : 0);
  const r = reps ?? seed?.reps ?? 5;

  const add = (close: boolean) => run(async () => {
    await api.post(`/workouts/${workoutId}/sets`, { exercise_id: exercise.id, reps: r, weight_kg: toKg(w, units), rir, is_warmup: warmup });
    onSaved();
    if (close) onClose();
  }, close ? undefined : "Set added");

  return (
    <Sheet title={exercise.name} onClose={onClose}>
      <div className="stack">
        {last?.date && (
          <p className="tiny muted" style={{ margin: 0 }}>
            Last time ({dayLabel(last.date.slice(0, 10))}): {last.sets.filter((s) => !s.is_warmup)
              .map((s) => `${showWeight(s.weight_kg, units, 0)}×${s.reps}`).join(", ")}
            {last.best_e1rm_kg ? ` · best e1RM ${showWeight(last.best_e1rm_kg, units, 0)} ${weightUnit(units)}` : ""}
          </p>
        )}
        <label className="field">Weight ({weightUnit(units)})
          <Stepper value={w} onChange={setWeight} step={weightStep(units)} min={0} max={1200} digits={units === "imperial" ? 0 : 1} label="weight" />
        </label>
        <label className="field">Reps<Stepper value={r} onChange={setReps} step={1} min={1} max={100} label="reps" /></label>
        <label className="field">Reps in reserve<Scale n={5} start={0} value={rir} onChange={setRir} label="reps in reserve" /></label>
        <p className="tiny muted" style={{ margin: 0 }}>0 = failure · 2 = two more were possible. Sets at RIR ≤ 3 count as hard sets.</p>
        <label className="row small"><input type="checkbox" checked={warmup} onChange={(e) => setWarmup(e.target.checked)} /> Warm-up set</label>
        <div className="row">
          <button className="btn grow" onClick={() => add(false)}>Add + another</button>
          <button className="btn primary grow" onClick={() => add(true)}>Add</button>
        </div>
      </div>
    </Sheet>
  );
}

const PATTERNS = ["squat", "hinge", "lunge", "horizontal_push", "vertical_push", "horizontal_pull", "vertical_pull",
  "carry", "core", "plyometric", "other"] as const;

function ExercisePicker({ onClose, onPick }: { onClose: () => void; onPick: (e: Exercise) => void }) {
  const run = useRun();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["exercises"], queryFn: () => api.get<Exercise[]>("/exercises") });
  const [q, setQ] = useState("");
  const [creating, setCreating] = useState(false);
  // Muscle groups start collapsed; Recent starts open. A search expands everything.
  const [open, setOpen] = useState<Set<string>>(new Set(["Recent"]));
  const toggle = (t: string) => setOpen((o) => { const n = new Set(o); if (n.has(t)) n.delete(t); else n.add(t); return n; });
  const [pattern, setPattern] = useState<(typeof PATTERNS)[number]>("other");
  const [primary, setPrimary] = useState<string[]>([]);
  const [secondary, setSecondary] = useState<string[]>([]);
  const [main, setMain] = useState(false);
  const needle = q.trim().toLowerCase();
  const list = (data ?? []).filter((e) => !needle || e.name.toLowerCase().includes(needle)
    || e.muscles.some((m) => m.muscle_group.replace("_", " ").includes(needle)));
  const recent = needle ? [] : list.filter((e) => e.last_used)
    .sort((a, b) => b.last_used!.localeCompare(a.last_used!)).slice(0, 5);
  const sections: [string, Exercise[]][] = [...(recent.length ? [["Recent", recent] as [string, Exercise[]]] : []),
    ...groupByMuscle(list).map(([g, es]) => [muscleLabel(g), es] as [string, Exercise[]])];

  const cycle = (m: string) => {
    if (primary.includes(m)) { setPrimary(primary.filter((x) => x !== m)); setSecondary([...secondary, m]); }
    else if (secondary.includes(m)) setSecondary(secondary.filter((x) => x !== m));
    else setPrimary([...primary, m]);
  };

  return (
    <Sheet title={creating ? "New exercise" : "Add exercise"} onClose={onClose}>
      {!creating ? (
        <div className="stack">
          <input autoFocus placeholder="Search exercises" value={q} onChange={(e) => setQ(e.target.value)} />
          {sections.map(([title, es]) => {
            const isOpen = !!needle || open.has(title);
            return (
            <div key={title} className="list">
              <button type="button" className="list-head group-toggle" aria-expanded={isOpen} onClick={() => toggle(title)}>
                <span>{title}</span>
                <span className="row" style={{ gap: 6 }}>{es.length}<Icon name="chevron" size={14} /></span>
              </button>
              {isOpen && es.map((e) => (
                <button key={e.id} className="list-row button" onClick={() => onPick(e)}>
                  <div className="grow">
                    <div className="title">{e.name}{e.is_main_lift && <span className="badge" style={{ marginLeft: 6 }}>main</span>}</div>
                    <div className="meta">{muscleSummary(e)}</div>
                  </div>
                </button>
              ))}
            </div>
            );
          })}
          {needle && list.length === 0 && <Empty>No match. Create it below.</Empty>}
          <button className="btn" onClick={() => setCreating(true)}><Icon name="plus" size={18} /> {q ? `Create "${q}"` : "New exercise"}</button>
        </div>
      ) : (
        <div className="stack">
          <label className="field">Name<input value={q} onChange={(e) => setQ(e.target.value)} /></label>
          <label className="field">Movement pattern<Chips options={PATTERNS} value={pattern} onChange={setPattern} /></label>
          <div className="field">Muscles <span className="muted">(tap once = primary, twice = secondary)</span>
            <div className="row wrap">
              {MUSCLE_ORDER.map((m) => (
                <button key={m} type="button" onClick={() => cycle(m)}
                        className={`chip${primary.includes(m) ? " on" : ""}`}
                        style={secondary.includes(m) ? { borderColor: "var(--pine-2)", borderStyle: "dashed" } : undefined}>
                  {muscleLabel(m)}{secondary.includes(m) ? " ½" : ""}
                </button>
              ))}
            </div>
          </div>
          <label className="row small"><input type="checkbox" checked={main} onChange={(e) => setMain(e.target.checked)} /> Main lift (track estimated 1RM)</label>
          <button className="btn primary" disabled={!q.trim() || !primary.length} onClick={() => run(async () => {
            const muscles = [...primary.map((m) => ({ muscle_group: m, role: "primary", set_credit: 1 })),
              ...secondary.map((m) => ({ muscle_group: m, role: "secondary", set_credit: 0.5 }))];
            const { id } = await api.post<{ id: number }>("/exercises", { name: q.trim(), movement_pattern: pattern, is_main_lift: main, muscles });
            qc.invalidateQueries({ queryKey: ["exercises"] });
            onPick({ id, name: q.trim(), movement_pattern: pattern, is_main_lift: main, muscles: [], last_used: null } as unknown as Exercise);
          })}>Create</button>
        </div>
      )}
    </Sheet>
  );
}
