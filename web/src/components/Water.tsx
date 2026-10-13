import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type WaterDay } from "../api";
import { onDateNow, isoDate } from "../dates";
import { showVolume, volumeToMl } from "../units";
import { Bar, Icon, Sheet, Stepper, useRun, useUnits } from "./ui";

// One-tap sizes: a glass and a standard bottle, in the units people actually pour.
const QUICK = {
  imperial: [{ label: "8 oz glass", ml: 236.6 }, { label: "16.9 oz bottle", ml: 500 }],
  metric: [{ label: "250 ml glass", ml: 250 }, { label: "500 ml bottle", ml: 500 }],
} as const;

/** Today's (or a past day's) water with quick-add buttons. */
export function WaterCard({ date = isoDate() }: { date?: string }) {
  const units = useUnits();
  const run = useRun();
  const qc = useQueryClient();
  const [custom, setCustom] = useState(false);
  const { data } = useQuery({ queryKey: ["water", date], queryFn: () => api.get<WaterDay>(`/water/${date}`) });
  const isToday = date === isoDate();

  const add = (ml: number) => run(async () => {
    await api.post("/water", { ml, ts: (isToday ? new Date() : onDateNow(date)).toISOString() });
    qc.invalidateQueries({ queryKey: ["water", date] });
  }, `Added ${showVolume(ml, units)}`);
  const last = data?.entries.at(-1);
  const undo = () => last && run(async () => {
    await api.del(`/water/${last.id}`);
    qc.invalidateQueries({ queryKey: ["water", date] });
  }, "Removed");

  const total = data?.total_ml ?? 0;
  const target = data?.target_ml ?? 0;
  return (
    <section className="card stack hue-water" style={{ gap: 12 }} aria-label="Water">
      <div className="spread">
        <span className="row" style={{ gap: 10 }}>
          <span className="icon-circle hue-water" style={{ width: 32, height: 32 }}><Icon name="drop" size={16} /></span>
          <h3>Water</h3>
        </span>
        <span className="small num">
          <b style={{ fontWeight: 600 }}>{data ? showVolume(total, units) : "–"}</b>
          {data && <span className="muted"> / {showVolume(target, units)}</span>}
        </span>
      </div>
      <Bar value={total} target={target || null} hue="water" />
      <div className="row">
        {QUICK[units].map((q) => (
          <button key={q.label} className="btn small grow nowrap" disabled={!data} onClick={() => add(q.ml)}>
            <Icon name="plus" size={15} /> {q.label}
          </button>
        ))}
        <button className="btn small nowrap" disabled={!data} onClick={() => setCustom(true)} aria-label="Add a custom amount">
          Other
        </button>
      </div>
      <div className="spread tiny muted">
        <span>
          {data?.entries.length ? `${data.entries.length} ${data.entries.length === 1 ? "drink" : "drinks"}` : "Nothing logged yet"}
          {data?.target_basis === "weight" ? " · goal ≈ 35 ml per kg of body weight" : ""}
        </span>
        {last && <button className="btn ghost small" style={{ minHeight: 28, padding: "0 8px" }} onClick={undo}>Undo last</button>}
      </div>
      {custom && <CustomWater onClose={() => setCustom(false)} onAdd={(ml) => { add(ml); setCustom(false); }} />}
    </section>
  );
}

function CustomWater({ onClose, onAdd }: { onClose: () => void; onAdd: (ml: number) => void }) {
  const units = useUnits();
  const [v, setV] = useState(units === "imperial" ? 12 : 330);
  return (
    <Sheet title="Add water" onClose={onClose}>
      <div className="stack">
        <label className="field">Amount ({units === "imperial" ? "fl oz" : "ml"})
          <Stepper value={v} onChange={setV} step={units === "imperial" ? 1 : 50} min={1} max={units === "imperial" ? 100 : 3000} label="amount" />
        </label>
        <button className="btn primary" onClick={() => onAdd(volumeToMl(v, units))}>Add</button>
      </div>
    </Sheet>
  );
}
