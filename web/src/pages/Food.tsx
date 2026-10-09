import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { api, type Food as FoodItem, type FoodDay, type FoodEntry, type Meal, type SavedMeal } from "../api";
import { Scanner } from "../components/Scanner";
import { Bar, Chips, Empty, Icon, Sheet, Stepper, useRun } from "../components/ui";
import { addDays, dayLabel, isoDate, mealForNow, onDateNow, timeLabel, toLocalInput } from "../dates";
import { round } from "../units";

const MEALS: Meal[] = ["breakfast", "lunch", "dinner", "snack", "pre_workout", "post_workout"];
const MEAL_LABELS: Partial<Record<Meal, string>> = { pre_workout: "pre-workout", post_workout: "post-workout" };

export default function Food() {
  const [date, setDate] = useState(isoDate());
  const qc = useQueryClient();
  const run = useRun();
  const { data } = useQuery({ queryKey: ["food-day", date], queryFn: () => api.get<FoodDay>(`/food-day/${date}`) });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<FoodEntry | null>(null);
  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["food-day", date] });
    qc.invalidateQueries({ queryKey: ["today"] });
    qc.invalidateQueries({ queryKey: ["foods-recent"] });
  }, [qc, date]);

  const byMeal = useMemo(() => {
    const groups = new Map<string, FoodEntry[]>();
    for (const e of data?.entries ?? []) {
      const k = e.meal ?? "other";
      groups.set(k, [...(groups.get(k) ?? []), e]);
    }
    return [...groups.entries()].sort((a, b) => MEALS.indexOf(a[0] as Meal) - MEALS.indexOf(b[0] as Meal));
  }, [data]);

  const target = (n: string) => data?.targets.find((t) => t.nutrient === n && t.evaluation === "daily");
  const t = data?.totals ?? {};

  return (
    <div className="stack">
      <header className="topbar">
        <button className="btn ghost icon-btn" aria-label="Previous day" onClick={() => setDate(addDays(date, -1))}><Icon name="back" /></button>
        <div style={{ textAlign: "center" }}>
          <h1>{dayLabel(date)}</h1>
          <div className="sub">{data?.complete ? "Marked complete" : "Food log"}</div>
        </div>
        <button className="btn ghost icon-btn" aria-label="Next day" disabled={date >= isoDate()}
                onClick={() => setDate(addDays(date, 1))}><Icon name="chevron" /></button>
      </header>

      <section className="card stack" aria-label="Totals">
        <Macro label="Calories" value={t.kcal} unit="kcal" target={target("kcal")} />
        <Macro label="Protein" value={t.protein_g} unit="g" target={target("protein_g")} />
        <div className="grid3 small">
          <div><div className="muted tiny">Carbs</div><b className="num">{round(t.carbs_g)} g</b></div>
          <div><div className="muted tiny">Fat</div><b className="num">{round(t.fat_g)} g</b></div>
          <div><div className="muted tiny">Fiber</div><b className="num">{round(t.fiber_g)} g</b></div>
        </div>
        {(t.caffeine_mg > 0 || t.alcohol_g > 0) && (
          <div className="tiny muted">
            {t.caffeine_mg > 0 && `Caffeine ${round(t.caffeine_mg)} mg`}
            {t.caffeine_mg > 0 && t.alcohol_g > 0 && " · "}
            {t.alcohol_g > 0 && `Alcohol ${(t.alcohol_g / 14).toFixed(1)} drinks`}
          </div>
        )}
      </section>

      <button className="btn primary block" onClick={() => setAdding(true)}><Icon name="plus" size={18} /> Add food</button>

      {byMeal.length === 0 && <Empty>No food logged for {dayLabel(date).toLowerCase()}.</Empty>}
      {byMeal.map(([meal, entries]) => (
        <div key={meal} className="list">
          <div className="list-head">
            <span>{MEAL_LABELS[meal as Meal] ?? meal}</span>
            <span className="num">{round(entries.reduce((s, e) => s + (e.kcal ?? 0), 0))} kcal</span>
          </div>
          {entries.map((e) => (
            <button key={e.id} className="list-row button" onClick={() => setEditing(e)}>
              <div className="grow">
                <div className="title">{e.name}</div>
                <div className="meta">{e.servings !== 1 ? `${e.servings} × ` : ""}{e.serving_desc ?? "serving"} · {timeLabel(e.logged_ts)}</div>
              </div>
              <div className="num small" style={{ textAlign: "right" }}>
                {round(e.kcal)} kcal<div className="tiny muted">{round(e.protein_g)} g P</div>
              </div>
            </button>
          ))}
          <SaveMealButton date={date} meal={meal as Meal} />
        </div>
      ))}

      <label className="card row" style={{ cursor: "pointer" }}>
        <input type="checkbox" checked={data?.complete ?? false}
               onChange={(e) => run(async () => {
                 await api.put(`/food-day/${date}/complete`, { complete: e.target.checked });
                 refresh();
               })} />
        <span className="grow">
          <b>This day's log is complete</b>
          <div className="tiny muted">Only complete days count in nutrition stats, so a missed meal never reads as zero.</div>
        </span>
      </label>

      {adding && <AddFoodSheet date={date} onClose={() => setAdding(false)} onAdded={refresh} />}
      {editing && <EditEntrySheet entry={editing} onClose={() => setEditing(null)} onSaved={refresh} />}
    </div>
  );
}

