import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type Journal, type Today as TodayData } from "../api";
import { CoachText, Empty, Icon, ScaleRow, Stars, Stepper, useRun, useUnits } from "../components/ui";
import { addDays, dayLabel, isoDate } from "../dates";
import { fromKg, showWeight, toKg, weightUnit } from "../units";
import { GameSheet } from "./Train";
import { CheckinSheet } from "./Body";

export default function Today() {
  const qc = useQueryClient();
  const run = useRun();
  const units = useUnits();
  const today = isoDate();
  const { data, isLoading, error } = useQuery({ queryKey: ["today"], queryFn: () => api.get<TodayData>("/today") });
  const { data: pipe } = useQuery({
    queryKey: ["pipeline-status"], queryFn: () => api.get<{ running: boolean }>("/pipeline/status"),
    refetchInterval: (q) => (q.state.data?.running ? 3000 : false),
  });
  const [wasRunning, setWasRunning] = useState(false);
  const [sheet, setSheet] = useState<null | "game" | { injury: number; label: string }>(null);

  useEffect(() => {
    if (pipe?.running) setWasRunning(true);
    else if (wasRunning) {
      setWasRunning(false);
      qc.invalidateQueries();
    }
  }, [pipe?.running, wasRunning, qc]);

  const refresh = () =>
    run(async () => {
      await api.post("/pipeline/run", { job: "nightly" });
      setWasRunning(true);
      qc.invalidateQueries({ queryKey: ["pipeline-status"] });
    }, "Updating: pulling Garmin and rebuilding today's coaching");

  if (isLoading) return <div className="empty">Loading…</div>;
  if (error || !data) return <Empty>Can't reach the server. Is the API running?</Empty>;

  const rec = data.recommendation;
  const rv = data.status?.recovery ?? {};
  const tr = data.status?.training ?? {};
  const gameToday = data.planned.find((p) => p.session_type === "game" && p.status === "planned");

  return (
    <div className="stack">
      <header className="topbar">
        <div>
          <h1>{dayLabel(data.date, today)}</h1>
          <div className="sub">
            {new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
          </div>
        </div>
        <button className="btn small" onClick={refresh} disabled={pipe?.running} aria-label="Refresh coaching">
          <Icon name="refresh" size={18} /> {pipe?.running ? "Updating…" : "Refresh"}
        </button>
      </header>

      {data.pending_proposals > 0 && (
        <Link to="/more/coach" className="card tight row" style={{ textDecoration: "none", color: "inherit" }}>
          <span className="badge game">{data.pending_proposals}</span>
          <span className="grow small">The weekly job proposed rule changes for your approval</span>
          <Icon name="chevron" size={18} />
        </Link>
      )}

      <section className="card hero" aria-label="Today's coaching">
        {rec?.rendered_text ? <CoachText text={rec.rendered_text} /> : (
          <p style={{ margin: 0 }}>No coaching yet today. It's built each morning after your watch syncs. Tap Refresh after syncing.</p>
        )}
        {rec && (
          <div className="spread" style={{ marginTop: 10 }}>
            <span className="tiny muted">
              {rec.llm_model && rec.output_check === "passed" ? `Worded by ${rec.llm_model}` : "Plain template"}
              {" · "}code decides, the LLM only phrases
            </span>
            <Stars value={rec.rating}
                   onChange={(v) => run(async () => {
                     await api.put(`/recommendations/${rec.id}/rating`, { rating: v });
                     qc.invalidateQueries({ queryKey: ["today"] });
                   }, "Thanks, rating saved")} />
          </div>
        )}
      </section>

      <div className="tiles">
        <Tile label="HRV" value={rv.hrv_ms} unit="ms" z={rv.hrv_vs_baseline_sd as number | undefined} />
        <Tile label="Resting HR" value={rv.resting_hr_bpm} unit="bpm" z={rv.resting_hr_vs_baseline_sd as number | undefined} invert />
        <Tile label="Sleep" value={rv.sleep_last_night} sub={rv.sleep_score != null ? `score ${rv.sleep_score}` : undefined} />
        <Tile label="7-day load" value={tr.load_7d} sub={tr.acute_chronic_ratio != null ? `ACWR ${tr.acute_chronic_ratio}` : undefined} />
      </div>

      <h3 className="section-title">Today's plan</h3>
      <div className="list">
        {data.planned.length === 0 && <div className="list-row muted small">Nothing planned. Train as you like.</div>}
        {data.planned.map((p) => (
          <div key={p.id} className="list-row">
            {p.session_type === "game" ? <span className="badge game">game</span> : <span className="badge">{p.session_type}</span>}
            <div className="grow">
              <div className="title">{p.notes || p.sport}</div>
              <div className="meta">
                {p.planned_duration_min ? `${p.planned_duration_min} min` : ""}
                {p.planned_rpe ? ` · RPE ${p.planned_rpe}` : ""}
                {p.focus_muscles?.length ? ` · ${p.focus_muscles.join(", ").replaceAll("_", " ")}` : ""}
              </div>
            </div>
            <span className="tiny muted">{p.status}</span>
          </div>
        ))}
      </div>
      <div className="row">
        <button className={`btn grow ${gameToday ? "accent" : ""}`} onClick={() => setSheet("game")}>
          <Icon name="disc" size={18} /> Log a game
        </button>
        <Link className="btn grow" to="/train"><Icon name="train" size={18} /> Workouts</Link>
      </div>

      {data.injuries.filter((i) => !i.checked_in).map((i) => (
        <button key={i.id} className="card tight row" style={{ cursor: "pointer", textAlign: "left" }}
                onClick={() => setSheet({ injury: i.id, label: `${i.side && i.side !== "n/a" ? i.side + " " : ""}${i.body_part}` })}>
          <span className="sev sev-2">!</span>
          <span className="grow small">Check in on your {i.side && i.side !== "n/a" ? i.side + " " : ""}{i.body_part}</span>
          <Icon name="chevron" size={18} />
        </button>
      ))}

      <h3 className="section-title">Check-in</h3>
      <JournalCard date={data.date} journal={data.journal} />
      <WeightCard weightKg={data.weight?.weight_kg ?? null} units={units} />

      <h3 className="section-title">Food</h3>
      <Link to="/food" className="card row" style={{ textDecoration: "none", color: "inherit" }}>
        <div className="grow">
          <div className="title">{data.food.n ? `${Math.round(data.food.kcal ?? 0)} kcal · ${Math.round(data.food.protein_g ?? 0)} g protein` : "Nothing logged yet"}</div>
          <div className="meta small muted">
            {data.food.complete ? "Today marked complete" : `${data.food.n} entries today`}
            {!data.food.yesterday_complete && " · yesterday isn't marked complete"}
          </div>
        </div>
        <Icon name="chevron" size={18} />
      </Link>
      {!data.food.yesterday_complete && (
        <button className="btn" onClick={() => run(async () => {
          await api.put(`/food-day/${addDays(data.date, -1)}/complete`, { complete: true });
          qc.invalidateQueries({ queryKey: ["today"] });
        }, "Yesterday marked complete")}>
          Yesterday's log is complete
        </button>
      )}

      {data.week_plan?.rendered_text && (
        <details className="card">
          <summary>This week's plan</summary>
          <div style={{ marginTop: 8 }}><CoachText text={data.week_plan.rendered_text} /></div>
        </details>
      )}
      {data.last_run && !data.last_run.ok && (
        <p className="tiny bad">Last {data.last_run.job} run had failures ({data.last_run.failed_steps}). See the dashboard's Pipeline page.</p>
      )}

      {sheet === "game" && <GameSheet onClose={() => setSheet(null)} planned={gameToday} />}
      {sheet && typeof sheet === "object" && (
        <CheckinSheet injuryId={sheet.injury} label={sheet.label} onClose={() => setSheet(null)} />
      )}
    </div>
  );
}

function Tile({ label, value, unit, sub, z, invert }: {
  label: string; value: number | string | undefined; unit?: string; sub?: string; z?: number; invert?: boolean;
}) {
  let cls = "";
  let text = sub;
  if (z != null) {
    const good = invert ? -z : z;
    cls = good <= -1 ? "bad" : good <= -0.5 ? "warn" : "good";
    const zr = Math.round(z * 10) / 10 + 0;
    text = `${zr > 0 ? "+" : ""}${zr.toFixed(1)} SD vs you`;
  }
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className="value num">{value ?? "–"}{unit && value != null && <small>{unit}</small>}</div>
      {text && <div className={`delta ${cls}`}>{text}</div>}
    </div>
  );
}

