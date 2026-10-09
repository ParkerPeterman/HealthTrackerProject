import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Proposals } from "../api";
import { Icon } from "../components/ui";

export function dashboardUrl(): string {
  try {
    const saved = localStorage.getItem("dashboardUrl");
    if (saved) return saved;
  } catch {
    /* storage unavailable */
  }
  return `${location.protocol}//${location.hostname}:8501`;
}

export default function More() {
  const { data } = useQuery({ queryKey: ["proposals"], queryFn: () => api.get<Proposals>("/proposals") });
  const pending = (data?.parameters.length ?? 0) + (data?.statuses.length ?? 0);
  const [dash] = useState(dashboardUrl);
  return (
    <div className="stack">
      <header className="topbar"><h1>More</h1></header>
      <div className="list">
        <Link to="/more/coach" className="list-row button" style={{ textDecoration: "none", color: "inherit" }}>
          <div className="grow">
            <div className="title">Coach</div>
            <div className="meta">Approvals, rules, personal findings, experiments, history</div>
          </div>
          {pending > 0 && <span className="badge game">{pending}</span>}
          <Icon name="chevron" size={18} />
        </Link>
        <Link to="/more/settings" className="list-row button" style={{ textDecoration: "none", color: "inherit" }}>
          <div className="grow">
            <div className="title">Settings</div>
            <div className="meta">Profile, units, training preferences, nutrition targets, goals</div>
          </div>
          <Icon name="chevron" size={18} />
        </Link>
        <a href={dash} target="_blank" rel="noreferrer" className="list-row button" style={{ textDecoration: "none", color: "inherit" }}>
          <div className="grow">
            <div className="title">Dashboard</div>
            <div className="meta">Trends, training load, nutrition, insights, pipeline health</div>
          </div>
          <Icon name="chevron" size={18} />
        </a>
      </div>
      <p className="tiny muted">
        Everything runs on your own machine. Code makes every decision; the local LLM only turns them into words.
      </p>
    </div>
  );
}
