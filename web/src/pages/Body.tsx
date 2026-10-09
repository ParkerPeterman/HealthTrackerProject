import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Exercise, type Injury, type Journal } from "../api";
import { groupByMuscle, muscleLabel } from "../exercises";
import { Chips, Empty, Icon, Scale, ScaleRow, Sheet, Stepper, useRun, useUnits } from "../components/ui";
import { dayLabel, isoDate, toLocalInput } from "../dates";
import { fromKg, showWeight, weightUnit } from "../units";

export default function Body() {
  const units = useUnits();
  const [sheet, setSheet] = useState<null | "injury" | "event" | { checkin: number; label: string } | { restrict: Injury } | { edit: Injury }>(null);
  const { data: weights } = useQuery({
    queryKey: ["weights", 90],
    queryFn: () => api.get<{ date: string; weight_kg: number; weight_kg_smoothed: number | null }[]>("/weights?days=90"),
  });
  const { data: injuries } = useQuery({ queryKey: ["injuries"], queryFn: () => api.get<Injury[]>("/injuries?include_resolved=true") });
  const { data: journal } = useQuery({ queryKey: ["journal"], queryFn: () => api.get<Journal[]>("/journal") });
  const { data: events } = useQuery({
    queryKey: ["events"], queryFn: () => api.get<{ id: number; ts: string; type_code: string; value: number | null; note: string | null }[]>("/events?days=21"),
  });
  const run = useRun();
  const qc = useQueryClient();
  const active = injuries?.filter((i) => i.status !== "resolved") ?? [];
  const past = injuries?.filter((i) => i.status === "resolved") ?? [];
  const latest = weights?.at(-1);

  return (
    <div className="stack">
      <header className="topbar"><h1>Body</h1></header>

      <section className="card">
        <div className="spread">
          <h3>Weight</h3>
          <span className="small num">
            {latest ? <>{showWeight(latest.weight_kg_smoothed ?? latest.weight_kg, units)} {weightUnit(units)} <span className="muted">trend</span></> : "–"}
          </span>
        </div>
        {weights && weights.length > 1 ? <Sparkline values={weights.map((w) => fromKg(w.weight_kg, units))}
                                                     trend={weights.map((w) => (w.weight_kg_smoothed == null ? null : fromKg(w.weight_kg_smoothed, units)))} />
          : <Empty>Log a morning weight on the Today tab.</Empty>}
        <p className="tiny muted" style={{ margin: "4px 0 0" }}>Dots are scale readings; the line is the smoothed trend (last 90 days).</p>
      </section>

      <div className="spread">
        <h3 className="section-title">Injuries</h3>
        <button className="btn small" onClick={() => setSheet("injury")}><Icon name="plus" size={16} /> New</button>
      </div>
      {active.length === 0 && <div className="card tight small muted">No active injuries.</div>}
      {active.map((i) => {
        const label = `${i.side && i.side !== "n/a" ? i.side + " " : ""}${i.body_part}`;
        const recent = i.checkins.slice(0, 5);
        return (
          <section key={i.id} className="card stack">
            <div className="spread">
              <div>
                <h3 style={{ textTransform: "capitalize" }}>{label} · {i.injury_type}</h3>
                <div className="tiny muted">since {dayLabel(i.onset_date)} · {i.status}{i.seen_professional ? " · seen a professional" : ""}</div>
              </div>
              <button className="btn small ghost" onClick={() => setSheet({ edit: i })}>Edit</button>
            </div>
            <div className="small">
              Activity pain, latest first:{" "}
              <b className="num">{recent.length ? recent.map((c) => c.pain_activity ?? "–").join(" · ") : "no check-ins"}</b>
            </div>
            {i.restrictions.length > 0 && (
              <div className="list">
                {i.restrictions.map((r) => (
                  <div key={r.id} className="list-row small">
                    <span className="grow">
                      <b>{r.exercise_name ?? r.muscle_group ?? r.movement_pattern ?? r.activity}</b>{" "}
                      <span className="muted">{r.max_rpe === 0 ? "avoid" : r.max_rpe != null ? `max RPE ${r.max_rpe}` : "limit"}</span>
                    </span>
                    <span className="tiny muted">{r.end_date ? `until ${dayLabel(r.end_date)}` : "until lifted"}</span>
                    <button className="btn ghost small danger" aria-label="Remove restriction"
                            onClick={() => run(async () => { await api.del(`/restrictions/${r.id}`); qc.invalidateQueries({ queryKey: ["injuries"] }); })}>
                      <Icon name="x" size={16} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="row">
              <button className={`btn grow ${i.checked_in_today ? "" : "accent"}`} onClick={() => setSheet({ checkin: i.id, label })}>
                {i.checked_in_today ? "Update check-in" : "Daily check-in"}
              </button>
              <button className="btn grow" onClick={() => setSheet({ restrict: i })}>Add restriction</button>
            </div>
          </section>
        );
      })}
      {past.length > 0 && (
        <details className="card tight">
          <summary className="small">Resolved ({past.length})</summary>
          {past.map((i) => (
            <div key={i.id} className="small" style={{ marginTop: 6 }}>
              {i.side && i.side !== "n/a" ? i.side + " " : ""}{i.body_part} {i.injury_type} · {dayLabel(i.onset_date)} – {i.resolved_date ? dayLabel(i.resolved_date) : "?"}
            </div>
          ))}
        </details>
      )}

      <div className="spread">
        <h3 className="section-title">Life events</h3>
        <button className="btn small" onClick={() => setSheet("event")}><Icon name="plus" size={16} /> Log</button>
      </div>
      <p className="tiny muted" style={{ margin: 0 }}>Sick and travel days are left out of your baselines so they don't skew "normal".</p>
      <div className="list">
        {!events?.length && <div className="list-row small muted">Nothing in the last three weeks.</div>}
        {events?.filter((e) => e.type_code !== "supplement").slice(0, 8).map((e) => (
          <div key={e.id} className="list-row small">
            <span className="badge">{e.type_code}</span>
            <span className="grow">{e.value != null ? `${e.value} min ` : ""}{e.note ?? ""}</span>
            <span className="tiny muted">{dayLabel(e.ts.slice(0, 10))}</span>
            <button className="btn ghost small danger" aria-label="Delete event"
                    onClick={() => run(async () => { await api.del(`/events/${e.id}`); qc.invalidateQueries({ queryKey: ["events"] }); })}>
              <Icon name="x" size={16} />
            </button>
          </div>
        ))}
      </div>

      <h3 className="section-title">Journal</h3>
      <div className="list">
        {!journal?.length && <div className="list-row small muted">No entries yet.</div>}
        {journal?.slice(0, 14).map((j) => (
          <div key={j.date} className="list-row small num">
            <span style={{ width: 92 }}>{dayLabel(j.date)}</span>
            <span className="grow muted">E {j.energy ?? "–"} · M {j.mood ?? "–"} · S {j.soreness ?? "–"} · St {j.stress ?? "–"}</span>
            {j.note && <span className="tiny muted" title={j.note}>note</span>}
          </div>
        ))}
      </div>

      {sheet === "injury" && <InjurySheet onClose={() => setSheet(null)} />}
      {sheet === "event" && <EventSheet onClose={() => setSheet(null)} />}
      {sheet && typeof sheet === "object" && "checkin" in sheet && <CheckinSheet injuryId={sheet.checkin} label={sheet.label} onClose={() => setSheet(null)} />}
      {sheet && typeof sheet === "object" && "restrict" in sheet && <RestrictionSheet injury={sheet.restrict} onClose={() => setSheet(null)} />}
      {sheet && typeof sheet === "object" && "edit" in sheet && <InjurySheet injury={sheet.edit} onClose={() => setSheet(null)} />}
    </div>
  );
}

function Sparkline({ values, trend }: { values: number[]; trend: (number | null)[] }) {
  const all = [...values, ...trend.filter((v): v is number => v != null)];
  const lo = Math.min(...all) - 0.5;
  const hi = Math.max(...all) + 0.5;
  const W = 320;
  const H = 90;
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * (W - 8) + 4;
  const y = (v: number) => H - 6 - ((v - lo) / (hi - lo || 1)) * (H - 12);
  const path = trend.map((v, i) => (v == null ? "" : `${trend.slice(0, i).some((t) => t != null) ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)).join("");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img"
         aria-label={`Weight from ${values[0].toFixed(1)} to ${values.at(-1)!.toFixed(1)}`} style={{ marginTop: 8 }}>
      {values.map((v, i) => <circle key={i} cx={x(i)} cy={y(v)} r={2.5} fill="var(--muted)" opacity={0.55} />)}
      <path d={path} fill="none" stroke="var(--pine-2)" strokeWidth={2} strokeLinecap="round" />
      <text x={W - 4} y={12} textAnchor="end" fontSize={10} fill="var(--muted)">{hi.toFixed(0)}</text>
      <text x={W - 4} y={H - 2} textAnchor="end" fontSize={10} fill="var(--muted)">{lo.toFixed(0)}</text>
    </svg>
  );
}

export function CheckinSheet({ injuryId, label, onClose }: { injuryId: number; label: string; onClose: () => void }) {
  const run = useRun();
  const qc = useQueryClient();
  const [painRest, setPainRest] = useState<number | null>(null);
  const [painAct, setPainAct] = useState<number | null>(null);
  const [fn, setFn] = useState<number | null>(null);
  const [swelling, setSwelling] = useState(false);
  const [note, setNote] = useState("");
  return (
    <Sheet title={`Check-in: ${label}`} onClose={onClose}>
      <div className="stack">
        <label className="field">Pain at rest (0-10)<Scale n={11} start={0} value={painRest} onChange={setPainRest} label="pain at rest" /></label>
        <label className="field">Pain during activity (0-10)<Scale n={11} start={0} value={painAct} onChange={setPainAct} label="pain during activity" /></label>
        <ScaleRow name="Function" hint="1 can't use · 5 normal" value={fn} onChange={setFn} label="function" />
        <label className="row small"><input type="checkbox" checked={swelling} onChange={(e) => setSwelling(e.target.checked)} /> Visible swelling</label>
        <input placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        <p className="tiny muted" style={{ margin: 0 }}>Load goes up only after three sessions with activity pain at 3 or lower. Swelling, being unable to bear weight, or pain rising over a week means: see a professional.</p>
        <button className="btn primary" onClick={() => run(async () => {
          await api.put(`/injuries/${injuryId}/checkins/${isoDate()}`, {
            pain_rest: painRest, pain_activity: painAct, function_score: fn, swelling, note: note || null,
          });
          qc.invalidateQueries({ queryKey: ["injuries"] });
          qc.invalidateQueries({ queryKey: ["today"] });
          onClose();
        }, "Check-in saved")}>Save</button>
      </div>
    </Sheet>
  );
}

const INJURY_TYPES = ["strain", "sprain", "tendon", "contusion", "soreness", "other"] as const;
const SIDES = ["left", "right", "both", "n/a"] as const;
const STATUSES = ["active", "recovering", "resolved"] as const;

function InjurySheet({ injury, onClose }: { injury?: Injury; onClose: () => void }) {
  const run = useRun();
  const qc = useQueryClient();
  const [i, setI] = useState<Partial<Injury>>(injury ?? { onset_date: isoDate(), injury_type: "strain", side: "n/a", status: "active", seen_professional: false });
  return (
    <Sheet title={injury ? "Edit injury" : "New injury"} onClose={onClose}>
      <div className="stack">
        <label className="field">Body part<input placeholder="ankle, hamstring, shoulder…" value={i.body_part ?? ""} onChange={(e) => setI({ ...i, body_part: e.target.value })} /></label>
        <Chips options={SIDES} value={(i.side as (typeof SIDES)[number]) ?? "n/a"} onChange={(v) => setI({ ...i, side: v })} />
        <Chips options={INJURY_TYPES} value={(i.injury_type as (typeof INJURY_TYPES)[number]) ?? "strain"} onChange={(v) => setI({ ...i, injury_type: v })} />
        <label className="field">Started<input type="date" value={i.onset_date} onChange={(e) => setI({ ...i, onset_date: e.target.value })} /></label>
        <label className="field">How it happened<input placeholder="rolled it on a cut" value={i.mechanism ?? ""} onChange={(e) => setI({ ...i, mechanism: e.target.value || null })} /></label>
        {injury && <Chips options={STATUSES} value={i.status ?? "active"} onChange={(v) => setI({ ...i, status: v })} />}
        <label className="row small"><input type="checkbox" checked={i.seen_professional ?? false} onChange={(e) => setI({ ...i, seen_professional: e.target.checked })} /> Seen a doctor or physical therapist</label>
        <input placeholder="Notes" value={i.notes ?? ""} onChange={(e) => setI({ ...i, notes: e.target.value || null })} />
        <p className="tiny muted" style={{ margin: 0 }}>This log supports training decisions; it isn't medical advice. Anything concerning goes to a professional.</p>
        <button className="btn primary" disabled={!i.body_part} onClick={() => run(async () => {
          const body = { body_part: i.body_part, side: i.side, injury_type: i.injury_type, onset_date: i.onset_date, mechanism: i.mechanism ?? null,
            status: i.status, resolved_date: i.resolved_date ?? null, seen_professional: i.seen_professional, notes: i.notes ?? null };
          if (injury) await api.put(`/injuries/${injury.id}`, body); else await api.post("/injuries", body);
          qc.invalidateQueries();
          onClose();
        }, "Saved")}>Save</button>
      </div>
    </Sheet>
  );
}

const TARGETS = ["activity", "muscle_group", "movement_pattern", "exercise"] as const;
const ACTIVITIES = ["running", "sprinting", "cutting", "jumping", "ultimate", "lifting", "cycling"];
const MUSCLES = ["quads", "hamstrings", "glutes", "calves", "adductors", "chest", "upper_back", "lats", "shoulders", "biceps", "triceps", "core"];
const PATTERNS = ["squat", "hinge", "lunge", "horizontal_push", "vertical_push", "horizontal_pull", "vertical_pull", "carry", "core", "plyometric"];

function RestrictionSheet({ injury, onClose }: { injury: Injury; onClose: () => void }) {
  const run = useRun();
  const qc = useQueryClient();
  const { data: exercises } = useQuery({ queryKey: ["exercises"], queryFn: () => api.get<Exercise[]>("/exercises") });
  const [type, setType] = useState<(typeof TARGETS)[number]>("activity");
  const [target, setTarget] = useState<string>("");
  const [avoid, setAvoid] = useState(true);
  const [maxRpe, setMaxRpe] = useState(5);
  const [until, setUntil] = useState("");
  const options = type === "activity" ? ACTIVITIES : type === "muscle_group" ? MUSCLES : type === "movement_pattern" ? PATTERNS : [];
  return (
    <Sheet title={`Restriction: ${injury.body_part}`} onClose={onClose}>
      <div className="stack">
        <Chips options={TARGETS} value={type} onChange={(t) => { setType(t); setTarget(""); }} />
        {type === "exercise" ? (
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Choose an exercise</option>
            {groupByMuscle(exercises ?? []).map(([g, es]) => (
              <optgroup key={g} label={muscleLabel(g)}>
                {es.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
              </optgroup>
            ))}
          </select>
        ) : (
          <div className="row wrap">
            {options.map((o) => (
              <button key={o} type="button" className={`chip${target === o ? " on" : ""}`} onClick={() => setTarget(o)}>{o.replace("_", " ")}</button>
            ))}
          </div>
        )}
        <Chips options={["avoid", "cap effort"] as const} value={avoid ? "avoid" : "cap effort"} onChange={(v) => setAvoid(v === "avoid")} />
        {!avoid && <label className="field">Max RPE<Stepper value={maxRpe} onChange={setMaxRpe} min={1} max={9} label="max RPE" /></label>}
        <label className="field">Until (blank = until you lift it)<input type="date" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
        <button className="btn primary" disabled={!target} onClick={() => run(async () => {
          const body: Record<string, unknown> = { target_type: type, max_rpe: avoid ? 0 : maxRpe, end_date: until || null };
          if (type === "exercise") body.exercise_id = Number(target);
          else body[type] = target;
          await api.post(`/injuries/${injury.id}/restrictions`, body);
          qc.invalidateQueries({ queryKey: ["injuries"] });
          onClose();
        }, "Restriction added")}>Add</button>
      </div>
    </Sheet>
  );
}

const EVENT_TYPES = ["sick", "travel", "nap", "sauna", "supplement"] as const;

function EventSheet({ onClose }: { onClose: () => void }) {
  const run = useRun();
  const qc = useQueryClient();
  const [type, setType] = useState<(typeof EVENT_TYPES)[number]>("sick");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [when, setWhen] = useState(toLocalInput(new Date()));
  return (
    <Sheet title="Log a life event" onClose={onClose}>
      <div className="stack">
        <Chips options={EVENT_TYPES} value={type} onChange={setType} />
        {(type === "nap" || type === "sauna") && (
          <label className="field">Minutes<input inputMode="numeric" value={value} onChange={(e) => setValue(e.target.value)} /></label>
        )}
        <input placeholder={type === "supplement" ? "What (e.g. creatine 5 g)" : "Note"} value={note} onChange={(e) => setNote(e.target.value)} />
        <label className="field">When<input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
        {(type === "sick" || type === "travel") && <p className="tiny muted" style={{ margin: 0 }}>Log one per day you're sick or traveling.</p>}
        <button className="btn primary" onClick={() => run(async () => {
          await api.post("/events", { type_code: type, ts: new Date(when).toISOString(), value: value ? Number(value) : null, note: note || null });
          qc.invalidateQueries({ queryKey: ["events"] });
          onClose();
        }, "Logged")}>Save</button>
      </div>
    </Sheet>
  );
}
