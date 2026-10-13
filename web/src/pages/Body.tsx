import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Exercise, type Goal, type Injury, type Journal } from "../api";
import { groupByMuscle, muscleLabel } from "../exercises";
import { cap, Chips, Empty, Icon, Scale, ScaleRow, Sheet, Stepper, useRun, useUnits } from "../components/ui";
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
  const { data: goals } = useQuery({ queryKey: ["goals"], queryFn: () => api.get<Goal[]>("/goals") });
  const weightGoal = goals?.find((g) => g.active && g.goal_type === "body_weight" && g.target_value != null);
  const run = useRun();
  const qc = useQueryClient();
  const active = injuries?.filter((i) => i.status !== "resolved") ?? [];
  const past = injuries?.filter((i) => i.status === "resolved") ?? [];
  const latest = weights?.at(-1);

  const first = weights?.find((w) => w.weight_kg_smoothed != null);
  const shownKg = latest ? latest.weight_kg_smoothed ?? latest.weight_kg : null;
  const change = first?.weight_kg_smoothed != null && shownKg != null ? fromKg(shownKg, units) - fromKg(first.weight_kg_smoothed, units) : null;

  return (
    <div className="stack">
      <header className="topbar"><div><div className="eyebrow">Weight · injuries · journal</div><h1>Body</h1></div></header>

      <section className="card" aria-label="Weight">
        <div className="spread" style={{ alignItems: "flex-end" }}>
          <div>
            <div className="eyebrow" style={{ marginBottom: 6 }}>Weight trend</div>
            <div className="big-num">{shownKg != null ? showWeight(shownKg, units) : "–"}<small>{weightUnit(units)}</small></div>
          </div>
          {change != null && (
            <div style={{ textAlign: "right" }}>
              <div className="display-num">{change > 0 ? "+" : change < 0 ? "−" : ""}{Math.abs(change).toFixed(1)} {weightUnit(units)}</div>
              <div className="tiny muted">over 90 days</div>
            </div>
          )}
        </div>
        {weights && weights.length > 1 ? <WeightChart values={weights.map((w) => fromKg(w.weight_kg, units))}
                                                      trend={weights.map((w) => (w.weight_kg_smoothed == null ? null : fromKg(w.weight_kg_smoothed, units)))}
                                                      dates={weights.map((w) => w.date)}
                                                      goal={weightGoal && {
                                                        target: fromKg(weightGoal.target_value!, units),
                                                        start: weightGoal.start_value != null ? fromKg(weightGoal.start_value, units) : null,
                                                        startDate: weightGoal.start_date,
                                                        perWeek: weightGoal.rate_per_week != null ? fromKg(weightGoal.rate_per_week, units) : null,
                                                      }} />
          : <Empty>{weights ? "Log a morning weight on the Today tab." : "Loading…"}</Empty>}
        <p className="tiny muted" style={{ margin: "6px 0 0" }}>
          Dots are scale readings; the line is the smoothed trend.{weightGoal ? " Dashed: your goal and its planned path." : ""}
        </p>
      </section>

      <div className="spread">
        <h3 className="section-title">Injuries</h3>
        <button className="btn small" onClick={() => setSheet("injury")} style={{ marginTop: 16 }}><Icon name="plus" size={16} /> New</button>
      </div>
      {injuries && active.length === 0 && <div className="card tight small muted">No active injuries.</div>}
      {active.map((i) => {
        const label = `${i.side && i.side !== "n/a" ? i.side + " " : ""}${i.body_part}`;
        return (
          <section key={i.id} className="card stack" style={{ gap: 14 }}>
            <div className="spread" style={{ alignItems: "flex-start" }}>
              <div className="row" style={{ alignItems: "flex-start" }}>
                <span className="icon-circle hue-heart"><Icon name="alert" size={18} /></span>
                <div>
                  <h3>{cap(label)} {i.injury_type}</h3>
                  <div className="tiny muted" style={{ marginTop: 3 }}>since {dayLabel(i.onset_date)}{i.seen_professional ? " · seen a professional" : ""}</div>
                </div>
              </div>
              <span className={`badge tint ${i.status === "active" ? "hue-heart" : "hue-load"}`}>{i.status}</span>
            </div>
            <PainTrend checkins={i.checkins} />
            {i.restrictions.length > 0 && (
              <div className="list">
                {i.restrictions.map((r) => (
                  <div key={r.id} className="list-row small">
                    <span className="grow">
                      <span style={{ fontWeight: 550 }}>{cap((r.exercise_name ?? r.muscle_group ?? r.movement_pattern ?? r.activity)?.replace("_", " "))}</span>
                      <span className="tiny muted" style={{ display: "block" }}>
                        {r.max_rpe === 0 ? "avoid" : r.max_rpe != null ? `max RPE ${r.max_rpe}` : "limit"} · {r.end_date ? `until ${dayLabel(r.end_date)}` : "until lifted"}
                      </span>
                    </span>
                    <button className="btn ghost small danger icon-btn" aria-label="Remove restriction"
                            onClick={() => run(async () => { await api.del(`/restrictions/${r.id}`); qc.invalidateQueries({ queryKey: ["injuries"] }); })}>
                      <Icon name="x" size={16} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="row">
              <button className={`btn grow nowrap ${i.checked_in_today ? "" : "primary"}`} onClick={() => setSheet({ checkin: i.id, label })}>
                {i.checked_in_today ? "Edit check-in" : "Check in"}
              </button>
              <button className="btn grow nowrap" onClick={() => setSheet({ restrict: i })}>Restriction</button>
              <button className="btn icon-btn" onClick={() => setSheet({ edit: i })} aria-label="Edit injury"><Icon name="gear" size={18} /></button>
            </div>
          </section>
        );
      })}
      {past.length > 0 && (
        <details className="card tight">
          <summary className="small">Resolved ({past.length})</summary>
          {past.map((i) => (
            <div key={i.id} className="small muted" style={{ marginTop: 8 }}>
              {i.side && i.side !== "n/a" ? i.side + " " : ""}{i.body_part} {i.injury_type} · {dayLabel(i.onset_date)} – {i.resolved_date ? dayLabel(i.resolved_date) : "?"}
            </div>
          ))}
        </details>
      )}

      <div className="spread">
        <h3 className="section-title">Life events</h3>
        <button className="btn small" onClick={() => setSheet("event")} style={{ marginTop: 16 }}><Icon name="plus" size={16} /> Log</button>
      </div>
      <p className="tiny muted" style={{ margin: "0 4px" }}>Sick and travel days are left out of your baselines so they don't skew "normal".</p>
      <div className="list">
        {events && !events.filter((e) => e.type_code !== "supplement").length && <div className="list-row small muted">Nothing in the last three weeks.</div>}
        {events?.filter((e) => e.type_code !== "supplement").slice(0, 8).map((e) => (
          <div key={e.id} className="list-row small">
            <span className={`icon-circle ${EVENT_HUE[e.type_code] ?? ""}`} style={{ width: 34, height: 34 }}><Icon name={EVENT_ICON[e.type_code] ?? "calendar"} size={16} /></span>
            <span className="grow">
              <span style={{ fontWeight: 550 }}>{cap(e.type_code)}</span>
              <span className="tiny muted" style={{ display: "block" }}>{[e.value != null ? `${e.value} min` : null, e.note, dayLabel(e.ts.slice(0, 10))].filter(Boolean).join(" · ")}</span>
            </span>
            <button className="btn ghost small danger icon-btn" aria-label="Delete event"
                    onClick={() => run(async () => { await api.del(`/events/${e.id}`); qc.invalidateQueries({ queryKey: ["events"] }); })}>
              <Icon name="x" size={16} />
            </button>
          </div>
        ))}
      </div>

      <h3 className="section-title">Journal</h3>
      <div className="list">
        {journal && !journal.length && <div className="list-row small muted">No entries yet.</div>}
        {journal?.slice(0, 14).map((j) => (
          <div key={j.date} className="list-row small num" style={{ minHeight: 48 }}>
            <span style={{ width: 104 }}>{dayLabel(j.date)}</span>
            <span className="grow row" style={{ gap: 12, justifyContent: "flex-end" }}>
              <JournalVal k="E" v={j.energy} hue="hrv" />
              <JournalVal k="M" v={j.mood} hue="sleep" />
              <JournalVal k="S" v={j.soreness} hue="heart" />
              <JournalVal k="St" v={j.stress} hue="load" />
            </span>
            <span className="tiny muted" title={j.note ?? undefined} style={{ width: 28, textAlign: "right" }}>{j.note ? "note" : ""}</span>
          </div>
        ))}
      </div>
      <p className="tiny muted" style={{ margin: "0 4px" }}>E energy · M mood · S soreness · St stress, each 1-5.</p>

      {sheet === "injury" && <InjurySheet onClose={() => setSheet(null)} />}
      {sheet === "event" && <EventSheet onClose={() => setSheet(null)} />}
      {sheet && typeof sheet === "object" && "checkin" in sheet && <CheckinSheet injuryId={sheet.checkin} label={sheet.label} onClose={() => setSheet(null)} />}
      {sheet && typeof sheet === "object" && "restrict" in sheet && <RestrictionSheet injury={sheet.restrict} onClose={() => setSheet(null)} />}
      {sheet && typeof sheet === "object" && "edit" in sheet && <InjurySheet injury={sheet.edit} onClose={() => setSheet(null)} />}
    </div>
  );
}

const EVENT_ICON: Record<string, string> = { sick: "alert", travel: "calendar", nap: "moon", sauna: "flame", supplement: "plus" };
const EVENT_HUE: Record<string, string> = { sick: "hue-heart", travel: "hue-food", nap: "hue-sleep", sauna: "hue-load" };

function JournalVal({ k, v, hue }: { k: string; v: number | null; hue: string }) {
  return (
    <span className={`row hue-${hue}`} style={{ gap: 5 }}>
      <span className="tiny muted">{k}</span>
      <span style={{ fontWeight: 600, color: v == null ? "var(--faint)" : "var(--hue)" }}>{v ?? "–"}</span>
    </span>
  );
}

/** Activity pain for the last check-ins as bars, oldest left. Green at 3 or lower (the return-to-play line). */
function PainTrend({ checkins }: { checkins: Injury["checkins"] }) {
  const recent = checkins.slice(0, 7).reverse();
  if (!recent.length) return <div className="small muted">No check-ins yet.</div>;
  return (
    <div>
      <div className="spread tiny muted" style={{ marginBottom: 8 }}>
        <span>Activity pain, last {recent.length} check-ins</span><span>0-10</span>
      </div>
      <div className="row" style={{ alignItems: "flex-end", gap: 6, height: 64 }} role="img"
           aria-label={`Activity pain, oldest first: ${recent.map((c) => c.pain_activity ?? "none").join(", ")}`}>
        {recent.map((c) => {
          const v = c.pain_activity ?? 0;
          const hue = v <= 3 ? "var(--good)" : v <= 5 ? "var(--warn)" : "var(--bad)";
          return (
            <div key={c.date} className="grow" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, height: "100%", justifyContent: "flex-end" }}>
              <span className="tiny num" style={{ color: hue, fontWeight: 600 }}>{c.pain_activity ?? "–"}</span>
              <span style={{ width: "100%", maxWidth: 28, height: `${Math.max(8, v * 10)}%`, borderRadius: 8, background: hue, opacity: 0.85,
                             boxShadow: `0 0 12px color-mix(in srgb, ${hue} var(--glow), transparent)` }} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

type ChartGoal = { target: number; start: number | null; startDate: string; perWeek: number | null };

function WeightChart({ values, trend, dates, goal }: {
  values: number[]; trend: (number | null)[]; dates: string[]; goal?: ChartGoal;
}) {
  // Planned path: start weight moving at the planned rate, stopping at the target.
  const plan = goal && goal.start != null && goal.perWeek != null
    ? dates.map((d) => {
        const days = (Date.parse(d) - Date.parse(goal.startDate)) / 86_400_000;
        if (days < 0) return null;
        const v = goal.start! + (goal.perWeek! * days) / 7;
        return goal.perWeek! < 0 ? Math.max(v, goal.target) : Math.min(v, goal.target);
      })
    : [];
  const all = [...values, ...trend.filter((v): v is number => v != null), ...(goal ? [goal.target] : []),
    ...plan.filter((v): v is number => v != null)];
  const lo = Math.floor(Math.min(...all) - 0.5);
  const hi = Math.ceil(Math.max(...all) + 0.5);
  const W = 340;
  const H = 130;
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * (W - 30) + 2;
  const y = (v: number) => H - 8 - ((v - lo) / (hi - lo || 1)) * (H - 20);
  const pts = trend.map((v, i) => (v == null ? null : [x(i), y(v)] as const)).filter((p): p is readonly [number, number] => p != null);
  const line = pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
  const area = pts.length ? `${line}L${pts.at(-1)![0].toFixed(1)},${H}L${pts[0][0].toFixed(1)},${H}Z` : "";
  const last = pts.at(-1);
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} height={H} role="img" style={{ marginTop: 14 }}
         aria-label={`Weight from ${values[0].toFixed(1)} to ${values.at(-1)!.toFixed(1)}`}>
      <defs>
        <linearGradient id="wfill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: "var(--sleep)", stopOpacity: 0.28 }} />
          <stop offset="1" style={{ stopColor: "var(--sleep)", stopOpacity: 0 }} />
        </linearGradient>
      </defs>
      {[hi, (hi + lo) / 2, lo].map((g) => (
        <g key={g}>
          <line x1={0} x2={W - 26} y1={y(g)} y2={y(g)} style={{ stroke: "color-mix(in srgb, var(--ink) 7%, transparent)" }} strokeDasharray="2 4" />
          <text x={W} y={y(g) + 3} textAnchor="end" fontSize={10} fill="var(--muted)">{g.toFixed(0)}</text>
        </g>
      ))}
      <path d={area} fill="url(#wfill)" />
      {goal && (
        <g>
          <line x1={0} x2={W - 26} y1={y(goal.target)} y2={y(goal.target)} stroke="var(--food)" strokeWidth={1.2} strokeDasharray="5 5" opacity={0.8} />
          <text x={4} y={y(goal.target) - 5} fontSize={10} fill="var(--food)">goal {goal.target.toFixed(0)}</text>
        </g>
      )}
      {plan.some((v) => v != null) && (
        <path d={plan.map((v, i) => (v == null ? "" : `${plan.slice(0, i).some((p) => p != null) ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`)).join("")}
              fill="none" stroke="var(--food)" strokeWidth={1.4} strokeDasharray="2 4" opacity={0.7} />
      )}
      {values.map((v, i) => <circle key={i} cx={x(i)} cy={y(v)} r={1.8} style={{ fill: "color-mix(in srgb, var(--ink) 28%, transparent)" }} />)}
      <path d={line} fill="none" stroke="var(--sleep)" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round"
            style={{ filter: "drop-shadow(0 0 6px color-mix(in srgb, var(--sleep) var(--glow), transparent))" }} />
      {last && <circle cx={last[0]} cy={last[1]} r={4.5} fill="var(--ink)" stroke="var(--sleep)" strokeWidth={2} />}
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