const JOURNAL_FIELDS: { key: keyof Journal; name: string; hint: string }[] = [
  { key: "energy", name: "Energy", hint: "1 drained · 5 great" },
  { key: "mood", name: "Mood", hint: "1 low · 5 great" },
  { key: "soreness", name: "Soreness", hint: "1 none · 5 very" },
  { key: "stress", name: "Stress", hint: "1 calm · 5 high" },
  { key: "motivation", name: "Motivation", hint: "1 none · 5 fired up" },
];

function JournalCard({ date, journal }: { date: string; journal: Journal | null }) {
  const qc = useQueryClient();
  const run = useRun();
  const [j, setJ] = useState<Partial<Journal>>(journal ?? {});
  const [open, setOpen] = useState(!journal?.energy);
  useEffect(() => setJ(journal ?? {}), [journal]);
  const save = () => run(async () => {
    await api.put(`/journal/${date}`, j);
    qc.invalidateQueries({ queryKey: ["today"] });
    setOpen(false);
  }, "Journal saved");

  if (!open && journal) {
    return (
      <button className="card tight row" onClick={() => setOpen(true)} style={{ cursor: "pointer", textAlign: "left" }}>
        <span className="good"><Icon name="check" size={18} /></span>
        <span className="grow small">
          Journal: energy {journal.energy ?? "–"} · mood {journal.mood ?? "–"} · soreness {journal.soreness ?? "–"}
        </span>
        <span className="tiny muted">edit</span>
      </button>
    );
  }
  return (
    <div className="card stack">
      <div className="spread"><h3>How are you today?</h3><span className="tiny muted">5 taps</span></div>
      {JOURNAL_FIELDS.map((f) => (
        <ScaleRow key={f.key} name={f.name} hint={f.hint} label={f.name}
                  value={(j[f.key] as number | null | undefined) ?? null}
                  onChange={(v) => setJ({ ...j, [f.key]: v })} />
      ))}
      <input placeholder="Note (optional)" value={j.note ?? ""} onChange={(e) => setJ({ ...j, note: e.target.value || null })} />
      <button className="btn primary" onClick={save}>Save</button>
    </div>
  );
}

