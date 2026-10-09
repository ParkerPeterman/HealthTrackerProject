import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Planned, type Workout } from "../api";
import { Chips, Empty, Icon, Scale, Sheet, Stepper, useRun, useUnits } from "../components/ui";
import { addDays, dayLabel, isoDate, toLocalInput } from "../dates";
import { fmtDuration, showDistance, showPace, distUnit } from "../units";

export default function Train() {
  const [sheet, setSheet] = useState<null | "game" | "workout" | "plan">(null);
  const [editPlan, setEditPlan] = useState<Planned | null>(null);
  const units = useUnits();
  const run = useRun();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const today = isoDate();
  const { data: workouts } = useQuery({
    queryKey: ["workouts"], queryFn: () => api.get<Workout[]>(`/workouts?start=${addDays(today, -21)}&end=${today}`),
  });
  const { data: planned } = useQuery({
    queryKey: ["planned"], queryFn: () => api.get<Planned[]>(`/planned?start=${today}&end=${addDays(today, 13)}`),
  });

  const startStrength = () => run(async () => {
    const w = await api.post<Workout>("/workouts", {
      sport: "strength", duration_min: 60, start_ts: new Date().toISOString(), notes: null,
    });
    qc.invalidateQueries({ queryKey: ["workouts"] });
    navigate(`/train/${w.id}`);
  });

  const byDay = new Map<string, Planned[]>();
  for (const p of planned ?? []) byDay.set(p.date, [...(byDay.get(p.date) ?? []), p]);

  return (
    <div className="stack">
      <header className="topbar"><h1>Training</h1></header>
      <div className="grid3">
        <button className="btn accent" onClick={() => setSheet("game")}><Icon name="disc" size={18} /> Game</button>
        <button className="btn primary" onClick={startStrength}><Icon name="train" size={18} /> Lift</button>
        <button className="btn" onClick={() => setSheet("workout")}><Icon name="plus" size={18} /> Other</button>
      </div>
      <p className="tiny muted" style={{ margin: 0 }}>
        Runs and anything recorded on the watch arrive from Garmin automatically. Log games (watch off) and lifts here.
      </p>

      <div className="spread">
        <h3 className="section-title">Next two weeks</h3>
        <button className="btn small" onClick={() => setSheet("plan")}><Icon name="plus" size={16} /> Plan</button>
      </div>
      <div className="list">
        {byDay.size === 0 && <div className="list-row muted small">Nothing planned. Add your games; the weekly job plans around them.</div>}
        {[...byDay.entries()].map(([d, items]) => (
          <div key={d}>
            <div className="list-head"><span>{dayLabel(d, today)}</span></div>
            {items.map((p) => (
              <button key={p.id} className="list-row button" onClick={() => setEditPlan(p)}>
                {p.session_type === "game" ? <span className="badge game">game</span> : <span className="badge">{p.session_type}</span>}
                <div className="grow">
                  <div className="title">{p.notes || p.sport}</div>
                  <div className="meta">
                    {[p.planned_duration_min && `${p.planned_duration_min} min`, p.planned_rpe && `RPE ${p.planned_rpe}`,
                      p.origin === "generated" ? "auto-planned" : null, p.status !== "planned" ? p.status : null]
                      .filter(Boolean).join(" · ")}
                  </div>
                </div>
                <Icon name="chevron" size={18} />
              </button>
            ))}
          </div>
        ))}
      </div>

      <h3 className="section-title">Recent workouts</h3>
      <div className="list">
        {!workouts?.length && <Empty>No workouts in the last three weeks.</Empty>}
        {workouts?.map((w) => (
          <Link key={w.id} to={`/train/${w.id}`} className="list-row button" style={{ textDecoration: "none", color: "inherit" }}>
            <div className="grow">
              <div className="title row" style={{ gap: 6 }}>
                {w.sport}
                {w.is_game && <span className="badge game">game</span>}
                {w.source === "garmin" && <span className="badge garmin">garmin</span>}
              </div>
              <div className="meta">
                {dayLabel(w.date ?? w.start_ts.slice(0, 10), today)} · {fmtDuration(w.duration_s)}
                {w.distance_m ? ` · ${showDistance(w.distance_m, units)} ${distUnit(units)} · ${showPace(w.distance_m, w.duration_s, units)}` : ""}
                {w.n_sets ? ` · ${w.n_sets} sets` : ""}
              </div>
            </div>
            <div className="num small" style={{ textAlign: "right" }}>
              {w.rpe ? `RPE ${w.rpe}` : <span className="warn">RPE?</span>}
              <div className="tiny muted">{w.session_load ? `load ${Math.round(w.session_load)}` : ""}</div>
            </div>
          </Link>
        ))}
      </div>

      {sheet === "game" && <GameSheet onClose={() => setSheet(null)} />}
      {sheet === "workout" && <WorkoutSheet onClose={() => setSheet(null)} />}
      {(sheet === "plan" || editPlan) && (
        <PlanSheet planned={editPlan ?? undefined} onClose={() => { setSheet(null); setEditPlan(null); }} />
      )}
    </div>
  );
}