function Macro({ label, value, unit, target }: {
  label: string; value: number | undefined; unit: string;
  target?: { min: number | null; max: number | null };
}) {
  const v = value ?? 0;
  const lo = target?.min ?? null;
  const hi = target?.max ?? null;
  return (
    <div>
      <div className="spread small">
        <b>{label}</b>
        <span className="num">
          {round(v)} {unit}
          {target && <span className="muted"> / {lo != null && hi != null ? `${lo}–${hi}` : lo ?? `≤ ${hi}`}</span>}
        </span>
      </div>
      {target && <Bar value={v} target={lo} max={hi} />}
    </div>
  );
}

function SaveMealButton({ date, meal }: { date: string; meal: Meal }) {
  const run = useRun();
  const qc = useQueryClient();
  return (
    <button className="list-row button tiny muted" style={{ minHeight: 40 }}
            onClick={() => {
              const name = window.prompt("Save this meal as…", `${MEAL_LABELS[meal] ?? meal} usual`);
              if (name) run(async () => {
                await api.post("/saved-meals", { name, from_date: date, from_meal: meal });
                qc.invalidateQueries({ queryKey: ["saved-meals"] });
              }, "Saved for one-tap logging");
            }}>
      <Icon name="copy" size={16} /> Save as a one-tap meal
    </button>
  );
}

// ------------------------------------------------------------------ add food
type Tab = "recent" | "search" | "saved" | "scan" | "new";

function AddFoodSheet({ date, onClose, onAdded }: { date: string; onClose: () => void; onAdded: () => void }) {
  const [tab, setTab] = useState<Tab>("recent");
  const [picked, setPicked] = useState<FoodItem | null>(null);
  const [meal, setMeal] = useState<Meal>(mealForNow());
  const run = useRun();
  const isToday = date === isoDate();

  const quickAdd = (f: FoodItem) => run(async () => {
    await api.post("/food-entries", {
      catalog_id: f.id, servings: f.last_servings ?? 1, meal, date,
      logged_ts: (isToday ? new Date() : onDateNow(date)).toISOString(),
    });
    onAdded();
  }, `Added ${f.name}`);

  if (picked) {
    return (
      <Sheet title="Add food" onClose={onClose}>
        <PickFood food={picked} date={date} initialMeal={meal} onBack={() => setPicked(null)}
                  onAdded={() => { onAdded(); setPicked(null); }} />
      </Sheet>
    );
  }
  return (
    <Sheet title="Add food" onClose={onClose}>
      <div className="stack">
        <Chips options={MEALS} value={meal} onChange={setMeal} labels={MEAL_LABELS} />
        <Chips options={["recent", "search", "saved", "scan", "new"] as const} value={tab} onChange={setTab}
               labels={{ new: "+ new food" }} />
        {tab === "recent" && <RecentFoods onQuickAdd={quickAdd} onPick={setPicked} />}
        {tab === "search" && <SearchFoods onPick={setPicked} />}
        {tab === "saved" && <SavedMeals date={date} meal={meal} onLogged={onAdded} />}
        {tab === "scan" && <ScanFood onFound={setPicked} />}
        {tab === "new" && <NewFood onCreated={setPicked} />}
      </div>
    </Sheet>
  );
}

