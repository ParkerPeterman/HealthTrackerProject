import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, PHASE_LABEL, type Profile, type SeasonData, type Target, type ZonesData } from "../api";
import { Chips, Icon, Stepper, useProfile, useRun } from "../components/ui";
import { dashboardUrl } from "./More";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;

export default function Settings() {
  const navigate = useNavigate();
  const run = useRun();
  const qc = useQueryClient();
  const { data: profile } = useProfile();
  const [p, setP] = useState<Profile | null>(null);
  useEffect(() => { if (profile) setP(profile); }, [profile]);
  const [dash, setDash] = useState(dashboardUrl());

  const saveProfile = () => p && run(async () => {
    await api.put("/profile", p);
    qc.invalidateQueries();
  }, "Profile saved");

  if (!p) return <div className="empty">Loading…</div>;
  const prefs = p.training_prefs;
  const setPrefs = (patch: Partial<Profile["training_prefs"]>) => setP({ ...p, training_prefs: { ...prefs, ...patch } });
  const heightIn = p.height_cm ? Math.round(p.height_cm / 2.54) : null;

  return (
    <div className="stack">
      <header className="topbar">
        <button className="btn ghost icon-btn" onClick={() => navigate(-1)} aria-label="Back"><Icon name="back" /></button>
        <h1>Settings</h1>
        <span style={{ width: 40 }} />
      </header>

      <section className="card stack">
        <h3>Profile</h3>
        <div className="grid2">
          <label className="field">Birth date<input type="date" value={p.birth_date ?? ""} onChange={(e) => setP({ ...p, birth_date: e.target.value || null })} /></label>
          <label className="field">Sex (for nutrient targets)
            <select value={p.sex ?? ""} onChange={(e) => setP({ ...p, sex: (e.target.value || null) as Profile["sex"] })}>
              <option value="">–</option><option value="male">male</option><option value="female">female</option><option value="other">other</option>
            </select>
          </label>
          <label className="field">Height ({p.display_units === "imperial" ? "in" : "cm"})
            <input inputMode="decimal" value={p.display_units === "imperial" ? heightIn ?? "" : p.height_cm ?? ""}
                   onChange={(e) => {
                     const v = parseFloat(e.target.value);
                     setP({ ...p, height_cm: Number.isFinite(v) ? (p.display_units === "imperial" ? v * 2.54 : v) : null });
                   }} />
          </label>
          <label className="field">Reference HR (bpm)
            <input inputMode="numeric" placeholder="auto" value={p.ref_hr_bpm ?? ""}
                   onChange={(e) => setP({ ...p, ref_hr_bpm: parseInt(e.target.value) || null })} />
          </label>
        </div>
        <p className="tiny muted" style={{ margin: 0 }}>Reference HR is the heart rate used for the "pace at reference HR" fitness trend. An easy aerobic HR (around 75% of max) works well.</p>
        <label className="field">Time zone<input value={p.timezone} onChange={(e) => setP({ ...p, timezone: e.target.value })} /></label>
        <label className="field">Units<Chips options={["imperial", "metric"] as const} value={p.display_units} onChange={(u) => setP({ ...p, display_units: u })} /></label>
      </section>

      <section className="card stack">
        <h3>Training plan</h3>
        <label className="row small"><input type="checkbox" checked={prefs.generate_plan} onChange={(e) => setPrefs({ generate_plan: e.target.checked })} /> Auto-plan each week around my games</label>
        <div className="grid2">
          <label className="field">Runs / week<Stepper value={prefs.runs_per_week} onChange={(v) => setPrefs({ runs_per_week: v })} min={0} max={7} label="runs per week" /></label>
          <label className="field">Lifts / week<Stepper value={prefs.lifts_per_week} onChange={(v) => setPrefs({ lifts_per_week: v })} min={0} max={5} label="lifts per week" /></label>
          <label className="field">Rest days (min)<Stepper value={prefs.min_rest_days} onChange={(v) => setPrefs({ min_rest_days: v })} min={0} max={3} label="rest days" /></label>
        </div>
        <label className="field">Long run day<Chips options={DAYS} value={prefs.long_run_day as (typeof DAYS)[number]} onChange={(d) => setPrefs({ long_run_day: d })} /></label>
      </section>
      <button className="btn primary" onClick={saveProfile}>Save profile</button>

      <ZonesCard />

      <SeasonCard />

      <Targets />

      <section className="card stack">
        <h3>System</h3>
        <button className="btn" onClick={() => run(async () => { await api.post("/pipeline/run", { job: "nightly" }); }, "Daily job started")}>Run the daily job now</button>
        <button className="btn" onClick={() => run(async () => { await api.post("/pipeline/run", { job: "weekly" }); }, "Weekly job started (insights, proposals, next week's plan)")}>Run the weekly job now</button>
        <label className="field">Dashboard address
          <input value={dash} onChange={(e) => setDash(e.target.value)} onBlur={() => {
            try { localStorage.setItem("dashboardUrl", dash); } catch { /* storage unavailable */ }
          }} />
        </label>
      </section>
    </div>
  );
}