function WeightCard({ weightKg, units }: { weightKg: number | null; units: "imperial" | "metric" }) {
  const qc = useQueryClient();
  const run = useRun();
  const { data: recent } = useQuery({
    queryKey: ["weights", 14], queryFn: () => api.get<{ weight_kg: number }[]>("/weights?days=14"),
  });
  const last = recent?.at(-1)?.weight_kg;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState<number>(last ? Math.round(fromKg(last, units) * 10) / 10 : units === "imperial" ? 180 : 80);
  useEffect(() => {
    if (last) setValue(Math.round(fromKg(last, units) * 10) / 10);
  }, [last, units]);

  if (weightKg != null && !editing) {
    return (
      <button className="card tight row" onClick={() => setEditing(true)} style={{ cursor: "pointer", textAlign: "left" }}>
        <span className="good"><Icon name="check" size={18} /></span>
        <span className="grow small">Weight {showWeight(weightKg, units)} {weightUnit(units)} logged</span>
        <span className="tiny muted">add</span>
      </button>
    );
  }
  return (
    <div className="card">
      <div className="spread" style={{ marginBottom: 8 }}>
        <h3>Morning weight</h3><span className="tiny muted">{weightUnit(units)}</span>
      </div>
      <div className="row">
        <div className="grow"><Stepper value={value} onChange={setValue} step={0.2} digits={1} min={50} max={600} label="weight" /></div>
        <button className="btn primary" onClick={() => run(async () => {
          await api.post("/weights", { weight_kg: toKg(value, units) });
          qc.invalidateQueries({ queryKey: ["today"] });
          qc.invalidateQueries({ queryKey: ["weights"] });
          setEditing(false);
        }, "Weight saved")}>Save</button>
      </div>
      <p className="tiny muted" style={{ margin: "6px 0 0" }}>
        Same scale, after waking, before eating. Day-to-day swings are mostly water; the trend line smooths them out.
      </p>
    </div>
  );
}

