import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Experiment, type Hypothesis, type Proposals, type Recommendation, type Rule } from "../api";
import { Chips, CoachText, Empty, Icon, Stars, useRun } from "../components/ui";
import { dayLabel } from "../dates";

type Tab = "approvals" | "rules" | "findings" | "history";

export default function Coach() {
  const [tab, setTab] = useState<Tab>("approvals");
  const navigate = useNavigate();
  return (
    <div className="stack">
      <header className="topbar">
        <button className="btn ghost icon-btn" onClick={() => navigate(-1)} aria-label="Back"><Icon name="back" /></button>
        <h1>Coach</h1>
        <span style={{ width: 40 }} />
      </header>
      <Chips options={["approvals", "rules", "findings", "history"] as const} value={tab} onChange={setTab} />
      {tab === "approvals" && <Approvals />}
      {tab === "rules" && <Rules />}
      {tab === "findings" && <Findings />}
      {tab === "history" && <History />}
    </div>
  );
}

function Approvals() {
  const run = useRun();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["proposals"], queryFn: () => api.get<Proposals>("/proposals") });
  const decide = (kind: "parameter" | "status", id: number, approve: boolean) => run(async () => {
    await api.post(`/proposals/${kind}/${id}`, { approve });
    qc.invalidateQueries({ queryKey: ["proposals"] });
    qc.invalidateQueries({ queryKey: ["rules"] });
    qc.invalidateQueries({ queryKey: ["today"] });
  }, approve ? "Approved" : "Rejected");
  if (!data) return <Empty>Loading…</Empty>;
  const none = !data.parameters.length && !data.statuses.length;
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>
        Each week the system checks your data and may propose changing a rule threshold (within safe bounds) or
        promoting a hidden rule that has earned it. Nothing changes until you approve.
      </p>
      {none && <Empty>Nothing waiting for approval.</Empty>}
      {data.parameters.map((p) => (
        <div key={p.id} className="card stack">
          <div>
            <div className="tiny muted">{p.rule_id}</div>
            <h3>{p.name.replaceAll("_", " ")}: {fmt(p.old_value)} → <span className="good">{fmt(p.new_value)}</span></h3>
          </div>
          <p className="small" style={{ margin: 0 }}>{p.reason}</p>
          <div className="tiny muted">{p.description}</div>
          <div className="row">
            <button className="btn grow" onClick={() => decide("parameter", p.id, false)}>Keep {fmt(p.old_value)}</button>
            <button className="btn primary grow" onClick={() => decide("parameter", p.id, true)}>Approve</button>
          </div>
        </div>
      ))}
      {data.statuses.map((s) => (
        <div key={s.id} className="card stack">
          <div>
            <div className="tiny muted">{s.description}</div>
            <h3>{s.rule_id}: {s.old_status} → <span className="good">{s.new_status}</span></h3>
          </div>
          <p className="small" style={{ margin: 0 }}>{s.reason}</p>
          <div className="row">
            <button className="btn grow" onClick={() => decide("status", s.id, false)}>Not yet</button>
            <button className="btn primary grow" onClick={() => decide("status", s.id, true)}>Approve</button>
          </div>
        </div>
      ))}
      {data.history.length > 0 && (
        <details className="card tight">
          <summary className="small">Decision history</summary>
          {data.history.map((h, i) => (
            <div key={i} className="small" style={{ marginTop: 6 }}>
              <b>{h.rule_id}</b> {h.name ?? "status"}: {h.old} → {h.new}{" "}
              <span className={h.approved ? "good" : "muted"}>{h.approved ? "approved" : "rejected"}</span>
              <span className="tiny muted"> · {h.proposed_by} · {new Date(h.changed_at).toLocaleDateString()}</span>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

const fmt = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/0+$/, ""));

function Rules() {
  const run = useRun();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["rules"], queryFn: () => api.get<Rule[]>("/rules") });
  const [open, setOpen] = useState<string | null>(null);
  if (!data) return <Empty>Loading…</Empty>;
  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>
        <b>Shadow</b> rules run every day and are graded, but their advice is hidden until they prove useful.
        <b> Active</b> rules show up in coaching. Safety rules are always active.
      </p>
      <div className="list">
        {data.filter((r) => r.rule_id !== "EXPERIMENT_PROTOCOL").map((r) => (
          <div key={r.rule_id}>
            <button className="list-row button" onClick={() => setOpen(open === r.rule_id ? null : r.rule_id)}>
              <div className="grow">
                <div className="title small">{r.description}</div>
                <div className="meta">
                  {r.rule_id} · {r.evidence_level}
                  {r.n_actions ? ` · followed ${Math.round((r.followed_rate ?? 0) * 100)}% of ${r.n_actions}` : ""}
                </div>
              </div>
              <span className={`badge ${r.status === "active" ? "active" : "shadow"}`}>{r.status}</span>
            </button>
            {open === r.rule_id && (
              <div className="list-row stack" style={{ alignItems: "stretch" }}>
                {r.avg_outcome_when_followed != null && (
                  <div className="small">
                    Outcome vs baseline: <b className="num">{r.avg_outcome_when_followed.toFixed(2)}</b> when followed,{" "}
                    <b className="num">{(r.avg_outcome_when_not ?? 0).toFixed(2)}</b> when not (higher is better)
                  </div>
                )}
                {r.min_history_days > 0 && <div className="tiny muted">Needs {r.min_history_days} days of history before it runs.</div>}
                {r.parameters.map((p) => (
                  <ParamRow key={p.name} rule={r.rule_id} p={p} />
                ))}
                {r.evidence_level !== "safety" && (
                  <Chips options={["shadow", "active", "retired"] as const} value={r.status}
                         onChange={(s) => run(async () => {
                           await api.put(`/rules/${r.rule_id}/status`, { status: s });
                           qc.invalidateQueries({ queryKey: ["rules"] });
                         }, `${r.rule_id} is now ${s}`)} />
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function ParamRow({ rule, p }: { rule: string; p: Rule["parameters"][number] }) {
  const run = useRun();
  const qc = useQueryClient();
  const [v, setV] = useState(String(p.value));
  const changed = parseFloat(v) !== p.value;
  return (
    <div className="row small">
      <span className="grow">{p.name.replaceAll("_", " ")} <span className="tiny muted">[{p.min_allowed}–{p.max_allowed}, default {fmt(p.default_value)}]</span></span>
      <input inputMode="decimal" style={{ width: 80, minHeight: 36 }} value={v} onChange={(e) => setV(e.target.value)} />
      <button className="btn small" disabled={!changed} onClick={() => run(async () => {
        await api.put(`/rules/${rule}/parameters/${p.name}`, { value: parseFloat(v) });
        qc.invalidateQueries({ queryKey: ["rules"] });
      }, "Saved and logged")}>Set</button>
    </div>
  );
}

function Findings() {
  const run = useRun();
  const qc = useQueryClient();
  const { data: hyps } = useQuery({ queryKey: ["hypotheses"], queryFn: () => api.get<Hypothesis[]>("/hypotheses") });
  const { data: exps } = useQuery({ queryKey: ["experiments"], queryFn: () => api.get<Experiment[]>("/experiments") });
  const runningExp = exps?.find((e) => e.status === "running" || e.status === "planned");
  if (!hyps) return <Empty>Loading…</Empty>;
  const supported = hyps.filter((h) => h.insight_status === "supported");
  const weak = hyps.filter((h) => h.insight_status === "weak");
  const waiting = hyps.filter((h) => !h.insight_status || h.insight_status === "insufficient_data");
  return (
    <div className="stack">
      {runningExp && (
        <div className="card stack">
          <div className="spread"><h3>Experiment {runningExp.status}</h3><span className="badge game">{runningExp.start_date} → {runningExp.end_date}</span></div>
          <div className="small">{runningExp.protocol?.action_text} on treatment days; normal routine on control days.</div>
          <ExperimentDays exp={runningExp} />
          <button className="btn small danger" style={{ alignSelf: "flex-start" }} onClick={() => run(async () => {
            await api.post(`/experiments/${runningExp.id}/abandon`);
            qc.invalidateQueries({ queryKey: ["experiments"] });
          }, "Experiment stopped")}>Stop experiment</button>
        </div>
      )}
      <h3 className="section-title">Supported for you ({supported.length})</h3>
      {supported.length === 0 && <Empty>Nothing has passed every check yet. That takes weeks of data, by design.</Empty>}
      {supported.map((h) => <Finding key={h.id} h={h} />)}
      <h3 className="section-title">Promising, not proven ({weak.length})</h3>
      <p className="tiny muted" style={{ margin: 0 }}>Randomized self-experiments are the strongest test. Start one for a hypothesis below.</p>
      {weak.map((h) => (
        <Finding key={h.id} h={h} action={h.can_experiment && !runningExp ? () => run(async () => {
          await api.post("/experiments", { hypothesis_id: h.id, days: 14 });
          qc.invalidateQueries({ queryKey: ["experiments"] });
          qc.invalidateQueries({ queryKey: ["today"] });
        }, "Experiment starts tomorrow: check Today each morning for your assigned condition") : undefined} />
      ))}
      <details className="card tight">
        <summary className="small">Waiting for data ({waiting.length})</summary>
        {waiting.map((h) => (
          <div key={h.id} className="small" style={{ marginTop: 6 }}>
            {h.exposure} → {h.outcome}{h.lag_days ? ` (+${h.lag_days}d)` : ""}
            <span className="tiny muted"> · {h.n_exposed ?? 0} vs {h.n_unexposed ?? 0} days (needs 20 each)</span>
          </div>
        ))}
      </details>
      {exps && exps.filter((e) => e.status === "done").map((e) => (
        <div key={e.id} className="card tight small"><b>Experiment result:</b> {e.result_summary}</div>
      ))}
    </div>
  );
}

function Finding({ h, action }: { h: Hypothesis; action?: () => void }) {
  return (
    <div className="card tight stack">
      <div className="small">{h.summary ?? `${h.exposure} → ${h.outcome}`}</div>
      {action && <button className="btn small" style={{ alignSelf: "flex-start" }} onClick={action}>Test it: 2-week experiment</button>}
    </div>
  );
}

function ExperimentDays({ exp }: { exp: Experiment }) {
  const days = Object.entries(exp.assignment ?? {}).sort();
  return (
    <div className="row wrap" aria-label="Experiment schedule">
      {days.map(([d, cond]) => (
        <span key={d} className={`badge ${cond === "treatment" ? "game" : ""}`} title={cond}>
          {new Date(d + "T12:00").toLocaleDateString(undefined, { month: "numeric", day: "numeric" })} {cond === "treatment" ? "T" : "C"}
        </span>
      ))}
    </div>
  );
}

function History() {
  const run = useRun();
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["recommendations"], queryFn: () => api.get<Recommendation[]>("/recommendations?limit=21") });
  if (!data) return <Empty>Loading…</Empty>;
  if (!data.length) return <Empty>No recommendations yet.</Empty>;
  return (
    <div className="stack">
      {data.map((r) => {
        const graded = r.actions.filter((a) => a.followed != null);
        const followed = graded.filter((a) => a.followed).length;
        return (
          <details key={r.id} className="card tight">
            <summary className="spread small">
              <span>{dayLabel(r.date)}</span>
              <span className="muted">
                {r.actions.length} actions{graded.length ? ` · ${followed}/${graded.length} followed` : ""}
                {r.rating ? ` · ${"★".repeat(r.rating)}` : ""}
              </span>
            </summary>
            <div style={{ marginTop: 8 }}>
              {r.rendered_text && <CoachText text={r.rendered_text} />}
              {graded.length > 0 && (
                <div className="list" style={{ marginTop: 8 }}>
                  {graded.map((a) => (
                    <div key={a.id} className="list-row small">
                      <span className={a.followed ? "good" : "warn"}><Icon name={a.followed ? "check" : "x"} size={16} /></span>
                      <span className="grow">{a.action_text.split(/[(:]/)[0]}</span>
                      <span className="tiny muted num">actual {a.actual_value ?? "–"}</span>
                    </div>
                  ))}
                </div>
              )}
              <div className="spread" style={{ marginTop: 8 }}>
                <span className="tiny muted">How useful was this?</span>
                <Stars value={r.rating} onChange={(v) => run(async () => {
                  await api.put(`/recommendations/${r.id}/rating`, { rating: v });
                  qc.invalidateQueries({ queryKey: ["recommendations"] });
                })} />
              </div>
            </div>
          </details>
        );
      })}
    </div>
  );
}