const LABELS: Record<string, string> = {
  kcal: "Calories (kcal)", protein_g: "Protein (g/kg)", fiber_g: "Fiber (g)", iron_mg: "Iron (mg)",
  magnesium_mg: "Magnesium (mg)", calcium_mg: "Calcium (mg)", potassium_mg: "Potassium (mg)", zinc_mg: "Zinc (mg)",
  vitamin_d_mcg: "Vitamin D (mcg)", sodium_mg: "Sodium (mg)",
};

function Targets() {
  const run = useRun();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["targets"], queryFn: () => api.get<Target[]>("/targets") });
  const active = (data ?? []).filter((t) => t.active);
  const kcal = active.find((t) => t.nutrient === "kcal");
  const [lo, setLo] = useState("");
  const [hi, setHi] = useState("");
  useEffect(() => { setLo(kcal?.min_value?.toString() ?? ""); setHi(kcal?.max_value?.toString() ?? ""); }, [kcal?.min_value, kcal?.max_value]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["targets"] });
  return (
    <section className="card stack">
      <h3>Nutrition targets</h3>
      <div className="grid2">
        <label className="field">Calories from<input inputMode="numeric" value={lo} onChange={(e) => setLo(e.target.value)} /></label>
        <label className="field">to<input inputMode="numeric" value={hi} onChange={(e) => setHi(e.target.value)} /></label>
      </div>
      <button className="btn" onClick={() => run(async () => {
        await api.post("/targets", { nutrient: "kcal", basis: "per_day", evaluation: "daily", min_value: parseFloat(lo) || null, max_value: parseFloat(hi) || null, source_ref: "my goal" });
        refresh();
      }, "Calorie goal saved")}>Save calorie goal</button>
      <div className="list">
        {active.map((t) => (
          <div key={t.id} className="list-row small">
            <span className="grow">{LABELS[t.nutrient] ?? t.nutrient}<div className="tiny muted">{t.evaluation === "weekly_avg" ? "weekly average" : "daily"}{t.source_ref ? ` · ${t.source_ref}` : ""}</div></span>
            <span className="num">{t.min_value != null && t.max_value != null ? `${t.min_value}–${t.max_value}` : t.min_value != null ? `${t.min_value}+` : `≤ ${t.max_value}`}</span>
            <button className="btn ghost small danger" aria-label={`Remove ${t.nutrient}`} onClick={() => run(async () => { await api.del(`/targets/${t.id}`); refresh(); })}>
              <Icon name="x" size={16} />
            </button>
          </div>
        ))}
      </div>
      <button className="btn" onClick={() => run(async () => { await api.post("/targets/defaults"); refresh(); }, "Loaded reference intakes for your age and sex")}>
        Load micronutrient defaults (NIH reference intakes)
      </button>
      <p className="tiny muted" style={{ margin: 0 }}>Defaults use your sex and age from the profile. Worth a check with a dietitian if you want sport-specific targets.</p>
    </section>
  );
}

const SOURCE: Record<string, string> = {
  self_report: "self-reported", field_test: "field test", observed: "seen in a recorded session", manual: "entered by hand",
};

