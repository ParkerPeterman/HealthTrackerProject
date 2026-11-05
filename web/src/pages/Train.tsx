import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  api, PHASE_LABEL, type FitnessData, type IntensityWeek, type Planned, type PlanReason, type SeasonData, type Workout,
} from "../api";
import { cap, Chips, Empty, Icon, Scale, sessionLook, Sheet, Stepper, useRun, useUnits } from "../components/ui";
import { addDays, dayLabel, isoDate, toLocalInput } from "../dates";
import { fmtDuration, pace_s_per_km, showDistance, showPace, distUnit } from "../units";
import { LibraryDetail, LibraryPicker } from "./Library";
import { TestsCard } from "./Tests";

export default function Train() {
  const [sheet, setSheet] = useState<null | "game" | "workout" | "plan">(null);
  const [editPlan, setEditPlan] = useState<Planned | null>(null);
  const units = useUnits();
  const run = useRun();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const today = isoDate();
  const { data: workouts, error: workoutsError } = useQuery({
    queryKey: ["workouts"], queryFn: () => api.get<Workout[]>(`/workouts?start=${addDays(today, -21)}&end=${today}`),
  });
  const { data: season } = useQuery({ queryKey: ["season"], queryFn: () => api.get<SeasonData>("/season") });
  const { data: planned, error: plannedError } = useQuery({
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
  const week = (workouts ?? []).filter((w) => (w.date ?? w.start_ts.slice(0, 10)) > addDays(today, -7));
  const weekMin = week.reduce((s, w) => s + w.duration_s, 0) / 60;
  const weekLoad = week.reduce((s, w) => s + (w.session_load ?? 0), 0);

  return (
    <div className="stack">
      <header className="topbar">
        <div><div className="eyebrow">{season ? `${PHASE_LABEL[season.current]} phase${season.weeks_to_first_game ? ` · ${season.weeks_to_first_game} weeks to the first game` : ""}` : "Last 7 days · next 14"}</div><h1>Training</h1></div>
      </header>
      {season && <p className="tiny muted" style={{ margin: "0 4px" }}>{season.current_focus}</p>}

      <section className="card" aria-label="Last 7 days">
        <div className="grid3">
          <WeekStat label="Sessions" value={workouts ? String(week.length) : "–"} />
          <WeekStat label="Time" value={!workouts ? "–" : weekMin >= 60 ? `${Math.floor(weekMin / 60)}h ${String(Math.round(weekMin % 60)).padStart(2, "0")}` : `${Math.round(weekMin)}m`} />
          <WeekStat label="Load" value={workouts ? Math.round(weekLoad).toLocaleString() : "–"} />
        </div>
        <IntensityBar />
      </section>

      <div className="action-grid">
        <button className="action-tile" onClick={() => setSheet("game")}>
          <span className="icon-circle t-game"><Icon name="disc" size={20} /></span>
          <span>Game<span className="sub">watch off</span></span>
        </button>
        <button className="action-tile" onClick={startStrength}>
          <span className="icon-circle t-strength"><Icon name="kettlebell" size={20} /></span>
          <span>Lift<span className="sub">sets and reps</span></span>
        </button>
        <button className="action-tile" onClick={() => setSheet("workout")}>
          <span className="icon-circle"><Icon name="plus" size={20} /></span>
          <span>Other<span className="sub">any session</span></span>
        </button>
      </div>
      <p className="tiny muted" style={{ margin: "0 4px" }}>
        Runs and anything recorded on the watch arrive from Garmin automatically. Log games (watch off) and lifts here.
      </p>

      <div className="spread">
        <h3 className="section-title">Next two weeks</h3>
        <button className="btn small" onClick={() => setSheet("plan")} style={{ marginTop: 16 }}><Icon name="plus" size={16} /> Plan</button>
      </div>
      <div className="list">
        {!planned && <div className="list-row muted small">{plannedError ? "Can't reach the server." : "Loading…"}</div>}
        {planned && byDay.size === 0 && <div className="list-row muted small">Nothing planned. Add your games; the weekly job plans around them.</div>}
        {[...byDay.entries()].map(([d, items]) => (
          <div key={d}>
            <div className="list-head"><span>{dayLabel(d, today)}</span></div>
            {items.map((p) => {
              const look = sessionLook(p.session_type, p.sport);
              return (
                <button key={p.id} className="list-row button" onClick={() => setEditPlan(p)}>
                  <span className={`icon-circle ${look.hue}`} style={{ width: 36, height: 36 }}><Icon name={look.icon} size={17} /></span>
                  <div className="grow">
                    <div className="title">{cap(p.notes || p.sport)}</div>
                    <div className="meta">
                      {[p.session_type, p.planned_duration_min && `${p.planned_duration_min} min`, p.planned_rpe && `RPE ${p.planned_rpe}`,
                        p.addon_names?.length ? `+ ${p.addon_names.join(", ")}` : null, p.optional ? "optional" : null,
                        p.origin === "generated" ? "auto-planned" : null, p.status !== "planned" ? p.status : null]
                        .filter(Boolean).join(" · ")}
                    </div>
                  </div>
                  <Icon name="chevron" size={18} />
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <FitnessCard />

      <TestsCard />

      <h3 className="section-title">Recent workouts</h3>
      <div className="list">
        {!workouts && <Empty>{workoutsError ? "Can't reach the server. Is the API running?" : "Loading…"}</Empty>}
        {workouts?.length === 0 && <Empty>No workouts in the last three weeks.</Empty>}
        {workouts?.map((w) => {
          const look = sessionLook(w.is_game ? "game" : w.sport === "strength" ? "strength" : (w.rpe ?? 0) >= 7 ? "hard" : "easy", w.sport);
          return (
            <Link key={w.id} to={`/train/${w.id}`} className="list-row button" style={{ textDecoration: "none", color: "inherit" }}>
              <span className={`icon-circle ${look.hue}`} style={{ width: 36, height: 36 }}><Icon name={look.icon} size={17} /></span>
              <div className="grow">
                <div className="title row" style={{ gap: 6 }}>
                  {cap(w.sport)}
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
          );
        })}
      </div>

      {sheet === "game" && <GameSheet onClose={() => setSheet(null)} />}
      {sheet === "workout" && <WorkoutSheet onClose={() => setSheet(null)} />}
      {(sheet === "plan" || editPlan) && (
        <PlanSheet planned={editPlan ?? undefined} onClose={() => { setSheet(null); setEditPlan(null); }} />
      )}
    </div>
  );
}

const PATTERN: Record<IntensityWeek["pattern"], string> = {
  pyramidal: "pyramidal", polarized: "polarized", threshold: "mostly moderate", moderate_heavy: "too much moderately hard",
  too_little: "too little to judge",
};

/** D4: this week's easy / moderate / hard minutes. */
function IntensityBar() {
  const { data } = useQuery({ queryKey: ["intensity"], queryFn: () => api.get<IntensityWeek[]>("/intensity?weeks=2") });
  const w = data?.at(-1);
  if (!w) return null;
  const total = w.low_min + w.mid_min + w.high_min;
  if (!total) return null;
  const parts: [number, string][] = [[w.low_min, "var(--hrv)"], [w.mid_min, "var(--load)"], [w.high_min, "var(--heart)"]];
  return (
    <div style={{ marginTop: 14 }}>
      <div className="row" style={{ gap: 2, height: 8, borderRadius: 6, overflow: "hidden" }} role="img"
           aria-label={`This week: ${Math.round(w.low_min)} easy, ${Math.round(w.mid_min)} moderate, ${Math.round(w.high_min)} hard minutes`}>
        {parts.map(([m, c], i) => m > 0 && <span key={i} style={{ width: `${(m / total) * 100}%`, height: "100%", background: c }} />)}
      </div>
      <div className="tiny muted" style={{ marginTop: 6 }}>
        Week of {w.week_start.slice(5)}: {w.easy_pct ?? "–"}% easy · {PATTERN[w.pattern]} (aim 75–80% easy)
      </div>
    </div>
  );
}

function WeekStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="eyebrow">{label}</div>
      <div className="big-num md" style={{ marginTop: 6 }}>{value}</div>
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

const SPORTS = ["run", "bike", "strength", "speed", "ultimate", "swim", "walk", "other"] as const;

function WorkoutSheet({ onClose }: { onClose: () => void }) {
  const run = useRun();
  const qc = useQueryClient();
  const units = useUnits();
  const [sport, setSport] = useState<(typeof SPORTS)[number]>("ultimate");
  const [minutes, setMinutes] = useState(60);
  const [rpe, setRpe] = useState<number | null>(null);
  const [distance, setDistance] = useState("");
  const [sprintM, setSprintM] = useState("");
  const [contacts, setContacts] = useState("");
  const [start, setStart] = useState(() => toLocalInput(new Date(Date.now() - 60 * 60_000)));
  const [notes, setNotes] = useState("");
  return (
    <Sheet title="Log a workout" onClose={onClose}>
      <div className="stack">
        <Chips options={SPORTS} value={sport} onChange={setSport} labels={{ ultimate: "ultimate practice", speed: "sprints & jumps" }} />
        <label className="field">Minutes<Stepper value={minutes} onChange={setMinutes} step={5} min={5} max={600} label="minutes" /></label>
        <label className="field">Effort (RPE 1-10)<Scale n={10} value={rpe} onChange={setRpe} label="effort" /></label>
        {sport === "speed" && (
          <div className="grid2">
            <label className="field">Sprint metres<input inputMode="numeric" value={sprintM} onChange={(e) => setSprintM(e.target.value)} /></label>
            <label className="field">Jump contacts<input inputMode="numeric" value={contacts} onChange={(e) => setContacts(e.target.value)} /></label>
          </div>
        )}
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
            sprint_m: sport === "speed" && sprintM ? Number(sprintM) : null,
            plyo_contacts: sport === "speed" && contacts ? Number(contacts) : null,
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
        {planned?.reason && <PlanReasonView reason={planned.reason} optional={planned.optional} />}
        {planned?.session_code && p.date && (
          <div className="card tight"><LibraryDetail code={planned.session_code} date={p.date} addons={planned.addons} /></div>
        )}
        <label className="field">Date<input type="date" value={p.date} onChange={(e) => setP({ ...p, date: e.target.value })} /></label>
        {!planned && p.date && p.session_type !== "game" && (
          <LibraryPicker date={p.date} value={p.session_code} onPick={(s) => setP(s ? {
            ...p, session_code: s.code, sport: s.sport, session_type: s.plan_type, notes: s.name, planned_rpe: s.rpe,
            planned_duration_min: Math.round((s.duration_min_lo + s.duration_min_hi) / 2),
          } : { ...p, session_code: null })} />
        )}
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

/** F3: the planner's structured reason for a session. */
export function PlanReasonView({ reason, optional }: { reason: PlanReason; optional?: boolean }) {
  const lines = [reason.progression, reason.copenhagen, reason.day !== "as planned" ? cap(reason.day) : null,
    ...reason.changes.map(cap)].filter(Boolean) as string[];
  return (
    <div className="stack" style={{ gap: 4 }}>
      <div className="small">
        <span style={{ fontWeight: 550 }}>{reason.key ? "Key session" : optional ? "Optional" : "Why"}: </span>{reason.why}.
      </div>
      {lines.map((l) => <div key={l} className="tiny muted">{l}.</div>)}
    </div>
  );
}

const COVARIATE: Record<string, string> = { temp: "temperature", humid: "humidity", climb: "climb", duration: "run length" };

/** D3: aerobic fitness as pace at the reference HR, at reference conditions, with its trend. */
function FitnessCard() {
  const units = useUnits();
  const { data } = useQuery({ queryKey: ["fitness"], queryFn: () => api.get<FitnessData>("/fitness") });
  const pace = data?.aerobic_pace;
  const t = pace?.latest;
  if (!data || !t || t.level == null) return null;
  const perUnit = (v: number) => (units === "imperial" ? v * 1.609344 : v);
  const slope = t.slope_week, lo = t.slope_lo80, hi = t.slope_hi80;
  let trend = "";
  if (!pace.learning && slope != null && lo != null && hi != null) {
    const s = (v: number) => `${Math.abs(perUnit(v)).toFixed(1)}`;
    trend = hi < 0 ? `Improving about ${s(slope)} s/${distUnit(units)} a week (80% range ${s(hi)}–${s(lo)}).`
      : lo > 0 ? `Slower by about ${s(slope)} s/${distUnit(units)} a week (80% range ${s(lo)}–${s(hi)}).`
        : "No clear change yet.";
  }
  const vo2 = data.vo2max.latest;
  const dec = data.decoupling;
  return (
    <section className="card stack" aria-label="Aerobic fitness">
      <div className="spread"><h3>Aerobic fitness</h3><span className="tiny muted">pace at your reference HR</span></div>
      <div className="row" style={{ alignItems: "baseline", gap: 10 }}>
        <span className="big-num md">{pace_s_per_km(t.level, units)}</span>
        {t.level_lo80 != null && t.level_hi80 != null && (
          <span className="tiny muted">80% range {pace_s_per_km(t.level_lo80, units)}–{pace_s_per_km(t.level_hi80, units)}</span>
        )}
      </div>
      <p className="small" style={{ margin: 0 }}>
        {pace.learning ? `Still learning: ${t.n_obs} of ${data.min_runs} steady runs.` : trend}
        {pace.adjusted_for.length ? ` Adjusted for ${pace.adjusted_for.map((c) => COVARIATE[c] ?? c).join(", ")}.` : ""}
        {" "}Lower is fitter.
      </p>
      {vo2?.level != null && (
        <p className="tiny muted" style={{ margin: 0 }}>
          Garmin VO₂ max, smoothed: {vo2.level.toFixed(1)}. It depends on the max HR set in your Garmin profile.
        </p>
      )}
      {dec.recent.length > 0 && (
        <p className="tiny muted" style={{ margin: 0 }}>
          Decoupling on recent long steady runs: {dec.recent.map((r) => `${r.decoupling_pct.toFixed(1)}%`).join(", ")}
          {" "}(under {dec.threshold_pct}% is good aerobic durability).
        </p>
      )}
    </section>
  );
}