function RecentFoods({ onQuickAdd, onPick }: { onQuickAdd: (f: FoodItem) => void; onPick: (f: FoodItem) => void }) {
  const { data } = useQuery({ queryKey: ["foods-recent"], queryFn: () => api.get<FoodItem[]>("/foods/recent?limit=25") });
  if (!data?.length) return <Empty>Foods you log show up here for one-tap logging.</Empty>;
  return (
    <div className="list">
      {data.map((f) => (
        <div key={f.id} className="list-row">
          <button className="grow" style={{ background: "none", border: 0, textAlign: "left", padding: 0, cursor: "pointer" }}
                  onClick={() => onPick(f)}>
            <div className="title">{f.name}</div>
            <div className="meta">{f.last_servings ?? 1} × {f.serving_desc ?? "serving"} · {round((f.kcal ?? 0) * (f.last_servings ?? 1))} kcal</div>
          </button>
          <button className="btn small accent" onClick={() => onQuickAdd(f)} aria-label={`Add ${f.name}`}>
            <Icon name="plus" size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}

function SearchFoods({ onPick }: { onPick: (f: FoodItem) => void }) {
  const [q, setQ] = useState("");
  const { data } = useQuery({
    queryKey: ["foods", q], queryFn: () => api.get<FoodItem[]>(`/foods?q=${encodeURIComponent(q)}&limit=40`),
    placeholderData: (prev) => prev,
  });
  return (
    <div className="stack">
      <input autoFocus placeholder="Search your foods" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="list">
        {(data ?? []).map((f) => (
          <button key={f.id} className="list-row button" onClick={() => onPick(f)}>
            <div className="grow">
              <div className="title">{f.name}{f.brand ? <span className="muted"> · {f.brand}</span> : null}</div>
              <div className="meta">{f.serving_desc ?? "serving"} · {round(f.kcal)} kcal · {round(f.protein_g)} g protein</div>
            </div>
            <Icon name="chevron" size={18} />
          </button>
        ))}
        {data && data.length === 0 && <div className="list-row muted small">No match. Scan it, or add it under "+ new food".</div>}
      </div>
    </div>
  );
}

function SavedMeals({ date, meal, onLogged }: { date: string; meal: Meal; onLogged: () => void }) {
  const run = useRun();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["saved-meals"], queryFn: () => api.get<SavedMeal[]>("/saved-meals") });
  if (!data?.length) return <Empty>Save a meal from the food log ("Save as a one-tap meal") to log it here in one tap.</Empty>;
  return (
    <div className="list">
      {data.map((m) => (
        <div key={m.id} className="list-row">
          <div className="grow">
            <div className="title">{m.name}</div>
            <div className="meta">{m.items.map((i) => i.name).join(", ")} · {m.kcal} kcal · {m.protein_g} g P</div>
          </div>
          <button className="btn small ghost danger" aria-label={`Delete ${m.name}`}
                  onClick={() => window.confirm(`Delete "${m.name}"?`) && run(async () => {
                    await api.del(`/saved-meals/${m.id}`);
                    qc.invalidateQueries({ queryKey: ["saved-meals"] });
                  })}><Icon name="trash" size={16} /></button>
          <button className="btn small accent" onClick={() => run(async () => {
            await api.post(`/saved-meals/${m.id}/log`, {
              meal, date, logged_ts: (date === isoDate() ? new Date() : onDateNow(date)).toISOString(),
            });
            onLogged();
          }, `Logged ${m.name}`)}>Log</button>
        </div>
      ))}
    </div>
  );
}

function ScanFood({ onFound }: { onFound: (f: FoodItem) => void }) {
  const run = useRun();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const lookup = useCallback(async (barcode: string) => {
    setBusy(true);
    const f = await run(() => api.get<FoodItem>(`/foods/barcode/${barcode}`));
    setBusy(false);
    if (f) onFound(f);
  }, [run, onFound]);
  return (
    <div className="stack">
      {!busy && <Scanner onCode={lookup} />}
      {busy && <Empty>Looking up {code || "barcode"}…</Empty>}
      <div className="row">
        <input inputMode="numeric" placeholder="Or type the barcode number" value={code}
               onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
        <button className="btn" disabled={code.length < 6 || busy} onClick={() => lookup(code)}>Look up</button>
      </div>
      <p className="tiny muted" style={{ margin: 0 }}>Unknown barcodes are looked up on Open Food Facts and saved to your catalog.</p>
    </div>
  );
}

function NewFood({ onCreated }: { onCreated: (f: FoodItem) => void }) {
  const run = useRun();
  const [f, setF] = useState<Record<string, string>>({ name: "", serving_desc: "1 serving" });
  const num = (k: string) => (f[k] ? parseFloat(f[k]) : null);
  const field = (k: string, label: string, unit = "") => (
    <label className="field">{label}{unit && <span className="muted"> ({unit})</span>}
      <input inputMode="decimal" value={f[k] ?? ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} />
    </label>
  );
  return (
    <form className="stack" onSubmit={(e) => {
      e.preventDefault();
      run(async () => {
        const food = await api.post<FoodItem>("/foods", {
          name: f.name, brand: f.brand || null, serving_desc: f.serving_desc || null, kcal: num("kcal"),
          protein_g: num("protein_g"), carbs_g: num("carbs_g"), fat_g: num("fat_g"), fiber_g: num("fiber_g"),
          caffeine_mg: num("caffeine_mg"), alcohol_g: num("alcohol_g"), sodium_mg: num("sodium_mg"),
        });
        onCreated(food);
      }, "Food saved");
    }}>
      <label className="field">Name<input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
      <div className="grid2">
        <label className="field">Brand<input value={f.brand ?? ""} onChange={(e) => setF({ ...f, brand: e.target.value })} /></label>
        <label className="field">Serving<input value={f.serving_desc} onChange={(e) => setF({ ...f, serving_desc: e.target.value })} /></label>
      </div>
      <p className="tiny muted" style={{ margin: 0 }}>Values per serving, from the label.</p>
      <div className="grid2">
        {field("kcal", "Calories", "kcal")}{field("protein_g", "Protein", "g")}
        {field("carbs_g", "Carbs", "g")}{field("fat_g", "Fat", "g")}
        {field("fiber_g", "Fiber", "g")}{field("sodium_mg", "Sodium", "mg")}
        {field("caffeine_mg", "Caffeine", "mg")}{field("alcohol_g", "Alcohol", "g")}
      </div>
      <button className="btn primary" type="submit">Save and log</button>
    </form>
  );
}

function PickFood({ food, date, initialMeal, onBack, onAdded }: {
  food: FoodItem; date: string; initialMeal: Meal; onBack: () => void; onAdded: () => void;
}) {
  const run = useRun();
  const [servings, setServings] = useState(food.last_servings ?? 1);
  const [meal, setMeal] = useState<Meal>(food.last_meal && date !== isoDate() ? food.last_meal : initialMeal);
  const [when, setWhen] = useState(toLocalInput(date === isoDate() ? new Date() : onDateNow(date)));
  const per = (v: number | null) => (v == null ? "–" : round(v * servings));
  return (
    <div className="stack">
      <button className="btn ghost small" style={{ alignSelf: "flex-start" }} onClick={onBack}><Icon name="back" size={16} /> Back</button>
      <div>
        <h2>{food.name}</h2>
        <div className="muted small">{food.brand ? `${food.brand} · ` : ""}{food.serving_desc ?? "serving"}</div>
        {food.found_in === "open_food_facts" && (
          <div className="tiny warn" style={{ marginTop: 4 }}>From Open Food Facts. Check the values against the label once.</div>
        )}
      </div>
      <label className="field">Servings<Stepper value={servings} onChange={setServings} step={0.25} min={0.25} max={20} digits={2} label="servings" /></label>
      <div className="grid3 small num">
        <div><div className="muted tiny">kcal</div><b>{per(food.kcal)}</b></div>
        <div><div className="muted tiny">protein</div><b>{per(food.protein_g)} g</b></div>
        <div><div className="muted tiny">carbs / fat</div><b>{per(food.carbs_g)} / {per(food.fat_g)} g</b></div>
      </div>
      <Chips options={MEALS} value={meal} onChange={setMeal} labels={MEAL_LABELS} />
      <label className="field">Time<input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
      <button className="btn primary" onClick={() => run(async () => {
        await api.post("/food-entries", { catalog_id: food.id, servings, meal, logged_ts: new Date(when).toISOString() });
        onAdded();
      }, `Added ${food.name}`)}>Add</button>
    </div>
  );
}

function EditEntrySheet({ entry, onClose, onSaved }: { entry: FoodEntry; onClose: () => void; onSaved: () => void }) {
  const run = useRun();
  const [servings, setServings] = useState(entry.servings);
  const [meal, setMeal] = useState<Meal>(entry.meal ?? "snack");
  const [when, setWhen] = useState(toLocalInput(new Date(entry.logged_ts)));
  return (
    <Sheet title={entry.name} onClose={onClose}>
      <div className="stack">
        <label className="field">Servings<Stepper value={servings} onChange={setServings} step={0.25} min={0.25} max={20} digits={2} label="servings" /></label>
        <Chips options={MEALS} value={meal} onChange={setMeal} labels={MEAL_LABELS} />
        <label className="field">Time<input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
        <div className="row">
          <button className="btn danger" onClick={() => run(async () => {
            await api.del(`/food-entries/${entry.id}`);
            onSaved(); onClose();
          }, "Removed")}><Icon name="trash" size={18} /> Remove</button>
          <button className="btn primary grow" onClick={() => run(async () => {
            await api.put(`/food-entries/${entry.id}`, { servings, meal, logged_ts: new Date(when).toISOString() });
            onSaved(); onClose();
          }, "Updated")}>Save</button>
        </div>
      </div>
    </Sheet>
  );
}
