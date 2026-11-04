// Performance tests (A8): max HR, 5 km time trial, sprints, and jumps, under the Train tab.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type PerfTest, type TestKey, type TestsData } from "../api";
import { Chips, Icon, Sheet, useRun, useUnits } from "../components/ui";
import { dayLabel, isoDate } from "../dates";
import { clock, parseClock } from "../goals";
import { lengthUnit, showLength, toCm, type Units } from "../units";

const KEYS: TestKey[] = ["max_hr", "time_trial_5k", "sprint_10m", "sprint_30m", "cmj", "broad_jump"];
const SHORT: Record<TestKey, string> = {
  max_hr: "Max HR", time_trial_5k: "5 km", sprint_10m: "10 m", sprint_30m: "30 m", cmj: "Jump", broad_jump: "Broad jump",
};

export function fmtTest(key: TestKey, v: number | null | undefined, units: Units): string {
  if (v == null) return "–";
  if (key === "max_hr") return `${Math.round(v)} bpm`;
  if (key === "time_trial_5k") return clock(v / 60);
  if (key === "cmj" || key === "broad_jump") return showLength(v, units);
  return `${v.toFixed(2)} s`;
}

export function TestsCard() {
  const units = useUnits();
  const today = isoDate();
  const { data, error } = useQuery({ queryKey: ["tests"], queryFn: () => api.get<TestsData>("/tests") });
  const [sheet, setSheet] = useState<TestKey | null>(null);
  return (
    <>
      <div className="spread">
        <h3 className="section-title">Performance tests</h3>
        <button className="btn small" onClick={() => setSheet("sprint_10m")} style={{ marginTop: 16 }}>
          <Icon name="plus" size={16} /> Log
        </button>
      </div>
      <div className="list">
        {!data && <div className="list-row muted small">{error ? "Can't reach the server." : "Loading…"}</div>}
        {data && KEYS.map((k) => {
          const s = data.summary[k];
          const label = data.protocols.find((p) => p.key === k)?.label ?? k;
          return (
            <button key={k} className="list-row button" onClick={() => setSheet(k)}>
              <span className="icon-circle hue-load" style={{ width: 36, height: 36 }}><Icon name={k === "max_hr" ? "pulse" : "bolt"} size={17} /></span>
              <div className="grow">
                <div className="title">{label}</div>
                <div className="meta">
                  {s.last ? `${fmtTest(k, s.last.value, units)} · ${dayLabel(s.last.date, today)}` : "Not tested yet"}
                  {s.best != null && s.count > 1 ? ` · best ${fmtTest(k, s.best, units)}` : ""}
                  {k === "max_hr" && s.last ? (s.last.chest_strap ? " · chest strap" : " · wrist") : ""}
                </div>
              </div>
              {s.due && <span className={`badge ${s.overdue ? "game" : ""}`}>{s.overdue ? "due" : `next ${dayLabel(s.due, today)}`}</span>}
            </button>
          );
        })}
      </div>
      {data && <p className="tiny muted" style={{ margin: "0 4px" }}>{data.when}</p>}
      {sheet && data && <TestSheet initial={sheet} data={data} onClose={() => setSheet(null)} />}
    </>
  );
}

function TestSheet({ initial, data, onClose }: { initial: TestKey; data: TestsData; onClose: () => void }) {
  const run = useRun();
  const qc = useQueryClient();
  const units = useUnits();
  const [key, setKey] = useState<TestKey>(initial);
  const [trials, setTrials] = useState<string[]>(["", "", ""]);
  const [value, setValue] = useState("");
  const [strap, setStrap] = useState(false);
  const [date, setDate] = useState(isoDate());
  const [method, setMethod] = useState("");
  const [notes, setNotes] = useState("");
  const proto = data.protocols.find((p) => p.key === key)!;
  const isJump = key === "cmj" || key === "broad_jump";
  const multi = proto.best_of > 1;
  const recent = data.tests.filter((t) => t.test === key).slice(0, 5);

  const parseOne = (txt: string): number | null => {
    if (key === "time_trial_5k") {
      const min = parseClock(txt);
      return min == null ? null : Math.round(min * 60);
    }
    const n = parseFloat(txt.replace(",", "."));
    if (!Number.isFinite(n)) return null;
    return isJump ? Math.round(toCm(n, units) * 10) / 10 : n;
  };
  const parsedTrials = trials.map(parseOne).filter((v): v is number => v != null);
  const single = parseOne(value);
  const ready = multi ? parsedTrials.length > 0 : single != null;
  const unit = isJump ? lengthUnit(units) : key === "max_hr" ? "bpm" : key === "time_trial_5k" ? "mm:ss" : "s";

  const save = () => run(async () => {
    await api.post<PerfTest>("/tests", {
      test: key, date, chest_strap: key === "max_hr" && strap, method: method || null, notes: notes || null,
      ...(multi ? { trials: parsedTrials } : { value: single }),
    });
    ["tests", "goals", "goal-metrics", "zones", "profile"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    onClose();
  }, "Test saved");

  return (
    <Sheet title="Log a test" onClose={onClose}>
      <div className="stack">
        <Chips options={KEYS} value={key} labels={SHORT} onChange={(k) => { setKey(k); setTrials(["", "", ""]); setValue(""); }} />
        <p className="small" style={{ margin: 0 }}>{proto.how}</p>
        {multi ? (
          <div className="field">Attempts ({unit}); the best one counts
            <div className="grid3">
              {trials.map((t, i) => (
                <input key={i} inputMode="decimal" aria-label={`Attempt ${i + 1}`} value={t} placeholder={`#${i + 1}`}
                       onChange={(e) => setTrials(trials.map((x, j) => (j === i ? e.target.value : x)))} />
              ))}
            </div>
          </div>
        ) : (
          <label className="field">Result ({unit})
            <input inputMode={key === "time_trial_5k" ? "text" : "numeric"} value={value} placeholder={key === "time_trial_5k" ? "21:30" : "195"}
                   onChange={(e) => setValue(e.target.value)} />
          </label>
        )}
        {key === "max_hr" && (
          <label className="row small"><input type="checkbox" checked={strap} onChange={(e) => setStrap(e.target.checked)} /> Measured with a chest strap</label>
        )}
        <label className="field">Date<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        <input placeholder={isJump ? "Method (jump app, wall reach)" : key.startsWith("sprint") ? "Method (slow-motion video, partner)" : "Method (optional)"}
               value={method} onChange={(e) => setMethod(e.target.value)} />
        <input placeholder="Conditions or notes (surface, warm-up, how it felt)" value={notes} onChange={(e) => setNotes(e.target.value)} />
        <p className="tiny muted" style={{ margin: 0 }}>{data.when}</p>
        <button className="btn primary" disabled={!ready} onClick={save}>Save</button>
        {recent.length > 0 && (
          <div className="list">
            {recent.map((t) => (
              <div key={t.id} className="list-row small">
                <span className="grow">{dayLabel(t.date)}{t.trials?.length ? <span className="tiny muted"> · {t.trials.map((v) => fmtTest(key, v, units)).join(", ")}</span> : null}</span>
                <span className="num">{fmtTest(key, t.value, units)}</span>
                <button className="btn ghost small danger icon-btn" aria-label="Delete test"
                        onClick={() => run(async () => { await api.del(`/tests/${t.id}`); qc.invalidateQueries({ queryKey: ["tests"] }); })}>
                  <Icon name="x" size={16} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </Sheet>
  );
}