export function GameSheet({ onClose, planned }: { onClose: () => void; planned?: Planned }) {
  const run = useRun();
  const qc = useQueryClient();
  const [minutes, setMinutes] = useState(planned?.planned_duration_min ?? 90);
  const [rpe, setRpe] = useState<number | null>(null);
  const [start, setStart] = useState(() => toLocalInput(new Date(Date.now() - 90 * 60_000)));
  const [notes, setNotes] = useState("");
  return (
    <Sheet title="Log a game" onClose={onClose}>
      <div className="stack">
        <label className="field">Minutes played (including warm-up)
          <Stepper value={minutes} onChange={setMinutes} step={5} min={5} max={300} label="minutes" />
        </label>
        <label className="field">Effort (RPE 1-10)<Scale n={10} value={rpe} onChange={setRpe} label="effort" /></label>
        <p className="tiny muted" style={{ margin: 0 }}>1 very easy · 5 hard but steady · 8 very hard · 10 max</p>
        <label className="field">Started<input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <input placeholder="Notes (opponent, how it felt)" value={notes} onChange={(e) => setNotes(e.target.value)} />
        <button className="btn primary" disabled={!rpe} onClick={() => run(async () => {
          await api.post("/workouts", {
            sport: "ultimate", is_game: true, duration_min: minutes, rpe, start_ts: new Date(start).toISOString(),
            notes: notes || null,
          });
          qc.invalidateQueries();
          onClose();
        }, "Game logged")}>Save game</button>
      </div>
    </Sheet>
  );
}

const SPORTS = ["run", "bike", "strength", "ultimate", "swim", "walk", "other"] as const;

function WorkoutSheet({ onClose }: { onClose: () => void }) {
  const run = useRun();
  const qc = useQueryClient();
  const units = useUnits();
  const [sport, setSport] = useState<(typeof SPORTS)[number]>("ultimate");
  const [minutes, setMinutes] = useState(60);
  const [rpe, setRpe] = useState<number | null>(null);
  const [distance, setDistance] = useState("");
  const [start, setStart] = useState(() => toLocalInput(new Date(Date.now() - 60 * 60_000)));
  const [notes, setNotes] = useState("");
  return (
    <Sheet title="Log a workout" onClose={onClose}>
      <div className="stack">
        <Chips options={SPORTS} value={sport} onChange={setSport} labels={{ ultimate: "ultimate practice" }} />
        <label className="field">Minutes<Stepper value={minutes} onChange={setMinutes} step={5} min={5} max={600} label="minutes" /></label>
        <label className="field">Effort (RPE 1-10)<Scale n={10} value={rpe} onChange={setRpe} label="effort" /></label>
        {["run", "bike", "swim", "walk"].includes(sport) && (
          <label className="field">Distance ({distUnit(units)})
            <input inputMode="decimal" value={distance} onChange={(e) => setDistance(e.target.value)} />
          </label>
        )}
        <label className="field">Started<input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <input placeholder="Notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
        <button className="btn primary" disabled={!rpe} onClick={() => run(async () => {
          const d = parseFloat(distance);
          await api.post("/workouts", {
            sport, duration_min: minutes, rpe, start_ts: new Date(start).toISOString(), notes: notes || null,
            distance_m: Number.isFinite(d) ? (units === "imperial" ? d * 1609.344 : d * 1000) : null,
          });
          qc.invalidateQueries();
          onClose();
        }, "Workout logged")}>Save</button>
      </div>
    </Sheet>
  );
}

