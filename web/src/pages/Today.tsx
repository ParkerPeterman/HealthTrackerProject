import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api, type DayTags, type FoodDay, type GoalSummary, type Journal, type Planned, type ShoulderState, type TagCode,
  type Today as TodayData,
} from "../api";
import {
  cap, CoachText, Empty, Icon, Ring, Scale, ScaleRow, sessionLook, Sheet, Stars, Stepper, useRun, useUnits,
} from "../components/ui";
import { addDays, dayLabel, isoDate } from "../dates";
import { fromKg, showWeight, toKg, weightUnit } from "../units";
import { GameSheet, PlanReasonView } from "./Train";
import { CheckinSheet } from "./Body";
import { LibraryDetail } from "./Library";
import { MedicationCards, MedicationLines } from "./Medication";
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
  const [sheet, setSheet] = useState<null | "game" | { injury: number; label: string } | { plan: Planned }>(null);

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
        <SevenDay rv={rv} />
        <Regularity rv={rv} />
      </section>

      <LoadCard load7={num(tr.internal_load_7d) ?? num(tr.load_7d)} change={num(tr.internal_load_change_vs_prior_week_pct)}
                acwr={num(tr.acute_chronic_ratio)} chronic={num(tr.chronic_internal_load_per_week)} />

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
        <MedicationLines date={data.date} />
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
      {data.planned.map((p) => <PlanCard key={p.id} p={p} onClick={p.session_code ? () => setSheet({ plan: p }) : undefined} />)}
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
      <MedicationCards date={data.date} />
      <JournalCard date={data.date} journal={data.journal} />
      <TagsCard date={data.date} />
      <ShoulderCard date={data.date} />
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
      {sheet && typeof sheet === "object" && "plan" in sheet && sheet.plan.session_code && (
        <Sheet title={cap(sheet.plan.notes || sheet.plan.sport)} onClose={() => setSheet(null)}>
          <div className="stack">
            {sheet.plan.reason && <PlanReasonView reason={sheet.plan.reason} optional={sheet.plan.optional} />}
            <LibraryDetail code={sheet.plan.session_code} date={sheet.plan.date} addons={sheet.plan.addons} />
          </div>
        </Sheet>
      )}
      {sheet && typeof sheet === "object" && "injury" in sheet && (
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

const BAND_WORD: Record<string, string> = { below: "below", within: "within", above: "above" };

/** A2: the 7-day averages that matter more than any single night, and where they sit in your normal band. */
function SevenDay({ rv }: { rv: Record<string, number | string | number[]> }) {
  const hrv7 = rv.hrv_7d_ms as number | undefined;
  if (hrv7 == null) return null;
  const band = rv.hrv_normal_band_ms as number[] | undefined;
  const status = rv.hrv_7d_status as string | undefined;
  const rhr7 = rv.resting_hr_7d_bpm as number | undefined;
  const rhrStatus = rv.resting_hr_7d_status as string | undefined;
  const learning = rv.baseline_learning_days as number | undefined;
  const cls = status === "below" ? "warn" : status ? "good" : "muted";
  return (
    <div className="tiny" style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--line)" }}>
      <span className="muted">7-day HRV </span><b className="num">{hrv7} ms</b>
      {rv.hrv_cv_7d_pct != null && <span className="muted"> · CV {rv.hrv_cv_7d_pct}%</span>}
      {rhr7 != null && <><span className="muted"> · resting HR </span><b className="num">{rhr7}</b></>}
      <div className={cls} style={{ marginTop: 4 }}>
        {status && band
          ? `HRV ${BAND_WORD[status]} your normal band (${band[0]}–${band[1]} ms)${rhrStatus === "above" ? "; resting HR above its band" : ""}`
          : learning != null ? `Still learning your normal: ${learning} of 28 days` : "Single nights are noisy; the 7-day average is the signal"}
      </div>
    </div>
  );
}

/** E1: sleep regularity (SRI: 100 = the same sleep times every day, 0 = no pattern), midpoint spread, weekend shift. */
function Regularity({ rv }: { rv: Record<string, number | string | number[]> }) {
  const sri = rv.sleep_regularity_index as number | undefined;
  if (sri == null) return null;
  const spread = rv.sleep_midpoint_spread_min as number | undefined;
  const weekend = rv.weekend_sleep_later_min as number | undefined;
  return (
    <div className="tiny" style={{ marginTop: 10 }}>
      <span className="muted">Sleep regularity </span><b className="num">{sri}</b><span className="muted"> of 100</span>
      {spread != null && <span className="muted"> · midpoint varies ±{spread} min</span>}
      {weekend != null && Math.abs(weekend) >= 15 && (
        <span className="muted"> · weekends {Math.abs(weekend)} min {weekend > 0 ? "later" : "earlier"}</span>
      )}
    </div>
  );
}

/** D1: decisions use the week-over-week change and chronic load; the ratio is shown only as a description. */
function LoadCard({ load7, change, acwr, chronic }: {
  load7: number | undefined; change: number | undefined; acwr: number | undefined; chronic: number | undefined;
}) {
  const fmt = (v: number | undefined) => (v == null ? "–" : Math.round(v).toLocaleString());
  const cls = change == null ? "muted" : change > 10 ? "warn" : "good";
  return (
    <section className="card" aria-label="Training load">
      <div className="spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 6 }}>7-day load</div>
          <div className="big-num">{fmt(load7)}</div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="eyebrow" style={{ marginBottom: 6 }}>vs last week</div>
          <div className={`big-num md ${cls}`}>{change == null ? "–" : `${change > 0 ? "+" : ""}${Math.round(change)}%`}</div>
        </div>
      </div>
      <div className="tiny muted" style={{ marginTop: 10 }}>
        Usual week {fmt(chronic)} · acute:chronic {acwr?.toFixed(2) ?? "–"} (descriptive) · runs, rides: heart-rate load; games, lifts: effort × minutes
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
    p.focus_muscles?.length ? p.focus_muscles.join(", ").replaceAll("_", " ") : null,
    p.addon_names?.length ? `+ ${p.addon_names.join(", ")}` : null].filter(Boolean).join(" · ");
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

const TAG_LABEL: Record<TagCode, string> = {
  sick: "Sick", travel: "Travel", alcohol: "Drinks", high_stress: "High stress", dizzy: "Dizzy",
  dizzy_training: "Dizzy in training", hamstring_tight: "Tight hamstring", groin_tweak: "Groin tweak",
  shoulder_sore: "Sore shoulders",
};

/** Optional one-tap tags for the day. Each tap saves; sick and travel days stay out of baselines. */
function TagsCard({ date }: { date: string }) {
  const qc = useQueryClient();
  const run = useRun();
  const { data } = useQuery({ queryKey: ["tags", date], queryFn: () => api.get<DayTags>(`/tags/${date}`) });
  if (!data) return null;
  const toggle = (t: TagCode) => run(async () => {
    const tags = data.tags.includes(t) ? data.tags.filter((x) => x !== t) : [...data.tags, t];
    await api.put(`/tags/${date}`, { tags });
    qc.invalidateQueries({ queryKey: ["tags", date] });
    qc.invalidateQueries({ queryKey: ["events"] });
    qc.invalidateQueries({ queryKey: ["shoulder", date] });
  });
  return (
    <div className="card tight stack" style={{ gap: 10 }}>
      <div className="spread"><span className="small" style={{ fontWeight: 550 }}>Anything to note?</span><span className="tiny muted">optional</span></div>
      <div className="row wrap" role="group" aria-label="Tags for today">
        {data.available.map((t) => (
          <button key={t} type="button" aria-pressed={data.tags.includes(t)} className={`chip${data.tags.includes(t) ? " on" : ""}`}
                  onClick={() => toggle(t)}>{TAG_LABEL[t]}</button>
        ))}
      </div>
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

/** F2: while the shoulders are flagged (a sore tag or check-in this week), a 0-10 check-in each day. */
function ShoulderCard({ date }: { date: string }) {
  const qc = useQueryClient();
  const run = useRun();
  const { data } = useQuery({ queryKey: ["shoulder", date], queryFn: () => api.get<ShoulderState>(`/shoulder/${date}`) });
  if (!data || data.source === "injury" || (!data.flagged && data.pain_today == null)) return null;
  const save = (v: number | null) => v != null && run(async () => {
    await api.put(`/shoulder/${date}`, { pain: v });
    qc.invalidateQueries({ queryKey: ["shoulder", date] });
  }, "Shoulder check-in saved");
  return (
    <div className="card tight stack" style={{ gap: 10 }}>
      <div className="spread">
        <span className="small" style={{ fontWeight: 550 }}>Shoulder pain today</span>
        <span className="tiny muted">0 none · 10 worst</span>
      </div>
      <Scale n={11} start={0} value={data.pain_today} onChange={save} label="shoulder pain" />
      <p className="tiny muted" style={{ margin: 0 }}>
        {data.flagged
          ? "Asked daily while your shoulders are sore. Landmine presses replace overhead pressing until two low days in a row."
          : "Shoulders look fine again. This check-in stops tomorrow."}
      </p>
    </div>
  );
}
