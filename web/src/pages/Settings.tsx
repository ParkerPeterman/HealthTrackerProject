import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Profile, type Target } from "../api";
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
            <span className="num">{t.min_value ?? "–"}{t.max_value != null ? `–${t.max_value}` : "+"}</span>
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
