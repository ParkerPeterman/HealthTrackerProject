import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type FoodDay, type GoalSummary, type Journal, type Planned, type Today as TodayData } from "../api";
import {
  cap, CoachText, Empty, Gauge, Icon, Ring, ScaleRow, sessionLook, Stars, Stepper, useRun, useUnits,
} from "../components/ui";
import { addDays, dayLabel, isoDate } from "../dates";
import { fromKg, showWeight, toKg, weightUnit } from "../units";
import { GameSheet } from "./Train";
import { CheckinSheet } from "./Body";
import { STATUS } from "../goals";
import { WaterCard } from "../components/Water";

const num = (v: number | string | undefined) => (v == null || v === "" ? undefined : Number(v));

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
  const pending = data.pending_proposals;

  return (
    <div className="stack">
      <header className="topbar">
        <div>
          <div className="eyebrow">
            {new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}
          </div>
          <h1>{dayLabel(data.date, today)}</h1>
        </div>
        <button className="btn icon-btn" onClick={refresh} disabled={pipe?.running}
                aria-label={pipe?.running ? "Updating coaching" : "Refresh coaching"} title="Pull Garmin and rebuild today's coaching">
          <span className={pipe?.running ? "spin" : ""}><Icon name="refresh" size={19} /></span>
        </button>
      </header>

      {pending > 0 && (
        <Link to="/more/coach" className="card tight row">
          <span className="icon-circle hue-signal"><Icon name="inbox" size={18} /></span>
          <span className="grow">
            <span className="small" style={{ fontWeight: 550 }}>{pending} rule {pending === 1 ? "change" : "changes"} to review</span>
            <span className="tiny muted" style={{ display: "block" }}>Proposed by the weekly job. Nothing changes until you approve.</span>
          </span>
          <Icon name="chevron" size={18} />
        </Link>
      )}

      <section className="card" aria-label="Recovery">
        <div className="spread" style={{ marginBottom: 16 }}>
          <h3>Recovery</h3>
          <span className="tiny muted">vs your 30-day baseline</span>
        </div>
        <div className="rings">
          <BaselineRing name="HRV" hue="hrv" value={num(rv.hrv_ms)} unit="ms" z={num(rv.hrv_vs_baseline_sd)} />
          <BaselineRing name="Resting HR" hue="heart" value={num(rv.resting_hr_bpm)} unit="bpm" z={num(rv.resting_hr_vs_baseline_sd)} invert />
          <div className="ring-cell">
            <Ring value={num(rv.sleep_score)} max={100} hue="sleep"
                  label={`Sleep ${rv.sleep_last_night ?? "unknown"}, score ${rv.sleep_score ?? "unknown"} of 100`}>
              <div><span className="v sm">{rv.sleep_last_night ?? "–"}</span>
                <span className="u">{rv.sleep_score != null ? `score ${rv.sleep_score}` : "sleep"}</span></div>
            </Ring>
            <span className="name">Sleep</span>
            <span className="delta muted">of 100</span>
          </div>
        </div>
      </section>

      <LoadCard load7={num(tr.load_7d)} acwr={num(tr.acute_chronic_ratio)} yesterday={num(tr.load_yesterday)}
                avg28={num(tr.weekly_avg_load_28d)} />

      <section className="card hero" aria-label="Today's coaching">
        <div className="spread" style={{ marginBottom: 14 }}>
          <span className="row" style={{ gap: 10 }}>
            <span className="icon-circle hue-signal" style={{ width: 32, height: 32 }}><Icon name="message" size={16} /></span>
            <span className="eyebrow" style={{ color: "var(--ink-2)" }}>Coach</span>
          </span>
          {rec && (
            <span className="tiny muted">
              {rec.llm_model && rec.output_check === "passed" ? `Worded by ${rec.llm_model}` : "Plain template"}
            </span>
          )}
        </div>
        {rec?.rendered_text ? <CoachText text={rec.rendered_text} /> : (
          <p style={{ margin: 0 }}>No coaching yet today. It's built each morning after your watch syncs. Tap refresh after syncing.</p>
        )}
        {rec && (
          <div className="spread" style={{ marginTop: 4, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
            <span className="tiny muted">Code decides; the LLM only phrases.<br />How useful was this?</span>
            <Stars value={rec.rating}
                   onChange={(v) => run(async () => {
                     await api.put(`/recommendations/${rec.id}/rating`, { rating: v });
                     qc.invalidateQueries({ queryKey: ["today"] });
                   }, "Thanks, rating saved")} />
          </div>
        )}
      </section>

      <GoalsCard goals={data.goals ?? []} />

      <h3 className="section-title">Today's plan</h3>
      {data.planned.length === 0 && <div className="card tight small muted">Nothing planned. Train as you like.</div>}
      {data.planned.map((p) => <PlanCard key={p.id} p={p} />)}
      <div className="row">
        <button className={`btn grow ${gameToday ? "accent" : ""}`} onClick={() => setSheet("game")}>
          <Icon name="disc" size={18} /> Log a game
        </button>
        <Link className="btn grow" to="/train"><Icon name="train" size={18} /> Workouts</Link>
      </div>

      {data.injuries.filter((i) => !i.checked_in).map((i) => {
        const label = `${i.side && i.side !== "n/a" ? i.side + " " : ""}${i.body_part}`;
        return (
          <button key={i.id} className="card tight row" onClick={() => setSheet({ injury: i.id, label })}>
            <span className="icon-circle hue-heart"><Icon name="alert" size={18} /></span>
            <span className="grow">
              <span className="small" style={{ fontWeight: 550 }}>Check in on your {label}</span>
              <span className="tiny muted" style={{ display: "block" }}>Pain at rest and during activity, 30 seconds</span>
            </span>
            <Icon name="chevron" size={18} />
          </button>
        );
      })}

      <h3 className="section-title">Check-in</h3>
      <JournalCard date={data.date} journal={data.journal} />
      <WeightCard weightKg={data.weight?.weight_kg ?? null} units={units} />
      <WaterCard />

      <h3 className="section-title">Food</h3>
      <FoodSummary date={data.date} food={data.food} />
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
          <div style={{ marginTop: 12 }}><CoachText text={data.week_plan.rendered_text} /></div>
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

/** A ring whose fill is where today sits in your own range: -2 SD empty, +2 SD full (inverted for resting HR). */
function BaselineRing({ name, hue, value, unit, z, invert }: {
  name: string; hue: string; value: number | undefined; unit: string; z: number | undefined; invert?: boolean;
}) {
  let cls = "muted";
  let text = "no baseline yet";
  let fill: number | undefined;
  if (z != null) {
    const good = invert ? -z : z;
    cls = good <= -1 ? "bad" : good <= -0.5 ? "warn" : "good";
    // Arrow shows which way the number moved; colour shows whether that's good for you.
    const zr = Math.round(z * 10) / 10 + 0;
    text = `${zr > 0 ? "▲" : zr < 0 ? "▼" : "●"} ${Math.abs(zr).toFixed(1)} SD`;
    fill = Math.max(0.04, Math.min(1, (good + 2) / 4));
  }
  return (
    <div className="ring-cell">
      <Ring value={fill} max={1} hue={hue} label={`${name} ${value ?? "unknown"} ${unit}, ${text} versus your baseline`}>
        <div><span className="v">{value ?? "–"}</span><span className="u">{unit}</span></div>
      </Ring>
      <span className="name">{name}</span>
      <span className={`delta ${cls}`}>{text}</span>
    </div>
  );
}

function LoadCard({ load7, acwr, yesterday, avg28 }: {
  load7: number | undefined; acwr: number | undefined; yesterday: number | undefined; avg28: number | undefined;
}) {
  const fmt = (v: number | undefined) => (v == null ? "–" : Math.round(v).toLocaleString());
  return (
    <section className="card" aria-label="Training load">
      <div className="spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>7-day load</div>
          <div className="big-num">{fmt(load7)}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Acute : chronic</div>
          <div className="big-num md">{acwr?.toFixed(2) ?? "–"}</div>
        </div>
      </div>
      <Gauge value={acwr} min={0.4} max={1.8} band={[0.8, 1.3]} hue="load" ticks={[0.4, 0.8, 1.3, 1.8]}
             label={`Acute to chronic load ratio ${acwr ?? "unknown"}; shaded range 0.8 to 1.3`} />
      <div className="tiny muted" style={{ marginTop: 6 }}>
        Yesterday {fmt(yesterday)} · 4-week weekly average {fmt(avg28)} · load = RPE × minutes
      </div>
    </section>
  );
}

function GoalsCard({ goals }: { goals: GoalSummary[] }) {
  if (!goals.length) {
    return (
      <Link to="/more/goals" className="card tight row">
        <span className="icon-circle hue-signal"><Icon name="target" size={18} /></span>
        <span className="grow">
          <span className="small" style={{ fontWeight: 550 }}>Set a goal</span>
          <span className="tiny muted" style={{ display: "block" }}>Lose or gain weight, run faster, sleep more</span>
        </span>
        <Icon name="chevron" size={18} />
      </Link>
    );
  }
  const good = goals.filter((g) => ["on_track", "ahead", "achieved"].includes(g.status)).length;
  return (
    <Link to="/more/goals" className="card stack" style={{ gap: 14 }} aria-label="Goals">
      <div className="spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>Goals</div>
          <div className="big-num md">{good}/{goals.length}<small>on track</small></div>
        </div>
        <span className="row tiny muted" style={{ gap: 4 }}>All goals <Icon name="chevron" size={14} /></span>
      </div>
      <div className="stack" style={{ gap: 10 }}>
        {goals.slice(0, 3).map((g) => {
          const st = STATUS[g.status];
          return (
            <div key={g.id} className="stack" style={{ gap: 6 }}>
              <div className="spread small">
                <span className="row grow" style={{ gap: 8, minWidth: 0 }}>
                  <span className={`dot hue-${st.hue}`} />
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{g.description}</span>
                </span>
                <span className="tiny muted" style={{ fontFamily: "var(--font-label)" }}>{st.label}</span>
              </div>
              {g.pct != null && <div className={`bar hue-${st.hue === "muted" ? "sleep" : st.hue}`}><span style={{ width: `${Math.round(g.pct * 100)}%` }} /></div>}
            </div>
          );
        })}
      </div>
    </Link>
  );
}

export function PlanCard({ p, onClick }: { p: Planned; onClick?: () => void }) {
  const look = sessionLook(p.session_type, p.sport);
  const meta = [p.planned_duration_min && `${p.planned_duration_min} min`, p.planned_rpe && `RPE ${p.planned_rpe}`,
    p.focus_muscles?.length ? p.focus_muscles.join(", ").replaceAll("_", " ") : null].filter(Boolean).join(" · ");
  const body = (
    <>
      <span className={`icon-circle ${look.hue}`}><Icon name={look.icon} size={19} /></span>
      <span className="grow">
        <span style={{ fontWeight: 550, display: "block" }}>{cap(p.notes || p.sport)}</span>
        <span className="tiny muted">{meta}</span>
      </span>
      <span className={`badge tint ${look.hue}`}>{p.session_type}</span>
    </>
  );
  return onClick
    ? <button className="card tight row" onClick={onClick}>{body}</button>
    : <div className="card tight row">{body}</div>;
}

function FoodSummary({ date, food }: { date: string; food: TodayData["food"] }) {
  const { data } = useQuery({ queryKey: ["food-day", date], queryFn: () => api.get<FoodDay>(`/food-day/${date}`) });
  const target = (n: string) => data?.targets.find((t) => t.nutrient === n && t.evaluation === "daily");
  const kcalT = target("kcal");
  const protT = target("protein_g");
  const kcal = Math.round(food.kcal ?? 0);
  const prot = Math.round(food.protein_g ?? 0);
  return (
    <Link to="/food" className="card stack" style={{ gap: 14 }}>
      <div className="food-sum">
        <div>
          <Ring value={kcal} max={kcalT?.min ?? kcalT?.max ?? 2500} size={60} stroke={6} hue="food" className="sm"
                label={`${kcal} kcal of ${kcalT?.min ?? "no"} target`}>
            <Icon name="flame" size={18} />
          </Ring>
          <div className="grow">
            <div className="tiny muted">Calories</div>
            <div className="display-num">{kcal.toLocaleString()}</div>
            <div className="tiny muted num">{kcalT ? `of ${(kcalT.min ?? kcalT.max)?.toLocaleString()}` : "kcal"}</div>
          </div>
        </div>
        <div>
          <Ring value={prot} max={protT?.min ?? 140} size={60} stroke={6} hue="protein" className="sm"
                label={`${prot} grams protein of ${protT?.min ?? "no"} target`}>
            <span style={{ fontWeight: 650, fontSize: "0.8rem" }}>P</span>
          </Ring>
          <div className="grow">
            <div className="tiny muted">Protein</div>
            <div className="display-num">{prot} g</div>
            <div className="tiny muted num">{protT?.min ? `of ${protT.min} g` : "logged"}</div>
          </div>
        </div>
      </div>
      <div className="spread tiny muted" style={{ paddingTop: 12, borderTop: "1px solid var(--line)" }}>
        <span>
          {food.complete ? "Today marked complete" : food.n ? `${food.n} entries today` : "Nothing logged yet"}
          {!food.yesterday_complete && " · yesterday isn't marked complete"}
        </span>
        <span className="row" style={{ gap: 4, color: "var(--ink-2)" }}>Log food <Icon name="chevron" size={14} /></span>
      </div>
    </Link>
  );
}

const JOURNAL_FIELDS: { key: keyof Journal; name: string; hint: string }[] = [
  { key: "energy", name: "Energy", hint: "1 drained · 5 great" },
  { key: "mood", name: "Mood", hint: "1 low · 5 great" },
  { key: "soreness", name: "Soreness", hint: "1 none · 5 very" },
  { key: "stress", name: "Stress", hint: "1 calm · 5 high" },
  { key: "motivation", name: "Motivation", hint: "1 none · 5 fired up" },
];

function DoneRow({ text, action, onClick }: { text: string; action: string; onClick: () => void }) {
  return (
    <button className="card tight row" onClick={onClick}>
      <span className="icon-circle hue-hrv" style={{ width: 32, height: 32 }}><Icon name="check" size={16} /></span>
      <span className="grow small">{text}</span>
      <span className="tiny muted">{action}</span>
    </button>
  );
}

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
    return <DoneRow onClick={() => setOpen(true)} action="edit"
                    text={`Energy ${journal.energy ?? "–"} · mood ${journal.mood ?? "–"} · soreness ${journal.soreness ?? "–"}`} />;
  }
  return (
    <div className="card stack" style={{ gap: 14 }}>
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
    return <DoneRow onClick={() => setEditing(true)} action="add"
                    text={`Weight ${showWeight(weightKg, units)} ${weightUnit(units)} logged`} />;
  }
  return (
    <div className="card stack" style={{ gap: 12 }}>
      <div className="spread">
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
      <p className="tiny muted" style={{ margin: 0 }}>
        Same scale, after waking, before eating. Day-to-day swings are mostly water; the trend line smooths them out.
      </p>
    </div>
  );
}