/** Max HR and the zones built from it (D3). Provisional until the field test confirms it. */
function ZonesCard() {
  const run = useRun();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["zones"], queryFn: () => api.get<ZonesData>("/zones") });
  const [maxHr, setMaxHr] = useState("");
  const z = data?.current;
  return (
    <section className="card stack">
      <div className="spread">
        <h3>Heart-rate zones</h3>
        {z && <span className={`badge tint ${z.confirmed ? "hue-hrv" : "hue-load"}`}>{z.confirmed ? "confirmed" : "provisional"}</span>}
      </div>
      {z ? (
        <>
          <div className="small">
            Max HR <b className="num">{z.max_hr} bpm</b> <span className="muted">({SOURCE[z.source] ?? z.source}, {z.valid_from})</span>
          </div>
          <div className="list">
            {z.five.map((r) => (
              <div key={r.zone} className="list-row small num">
                <span style={{ width: 64 }}>Zone {r.zone}</span>
                <span className="grow muted">{r.pct_max[0]}–{r.pct_max[1]}% max</span>
                <span>{r.bpm[0]}–{r.bpm[1]}</span>
                {r.hrr_bpm && <span className="tiny muted" style={{ width: 84, textAlign: "right" }}>HRR {r.hrr_bpm[0]}–{r.hrr_bpm[1]}</span>}
              </div>
            ))}
          </div>
          <p className="tiny muted" style={{ margin: 0 }}>
            Easy under {z.three[1].bpm[0]} bpm, hard from {z.three[2].bpm[0]} bpm. 4 × 4 intervals target {z.vo2_target_bpm[0]}–{z.vo2_target_bpm[1]} bpm.
            HRR uses your resting HR{z.resting_hr ? ` (${z.resting_hr} bpm)` : ""}.
          </p>
        </>
      ) : <p className="small muted" style={{ margin: 0 }}>No max HR yet. Enter what you know; the field test will confirm it.</p>}
      {!z?.confirmed && (
        <p className="tiny muted" style={{ margin: 0 }}>
          A max HR you type in stays provisional until the max HR test (Train → Performance tests) confirms it. Wrist sensors
          can lock onto running cadence, so a chest strap gives the cleanest test. Every past session is recomputed when it changes.
        </p>
      )}
      <div className="row">
        <input className="grow" inputMode="numeric" placeholder="Max HR (bpm)" value={maxHr} onChange={(e) => setMaxHr(e.target.value)} />
        <button className="btn" disabled={!(parseInt(maxHr) >= 120 && parseInt(maxHr) <= 240)} onClick={() => run(async () => {
          await api.post("/zones", { max_hr: parseInt(maxHr), source: "self_report" });
          setMaxHr("");
          qc.invalidateQueries({ queryKey: ["zones"] });
        }, "Max HR saved (provisional)")}>Set</button>
      </div>
    </section>
  );
}

/** F1: the season's phases, all following from the first game unless set by hand. */
function SeasonCard() {
  const run = useRun();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["season"], queryFn: () => api.get<SeasonData>("/season") });
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  useEffect(() => {
    if (data) { setFirst(data.season.first_game); setLast(data.season.season_end ?? ""); }
  }, [data]);
  if (!data) return null;
  const changed = first !== data.season.first_game || last !== (data.season.season_end ?? "");
  return (
    <section className="card stack">
      <div className="spread"><h3>Season</h3><span className="badge tint hue-signal">{PHASE_LABEL[data.current]}</span></div>
      <div className="grid2">
        <label className="field">First game<input type="date" value={first} onChange={(e) => setFirst(e.target.value)} /></label>
        <label className="field">Last game (optional)<input type="date" value={last} onChange={(e) => setLast(e.target.value)} /></label>
      </div>
      <button className="btn" disabled={!changed || !first} onClick={() => run(async () => {
        await api.put("/season", { first_game: first, season_end: last || null });
        ["season", "planned", "today"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      }, "Season saved; the phases follow from it")}>Save season</button>
      <div className="list">
        {data.phases.map((ph) => (
          <div key={ph.phase} className="list-row small">
            <span className="grow"><b>{PHASE_LABEL[ph.phase]}</b><span className="tiny muted" style={{ display: "block" }}>{ph.focus}</span></span>
            <span className="tiny muted num" style={{ textAlign: "right" }}>{ph.start_date.slice(5)} – {ph.end_date ? ph.end_date.slice(5) : "…"}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