const TYPES = ["game", "easy", "moderate", "hard", "strength", "rest"] as const;
const MUSCLES = ["quads", "hamstrings", "glutes", "calves", "adductors", "chest", "upper_back", "lats",
  "shoulders", "biceps", "triceps", "core"];

function PlanSheet({ planned, onClose }: { planned?: Planned; onClose: () => void }) {
  const run = useRun();
  const qc = useQueryClient();
  const [p, setP] = useState<Partial<Planned>>(planned ?? {
    date: addDays(isoDate(), 1), session_type: "game", sport: "ultimate", planned_duration_min: 90, planned_rpe: 8,
    focus_muscles: [], status: "planned",
  });
  const save = () => run(async () => {
    const body = { ...p, focus_muscles: p.focus_muscles?.length ? p.focus_muscles : null };
    if (planned) await api.put(`/planned/${planned.id}`, body);
    else await api.post("/planned", body);
    qc.invalidateQueries();
    onClose();
  }, "Plan saved");
  const toggleMuscle = (m: string) => {
    const cur = new Set(p.focus_muscles ?? []);
    if (cur.has(m)) cur.delete(m); else cur.add(m);
    setP({ ...p, focus_muscles: [...cur] });
  };
  return (
    <Sheet title={planned ? "Planned session" : "Plan a session"} onClose={onClose}>
      <div className="stack">
        {planned?.origin === "generated" && (
          <p className="tiny muted" style={{ margin: 0 }}>Auto-planned by the weekly job. Editing it makes it yours, so it won't be regenerated.</p>
        )}
        <label className="field">Date<input type="date" value={p.date} onChange={(e) => setP({ ...p, date: e.target.value })} /></label>
        <Chips options={TYPES} value={p.session_type ?? null}
               onChange={(t) => setP({ ...p, session_type: t, sport: t === "game" ? "ultimate" : t === "strength" ? "strength" : t === "rest" ? "rest" : p.sport === "ultimate" ? "run" : p.sport })} />
        <label className="field">Sport<input value={p.sport ?? ""} onChange={(e) => setP({ ...p, sport: e.target.value })} /></label>
        <div className="grid2">
          <label className="field">Minutes<input inputMode="numeric" value={p.planned_duration_min ?? ""}
                 onChange={(e) => setP({ ...p, planned_duration_min: parseInt(e.target.value) || null })} /></label>
          <label className="field">Planned RPE<input inputMode="numeric" value={p.planned_rpe ?? ""}
                 onChange={(e) => setP({ ...p, planned_rpe: Math.min(10, Math.max(1, parseInt(e.target.value))) || null })} /></label>
        </div>
        {p.session_type === "strength" && (
          <div className="field">Focus muscles
            <div className="row wrap">
              {MUSCLES.map((m) => (
                <button key={m} type="button" className={`chip${p.focus_muscles?.includes(m) ? " on" : ""}`}
                        onClick={() => toggleMuscle(m)}>{m.replace("_", " ")}</button>
              ))}
            </div>
          </div>
        )}
        {planned && (
          <Chips options={["planned", "done", "skipped", "modified"] as const} value={p.status ?? "planned"}
                 onChange={(s) => setP({ ...p, status: s })} />
        )}
        <input placeholder="Notes" value={p.notes ?? ""} onChange={(e) => setP({ ...p, notes: e.target.value || null })} />
        <div className="row">
          {planned && (
            <button className="btn danger" onClick={() => run(async () => {
              await api.del(`/planned/${planned.id}`);
              qc.invalidateQueries();
              onClose();
            }, "Removed")}><Icon name="trash" size={18} /></button>
          )}
          <button className="btn primary grow" onClick={save} disabled={!p.date || !p.sport}>Save</button>
        </div>
      </div>
    </Sheet>
  );
}
