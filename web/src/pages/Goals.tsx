import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api, type Goal, type GoalMetrics, type GoalPlan, type GoalType, type Target,
} from "../api";
import { Bar, Chips, Empty, Icon, Ring, Sheet, Stepper, useRun, useUnits } from "../components/ui";
import { dayLabel } from "../dates";
import { clock, fmtMetric, KM_PER_MI, parseClock, STATUS } from "../goals";
import { distUnit, fromKg, showWeight, toKg, weightUnit, type Units } from "../units";

const TYPE_LOOK: Record<GoalType, { icon: string; hue: string; label: string }> = {
  body_weight: { icon: "scale", hue: "food", label: "Weight" },
  performance: { icon: "bolt", hue: "load", label: "Performance" },
  health: { icon: "pulse", hue: "heart", label: "Health" },
  habit: { icon: "calendar", hue: "sleep", label: "Habit" },
};

const perWeek = (kg: number, units: Units) => `${fromKg(Math.abs(kg), units).toFixed(units === "imperial" ? 1 : 2)} ${weightUnit(units)}/wk`;
const shortDate = (iso: string) => new Date(iso + "T12:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });

// ------------------------------------------------------------------ page
export default function Goals() {
  const navigate = useNavigate();
  const units = useUnits();
  const { data: goals } = useQuery({ queryKey: ["goals"], queryFn: () => api.get<Goal[]>("/goals") });
  const [editing, setEditing] = useState<Goal | "new" | null>(null);
  const active = (goals ?? []).filter((g) => g.active && !g.achieved_date);
  const weight = active.find((g) => g.goal_type === "body_weight");
  const others = active.filter((g) => g !== weight);
  const done = (goals ?? []).filter((g) => !g.active || g.achieved_date);
  const onTrack = active.filter((g) => ["on_track", "ahead", "achieved"].includes(g.progress.status)).length;

  return (
    <div className="stack">
      <header className="topbar">
        <button className="btn icon-btn" onClick={() => navigate(-1)} aria-label="Back"><Icon name="back" size={20} /></button>
        <div style={{ textAlign: "center" }}>
          <div className="eyebrow">{active.length ? `${onTrack} of ${active.length} on track` : "Weight · performance · habits"}</div>
          <h1 className="compact">Goals</h1>
        </div>
        <button className="btn icon-btn" onClick={() => setEditing("new")} aria-label="New goal"><Icon name="plus" size={20} /></button>
      </header>

      {!goals && <Empty>Loading…</Empty>}
      {goals && active.length === 0 && (
        <div className="card stack" style={{ alignItems: "center", textAlign: "center", padding: 28 }}>
          <span className="icon-circle lg hue-signal"><Icon name="target" size={22} /></span>
          <h2>Set your first goal</h2>
          <p className="small muted" style={{ margin: 0 }}>
            A weight goal sets your calorie and protein targets from your own data. Performance, health, and habit
            goals are tracked from what you already log.
          </p>
        </div>
      )}

      {weight && <WeightGoalCard goal={weight} units={units} onEdit={() => setEditing(weight)} />}

      {others.length > 0 && <h3 className="section-title">Tracking</h3>}
      {others.map((g) => <GoalCard key={g.id} goal={g} units={units} onEdit={() => setEditing(g)} />)}

      <button className="btn primary block" onClick={() => setEditing("new")}><Icon name="plus" size={18} /> New goal</button>

      {done.length > 0 && (
        <details className="card tight">
          <summary className="small">Achieved and archived ({done.length})</summary>
          {done.map((g) => (
            <button key={g.id} className="list-row button small" style={{ padding: "10px 0", minHeight: 0 }} onClick={() => setEditing(g)}>
              <span className="grow">{g.description}</span>
              <span className="tiny muted">{g.achieved_date ? `achieved ${dayLabel(g.achieved_date)}` : "archived"}</span>
            </button>
          ))}
        </details>
      )}
      <p className="tiny muted" style={{ margin: "0 4px" }}>
        Progress uses your smoothed trend, so one heavy morning never reads as failure. Calorie targets only change
        when you approve them.
      </p>

      {editing && <GoalSheet goal={editing === "new" ? undefined : editing} onClose={() => setEditing(null)} />}
    </div>
  );
}

function WeightGoalCard({ goal, units, onEdit }: { goal: Goal; units: Units; onEdit: () => void }) {
  const p = goal.progress;
  const st = STATUS[p.status];
  const { data: targets } = useQuery({ queryKey: ["targets"], queryFn: () => api.get<Target[]>("/targets") });
  const kcal = targets?.find((t) => t.active && t.nutrient === "kcal");
  const protein = targets?.find((t) => t.active && t.nutrient === "protein_g");
  const u = weightUnit(units);
  const maintain = goal.direction === "maintain";
  const moved = p.current != null && p.start != null ? fromKg(p.current, units) - fromKg(p.start, units) : null;
  const total = p.target != null && p.start != null ? fromKg(p.target, units) - fromKg(p.start, units) : null;
  const signed = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}`;

  return (
    <button className="card hero stack" onClick={onEdit} style={{ gap: 16 }} aria-label={`Edit goal: ${goal.description}`}>
      <div className="spread">
        <span className="row" style={{ gap: 10 }}>
          <span className="icon-circle hue-food" style={{ width: 32, height: 32 }}><Icon name="scale" size={16} /></span>
          <span className="eyebrow" style={{ color: "var(--ink-2)" }}>Weight goal</span>
        </span>
        <span className={`badge tint hue-${st.hue}`}>{st.label}</span>
      </div>
      <h2>{goal.description}</h2>
      <div className="row" style={{ gap: 18, alignItems: "center" }}>
        <Ring value={p.pct ?? 0} max={1} size={116} stroke={10} hue={st.hue === "muted" ? "food" : st.hue}
              label={`${Math.round((p.pct ?? 0) * 100)}% of the way`}>
          <div>
            <span className="v">{maintain ? showWeight(p.current, units) : moved != null ? signed(moved) : "–"}</span>
            <span className="u">{maintain ? u : total != null ? `of ${signed(total)} ${u}` : u}</span>
          </div>
        </Ring>
        <dl className="kv grow" style={{ gridTemplateColumns: "auto 1fr", fontSize: "0.85rem" }}>
          <dt>Trend</dt><dd className="num">{showWeight(p.current, units)} → {showWeight(p.target, units)} {u}</dd>
          <dt>Pace</dt>
          <dd className="num">
            {p.rate_per_week == null ? "–" : Math.abs(fromKg(p.rate_per_week, units)) < 0.05 ? "flat"
              : `${p.rate_per_week > 0 ? "+" : "−"}${perWeek(p.rate_per_week, units)}`}
            {goal.rate_per_week ? <span className="muted"> · plan {goal.rate_per_week > 0 ? "+" : "−"}{perWeek(goal.rate_per_week, units)}</span> : null}
          </dd>
          {!maintain && <><dt>Lands</dt><dd>{p.projected_date ? shortDate(p.projected_date) : <span className="muted">not at this pace</span>}</dd></>}
        </dl>
      </div>
      <div className="spread small" style={{ paddingTop: 12, borderTop: "1px solid var(--line)" }}>
        <span className="row" style={{ gap: 8 }}><Icon name="flame" size={16} />
          <span className="num">
            {kcal ? `${kcal.min_value?.toLocaleString() ?? "–"}–${kcal.max_value?.toLocaleString() ?? "–"} kcal` : "No calorie target"}
            {protein?.basis === "per_kg_bodyweight" && protein.min_value ? ` · ${protein.min_value} g/kg protein` : ""}
          </span>
        </span>
        <span className="tiny muted">{kcal?.source_ref?.startsWith("weight goal") ? "from this goal" : "edit to set"}</span>
      </div>
    </button>
  );
}

function DayDots({ days }: { days: NonNullable<Goal["progress"]["days"]> }) {
  return (
    <div className="row" style={{ gap: 6 }} role="img"
         aria-label={days.map((d) => `${d.date}: ${d.hit == null ? "no data" : d.hit ? "hit" : "missed"}`).join(", ")}>
      {days.map((d) => {
        const dow = new Date(d.date + "T12:00").toLocaleDateString(undefined, { weekday: "narrow" });
        return (
          <span key={d.date} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
            <span style={{
              width: 22, height: 22, borderRadius: "50%",
              background: d.hit ? "var(--sleep)" : "transparent",
              border: d.hit ? "0" : d.hit === false ? "1.5px solid var(--faint)" : "1.5px dashed var(--surface-3)",
              boxShadow: d.hit ? "0 0 10px color-mix(in srgb, var(--sleep) var(--glow), transparent)" : "none",
            }} />
            <span className="tiny muted" style={{ fontFamily: "var(--font-label)", fontSize: "0.62rem" }}>{dow}</span>
          </span>
        );
      })}
    </div>
  );
}

function GoalCard({ goal, units, onEdit }: { goal: Goal; units: Units; onEdit: () => void }) {
  const p = goal.progress;
  const st = STATUS[p.status];
  const look = TYPE_LOOK[goal.goal_type];
  return (
    <button className="card stack" onClick={onEdit} style={{ gap: 14 }} aria-label={`Edit goal: ${goal.description}`}>
      <div className="row">
        <span className={`icon-circle hue-${look.hue}`}><Icon name={look.icon} size={18} /></span>
        <span className="grow">
          <span style={{ fontWeight: 550, display: "block" }}>{goal.description}</span>
          <span className="tiny muted">{look.label}{goal.target_date ? ` · by ${shortDate(goal.target_date)}` : ""}</span>
        </span>
        <span className={`badge tint hue-${st.hue}`}>{st.label}</span>
      </div>
      {goal.goal_type === "habit" && p.days ? (
        <div className="spread">
          <DayDots days={p.days} />
          <span className="num" style={{ textAlign: "right" }}>
            <span className="display-num">{p.hits ?? 0}/{p.needed ?? 7}</span>
            <span className="tiny muted" style={{ display: "block" }}>last 7 days</span>
          </span>
        </div>
      ) : (
        <div className="stack" style={{ gap: 8 }}>
          <div className="spread small">
            <span className="muted">Now <b className="num" style={{ color: "var(--ink)", fontWeight: 600 }}>{fmtMetric(goal.metric, p.current, units)}</b></span>
            <span className="muted">Target <b className="num" style={{ color: "var(--ink)", fontWeight: 600 }}>{fmtMetric(goal.metric, goal.target_value, units)}</b></span>
          </div>
          <Bar value={p.pct ?? 0} target={1} hue={st.hue === "muted" ? look.hue : st.hue} />
        </div>
      )}
    </button>
  );
}

// ------------------------------------------------------------------ create / edit
const PERF = ["run_5k_min", "e1rm_kg", "pace_at_ref_hr_s_km", "vo2max"] as const;
const HEALTH = ["resting_hr", "hrv", "vo2max"] as const;
const HABITS = ["sleep_total_min", "protein_g_per_kg", "food_log_complete", "training_day", "alcohol_drinks"] as const;
const METRIC_LABEL: Record<string, string> = {
  run_5k_min: "5K time", e1rm_kg: "Lift 1RM", pace_at_ref_hr_s_km: "Easy pace", vo2max: "VO₂ max",
  resting_hr: "Resting HR", hrv: "HRV", sleep_total_min: "Sleep", protein_g_per_kg: "Protein",
  food_log_complete: "Food log", training_day: "Train", alcohol_drinks: "Alcohol",
};
const HABIT_DEFAULT: Record<string, number> = { sleep_total_min: 7.5, protein_g_per_kg: 1.6, alcohol_drinks: 2 };
const PACES: Record<Units, { lose: number[]; gain: number[] }> = {
  imperial: { lose: [0.25, 0.5, 0.75, 1], gain: [0.25, 0.5] },
  metric: { lose: [0.1, 0.25, 0.35, 0.5], gain: [0.1, 0.25] },
};

/** Stored metric value → what the user types in the target field. */
function toInput(metric: string, v: number | null, units: Units): string {
  if (v == null) return "";
  if (metric === "run_5k_min") return clock(v);
  if (metric === "pace_at_ref_hr_s_km") return clock((units === "imperial" ? v * KM_PER_MI : v) / 60);
  if (metric === "e1rm_kg") return String(Math.round(fromKg(v, units)));
  return String(Math.round(v * 10) / 10);
}

/** What the user typed → stored metric value. */
function fromInput(metric: string, txt: string, units: Units): number | null {
  if (metric === "run_5k_min") return parseClock(txt);
  if (metric === "pace_at_ref_hr_s_km") {
    const min = parseClock(txt);
    return min == null ? null : (min * 60) / (units === "imperial" ? KM_PER_MI : 1);
  }
  const n = parseFloat(txt);
  if (!Number.isFinite(n)) return null;
  return metric === "e1rm_kg" ? toKg(n, units) : n;
}

function GoalSheet({ goal, onClose }: { goal?: Goal; onClose: () => void }) {
  const units = useUnits();
  const run = useRun();
  const qc = useQueryClient();
  const { data: metrics } = useQuery({ queryKey: ["goal-metrics"], queryFn: () => api.get<GoalMetrics>("/goals/metrics") });
  const currentOf = (key: string) => metrics?.metrics.find((m) => m.key === key)?.current ?? null;

  const [type, setType] = useState<GoalType>(goal?.goal_type ?? "body_weight");
  // weight
  const [dir, setDir] = useState<"decrease" | "increase" | "maintain">(goal?.direction ?? "decrease");
  const [targetW, setTargetW] = useState<number | null>(goal?.target_value != null ? Math.round(fromKg(goal.target_value, units) * 10) / 10 : null);
  const [pace, setPace] = useState<number>(goal?.rate_per_week ? Math.round(fromKg(Math.abs(goal.rate_per_week), units) * 100) / 100
    : units === "imperial" ? 0.5 : 0.25);
  const [apply, setApply] = useState(true);
  // other goals
  const [metric, setMetric] = useState<string>(goal?.metric && goal.goal_type !== "body_weight" ? goal.metric : "run_5k_min");
  const [targetTxt, setTargetTxt] = useState(goal?.metric ? toInput(goal.metric, goal.target_value, units) : "");
  const [exerciseId, setExerciseId] = useState<number | null>(goal?.exercise_id ?? null);
  const [habitVal, setHabitVal] = useState<number>(goal?.goal_type === "habit" && goal.target_value != null
    ? (goal.metric === "sleep_total_min" ? goal.target_value / 60 : goal.target_value) : 7.5);
  const [days, setDays] = useState(goal?.days_per_week ?? 5);
  const [date, setDate] = useState(goal?.target_date ?? "");
  const [desc, setDesc] = useState(goal?.description ?? "");
  const [descTouched, setDescTouched] = useState(!!goal);

  // Prefill a weight target 5 lb (2.5 kg) from today's trend once it loads.
  const nowKg = currentOf("weight_kg");
  useEffect(() => {
    if (targetW == null && nowKg != null) {
      const now = fromKg(nowKg, units);
      setTargetW(Math.round((dir === "maintain" ? now : now + (dir === "decrease" ? -1 : 1) * (units === "imperial" ? 5 : 2.5)) * 2) / 2);
    }
  }, [nowKg, units, dir, targetW]);

  const pickType = (t: GoalType) => {
    setType(t);
    if (t === "performance") setMetric("run_5k_min");
    if (t === "health") setMetric("resting_hr");
    if (t === "habit") { setMetric("sleep_total_min"); setHabitVal(HABIT_DEFAULT.sleep_total_min); }
    setTargetTxt("");
  };
  const pickMetric = (m: string) => {
    setMetric(m);
    setTargetTxt("");
    if (m in HABIT_DEFAULT) setHabitVal(HABIT_DEFAULT[m]);
  };

  const rateKg = dir === "maintain" ? 0 : (dir === "decrease" ? -1 : 1) * toKg(pace, units);
  const targetKg = targetW != null ? toKg(targetW, units) : null;
  const { data: plan, isFetching: planLoading } = useQuery({
    queryKey: ["goal-plan", rateKg.toFixed(3), targetKg?.toFixed(2)],
    queryFn: () => api.get<GoalPlan>(`/goals/plan?rate_kg_per_week=${rateKg.toFixed(3)}${targetKg != null ? `&target_kg=${targetKg.toFixed(2)}` : ""}`),
    enabled: type === "body_weight", placeholderData: keepPreviousData,
  });

  const lift = metrics?.lifts.find((l) => l.id === exerciseId);
  const habitTarget = metric === "sleep_total_min" ? habitVal * 60 : ["food_log_complete", "training_day"].includes(metric) ? null : habitVal;
  const levelTarget = fromInput(metric, targetTxt, units);

  const autoDesc = useMemo(() => {
    const u = weightUnit(units);
    if (type === "body_weight") {
      if (targetW == null) return "Weight goal";
      return dir === "maintain" ? `Hold ${targetW} ${u}` : `${dir === "decrease" ? "Lose" : "Gain"} to ${targetW} ${u}`;
    }
    if (type === "habit") {
      const n = `${days} ${days === 1 ? "day" : "days"} a week`;
      return {
        sleep_total_min: `Sleep ${habitVal} h, ${days} nights a week`, protein_g_per_kg: `${habitVal} g/kg protein, ${n}`,
        food_log_complete: `Complete food log, ${n}`, training_day: `Train ${n}`, alcohol_drinks: `${habitVal} drinks or fewer, ${n}`,
      }[metric] ?? "Habit";
    }
    const t = levelTarget == null ? "…" : fmtMetric(metric, levelTarget, units);
    return {
      run_5k_min: `5K in ${t}`, e1rm_kg: `${lift?.name ?? "Lift"} ${t} 1RM`, pace_at_ref_hr_s_km: `${t} at reference HR`,
      vo2max: `VO₂ max ${t}`, resting_hr: `Resting HR ${t}`, hrv: `HRV ${t}`,
    }[metric] ?? "Goal";
  }, [type, dir, targetW, units, days, habitVal, metric, levelTarget, lift]);

  const ready = type === "body_weight" ? targetW != null
    : type === "habit" ? true
    : levelTarget != null && (metric !== "e1rm_kg" || exerciseId != null);

  const refresh = () => ["goals", "today", "targets", "food-day", "proposals", "goal-metrics"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const save = (active = goal?.active ?? true) => run(async () => {
    const body = {
      goal_type: type, description: (descTouched && desc.trim()) || autoDesc,
      metric: type === "body_weight" ? "weight_kg" : metric,
      direction: type === "body_weight" ? dir : null,
      target_value: type === "body_weight" ? targetKg : type === "habit" ? habitTarget : levelTarget,
      target_date: type === "habit" ? null : date || null,
      rate_per_week: type === "body_weight" ? rateKg : null,
      days_per_week: type === "habit" ? days : null,
      exercise_id: metric === "e1rm_kg" && type === "performance" ? exerciseId : null,
      start_value: goal && goal.goal_type === type && goal.metric === (type === "body_weight" ? "weight_kg" : metric) ? goal.start_value : null,
      active, apply_targets: type === "body_weight" && apply && active,
    };
    if (goal) await api.put(`/goals/${goal.id}`, body);
    else await api.post("/goals", body);
    refresh();
    onClose();
  }, goal ? (active ? "Goal updated" : "Goal archived") : type === "body_weight" && apply ? "Goal set, and targets updated" : "Goal set");

  const now = currentOf(metric);
  const paces = dir === "increase" ? PACES[units].gain : PACES[units].lose;

  return (
    <Sheet title={goal ? "Edit goal" : "New goal"} onClose={onClose}>
      <div className="stack" style={{ gap: 14 }}>
        {!goal && (
          <Chips options={["body_weight", "performance", "habit", "health"] as const} value={type} onChange={pickType}
                 labels={{ body_weight: "Weight", performance: "Performance", habit: "Habit", health: "Health" }} />
        )}

        {type === "body_weight" && (
          <>
            <Chips options={["decrease", "increase", "maintain"] as const} value={dir}
                   onChange={(d) => { setDir(d); setTargetW(null); setPace(units === "imperial" ? 0.5 : 0.25); }}
                   labels={{ decrease: "Lose", increase: "Gain", maintain: "Maintain" }} />
            <label className="field">{dir === "maintain" ? "Weight to hold" : "Target weight"} ({weightUnit(units)})
              {nowKg != null && <span className="muted" style={{ fontWeight: 400 }}>Trend today: {showWeight(nowKg, units)} {weightUnit(units)}</span>}
              <Stepper value={targetW ?? 0} onChange={setTargetW} step={units === "imperial" ? 0.5 : 0.25} digits={1}
                       min={60} max={600} label="target weight" />
            </label>
            {dir !== "maintain" && (
              <div className="field">Pace ({weightUnit(units)} per week)
                <div className="chips" role="radiogroup">
                  {paces.map((v) => (
                    <button key={v} type="button" role="radio" aria-checked={pace === v} className={`chip${pace === v ? " on" : ""}`}
                            onClick={() => setPace(v)}>{v}</button>
                  ))}
                </div>
              </div>
            )}
            <PlanPreview plan={plan} loading={planLoading} units={units} dir={dir} />
            <label className="card tight row" style={{ cursor: "pointer" }}>
              <span className="grow small">
                <span style={{ fontWeight: 550 }}>Set my calorie and protein targets from this</span>
                <span className="tiny muted" style={{ display: "block", marginTop: 2 }}>
                  After that, the weekly check re-estimates what you burn and proposes changes for you to approve.
                </span>
              </span>
              <input type="checkbox" className="switch" checked={apply} onChange={(e) => setApply(e.target.checked)} />
            </label>
          </>
        )}

        {(type === "performance" || type === "health") && (
          <>
            <Chips options={(type === "performance" ? PERF : HEALTH) as readonly string[]} value={metric} onChange={pickMetric}
                   labels={METRIC_LABEL} />
            {metric === "e1rm_kg" && (
              <div className="field">Lift
                <div className="row wrap">
                  {(metrics?.lifts ?? []).map((l) => (
                    <button key={l.id} type="button" className={`chip${exerciseId === l.id ? " on" : ""}`} onClick={() => setExerciseId(l.id)}>{l.name}</button>
                  ))}
                  {metrics && !metrics.lifts.length && <span className="tiny muted">Mark an exercise as a main lift to track its 1RM.</span>}
                </div>
              </div>
            )}
            <label className="field">
              Target {metric === "run_5k_min" ? "(mm:ss)" : metric === "pace_at_ref_hr_s_km" ? `(mm:ss per ${distUnit(units)})`
                : metric === "e1rm_kg" ? `(${weightUnit(units)})` : metric === "resting_hr" ? "(bpm)" : metric === "hrv" ? "(ms)" : ""}
              <span className="muted" style={{ fontWeight: 400 }}>
                Now: {metric === "e1rm_kg" ? fmtMetric(metric, lift?.best_e1rm_kg ?? null, units) : fmtMetric(metric, now, units)}
              </span>
              <input inputMode={metric.endsWith("_min") || metric.startsWith("pace") ? "text" : "decimal"} value={targetTxt}
                     placeholder={metric === "run_5k_min" ? "21:00" : metric === "pace_at_ref_hr_s_km" ? "8:30" : ""}
                     onChange={(e) => setTargetTxt(e.target.value)} />
            </label>
            <label className="field">By (optional)<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
          </>
        )}

        {type === "habit" && (
          <>
            <Chips options={HABITS} value={metric as (typeof HABITS)[number]} onChange={pickMetric} labels={METRIC_LABEL} />
            {metric in HABIT_DEFAULT && (
              <label className="field">
                {metric === "sleep_total_min" ? "At least (hours a night)" : metric === "protein_g_per_kg" ? "At least (g per kg)" : "At most (drinks a day)"}
                <Stepper value={habitVal} onChange={setHabitVal} step={metric === "alcohol_drinks" ? 1 : metric === "sleep_total_min" ? 0.25 : 0.1}
                         min={0} max={metric === "sleep_total_min" ? 10 : metric === "protein_g_per_kg" ? 3 : 10}
                         digits={metric === "alcohol_drinks" ? 0 : metric === "sleep_total_min" ? 2 : 1} label="daily target" />
              </label>
            )}
            <label className="field">Days per week
              <Stepper value={days} onChange={setDays} min={1} max={7} label="days per week" />
            </label>
          </>
        )}

        <label className="field">Name
          <input value={descTouched ? desc : autoDesc} onChange={(e) => { setDesc(e.target.value); setDescTouched(true); }} />
        </label>
        <button className="btn primary" disabled={!ready} onClick={() => save()}>{goal ? "Save" : "Set goal"}</button>
        {goal && (
          <div className="row">
            <button className="btn grow" onClick={() => save(!goal.active)}>{goal.active ? "Archive" : "Restore"}</button>
            <button className="btn danger icon-btn" aria-label="Delete goal" onClick={() => window.confirm("Delete this goal?") && run(async () => {
              await api.del(`/goals/${goal.id}`);
              refresh();
              onClose();
            }, "Goal deleted")}><Icon name="trash" size={18} /></button>
          </div>
        )}
      </div>
    </Sheet>
  );
}

function PlanPreview({ plan, loading, units, dir }: { plan?: GoalPlan; loading: boolean; units: Units; dir: string }) {
  if (!plan) return <div className="card tight small muted">{loading ? "Working out your numbers…" : ""}</div>;
  if (plan.missing || !plan.plan || !plan.expenditure) return <div className="card tight small muted">{plan.missing}</div>;
  const e = plan.expenditure;
  const p = plan.plan;
  return (
    <div className="card stack" style={{ gap: 10, background: "var(--surface-2)" }} aria-live="polite">
      <div className="spread">
        <span className="eyebrow">Your plan</span>
        <span className="tiny muted">{e.source === "data" ? `from ${e.logged_days} logged days` : "estimate from your profile"}</span>
      </div>
      <div className="row" style={{ alignItems: "baseline", gap: 8 }}>
        <span className="big-num md">{p.kcal_min.toLocaleString()}–{p.kcal_max.toLocaleString()}</span>
        <span className="small muted">kcal a day</span>
      </div>
      <div className="small" style={{ color: "var(--ink-2)" }}>
        You burn about <b className="num">{Math.round(e.kcal).toLocaleString()}</b> kcal a day.
        {" "}Protein <b className="num">{p.protein_g_per_kg} g/kg</b>.
        {dir !== "maintain" && (plan.projected_date
          ? <> Reaches your target around <b>{shortDate(plan.projected_date)}</b>.</>
          : null)}
      </div>
      {e.source === "formula" && (
        <div className="tiny muted">Two weeks of complete food logs replace this estimate with what you actually burn.</div>
      )}
      {p.capped && (
        <div className="tiny warn">
          Pace capped at {perWeek(p.rate_kg_per_week, units)} ({dir === "increase" ? "0.5" : "1"}% of body weight a week).
        </div>
      )}
      {p.floor_applied && <div className="tiny warn">Held at the minimum calorie floor; the pace will be slower.</div>}
    </div>
  );
}
